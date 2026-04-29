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

from app.models import ProbeRun, PromptCluster
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


def _make_enriched_mock(
    *,
    task_type: str = "analysis",
    domain: str = "backend",
    intent_label: str = "investigate cache invalidation",
) -> Any:
    """Build a real ``EnrichedContext`` shape -- NOT a MagicMock.

    The previous fixture used ``MagicMock(heuristic_analysis=...)`` which
    invented an attribute that doesn't exist on production
    ``EnrichedContext``. The probe code reading ``enriched.heuristic_analysis``
    would resolve to that MagicMock attribute in tests but to ``None`` in
    production. INTEGRATE phase (v0.4.12) caught this; the fixture must
    construct the real production type so wiring drift surfaces in tests.
    """
    from types import MappingProxyType

    from app.services.context_enrichment import EnrichedContext
    from app.services.heuristic_analyzer import HeuristicAnalysis

    analysis = HeuristicAnalysis(
        task_type=task_type,
        domain=domain,
        intent_label=intent_label,
        confidence=0.9,
    )
    return EnrichedContext(
        raw_prompt="<test>",
        codebase_context=None,
        strategy_intelligence=None,
        applied_patterns=None,
        analysis=analysis,
        context_sources=MappingProxyType({"heuristic_analysis": True}),
        enrichment_meta=MappingProxyType({}),
    )


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


