"""Tests for ProbeService 5-phase orchestrator (Topic Probe Tier 1).

AC-C4-1 through AC-C4-6 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 4.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProbeRun
from app.schemas.probes import (
    ProbeCompletedEvent,
    ProbeError,
    ProbeFailedEvent,
    ProbeProgressEvent,
    ProbeRunRequest,
    ProbeRunResult,
    ProbeStartedEvent,
)
from app.services.probe_service import ProbeService


def _make_request(**overrides) -> ProbeRunRequest:
    base = dict(
        topic="embedding cache invalidation",
        scope=None,
        intent_hint=None,
        n_prompts=12,
        repo_full_name="owner/repo",
    )
    base.update(overrides)
    return ProbeRunRequest(**base)


def _make_optimization_mock(
    *,
    optimization_id: str | None = None,
    task_type: str = "analysis",
    clarity: float = 9.0,
    specificity: float = 9.0,
    structure: float = 8.0,
    faithfulness: float = 4.0,
    conciseness: float = 4.0,
    intent_label: str = "investigate cache",
    cluster_id: str | None = None,
    cluster_label: str | None = None,
    domain: str = "backend",
) -> MagicMock:
    """Mock per-prompt OptimizationResult-shaped row consumed by ProbeService."""
    opt = MagicMock()
    opt.id = optimization_id or str(uuid4())
    opt.task_type = task_type
    opt.intent_label = intent_label
    opt.cluster_id = cluster_id
    opt.cluster_label = cluster_label
    opt.domain = domain
    # Per-dimension scores in the shape ProbeService aggregates over.
    scores = MagicMock()
    scores.clarity = clarity
    scores.specificity = specificity
    scores.structure = structure
    scores.faithfulness = faithfulness
    scores.conciseness = conciseness
    opt.dimension_scores = scores
    opt.scores = scores
    # Default-weights overall is ~6.80, analysis-weights overall is ~7.30.
    # ProbeService should compute via compute_overall(task_type) — tests
    # assert the analysis path lands at ~7.30, not ~6.80.
    opt.overall_score = None  # forces ProbeService to recompute via F3.1
    return opt


# --- Inline mock fixtures ---


@pytest.fixture
def mock_provider() -> AsyncMock:
    """LLMProvider mock — generator returns a list of probe prompts.

    Each prompt embeds a backtick-wrapped code identifier so the production
    ``generate_probe_prompts`` primitive (C3) clears the F1 backtick-density
    gate (>50% drop threshold). The probe-agent template requires this in
    real use too — see ``probe_generation._BACKTICK_RX``.
    """
    provider = AsyncMock()

    async def _complete_parsed(*args, **kwargs):
        result = MagicMock()
        result.prompts = [
            "Audit `repo_index_service.invalidate` cache invalidation logic.",
            "Compare `explore_cache.py` TTL strategy vs curated cache.",
            "Identify race conditions in `RepoIndexQuery.refresh` writes.",
            "Review cache key derivation in `repo_index_service.py` for SHA collisions.",
            "Find missing invalidation hooks for `file_rename` events.",
        ]
        result.model = "claude-haiku-4-5"
        return result

    provider.complete_parsed = AsyncMock(side_effect=_complete_parsed)
    provider.complete_parsed_streaming = AsyncMock(side_effect=_complete_parsed)
    return provider


@pytest.fixture
def mock_repo_query() -> MagicMock:
    """RepoIndexQuery returning a curated context shaped like production.

    Uses ``selected_files`` (list[dict] with ``path`` keys), matching the real
    ``CuratedCodebaseContext`` dataclass in ``repo_index_query.py``. The
    probe-specific ``explore_synthesis_excerpt``/``dominant_stack`` attrs are
    additive — production grounding will source them from cached synthesis +
    workspace_intelligence respectively (Tier 2 wiring).
    """
    repo_query = MagicMock()
    curated = MagicMock()
    curated.selected_files = [
        {"path": "backend/app/services/repo_index_service.py", "score": 0.91},
        {"path": "backend/app/services/explore_cache.py", "score": 0.88},
        {"path": "backend/app/services/repo_index_query.py", "score": 0.85},
    ]
    curated.context_text = "Repo index uses SHA-keyed file cache with TTL eviction."
    curated.explore_synthesis_excerpt = (
        "Repo index uses SHA-keyed file cache with TTL eviction."
    )
    curated.dominant_stack = ["python", "fastapi", "sqlalchemy"]
    repo_query.query_curated_context = AsyncMock(return_value=curated)
    return repo_query


@pytest.fixture
def mock_repo_query_partial() -> MagicMock:
    """RepoIndexQuery for AC-C4-3 partial-status path.

    Identical to ``mock_repo_query`` shape — the partial-failure injection
    happens via ``mock_context_service_partial.enrich`` (alternating raise),
    not via a flag on the repo-query mock. This keeps test affordances out
    of the production code path.
    """
    repo_query = MagicMock()
    curated = MagicMock()
    curated.selected_files = [
        {"path": "backend/app/services/repo_index_service.py", "score": 0.90},
    ]
    curated.context_text = "Partial index — only one file present."
    curated.explore_synthesis_excerpt = "Partial index — only one file present."
    curated.dominant_stack = ["python"]
    repo_query.query_curated_context = AsyncMock(return_value=curated)
    return repo_query


def _make_enriched_mock() -> MagicMock:
    enriched = MagicMock()
    enriched.codebase_context = ""
    enriched.strategy_intelligence = ""
    enriched.applied_patterns = []
    enriched.divergence_alerts = ""
    enriched.heuristic_analysis = MagicMock(task_type="analysis", domain="backend")
    enriched.enrichment_meta = {}
    return enriched


@pytest.fixture
def mock_context_service() -> MagicMock:
    """ContextEnrichmentService mock — returns empty enrichment context."""
    svc = MagicMock()
    svc.enrich = AsyncMock(return_value=_make_enriched_mock())
    return svc


@pytest.fixture
def mock_context_service_partial() -> MagicMock:
    """ContextEnrichmentService that fails enrich() on every other call.

    Drives AC-C4-3 partial-status via the orchestrator's natural exception
    handler (``_execute_one`` catches and returns ``status='failed'``) — no
    test-only flag in production code.
    """
    svc = MagicMock()
    call_count = {"n": 0}

    async def _enrich_alternating(*args, **kwargs):
        call_count["n"] += 1
        # Fail on the 2nd, 4th, ... calls (every other prompt).
        if call_count["n"] % 2 == 0:
            raise RuntimeError("simulated per-prompt enrichment failure")
        return _make_enriched_mock()

    svc.enrich = AsyncMock(side_effect=_enrich_alternating)
    return svc


@pytest.fixture
def mock_event_bus() -> MagicMock:
    bus = MagicMock()
    bus.publish = MagicMock()
    return bus


class TestProbeService:
    @pytest.mark.asyncio
    async def test_5_phase_event_ordering(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """AC-C4-1: yields events in order: started, grounding, generating, prompt_completed×N, completed."""
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        events = []
        async for ev in svc.run(_make_request(n_prompts=5), probe_id="p-test-1"):
            events.append(type(ev).__name__)
        assert events[0] == "ProbeStartedEvent"
        assert events[1] == "ProbeGroundingEvent"
        assert events[2] == "ProbeGeneratingEvent"
        assert events.count("ProbeProgressEvent") == 5
        assert events[-1] == "ProbeCompletedEvent"

    @pytest.mark.asyncio
    async def test_persists_running_then_completed(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """AC-C4-2: ProbeRun row inserted at start (running), updated at end (completed)."""
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        probe_id = "p-test-running-completed"
        async for _ in svc.run(_make_request(n_prompts=5), probe_id=probe_id):
            pass
        row = await db_session.get(ProbeRun, probe_id)
        assert row is not None
        assert row.status == "completed"
        assert row.completed_at is not None
        assert row.prompts_generated == 5

    @pytest.mark.asyncio
    async def test_partial_status_on_some_failures(
        self,
        db_session,
        mock_provider,
        mock_repo_query_partial,
        mock_context_service_partial,
        mock_event_bus,
    ):
        """AC-C4-3: 1+ failures < n_prompts → status=partial; all-fail → status=failed; all-succeed → completed."""
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query_partial,
            mock_context_service_partial,
            mock_event_bus,
        )
        probe_id = "p-test-partial"
        async for _ in svc.run(_make_request(n_prompts=5), probe_id=probe_id):
            pass
        row = await db_session.get(ProbeRun, probe_id)
        assert row.status == "partial"

    @pytest.mark.asyncio
    async def test_link_repo_first_error(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """AC-C4-4: missing repo_full_name → ProbeError(link_repo_first); status=failed."""
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        request = _make_request(repo_full_name=None)
        events = []
        with pytest.raises(ProbeError, match=r"link_repo_first"):
            async for ev in svc.run(request, probe_id="p-test-link-err"):
                events.append(ev)
        # Final event should be probe_failed
        assert any(isinstance(e, ProbeFailedEvent) for e in events)

    @pytest.mark.asyncio
    async def test_final_report_deterministic_sections(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """AC-C4-5: final_report contains Top 3 / Score Distribution / Taxonomy Delta /
        Recommended Follow-ups / Run Metadata sections (header presence + minimal content).
        """
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        async for _ in svc.run(_make_request(n_prompts=5), probe_id="p-test-report"):
            pass
        row = await db_session.get(ProbeRun, "p-test-report")
        report = row.final_report
        assert "## Top" in report
        assert "## Score Distribution" in report
        assert "## Taxonomy Delta" in report
        assert "## Recommended Follow-ups" in report
        assert "## Run Metadata" in report
        # Run metadata must include scoring_formula_version
        assert "scoring_formula_version" in report

    @pytest.mark.asyncio
    async def test_aggregate_uses_compute_overall_task_type(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """AC-C4-6: aggregate.mean_overall computed via compute_overall(task_type) per F3.1.

        Fixture: per-prompt task_type='analysis' → analysis weights apply →
        mean diverges from default-weights mean for the same per-dim scores.
        """
        # Mock returns optimizations with task_type='analysis' and divergent
        # per-dim scores (clarity 9, specificity 9, structure 8, faithfulness 4, conciseness 4).
        # Default weights: 6.80; analysis weights: 7.30.
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        async for _ in svc.run(_make_request(n_prompts=5), probe_id="p-test-f31"):
            pass
        row = await db_session.get(ProbeRun, "p-test-f31")
        assert row.aggregate["mean_overall"] == pytest.approx(7.30, abs=0.05)
        assert row.aggregate["scoring_formula_version"] == 4


class TestProbeCancellation:
    """Cancellation hardening: client disconnect mid-stream must not leave
    the ProbeRun row stuck at ``status='running'`` forever.

    Surfaced by validation probe ``97512f6a`` -- curl disconnected after
    Phase 2, FastAPI raised ``ClientDisconnect`` which cancelled the
    ``probe_service.run()`` async generator. Pre-fix: row stayed
    ``running`` indefinitely with no error marker.
    """

    @pytest.mark.asyncio
    async def test_cancellation_marks_row_as_failed(
        self,
        db_session,
        mock_provider,
        mock_context_service,
        mock_event_bus,
    ):
        """When ``run()`` is cancelled mid-stream, ProbeRun row is marked
        ``status='failed'`` with ``error='cancelled'`` (not left as
        ``'running'``).

        Mirrors FastAPI ``ClientDisconnect`` semantics: the SSE-streaming
        task is cancelled while the generator is awaiting an internal
        operation. ``asyncio.CancelledError`` is then injected into the
        generator at its current await point (here:
        ``query_curated_context``). We simulate that by stalling the
        repo-query mock with a long sleep and cancelling the consumer
        task while it's blocked there.
        """
        # Slow repo-query: hangs long enough for us to cancel mid-call.
        slow_repo_query = MagicMock()

        async def _hang(*args, **kwargs):
            await asyncio.sleep(60.0)

        slow_repo_query.query_curated_context = AsyncMock(side_effect=_hang)

        svc = ProbeService(
            db_session,
            AsyncMock(),
            slow_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        probe_id = "p-test-cancellation"
        started_seen = asyncio.Event()

        async def consume():
            async for ev in svc.run(
                _make_request(n_prompts=5), probe_id=probe_id,
            ):
                if isinstance(ev, ProbeStartedEvent):
                    started_seen.set()

        task = asyncio.create_task(consume())
        await started_seen.wait()
        # Give the generator a tick to advance past `yield` and into the
        # next await (query_curated_context, which hangs).
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        # Row should now be marked failed with error='cancelled'.
        row = await db_session.get(ProbeRun, probe_id)
        assert row is not None
        assert row.status == "failed"
        assert row.error == "cancelled"
        assert row.completed_at is not None


class TestProbeServiceErrorPaths:
    """Tier-1 production-bug regression guards.

    Two related defects surfaced during integration validation
    (probe ``470e21ce-31fc-4386-8b34-e7423c64096d``):

      1. ``_execute_one`` called ``ContextEnrichmentService.enrich()``
         without the required ``tier`` and ``db`` kwargs, so every
         Phase-3 prompt failed with TypeError. The ``except TypeError``
         fallback to ``enrich(prompt)`` re-raised TypeError (the second
         TypeError isn't caught by the sibling ``except Exception``),
         which propagated past ``run()`` unhandled.

      2. The TypeError propagating past ``run()`` left the ProbeRun row
         in ``status='running'`` forever — the existing CancelledError
         handler does not cover generic exceptions.
    """

    @pytest.mark.asyncio
    async def test_enrich_signature_real_kwargs(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """``ContextEnrichmentService.enrich()`` is called with the
        canonical kwargs (``tier='internal'``, ``db=<AsyncSession>``).

        Regression guard for the Tier-1 production bug where
        ``_execute_one`` omitted these required args and probes failed
        at Phase 3.
        """
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        async for _ in svc.run(
            _make_request(n_prompts=5),
            probe_id="p-test-enrich-kwargs",
        ):
            pass

        # mock_context_service.enrich must have been invoked with the
        # canonical signature on every prompt.
        enrich_calls = mock_context_service.enrich.call_args_list
        assert len(enrich_calls) >= 1
        for call in enrich_calls:
            assert call.kwargs.get("tier") == "internal"
            assert call.kwargs.get("db") is not None

    @pytest.mark.asyncio
    async def test_uncaught_exception_marks_row_as_failed(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """Any uncaught exception during ``run()`` marks the row failed
        and propagates. Defense in depth against future regressions
        where a code path beyond the existing per-phase try/except
        wrappers raises (e.g. reporting-phase computation, db.commit
        retry exhaustion).

        Patches ``_render_final_report`` — invoked at module level in
        the reporting phase, with NO surrounding try/except. Pre-fix,
        any RuntimeError there propagated past ``run()`` and the row
        stayed at ``status='running'`` forever.
        """
        def boom(*a, **k):
            raise RuntimeError("synthetic uncaught failure")

        from app.services import probe_service as probe_service_mod

        probe_id = "p-test-uncaught"
        with patch.object(
            probe_service_mod,
            "_render_final_report",
            side_effect=boom,
        ):
            with pytest.raises(RuntimeError, match=r"synthetic uncaught failure"):
                async for _ in ProbeService(
                    db_session,
                    mock_provider,
                    mock_repo_query,
                    mock_context_service,
                    mock_event_bus,
                ).run(
                    _make_request(n_prompts=5),
                    probe_id=probe_id,
                ):
                    pass

        # Row must be marked failed with structured error info.
        row = await db_session.get(ProbeRun, probe_id)
        assert row is not None
        assert row.status == "failed"
        assert row.error is not None
        assert "RuntimeError" in row.error
        assert row.completed_at is not None
