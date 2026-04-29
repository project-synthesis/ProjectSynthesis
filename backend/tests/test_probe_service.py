"""Tests for ProbeService 5-phase orchestrator (Topic Probe Tier 1).

AC-C4-1 through AC-C4-6 per docs/specs/topic-probe-2026-04-29.md §8 Cycle 4.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
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
    """LLMProvider mock — generator returns a list of probe prompts."""
    provider = AsyncMock()

    async def _complete_parsed(*args, **kwargs):
        result = MagicMock()
        result.prompts = [
            "Audit cache invalidation logic in repo_index_service.",
            "Compare TTL strategies between explore_cache and curated cache.",
            "Identify race conditions in concurrent cache writes.",
            "Review cache key derivation for SHA collisions.",
            "Find missing invalidation hooks on file rename.",
        ]
        result.model = "claude-haiku-4-5"
        return result

    provider.complete_parsed = AsyncMock(side_effect=_complete_parsed)
    provider.complete_parsed_streaming = AsyncMock(side_effect=_complete_parsed)
    return provider


@pytest.fixture
def mock_repo_query() -> MagicMock:
    """RepoIndexQuery returning a non-empty curated context (all prompts succeed)."""
    repo_query = MagicMock()
    curated = MagicMock()
    curated.files = [
        MagicMock(file_path="backend/app/services/repo_index_service.py"),
        MagicMock(file_path="backend/app/services/explore_cache.py"),
        MagicMock(file_path="backend/app/services/repo_index_query.py"),
    ]
    curated.synthesis_excerpt = "Repo index uses SHA-keyed file cache with TTL eviction."
    curated.dominant_stack = ["python", "fastapi", "sqlalchemy"]
    repo_query.query_curated_context = AsyncMock(return_value=curated)
    return repo_query


@pytest.fixture
def mock_repo_query_partial() -> MagicMock:
    """RepoIndexQuery configured so SOME per-prompt runs fail.

    Used by AC-C4-3 (partial status). The 5-phase orchestrator persists
    status='partial' when 1+ failures < n_prompts succeed.
    """
    repo_query = MagicMock()
    curated = MagicMock()
    curated.files = [MagicMock(file_path="backend/app/services/repo_index_service.py")]
    curated.synthesis_excerpt = "Partial index — only one file present."
    curated.dominant_stack = ["python"]
    repo_query.query_curated_context = AsyncMock(return_value=curated)
    # The per-prompt run will be configured to fail on alternating indexes
    # via context_service / batch_pipeline.run_single_prompt mock — see
    # mock_context_service below for the partial-failure injection knob.
    repo_query._partial_mode = True
    return repo_query


@pytest.fixture
def mock_context_service() -> MagicMock:
    """ContextEnrichmentService mock — returns empty enrichment context."""
    svc = MagicMock()
    enriched = MagicMock()
    enriched.codebase_context = ""
    enriched.strategy_intelligence = ""
    enriched.applied_patterns = []
    enriched.divergence_alerts = ""
    enriched.heuristic_analysis = MagicMock(task_type="analysis", domain="backend")
    enriched.enrichment_meta = {}
    svc.enrich = AsyncMock(return_value=enriched)
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
        async for ev in svc.run(_make_request(n_prompts=3), probe_id="p-test-1"):
            events.append(type(ev).__name__)
        assert events[0] == "ProbeStartedEvent"
        assert events[1] == "ProbeGroundingEvent"
        assert events[2] == "ProbeGeneratingEvent"
        assert events.count("ProbeProgressEvent") == 3
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
        async for _ in svc.run(_make_request(n_prompts=3), probe_id=probe_id):
            pass
        row = await db_session.get(ProbeRun, probe_id)
        assert row is not None
        assert row.status == "completed"
        assert row.completed_at is not None
        assert row.prompts_generated == 3

    @pytest.mark.asyncio
    async def test_partial_status_on_some_failures(
        self,
        db_session,
        mock_provider,
        mock_repo_query_partial,
        mock_context_service,
        mock_event_bus,
    ):
        """AC-C4-3: 1+ failures < n_prompts → status=partial; all-fail → status=failed; all-succeed → completed."""
        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query_partial,
            mock_context_service,
            mock_event_bus,
        )
        probe_id = "p-test-partial"
        async for _ in svc.run(_make_request(n_prompts=3), probe_id=probe_id):
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
        async for _ in svc.run(_make_request(n_prompts=3), probe_id="p-test-f31"):
            pass
        row = await db_session.get(ProbeRun, "p-test-f31")
        assert row.aggregate["mean_overall"] == pytest.approx(7.30, abs=0.05)
        assert row.aggregate["scoring_formula_version"] == 4