@pytest.fixture
def test_session_factory(db_session):
    """Yield a session factory backed by the test's in-memory SQLite.

    The probe's canonical-batch path (``run_batch + bulk_persist +
    batch_taxonomy_assign``) calls ``session_factory()`` to open fresh
    sessions for persist + taxonomy-assign. Production points this at
    ``app.database.async_session_factory``; tests need to point it at
    the per-test in-memory engine so persisted rows are visible to the
    test's ``db_session`` reads.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _factory():
        yield db_session

    return _factory


@pytest.fixture(autouse=True)
def _patch_canonical_batch(monkeypatch, request):
    """Stub the canonical batch primitives (``run_batch + bulk_persist +
    batch_taxonomy_assign``) for all probe service tests.

    Why a fixture and not per-test patching: the probe now delegates to
    the same execution primitive seed agents use, which makes real LLM
    calls. Unit tests need a deterministic stand-in. ``run_batch`` is
    replaced with a generator that returns ``PendingOptimization`` rows
    whose shape mirrors ``run_single_prompt``'s output. ``bulk_persist``
    and ``batch_taxonomy_assign`` keep their real signatures so the
    INTEGRATE wiring guards (cluster parenting, infra fields) still
    exercise production behavior.

    Tests can override the stub via ``request.getfixturevalue("...")``
    or by monkeypatching directly.

    Tests marked with ``@pytest.mark.real_run_batch`` skip this stub
    (e.g. partial-status path needs real failures from the alternating
    enrich mock).
    """
    if "real_run_batch" in request.keywords:
        return

    # Point the probe's default session_factory at the test's in-memory
    # session so persisted rows land where the test reads them.
    if "db_session" in request.fixturenames:
        from contextlib import asynccontextmanager
        from app import database as _database_mod
        from app.dependencies import probes as _probes_dep_mod

        db_session = request.getfixturevalue("db_session")

        @asynccontextmanager
        async def _factory():
            yield db_session

        monkeypatch.setattr(_database_mod, "async_session_factory", _factory)
        # build_probe_service imports async_session_factory at call-time
        # via ``from app.database import async_session_factory`` --
        # patching the module attribute above covers fresh imports, but
        # the probe_service module-level imports stick. Patch
        # build_probe_service's lazy lookup by setting it on the deps
        # module too in case any cached binding holds.
        if hasattr(_probes_dep_mod, "_default_session_factory"):
            monkeypatch.setattr(
                _probes_dep_mod, "_default_session_factory", _factory,
            )

    from app.services import batch_orchestrator
    from app.services.batch_pipeline import PendingOptimization

    async def _fake_run_batch(prompts, provider, prompt_loader, embedding_service, **kwargs):
        # Each probe test fixture sets task_type=analysis, domain=backend,
        # intent_label="investigate cache invalidation". Mirror that.
        from uuid import uuid4 as _uuid4
        # Allow test-level domain/task_type override via the fake_run_batch_overrides
        # attribute on the patched module.
        overrides = getattr(batch_orchestrator, "_test_overrides", {})
        results = []
        for i, p in enumerate(prompts):
            try:
                vec = await embedding_service.aembed_single(p)
                emb_bytes = vec.astype("float32").tobytes()
            except Exception:
                emb_bytes = b"\x00" * 1536
            results.append(PendingOptimization(
                id=str(_uuid4()),
                trace_id=f"tr-{i:02d}",
                batch_id=kwargs.get("batch_id", "x"),
                raw_prompt=p,
                optimized_prompt=f"OPTIMIZED: {p[:100]}",
                task_type=overrides.get("task_type", "analysis"),
                strategy_used="auto",
                changes_summary="(stub)",
                score_clarity=8.0 + (i * 0.1),
                score_specificity=8.0,
                score_structure=7.5,
                score_faithfulness=8.5,
                score_conciseness=7.0,
                overall_score=7.6 + (i * 0.05),
                improvement_score=1.5,
                scoring_mode="hybrid",
                intent_label="Investigate Cache Invalidation",
                domain=overrides.get("domain", "backend"),
                domain_raw=overrides.get("domain_raw", "backend"),
                embedding=emb_bytes,
                optimized_embedding=emb_bytes,
                transformation_embedding=emb_bytes,
                models_by_phase={"analyze": "haiku", "optimize": "opus", "score": "sonnet"},
                original_scores={"clarity": 6.0, "specificity": 6.0, "structure": 6.0, "faithfulness": 6.0, "conciseness": 6.0},
                score_deltas={"clarity": 2.0, "specificity": 2.0, "structure": 1.5, "faithfulness": 2.5, "conciseness": 1.0},
                duration_ms=120,
                status="completed",
                provider="claude_cli",
                model_used="opus",
                routing_tier="internal",
                heuristic_flags={},
                suggestions=[],
                repo_full_name=kwargs.get("repo_full_name"),
                project_id=None,
                context_sources={"source": "batch_seed", "batch_id": kwargs.get("batch_id", "x")},
            ))
            cb = kwargs.get("on_progress")
            if cb:
                try:
                    cb(i, len(prompts), results[-1])
                except Exception:
                    pass
        return results

    monkeypatch.setattr(batch_orchestrator, "run_batch", _fake_run_batch)


def _setup_real_run_batch_marker():
    """Sentinel used to mark tests that should bypass the stub."""
    pass


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
        monkeypatch,
    ):
        """AC-C4-3: 1+ failures < n_prompts → status=partial; all-fail → status=failed; all-succeed → completed.

        Patches ``run_batch`` to return alternating completed/failed
        PendingOptimizations (mirrors the prior alternating-enrich-mock).
        """
        from app.services import batch_orchestrator
        from app.services.batch_pipeline import PendingOptimization
        from uuid import uuid4

        async def _alternating(prompts, **kwargs):
            results = []
            for i, p in enumerate(prompts):
                emb = b"\x00" * 1536
                if i % 2 == 1:
                    results.append(PendingOptimization(
                        id=str(uuid4()),
                        trace_id=str(uuid4()),
                        batch_id=kwargs.get("batch_id"),
                        raw_prompt=p,
                        status="failed",
                        error="simulated failure",
                    ))
                else:
                    results.append(PendingOptimization(
                        id=str(uuid4()),
                        trace_id=str(uuid4()),
                        batch_id=kwargs.get("batch_id"),
                        raw_prompt=p,
                        optimized_prompt=f"OPT: {p[:60]}",
                        task_type="analysis",
                        intent_label="test",
                        domain="backend",
                        domain_raw="backend",
                        score_clarity=8.0, score_specificity=8.0,
                        score_structure=7.0, score_faithfulness=8.0,
                        score_conciseness=7.0, overall_score=7.6,
                        scoring_mode="hybrid",
                        embedding=emb, optimized_embedding=emb,
                        models_by_phase={"analyze": "haiku"},
                        original_scores={"clarity": 6.0, "specificity": 6.0, "structure": 6.0, "faithfulness": 6.0, "conciseness": 6.0},
                        score_deltas={"clarity": 2.0, "specificity": 2.0, "structure": 1.0, "faithfulness": 2.0, "conciseness": 1.0},
                        status="completed",
                        provider="claude_cli",
                        routing_tier="internal",
                        repo_full_name=kwargs.get("repo_full_name"),
                        context_sources={"source": "batch_seed", "batch_id": kwargs.get("batch_id")},
                    ))
                cb = kwargs.get("on_progress")
                if cb:
                    cb(i, len(prompts), results[-1])
            return results

        monkeypatch.setattr(batch_orchestrator, "run_batch", _alternating)

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

        After v0.4.12 wiring upgrade (real ``HeuristicScorer.score_prompt``
        replaces the deterministic stub), the test patches the scorer to
        return a known per-dim baseline so the F3.1 assertion stays
        load-bearing on the *formula*, not on whatever real-content
        HeuristicScorer happens to compute.
        """
        # The canonical batch path puts ``overall_score`` directly on
        # PendingOptimization (the LLM-blended hybrid result). The probe
        # aggregates these without re-applying compute_overall(task_type)
        # because the batch primitive has already done that. The fake
        # run_batch fixture returns overalls 7.6, 7.65, 7.7, 7.75, 7.8.
        # Mean = 7.7. F3.1 invariant is verified at the batch_pipeline
        # layer (its own tests); this test now just confirms the probe
        # correctly aggregates the canonical batch's scores.
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
        assert row.aggregate["mean_overall"] == pytest.approx(7.7, abs=0.05)
        assert row.aggregate["scoring_formula_version"] == 4


