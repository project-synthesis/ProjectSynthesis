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


# ---------------------------------------------------------------------------
# Cycle 4 OPERATE: dynamic concurrency + legacy-path stress + provenance/usage
#                  propagation under realistic load
# ---------------------------------------------------------------------------
#
# Per ``feedback_tdd_protocol.md`` Phase 5, dynamic verification under
# realistic concurrent load — proves the migrated ``persist_and_propagate``
# actually delivers on the queue's promises (no ``database is locked`` under
# N=5 contention) AND pins five invariants the integrate review surfaced:
#
# * Test #1 — N=5 concurrent QUEUE callers: queue serialization eliminates
#   writer contention, all 5 events fire, all 5 rows land, queue depth never
#   exceeds the cap.
# * Test #2 — N=5 concurrent LEGACY callers: documents the v0.4.13 cycle 4
#   transitional risk surfaced by integrate concern #2 — the legacy
#   ``(db, inputs)`` path is still in use by ``pipeline.py`` until cycle 5
#   migrates it. Verifies whether ``database is locked`` is recoverable via
#   ``WriterLockedAsyncSession`` or surfaces to the caller. Findings inform
#   cycle 5 migration scope.
# * Test #3 — provenance writes after commit: pins the v0.4.5 invariant
#   surfaced by integrate concern #3 (full ``auto_injected_*`` propagation)
#   surviving cycle 4. ``OptimizationPattern(relationship='injected')`` rows
#   land because the parent commit happens BEFORE
#   ``record_injection_provenance``.
# * Test #4 — usage propagation through queue: pins integrate concern #3 —
#   ``taxonomy_engine.increment_usage`` fires for ``auto_injected_cluster_ids``
#   AND ``applied_pattern_ids`` paths inside the same queue callback, with
#   ``PromptCluster.usage_count`` actually incremented (no separate
#   ``usage_db`` session).
# * Test #5 — event emission ordering: ``optimization_created`` fires AFTER
#   ``submit()`` returns; if ``submit()`` raises, no event fires (failure
#   semantics from cycle 2/3 contract).
# ---------------------------------------------------------------------------


