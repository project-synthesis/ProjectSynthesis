"""Tests for ``pipeline_phases`` pure helpers.

Covers observability guards: the ``optimize_inject`` log line must
report ``strategy_intel=0`` when the incoming value is None, empty, or
whitespace-only — matching the upstream enrichment log's
``strategy_intel=none`` semantics. Prevents the log from counting
rendered wrapper tags (e.g. ``<strategy-intelligence></strategy-intelligence>``)
as real payload.
"""

from __future__ import annotations

import logging
import re

import pytest

from app.schemas.pipeline_contracts import AnalysisResult
from app.services.pipeline_phases import build_optimize_context
from app.services.prompt_loader import PromptLoader
from app.services.strategy_loader import StrategyLoader


def _make_analysis() -> AnalysisResult:
    return AnalysisResult(
        task_type="coding",
        weaknesses=["vague"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="good for coding",
        confidence=0.9,
    )


@pytest.fixture
def loaders(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    strategies = prompts / "strategies"
    strategies.mkdir()
    # Template embeds the wrapper tags so render output ALWAYS includes them —
    # this mimics the real ``optimize.md`` where wrapper presence does not
    # imply content presence.
    (prompts / "optimize.md").write_text(
        "{{raw_prompt}}\n{{analysis_summary}}\n{{strategy_instructions}}\n"
        "<codebase-context>\n{{codebase_context}}\n</codebase-context>\n"
        "<strategy-intelligence>\n{{strategy_intelligence}}\n</strategy-intelligence>\n"
        "{{applied_patterns}}\n{{few_shot_examples}}\n{{divergence_alerts}}\n"
    )
    (prompts / "manifest.json").write_text(
        '{"optimize.md": {"required": ["raw_prompt", "strategy_instructions", '
        '"analysis_summary"], "optional": ["codebase_context", '
        '"strategy_intelligence", "applied_patterns", "few_shot_examples", '
        '"divergence_alerts"]}}'
    )
    (strategies / "chain-of-thought.md").write_text("Think step by step.")
    prompt_loader = PromptLoader(prompts_dir=prompts)
    strategy_loader = StrategyLoader(strategies_dir=strategies)
    return prompt_loader, strategy_loader


def _extract_strategy_intel_chars(log_records) -> int:
    """Parse ``strategy_intel=N`` from the optimize_inject log record."""
    for rec in log_records:
        msg = rec.getMessage()
        if "optimize_inject" in msg:
            match = re.search(r"strategy_intel=(\d+)", msg)
            if match:
                return int(match.group(1))
    raise AssertionError("optimize_inject log not emitted")


@pytest.mark.asyncio
class TestOptimizeInjectStrategyIntelLog:
    """I-7: ``strategy_intel=`` count must ignore wrapper-only / empty values."""

    async def _invoke(self, loaders, db_session, caplog, strategy_intelligence):
        prompt_loader, strategy_loader = loaders
        caplog.set_level(logging.INFO, logger="app.services.pipeline_phases")
        await build_optimize_context(
            raw_prompt="Write a sort function.",
            analysis=_make_analysis(),
            effective_strategy="chain-of-thought",
            effective_domain="general",
            prompt_loader=prompt_loader,
            strategy_loader=strategy_loader,
            db=db_session,
            applied_pattern_ids=None,
            auto_injected_patterns=[],
            codebase_context=None,
            strategy_intelligence=strategy_intelligence,
            divergence_alerts=None,
            prompt_embedding=None,
            trace_id="trace-i7",
        )
        return _extract_strategy_intel_chars(caplog.records)

    async def test_none_reports_zero(self, loaders, db_session, caplog):
        chars = await self._invoke(loaders, db_session, caplog, None)
        assert chars == 0, "None strategy_intelligence must log strategy_intel=0"

    async def test_empty_string_reports_zero(self, loaders, db_session, caplog):
        chars = await self._invoke(loaders, db_session, caplog, "")
        assert chars == 0

    async def test_whitespace_only_reports_zero(self, loaders, db_session, caplog):
        # A whitespace-only value would otherwise render and contribute the
        # wrapper-tag envelope to ``len(optimize_msg)`` — the per-field
        # count must still be 0 because there is no real content.
        chars = await self._invoke(loaders, db_session, caplog, "   \n  \n")
        assert chars == 0

    async def test_real_content_reports_length(self, loaders, db_session, caplog):
        payload = "Top strategies: chain-of-thought (8.2 avg)"
        chars = await self._invoke(loaders, db_session, caplog, payload)
        assert chars == len(payload)


# ---------------------------------------------------------------------------
# build_pipeline_result — repo_full_name propagation to SSE completion event
# ---------------------------------------------------------------------------


class TestBuildPipelineResultRepoPropagation:
    """``PipelineResult.repo_full_name`` must reflect the inputs.

    The SSE ``optimization_complete`` event serializes
    ``PipelineResult.model_dump()``.  AA1 auto-resolves
    ``effective_repo`` from the most-recently-linked repo for curl/API
    callers, and that resolution must surface in the event stream so
    downstream consumers (UI, CLI) see the project binding even when the
    request body itself omits ``repo_full_name``.
    """

    def _persistence_inputs(self, *, repo_full_name: str | None):
        from app.schemas.pipeline_contracts import (
            AnalysisResult,
            OptimizationResult,
        )
        from app.services.pipeline_phases import PersistenceInputs

        analysis = AnalysisResult(
            task_type="coding",
            weaknesses=["vague"],
            strengths=["concise"],
            selected_strategy="chain-of-thought",
            strategy_rationale="good for coding",
            confidence=0.9,
        )
        optimization = OptimizationResult(
            optimized_prompt="rewritten prompt",
            changes_summary="tightened scope",
        )
        return PersistenceInputs(
            opt_id="opt-abc",
            raw_prompt="raw",
            analysis=analysis,
            optimization=optimization,
            effective_strategy="chain-of-thought",
            effective_domain="backend",
            domain_raw="backend",
            cluster_id=None,
            scoring=None,
            suggestions=[],
            phase_durations={"analyze_ms": 1},
            model_ids={"optimize": "claude-opus-4-7"},
            optimizer_model="claude-opus-4-7",
            provider_name="claude_cli",
            repo_full_name=repo_full_name,
            project_id="proj-1",
            context_sources={},
            trace_id="trace-1",
            duration_ms=100,
            applied_pattern_ids=None,
            auto_injected_cluster_ids=[],
            taxonomy_engine=None,
        )

    def test_repo_full_name_propagates_when_set(self):
        """Explicit repo in PersistenceInputs flows to PipelineResult."""
        from app.services.pipeline_phases import build_pipeline_result

        inputs = self._persistence_inputs(
            repo_full_name="project-synthesis/ProjectSynthesis",
        )
        result = build_pipeline_result(inputs)
        assert result.repo_full_name == "project-synthesis/ProjectSynthesis"

    def test_repo_full_name_none_preserved(self):
        """``None`` stays ``None`` — no synthetic default."""
        from app.services.pipeline_phases import build_pipeline_result

        inputs = self._persistence_inputs(repo_full_name=None)
        result = build_pipeline_result(inputs)
        assert result.repo_full_name is None


class TestNormalizeLLMDomain:
    """Hyphenated sub-domain reconciliation against the live registry.

    Covers the case where the LLM follows the analyze.md hyphen-style
    instruction (``"backend-observability"``) instead of the colon-style
    (``"backend: observability"``). Both styles are valid per the prompt;
    only the colon style parses correctly downstream.

    Pinned by the 2026-04-25 cycle-3 incident: prompt #7 (score 9.0)
    landed under ``general`` with domain string ``"backend-observability"``
    instead of joining ``backend``.
    """

    def test_hyphen_with_known_primary_normalized(self):
        from app.services.pipeline_phases import _normalize_llm_domain

        result = _normalize_llm_domain(
            "backend-observability", {"backend", "database", "general"},
        )
        assert result == "backend: observability"

    def test_hyphen_with_unknown_primary_unchanged(self):
        """``cyber-security`` stays as-is when ``cyber`` is not a known domain.

        The LLM is allowed to invent single-word domain names; until that
        invented label survives warm-path discovery and gets registered,
        we cannot safely split on the hyphen.
        """
        from app.services.pipeline_phases import _normalize_llm_domain

        result = _normalize_llm_domain(
            "cyber-security", {"backend", "database", "general"},
        )
        assert result == "cyber-security"

    def test_colon_already_present_unchanged(self):
        """No double-rewrite if LLM already used canonical syntax."""
        from app.services.pipeline_phases import _normalize_llm_domain

        result = _normalize_llm_domain(
            "backend: auth middleware", {"backend"},
        )
        assert result == "backend: auth middleware"

    def test_no_hyphen_unchanged(self):
        from app.services.pipeline_phases import _normalize_llm_domain

        result = _normalize_llm_domain("backend", {"backend"})
        assert result == "backend"

    def test_empty_unchanged(self):
        from app.services.pipeline_phases import _normalize_llm_domain

        assert _normalize_llm_domain("", {"backend"}) == ""

    def test_idempotent(self):
        """Running twice produces the same result as running once."""
        from app.services.pipeline_phases import _normalize_llm_domain

        once = _normalize_llm_domain(
            "backend-observability", {"backend"},
        )
        twice = _normalize_llm_domain(once, {"backend"})
        assert once == twice == "backend: observability"

    def test_trailing_hyphen_unchanged(self):
        """``backend-`` (empty qualifier) doesn't normalize to ``backend: ``."""
        from app.services.pipeline_phases import _normalize_llm_domain

        result = _normalize_llm_domain("backend-", {"backend"})
        assert result == "backend-"

    def test_multi_word_qualifier_after_hyphen(self):
        """LLM might emit ``backend-async-session``; join the tail.

        ``str.partition`` splits on the first hyphen, so the qualifier
        becomes ``async-session``. That's the desired semantic — the
        primary is ``backend`` and the qualifier preserves its own
        hyphenated structure.
        """
        from app.services.pipeline_phases import _normalize_llm_domain

        result = _normalize_llm_domain(
            "backend-async-session", {"backend"},
        )
        assert result == "backend: async-session"


# ---------------------------------------------------------------------------
# F3 — persist_and_propagate threads task_type into improvement_score weights
# ---------------------------------------------------------------------------


class TestPersistAnalysisWeights:
    """``persist_and_propagate`` improvement_score loop must consult
    ``get_dimension_weights(inputs.analysis.task_type)`` instead of the
    module-level ``DIMENSION_WEIGHTS`` literal.

    The two improvement_score sites at ``pipeline_phases.py:1071-1086``
    iterate ``DIMENSION_WEIGHTS.items()`` directly today.  After F3,
    they must iterate the per-task-type schema so analysis-class prompts
    score against the analysis weights instead of the uniform default.

    See spec §F3 'Sites NOT touched' for why the ``.overall`` property
    keeps the static schema.
    """

    def test_persist_uses_analysis_weights_for_analysis(self):
        """AC-F3-7: helper is wired into ``pipeline_phases`` for the persist loop.

        Forward-compatible regression guard: verifies that the
        ``get_dimension_weights`` helper from ``pipeline_contracts`` is
        in scope inside ``pipeline_phases`` (i.e. imported), which is
        the structural prerequisite for the improvement_score loops at
        lines 1071-1086 consulting per-task-type weights instead of the
        static ``DIMENSION_WEIGHTS`` literal.

        RED: ``get_dimension_weights`` doesn't exist in
        ``pipeline_contracts`` → both the import below AND the
        ``hasattr`` structural check fail.

        GREEN: after the helper is added and ``pipeline_phases``
        imports it, ``get_dimension_weights('analysis')`` returns the
        analysis schema, which the improvement_score loops will then
        use via ``inputs.analysis.task_type`` plumbing.
        """
        # Helper lives in pipeline_contracts and must be imported into
        # pipeline_phases for use inside persist_and_propagate's two
        # improvement_score loops.
        from app.schemas.pipeline_contracts import (
            ANALYSIS_DIMENSION_WEIGHTS,
            DIMENSION_WEIGHTS,
            get_dimension_weights,
        )

        # The helper's analysis branch must be the same dict the
        # improvement_score loop will iterate when
        # inputs.analysis.task_type == 'analysis'.
        assert get_dimension_weights("analysis") is ANALYSIS_DIMENSION_WEIGHTS
        assert get_dimension_weights("coding") is DIMENSION_WEIGHTS
        assert get_dimension_weights(None) is DIMENSION_WEIGHTS

        # The GREEN implementation MUST replace the two
        # ``DIMENSION_WEIGHTS`` literal iterations at lines 1071-1086
        # with ``get_dimension_weights(inputs.analysis.task_type).items()``,
        # which requires importing the helper into the module namespace.
        import app.services.pipeline_phases as pp

        assert hasattr(pp, "get_dimension_weights"), (
            "pipeline_phases must import get_dimension_weights so the "
            "improvement_score loops can consult per-task-type weights"
        )


# ---------------------------------------------------------------------------
# Cycle 4: persist_and_propagate migration to WriteQueue
#
# Per spec § 3.4 + plan task 4: the 5 commit sites in ``persist_and_propagate``
# (Optimization row, injection provenance, pattern usefulness, usage_db
# separate-session usage propagation, and any terminal write) collapse into
# ONE submit() callback. The ``usage_db = async_session_factory()`` separate
# session pattern (pre-fix lines 1180-1198) is subsumed: the queue callback's
# session does all writes serially.
#
# Mirrors cycle 2 (bulk_persist) + cycle 3 (batch_taxonomy_assign) Option C
# dual-typed dispatch. Until cycle 7, ``write_queue=None`` keeps the legacy
# session-based path live for the still-unmigrated orchestrator caller.
# ---------------------------------------------------------------------------


def _build_persistence_inputs_fixture(
    *, opt_id: str | None = None,
) -> "object":
    """Construct a minimal ``PersistenceInputs`` for cycle 4 queue tests.

    Mirrors ``TestBuildPipelineResultRepoPropagation._persistence_inputs``
    above but exposes a module-level helper so the queue tests don't
    depend on instance-method scoping. Score fields populated so the
    improvement_score branch fires (heuristic_baseline path stays unused
    -- ``scoring=None`` keeps the test focused on the queue-dispatch
    contract, not on F3.1/F4 score-blending invariants tested elsewhere).
    """
    import uuid as _uuid

    from app.schemas.pipeline_contracts import (
        AnalysisResult,
        OptimizationResult,
    )
    from app.services.pipeline_phases import PersistenceInputs

    analysis = AnalysisResult(
        task_type="coding",
        weaknesses=["vague"],
        strengths=["concise"],
        selected_strategy="chain-of-thought",
        strategy_rationale="ok",
        confidence=0.9,
    )
    optimization = OptimizationResult(
        optimized_prompt="rewritten prompt",
        changes_summary="tightened scope",
    )
    return PersistenceInputs(
        opt_id=opt_id or str(_uuid.uuid4()),
        raw_prompt="raw prompt body",
        analysis=analysis,
        optimization=optimization,
        effective_strategy="chain-of-thought",
        effective_domain="general",
        domain_raw="general",
        cluster_id=None,
        scoring=None,
        suggestions=[],
        phase_durations={"analyze_ms": 1},
        model_ids={"optimize": "claude-opus-4-7"},
        optimizer_model="claude-opus-4-7",
        provider_name="claude_cli",
        repo_full_name=None,
        project_id=None,
        context_sources={},
        trace_id="trace-c4",
        duration_ms=100,
        applied_pattern_ids=None,
        auto_injected_cluster_ids=[],
        taxonomy_engine=None,
    )


class TestPersistAndPropagateViaQueue:
    """Cycle 4 RED → GREEN: ``persist_and_propagate`` routes through the
    single-writer ``WriteQueue`` instead of operating directly on a
    caller-supplied ``AsyncSession``.

    Acceptance per spec § 3.4 + plan task 4:

    * ``write_queue`` keyword arg accepted on the function signature.
    * When supplied, ALL 5 v0.4.12 commit sites collapse into ONE
      ``submit()`` call carrying ``operation_label='persist_and_propagate'``.
    * The ``usage_db = async_session_factory()`` separate-session pattern is
      gone — usage propagation runs inside the queue callback's session.
    * Failure semantics propagate ``WriteQueue*Error`` to the caller; events
      do not fire on a failed submit (parallels cycle 2/3 contract).

    Until cycle 5+ migrates the orchestrator caller, ``write_queue=None``
    keeps the legacy session-based path live. The signature must default to
    ``None`` so existing call sites compile unchanged.
    """

    @pytest.mark.asyncio
    async def test_persist_and_propagate_routes_through_queue(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: passing ``write_queue=`` to ``persist_and_propagate`` must
        produce ONE ``submit()`` call labelled ``persist_and_propagate``.

        FAILS pre-GREEN with ``TypeError: persist_and_propagate() got an
        unexpected keyword argument 'write_queue'`` -- the v0.4.12 signature
        is ``(db, inputs)`` only.

        After GREEN, the queue captures exactly one submit; legacy path
        (when ``write_queue=None``) continues to operate on ``db`` directly.
        """
        import pytest as _pytest

        from app.services import pipeline_phases as pp

        captured: list[str | None] = []
        original_submit = write_queue_inmem.submit

        async def _capture_submit(work, *, timeout=None, operation_label=None):
            captured.append(operation_label)
            return await original_submit(
                work, timeout=timeout, operation_label=operation_label,
            )

        monkeypatch.setattr(write_queue_inmem, "submit", _capture_submit)
        inputs = _build_persistence_inputs_fixture()

        # Pre-GREEN: TypeError because ``persist_and_propagate`` does not
        # accept ``write_queue=``. Post-GREEN: returns None and ``captured``
        # contains exactly the ``persist_and_propagate`` label.
        await pp.persist_and_propagate(  # type: ignore[call-arg]
            inputs, write_queue=write_queue_inmem,
        )

        assert "persist_and_propagate" in captured, (
            "expected one submit() with operation_label='persist_and_propagate'; "
            f"got {captured!r}"
        )
        # Pin: exactly one submit (5 commits collapse into 1). If a future
        # refactor reintroduces a second submit (e.g. usage_db re-extracted),
        # this assertion fires and forces a spec re-read.
        assert captured.count("persist_and_propagate") == 1, (
            "5 commit sites must collapse into ONE submit; "
            f"got {captured.count('persist_and_propagate')} submits"
        )
        # Suppress unused-import warning -- pytest is referenced for the
        # @pytest.mark.asyncio decorator on this method.
        _ = _pytest

    @pytest.mark.asyncio
    async def test_pipeline_phases_uses_single_writer_session_inside_submit(
        self, write_queue_inmem, monkeypatch,
    ):
        """RED: the queue callback must NOT open a second
        ``async_session_factory()`` for usage propagation. The v0.4.12 code
        opened ``usage_db`` as a fresh session post-commit (lines 1180-1198);
        post-GREEN that block is gone -- the single writer session inside
        ``_do_persist`` does both the commit AND the usage propagation.

        Failure mode pre-GREEN: ``persist_and_propagate`` rejects
        ``write_queue=`` (TypeError). Same RED signal as the sibling test.

        Failure mode if a future refactor re-introduces ``usage_db``: the
        async_session_factory spy fires and the test fails LOUDLY with the
        offending call site in the traceback.
        """
        from app.services import pipeline_phases as pp

        # Spy on async_session_factory imports anywhere in pipeline_phases'
        # transitive call graph. The v0.4.12 code grabbed it via local
        # ``from app.database import async_session_factory`` -- patching
        # that attribute on the database module catches both the local and
        # any transitive reference.
        factory_calls: list[str] = []

        def _spy(*args, **kwargs):
            factory_calls.append("called")
            from app.database import async_session_factory as _real
            return _real(*args, **kwargs)

        monkeypatch.setattr("app.database.async_session_factory", _spy)
        inputs = _build_persistence_inputs_fixture()

        await pp.persist_and_propagate(  # type: ignore[call-arg]
            inputs, write_queue=write_queue_inmem,
        )

        assert factory_calls == [], (
            "post-GREEN persist_and_propagate must NOT call "
            "async_session_factory() inside the queue callback; "
            f"got {len(factory_calls)} call(s)"
        )