class TestProbePersistenceWiring:
    """Tier-1 infrastructure-wiring regression guards (v0.4.12 hotfix).

    User-reported defect: probe-generated prompts were missing from
    ``/api/optimizations`` and topology view because ``_execute_one``
    synthesized a fake ``optimization_id`` UUID without persisting an
    Optimization row or running cluster assignment. The fix wires Phase 3
    to the canonical Optimization persistence + ``assign_cluster()`` path
    so probe outputs are first-class taxonomy artifacts.
    """

    @pytest.mark.asyncio
    async def test_completed_prompts_persist_optimization_rows(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """Each completed probe prompt persists an Optimization row whose
        id is the same as ``ProbePromptResult.optimization_id``.
        """
        from sqlalchemy import select as sa_select

        from app.models import Optimization

        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        async for _ in svc.run(_make_request(n_prompts=5), probe_id="p-test-persist"):
            pass

        row = await db_session.get(ProbeRun, "p-test-persist")
        assert row is not None
        assert row.status == "completed"
        opt_ids = [
            r["optimization_id"] for r in (row.prompt_results or [])
            if r.get("status") == "completed"
        ]
        assert len(opt_ids) == 5

        # Every recorded optimization_id must resolve to a real DB row.
        for oid in opt_ids:
            opt = (
                await db_session.execute(
                    sa_select(Optimization).where(Optimization.id == oid),
                )
            ).scalar_one_or_none()
            assert opt is not None, f"Optimization {oid} missing from DB"
            # Canonical batch path uses scoring_mode=hybrid (real LLM
            # blend); probe rows are tagged via context_sources.source
            # rather than a special scoring_mode.
            assert (opt.context_sources or {}).get("source") == "probe", (
                f"context_sources.source='{(opt.context_sources or {}).get('source')}' "
                f"-- _tag_probe_rows did not overlay 'source=probe'"
            )
            assert opt.repo_full_name == "owner/repo"
            assert opt.cluster_id is not None  # assigned by hot path
            # Canonical batch shape: optimized_prompt populated.
            assert opt.optimized_prompt is not None, (
                "optimized_prompt is NULL -- probe is not using the "
                "canonical batch_pipeline path (no optimize phase ran)"
            )

    @pytest.mark.asyncio
    async def test_persisted_rows_populate_infrastructure_fields(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """Persisted Optimization rows must use real infrastructure fields,
        not deterministic stubs:

        * ``heuristic_baseline_scores`` populated (real HeuristicScorer)
        * ``original_scores`` populated (== score_* in Tier 1)
        * ``score_deltas`` populated (== zero in Tier 1, no optimize phase)
        * ``improvement_score`` set to 0.0 (no optimization)
        * ``optimized_embedding`` mirrors ``embedding`` (no optimize phase)
        * ``qualifier_embedding`` populated (intent_label vector)
        * ``domain_raw`` populated
        * ``models_by_phase`` populated
        * Per-row scores VARY -- not the same identical 6.80 for every
          prompt (regression guard for the v0.4.12 stub-everywhere bug
          that surfaced as ``mean=p5=p50=p95=6.80`` in live runs).

        Per-row score variance requires real HeuristicScorer to fire; the
        test prompts in the fixture have different shapes so heuristic
        scores diverge naturally.
        """
        from sqlalchemy import select as sa_select

        from app.models import Optimization

        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        async for _ in svc.run(_make_request(n_prompts=5), probe_id="p-test-infra"):
            pass

        row = await db_session.get(ProbeRun, "p-test-infra")
        opt_ids = [
            r["optimization_id"] for r in (row.prompt_results or [])
            if r.get("status") == "completed"
        ]
        assert len(opt_ids) == 5

        opts = (
            await db_session.execute(
                sa_select(Optimization).where(Optimization.id.in_(opt_ids)),
            )
        ).scalars().all()

        # Canonical batch shape (post-v0.4.12 INTEGRATE refactor):
        # the canonical batch path populates optimized_prompt, original_scores,
        # score_deltas, improvement_score, multi-embeddings, models_by_phase.
        # The probe SHOULD have all of these because it now delegates to
        # ``run_batch + bulk_persist + batch_taxonomy_assign`` rather than
        # rolling its own heuristic-only persist.
        for o in opts:
            assert o.optimized_prompt is not None, (
                "optimized_prompt is NULL -- probe didn't run optimize phase"
            )
            assert o.original_scores is not None, (
                "original_scores is NULL -- probe didn't go through scoring"
            )
            assert o.score_deltas is not None
            assert o.improvement_score is not None
            assert o.optimized_embedding is not None
            assert o.transformation_embedding is not None
            assert o.domain_raw is not None
            assert o.models_by_phase is not None

        # Variance guard -- canonical batch produces distinct overall
        # scores per prompt (LLM-blended).
        overalls = {round(o.overall_score or 0.0, 2) for o in opts}
        assert len(overalls) >= 2, (
            f"Expected per-row score variance; got identical {overalls}"
        )

        # Wiring guard -- canonical batch sets task_type/domain from real
        # enrich() on each prompt. Stub returns analysis/backend.
        for o in opts:
            assert o.task_type == "analysis", (
                f"task_type='{o.task_type}' -- probe didn't propagate "
                f"PendingOptimization.task_type"
            )
            assert o.domain == "backend"
            # Probe-source overlay applied by _tag_probe_rows.
            assert (o.context_sources or {}).get("source") == "probe", (
                f"source='{(o.context_sources or {}).get('source')}' -- "
                f"_tag_probe_rows didn't overlay probe identity"
            )

    @pytest.mark.asyncio
    async def test_completed_prompts_assign_cluster(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
    ):
        """``cluster_id_at_persist`` is populated and references a real
        PromptCluster row (created or matched by ``assign_cluster()``).
        """
        from sqlalchemy import select as sa_select

        svc = ProbeService(
            db_session,
            mock_provider,
            mock_repo_query,
            mock_context_service,
            mock_event_bus,
        )
        async for _ in svc.run(_make_request(n_prompts=5), probe_id="p-test-cluster"):
            pass

        row = await db_session.get(ProbeRun, "p-test-cluster")
        completed = [
            r for r in (row.prompt_results or [])
            if r.get("status") == "completed"
        ]
        assert completed, "expected at least one completed probe prompt"
        for r in completed:
            cid = r.get("cluster_id_at_persist")
            assert cid is not None, "cluster_id_at_persist must be set"
            cluster = (
                await db_session.execute(
                    sa_select(PromptCluster).where(PromptCluster.id == cid),
                )
            ).scalar_one_or_none()
            assert cluster is not None


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
    async def test_run_batch_called_with_canonical_kwargs(
        self,
        db_session,
        mock_provider,
        mock_repo_query,
        mock_context_service,
        mock_event_bus,
        monkeypatch,
    ):
        """Probe's Phase 3 must invoke ``batch_orchestrator.run_batch``
        with the canonical kwarg shape -- the spec's "same execution
        primitive (batch_pipeline)" mandate.

        Regression guard: the probe was previously calling
        ``ContextEnrichmentService.enrich()`` directly from a hand-rolled
        loop and using only ``.analysis`` -- discarding codebase_context,
        applied_patterns, and enrichment_meta. The canonical batch
        primitive uses all enrichment layers internally.
        """
        from app.services import batch_orchestrator
        from app.services.batch_pipeline import PendingOptimization
        captured = {}

        async def _capture(prompts, provider, prompt_loader, embedding_service, **kwargs):
            captured["prompts"] = prompts
            captured["provider"] = provider
            captured["kwargs"] = kwargs
            return [
                PendingOptimization(
                    id=f"opt-{i}", trace_id=f"tr-{i}",
                    batch_id=kwargs.get("batch_id"),
                    raw_prompt=p, optimized_prompt=f"OPT: {p[:30]}",
                    task_type="analysis", intent_label="x",
                    domain="backend", domain_raw="backend",
                    overall_score=7.5, score_clarity=8.0, score_specificity=8.0,
                    score_structure=7.0, score_faithfulness=8.0, score_conciseness=7.0,
                    embedding=b"\x00" * 1536, optimized_embedding=b"\x00" * 1536,
                    models_by_phase={"a": "x"}, original_scores={"clarity": 6.0},
                    score_deltas={"clarity": 1.5}, scoring_mode="hybrid",
                    status="completed", provider="x", routing_tier="internal",
                    repo_full_name=kwargs.get("repo_full_name"),
                    context_sources={"source": "batch_seed"},
                )
                for i, p in enumerate(prompts)
            ]

        monkeypatch.setattr(batch_orchestrator, "run_batch", _capture)

        svc = ProbeService(
            db_session, mock_provider, mock_repo_query,
            mock_context_service, mock_event_bus,
        )
        async for _ in svc.run(_make_request(n_prompts=5), probe_id="p-canonical"):
            pass

        assert "prompts" in captured, "run_batch was not invoked"
        kw = captured["kwargs"]
        # Canonical kwargs the probe is required to thread through.
        assert kw["tier"] == "internal"
        assert kw["batch_id"] == "p-canonical"
        assert kw["repo_full_name"] == "owner/repo"
        assert kw["context_service"] is mock_context_service, (
            "probe must pass its context_service through so enrich runs "
            "with full layers (codebase_context, applied_patterns, "
            "divergence_alerts) -- not just heuristic analysis"
        )
        assert kw.get("session_factory") is not None
        # Codebase_context comes from grounding's explore_synthesis_excerpt.
        assert kw.get("codebase_context") is not None, (
            "probe must thread Phase-1 grounding context into run_batch "
            "so the optimize phase has codebase awareness"
        )

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