class TestPersistAndPropagateOperate:
    """OPERATE phase: mirrors cycle 2/3 ``Test*Operate`` structure for
    cycle 4 ``persist_and_propagate``.

    Test #1 uses ``writer_engine_file`` for real WAL contention. Test #2
    uses a separate file-mode engine + ``WriterLockedAsyncSession`` to
    exercise the legacy path without the queue. Tests #3-5 use the
    in-memory queue fixture (logic-only, no contention required).

    The autouse ``_reset_taxonomy_engine`` fixture below ensures every
    test starts with a fresh ``TaxonomyEngine`` singleton + dirty-set so
    accumulated state from prior tests doesn't bleed into assert paths.
    """

    @pytest.fixture(autouse=True)
    def _reset_taxonomy_engine(self):
        """Each test gets a fresh process singleton.

        ``persist_and_propagate`` reads ``inputs.taxonomy_engine`` for
        usage propagation; tests that don't pass an engine still touch
        the singleton transitively through the event_bus + decision
        logger paths. Reusing a singleton across tests makes the engine
        state path-dependent on test ordering.
        """
        from app.services.taxonomy import reset_engine
        reset_engine()
        yield
        reset_engine()

    # -- Test #1: N=5 concurrent QUEUE callers, real WAL contention ---------

    @pytest.mark.asyncio
    async def test_persist_and_propagate_n5_concurrent_via_queue(
        self, writer_engine_file, caplog,
    ):
        """N=5 concurrent ``persist_and_propagate`` callers, each with a
        distinct trace_id + opt_id, routing through the ``WriteQueue``.

        The queue's serialization is the only defense against SQLite
        writer contention — without it, file-mode WAL with concurrent
        writers surfaces 'database is locked' in SQLAlchemy ERROR-level
        logs. Five commits per call (Optimization + applied + provenance
        + usefulness + usage) all collapse into ONE ``submit()``.

        Asserts:
        - All 5 ``optimization_created`` events fire (one per call).
        - All 5 ``Optimization`` rows in DB (verifiable via SELECT).
        - Zero 'database is locked' log records.
        - Queue depth never exceeds ``max_depth`` during the run.
        - Wall-clock budget < 30s.
        """
        import asyncio as _asyncio
        import logging as _logging
        import time as _time
        import uuid as _uuid

        from sqlalchemy import text as _sa_text

        from app.models import Base
        from app.services import pipeline_phases as pp
        from app.services.event_bus import event_bus
        from app.services.write_queue import WriteQueue

        # Materialize schema on the file-mode engine (writer_engine_file
        # does NOT auto-create tables, only writer_engine_inmem does).
        async with writer_engine_file.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        queue = WriteQueue(writer_engine_file, max_depth=64)
        await queue.start()

        # Subscribe BEFORE persists fire so we don't miss events.
        ev_queue: _asyncio.Queue = _asyncio.Queue(maxsize=500)
        event_bus._subscribers.add(ev_queue)

        # Track queue depth across callers — a sentinel sample loop.
        observed_depths: list[int] = []
        depth_sampler_done = _asyncio.Event()

        async def _sample_depth() -> None:
            while not depth_sampler_done.is_set():
                observed_depths.append(queue.queue_depth)
                try:
                    await _asyncio.wait_for(
                        depth_sampler_done.wait(), timeout=0.05,
                    )
                except _asyncio.TimeoutError:
                    pass

        sampler_task = _asyncio.create_task(_sample_depth())

        try:
            # Build 5 distinct PersistenceInputs with unique opt_ids/trace_ids.
            inputs_list = [
                _build_persistence_inputs_fixture(opt_id=str(_uuid.uuid4()))
                for _ in range(5)
            ]
            for i, inp in enumerate(inputs_list):
                # Pin trace_id so we can map events back to callers.
                # PersistenceInputs is a frozen-style dataclass via
                # ``@dataclass`` — assignment works, no immutability.
                object.__setattr__(inp, "trace_id", f"trace-c4-op-{i}")

            t0 = _time.monotonic()
            with caplog.at_level(_logging.WARNING):
                await _asyncio.gather(*[
                    pp.persist_and_propagate(inp, write_queue=queue)
                    for inp in inputs_list
                ])
            elapsed = _time.monotonic() - t0
            depth_sampler_done.set()
            await sampler_task

            # O1: SELECT to verify user-visible state. 5 rows in DB.
            opt_ids = [inp.opt_id for inp in inputs_list]
            ids_param = ",".join(f"'{oid}'" for oid in opt_ids)
            async with writer_engine_file.connect() as conn:
                count_q = await conn.execute(_sa_text(
                    f"SELECT COUNT(*) FROM optimizations "  # noqa: S608
                    f"WHERE id IN ({ids_param})"
                ))
                row = count_q.first()
                row_count = int(row[0]) if row else 0
            assert row_count == 5, (
                f"expected 5 Optimization rows from N=5 concurrent persists, "
                f"got {row_count}"
            )

            # O2: zero 'database is locked' anywhere in caplog.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            assert locked_records == [], (
                f"got {len(locked_records)} 'database is locked' records "
                f"under N=5 queue concurrency: "
                f"{[r.getMessage() for r in locked_records[:3]]}"
            )

            # 5 ``optimization_created`` events fired (one per call).
            collected: list[dict] = []
            while True:
                try:
                    collected.append(ev_queue.get_nowait())
                except _asyncio.QueueEmpty:
                    break
            created_events = [
                e for e in collected if e.get("event") == "optimization_created"
            ]
            assert len(created_events) == 5, (
                f"expected 5 optimization_created events, "
                f"got {len(created_events)}"
            )
            # Each trace_id appears exactly once in event payloads.
            trace_ids_in_events = sorted(
                e["data"].get("trace_id") for e in created_events
            )
            expected_traces = sorted(
                f"trace-c4-op-{i}" for i in range(5)
            )
            assert trace_ids_in_events == expected_traces, (
                f"trace_id set mismatch: got {trace_ids_in_events}, "
                f"expected {expected_traces}"
            )

            # Queue depth bounded — never exceeded our max_depth (64).
            max_seen_depth = max(observed_depths) if observed_depths else 0
            assert max_seen_depth <= 64, (
                f"queue depth peaked at {max_seen_depth}, exceeded cap"
            )

            # Wall-clock budget: 5 persists under N=5 concurrency comfortably <30s.
            assert elapsed < 30.0, (
                f"queue stress run took {elapsed:.1f}s, > 30s budget"
            )
        finally:
            depth_sampler_done.set()
            if not sampler_task.done():
                await sampler_task
            event_bus._subscribers.discard(ev_queue)
            await queue.stop(drain_timeout=5.0)

    # -- Test #2: legacy (db, inputs) path under contention -----------------

    @pytest.mark.asyncio
    async def test_persist_and_propagate_legacy_path_under_contention(
        self, tmp_path, caplog,
    ):
        """N=5 concurrent ``persist_and_propagate(db, inputs)`` callers
        on the LEGACY two-positional shape, each opening its own session
        via ``async_sessionmaker``.

        This is the path ``pipeline.py:651`` uses today — cycle 5 hasn't
        migrated it. The OPERATE intent here is to DOCUMENT (not assert
        zero) ``database is locked`` recovery behavior because the
        legacy path explicitly removed retry semantics in v0.4.13:
        single-attempt commits, exceptions propagate to the caller.

        Asserts:
        - All 5 calls complete (success or recoverable exception).
        - Successful rows are present in the DB.
        - If ``database is locked`` records appear, they're documented
          as a transitional risk for cycle 5 migration scope. The
          ``WriterLockedAsyncSession`` writer mutex (held across the
          flush→commit span) is the production mitigation.

        This test does NOT use the queue fixture — it directly exercises
        the legacy path that the queue replaces.
        """
        import asyncio as _asyncio
        import logging as _logging
        import uuid as _uuid

        from sqlalchemy import event as _sa_event
        from sqlalchemy import text as _sa_text
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.config import settings as _settings
        from app.database import WriterLockedAsyncSession
        from app.models import Base
        from app.services import pipeline_phases as pp

        # Build a fresh file-mode engine + production session class so the
        # writer-mutex contract from the prod code applies. We do NOT use
        # the ``writer_engine_file`` fixture here because we want each
        # caller to open its OWN session (mirrors pipeline.py's pattern).
        db_path = tmp_path / "legacy_test.db"
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{db_path}",
            pool_size=5,
            max_overflow=5,
        )

        # Apply production PRAGMAs so the test mirrors prod lock topology.

        @_sa_event.listens_for(engine.sync_engine, "connect")
        def _set_pragmas(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(
                f"PRAGMA busy_timeout="
                f"{_settings.DB_LOCK_TIMEOUT_SECONDS * 1000}",
            )
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            session_factory = async_sessionmaker(
                engine,
                class_=WriterLockedAsyncSession,
                expire_on_commit=False,
            )

            inputs_list = [
                _build_persistence_inputs_fixture(opt_id=str(_uuid.uuid4()))
                for _ in range(5)
            ]
            for i, inp in enumerate(inputs_list):
                object.__setattr__(inp, "trace_id", f"trace-c4-leg-{i}")

            async def _legacy_call(inp):
                """Open own session per caller — mirrors pipeline.py."""
                async with session_factory() as db:
                    await pp.persist_and_propagate(db, inp)

            with caplog.at_level(_logging.WARNING):
                results = await _asyncio.gather(
                    *[_legacy_call(inp) for inp in inputs_list],
                    return_exceptions=True,
                )

            # Documented finding: count successes vs recoverable exceptions.
            successes = [r for r in results if not isinstance(r, BaseException)]
            failures = [r for r in results if isinstance(r, BaseException)]

            # SELECT to count rows that actually landed.
            opt_ids = [inp.opt_id for inp in inputs_list]
            ids_param = ",".join(f"'{oid}'" for oid in opt_ids)
            async with engine.connect() as conn:
                count_q = await conn.execute(_sa_text(
                    f"SELECT COUNT(*) FROM optimizations "  # noqa: S608
                    f"WHERE id IN ({ids_param})"
                ))
                row = count_q.first()
                rows_landed = int(row[0]) if row else 0

            # Documented invariant: # successes == # rows landed.
            # ``WriterLockedAsyncSession`` should serialize the 5 callers
            # via ``db_writer_lock``, eliminating ``database is locked``.
            # If failures appear, they're propagated unhandled to the
            # caller (cycle 5 migration scope).
            assert len(successes) == rows_landed, (
                f"successes ({len(successes)}) != rows landed ({rows_landed}); "
                f"successes/failures inconsistent with DB state. "
                f"failures={[type(f).__name__ for f in failures]}"
            )

            # If WriterLockedAsyncSession does its job, all 5 succeed.
            # Pin the expectation but document the failure mode.
            if failures:
                # Diagnostic: log out the failure types for cycle 5 scoping.
                # We don't fail the test on this — the legacy path is
                # documented as having no retry; we want this test green
                # AND informative if behavior changes.
                fail_types = sorted({type(f).__name__ for f in failures})
                # The expected 'database is locked' surfaces as
                # OperationalError. Document if other exception types appear.
                pp.logger.warning(
                    "Legacy path under N=5 contention surfaced "
                    "%d/%d failures: types=%s",
                    len(failures), len(results), fail_types,
                )

            # Production assertion: writer mutex eliminates lock contention
            # under in-process callers. This is the mitigation cited in
            # the legacy-path docstring. If this fails, cycle 5 needs to
            # migrate pipeline.py URGENTLY.
            assert len(successes) == 5, (
                f"WriterLockedAsyncSession should serialize N=5 callers; "
                f"got {len(successes)}/5 successes, "
                f"failures={[type(f).__name__ for f in failures]}. "
                f"Cycle 5 migration scope must address this if surfaced."
            )

            # ``database is locked`` records ARE acceptable here (recovery
            # by busy_timeout retry inside SQLite is normal); the test
            # passes as long as all 5 rows landed. We grep for diagnostic
            # purposes only.
            locked_records = [
                r for r in caplog.records
                if "database is locked" in r.getMessage().lower()
            ]
            # Documented finding for cycle 5 plan.
            if locked_records:
                pp.logger.info(
                    "Legacy path emitted %d 'database is locked' records "
                    "(recoverable via busy_timeout); cycle 5 should migrate "
                    "pipeline.py for full elimination",
                    len(locked_records),
                )
        finally:
            await engine.dispose()

    # -- Test #3: provenance writes after commit ----------------------------

    @pytest.mark.asyncio
    async def test_persist_and_propagate_provenance_writes_after_commit(
        self, write_queue_inmem, writer_engine_inmem,
    ):
        """A ``persist_and_propagate`` call with non-empty
        ``auto_injected_*`` fields must result in
        ``OptimizationPattern(relationship='injected')`` rows being
        written post-commit inside the same queue callback.

        Pins the v0.4.5 invariant (cited in INTEGRATE concern #3): the
        FK on ``Optimization.id`` requires the parent row to be durable
        BEFORE provenance SAVEPOINTs run. The cycle 4 migration moves
        all 5 commits inside the queue callback; the parent commit
        (step 3) must precede ``record_injection_provenance`` (step 4).

        Asserts:
        - Optimization row landed.
        - At least one OptimizationPattern row with relationship='injected'.
        - The injected row carries the supplied cluster_id + similarity.
        """
        import uuid as _uuid

        from sqlalchemy import text as _sa_text

        from app.services import pipeline_phases as pp
        from tests._write_queue_helpers import create_prestaged_cluster

        # Pre-create cluster for FK resolution.
        cluster_id = await create_prestaged_cluster(
            writer_engine_inmem, cluster_id="c4-prov-cluster",
        )

        opt_id = str(_uuid.uuid4())
        inputs = _build_persistence_inputs_fixture(opt_id=opt_id)
        # Wire up auto_injected_* — both cluster_ids AND patterns must be
        # set so persist_and_propagate hits the ``cluster_ids`` branch
        # of record_injection_provenance.
        object.__setattr__(inputs, "auto_injected_cluster_ids", [cluster_id])
        object.__setattr__(
            inputs, "auto_injected_similarity_map",
            {cluster_id: 0.91},
        )
        # Empty injected patterns list — topic provenance only (no
        # cross-cluster meta-pattern injection). This keeps the test
        # focused on the post-commit ordering invariant.
        object.__setattr__(inputs, "auto_injected_patterns", [])

        await pp.persist_and_propagate(inputs, write_queue=write_queue_inmem)

        # O1: SELECT both the parent + the join row.
        async with writer_engine_inmem.connect() as conn:
            opt_check = await conn.execute(_sa_text(
                "SELECT id FROM optimizations WHERE id = :oid"
            ), {"oid": opt_id})
            assert opt_check.first() is not None, (
                "Optimization row was not committed"
            )

            prov_check = await conn.execute(_sa_text(
                "SELECT cluster_id, relationship, similarity "
                "FROM optimization_patterns "
                "WHERE optimization_id = :oid AND relationship = 'injected'"
            ), {"oid": opt_id})
            prov_rows = list(prov_check.fetchall())

        assert len(prov_rows) >= 1, (
            "expected >=1 OptimizationPattern row with "
            "relationship='injected' after persist_and_propagate via queue, "
            f"got {len(prov_rows)} — provenance write was skipped or "
            "rolled back (FK error if parent commit ordering inverted)"
        )
        assert prov_rows[0].cluster_id == cluster_id
        assert prov_rows[0].relationship == "injected"
        assert prov_rows[0].similarity is not None
        assert abs(prov_rows[0].similarity - 0.91) < 1e-6, (
            f"expected similarity=0.91 (from similarity_map), "
            f"got {prov_rows[0].similarity}"
        )

    # -- Test #4: usage propagation via queue (no separate usage_db) --------

    @pytest.mark.asyncio
    async def test_persist_and_propagate_usage_propagation_via_queue(
        self, write_queue_inmem, writer_engine_inmem,
    ):
        """``auto_injected_cluster_ids`` MUST trigger
        ``taxonomy_engine.increment_usage`` inside the queue callback
        (no separate ``usage_db = async_session_factory()`` session, per
        cycle 4 spec § 3.4).

        Builds a stub ``taxonomy_engine`` whose ``increment_usage``
        method directly bumps ``PromptCluster.usage_count`` against the
        provided session. Verifies that after the queue callback
        resolves, the cluster's ``usage_count`` is incremented.

        Pins INTEGRATE concern #3 — usage propagation works in the
        queue callback's session (no separate usage_db needed). Both
        ``auto_injected_cluster_ids`` AND ``applied_pattern_ids`` paths
        accumulate into ``local_applied_cluster_ids``; this test
        focuses on the ``auto_injected_cluster_ids`` path.

        Asserts:
        - cluster.usage_count incremented from 0 to 1.
        - increment_usage was called exactly once with the right cid.
        """
        import uuid as _uuid

        from sqlalchemy import text as _sa_text
        from sqlalchemy.ext.asyncio import AsyncSession

        from app.services import pipeline_phases as pp
        from tests._write_queue_helpers import create_prestaged_cluster

        cluster_id = await create_prestaged_cluster(
            writer_engine_inmem, cluster_id="c4-usage-cluster",
        )

        # Verify cluster starts at usage_count=0.
        async with writer_engine_inmem.connect() as conn:
            initial_q = await conn.execute(_sa_text(
                "SELECT usage_count FROM prompt_cluster WHERE id = :cid"
            ), {"cid": cluster_id})
            initial_row = initial_q.first()
            initial_usage = int(initial_row[0]) if initial_row else 0
        assert initial_usage == 0, (
            f"baseline usage_count expected 0, got {initial_usage}"
        )

        # Stub taxonomy engine: real ``increment_usage`` issues an atomic
        # SQL UPDATE on the supplied session. Mirror that contract
        # minimally — bump the cluster's usage_count by 1.
        increment_calls: list[tuple[str, AsyncSession]] = []

        class _StubTaxonomyEngine:
            async def increment_usage(self, cid: str, db) -> None:
                from sqlalchemy import update as sa_upd

                from app.models import PromptCluster
                increment_calls.append((cid, db))
                await db.execute(
                    sa_upd(PromptCluster)
                    .where(PromptCluster.id == cid)
                    .values(usage_count=PromptCluster.usage_count + 1)
                )

        opt_id = str(_uuid.uuid4())
        inputs = _build_persistence_inputs_fixture(opt_id=opt_id)
        # Wire up the path to taxonomy_engine.increment_usage:
        # auto_injected_cluster_ids feeds local_applied_cluster_ids
        # at line 1280, then taxonomy_engine.increment_usage fires at 1293.
        object.__setattr__(inputs, "auto_injected_cluster_ids", [cluster_id])
        object.__setattr__(
            inputs, "auto_injected_similarity_map",
            {cluster_id: 0.85},
        )
        object.__setattr__(inputs, "auto_injected_patterns", [])
        object.__setattr__(inputs, "taxonomy_engine", _StubTaxonomyEngine())

        await pp.persist_and_propagate(inputs, write_queue=write_queue_inmem)

        # O1: usage_count incremented to 1 in DB.
        async with writer_engine_inmem.connect() as conn:
            final_q = await conn.execute(_sa_text(
                "SELECT usage_count FROM prompt_cluster WHERE id = :cid"
            ), {"cid": cluster_id})
            final_row = final_q.first()
            final_usage = int(final_row[0]) if final_row else 0
        assert final_usage == 1, (
            f"expected usage_count incremented from 0 → 1 via queue "
            f"callback, got {final_usage}"
        )

        # increment_usage was invoked once with the right cluster_id.
        assert len(increment_calls) == 1, (
            f"expected exactly 1 increment_usage() call from "
            f"auto_injected_cluster_ids path, got {len(increment_calls)}"
        )
        called_cid, _called_db = increment_calls[0]
        assert called_cid == cluster_id, (
            f"increment_usage called with wrong cid: "
            f"got {called_cid}, expected {cluster_id}"
        )

    # -- Test #5: event emission AFTER queue resolves -----------------------

    @pytest.mark.asyncio
    async def test_persist_and_propagate_event_emission_after_queue_resolves(
        self, write_queue_inmem, writer_engine_inmem, monkeypatch,
    ):
        """``optimization_created`` event fires AFTER ``submit()`` returns.
        If ``submit()`` raises, no event fires (failure semantics from
        cycle 2/3 contract carry over to cycle 4).

        Asserts (success path):
        - Subscriber queue empty before invocation.
        - Exactly 1 ``optimization_created`` event after invocation.

        Asserts (failure path):
        - Forced ``WriteQueueOverloadedError`` from a mocked submit
          raises out to caller.
        - NO ``optimization_created`` event fires.
        """
        import asyncio as _asyncio
        import uuid as _uuid

        from app.services import pipeline_phases as pp
        from app.services.event_bus import event_bus
        from app.services.write_queue import WriteQueueOverloadedError

        # ---- Success path ----
        ev_queue: _asyncio.Queue = _asyncio.Queue(maxsize=200)
        event_bus._subscribers.add(ev_queue)
        try:
            assert ev_queue.qsize() == 0, (
                "test setup error: subscriber queue had pre-existing events"
            )

            opt_id_ok = str(_uuid.uuid4())
            inputs_ok = _build_persistence_inputs_fixture(opt_id=opt_id_ok)

            await pp.persist_and_propagate(
                inputs_ok, write_queue=write_queue_inmem,
            )

            collected: list[dict] = []
            while True:
                try:
                    collected.append(ev_queue.get_nowait())
                except _asyncio.QueueEmpty:
                    break
            created_events = [
                e for e in collected
                if e.get("event") == "optimization_created"
                and e["data"].get("id") == opt_id_ok
            ]
            assert len(created_events) == 1, (
                f"expected 1 optimization_created event after success, "
                f"got {len(created_events)}"
            )
        finally:
            event_bus._subscribers.discard(ev_queue)

        # ---- Failure path: forced submit raise ----
        ev_queue_fail: _asyncio.Queue = _asyncio.Queue(maxsize=200)
        event_bus._subscribers.add(ev_queue_fail)
        try:
            opt_id_fail = str(_uuid.uuid4())
            inputs_fail = _build_persistence_inputs_fixture(
                opt_id=opt_id_fail,
            )

            # Monkeypatch submit to raise WriteQueueOverloadedError.
            async def _raise_overloaded(*_a, **_kw):
                raise WriteQueueOverloadedError(
                    "simulated overload for failure-path test",
                )

            monkeypatch.setattr(
                write_queue_inmem, "submit", _raise_overloaded,
            )

            with pytest.raises(WriteQueueOverloadedError):
                await pp.persist_and_propagate(
                    inputs_fail, write_queue=write_queue_inmem,
                )

            # No optimization_created event fired for the failed call.
            collected_fail: list[dict] = []
            while True:
                try:
                    collected_fail.append(ev_queue_fail.get_nowait())
                except _asyncio.QueueEmpty:
                    break
            failed_events = [
                e for e in collected_fail
                if e.get("event") == "optimization_created"
                and e["data"].get("id") == opt_id_fail
            ]
            assert failed_events == [], (
                f"expected NO optimization_created event for failed submit, "
                f"got {len(failed_events)} — phantom event would represent "
                f"a row that never persisted"
            )
        finally:
            event_bus._subscribers.discard(ev_queue_fail)

        # Suppress unused-fixture warning — engine reachable via the queue.
        _ = writer_engine_inmem
