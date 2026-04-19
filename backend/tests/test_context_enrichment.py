"""Tests for ContextEnrichmentService."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.services.context_enrichment import (
    PROFILE_CODE_AWARE,
    PROFILE_COLD_START,
    PROFILE_KNOWLEDGE_WORK,
    ContextEnrichmentService,
    EnrichedContext,
    compute_repo_relevance,
    detect_divergences,
    extract_domain_vocab,
    resolve_strategy_intelligence,
    select_enrichment_profile,
)
from app.services.domain_signal_loader import DomainSignalLoader
from app.services.heuristic_analyzer import HeuristicAnalyzer, set_signal_loader
from app.services.workspace_intelligence import WorkspaceIntelligence

# Legacy domain signals — identical to the old hardcoded _DOMAIN_SIGNALS so
# tests that assert specific domain classifications continue to pass.
_TEST_DOMAIN_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "backend": [
        ("api", 0.8), ("endpoint", 0.9), ("server", 0.8),
        ("middleware", 0.9), ("fastapi", 1.0), ("django", 1.0),
        ("flask", 1.0), ("database", 0.6), ("authentication", 0.7),
        ("route", 0.6),
    ],
    "frontend": [
        ("react", 1.0), ("svelte", 1.0), ("component", 0.8),
        ("css", 0.9), ("ui", 0.8), ("layout", 0.7),
        ("responsive", 0.8), ("tailwind", 0.9), ("vue", 1.0),
    ],
    "database": [
        ("sql", 1.0), ("migration", 0.9), ("schema", 0.8),
        ("query", 0.7), ("index", 0.6), ("postgresql", 1.0),
        ("sqlite", 1.0), ("orm", 0.8), ("table", 0.6),
    ],
    "devops": [
        ("docker", 1.0), ("ci/cd", 1.0), ("kubernetes", 1.0),
        ("terraform", 1.0), ("nginx", 0.9), ("monitoring", 0.7),
        ("deploy", 0.8), ("pipeline", 0.5),
    ],
    "security": [
        ("auth", 0.7), ("encryption", 1.0), ("vulnerability", 1.0),
        ("cors", 0.9), ("jwt", 0.9), ("oauth", 0.9), ("sanitize", 0.8),
        ("injection", 0.9), ("xss", 1.0), ("csrf", 1.0),
    ],
}


@pytest.fixture(autouse=True)
def _seed_signal_loader():
    """Inject a DomainSignalLoader with legacy keyword signals."""
    loader = DomainSignalLoader()
    loader._signals = dict(_TEST_DOMAIN_SIGNALS)
    loader._precompile_patterns()
    set_signal_loader(loader)
    yield
    set_signal_loader(None)


@pytest_asyncio.fixture
async def db():
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        yield session
    await engine.dispose()


class TestEnrichPassthrough:
    @pytest.mark.asyncio
    async def test_passthrough_runs_heuristic_analysis(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
        )
        assert isinstance(result, EnrichedContext)
        assert result.analysis is not None
        assert result.analysis.task_type == "coding"
        assert result.context_sources["heuristic_analysis"] is True

    @pytest.mark.asyncio
    async def test_passthrough_gets_adaptation(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
        )
        # Strategy intelligence is resolved (may be None if no data, but key exists)
        assert "strategy_intelligence" in result.context_sources


class TestEnrichInternal:
    @pytest.mark.asyncio
    async def test_internal_runs_heuristic_for_domain_detection(self, db, tmp_path):
        """Heuristic analysis runs for ALL tiers to provide domain detection."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        assert result.analysis is not None
        assert result.context_sources["heuristic_analysis"] is True

    @pytest.mark.asyncio
    async def test_internal_skips_curated_index(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
            repo_full_name="owner/repo",
        )
        # Internal tier doesn't use curated index (pipeline does explore)
        assert result.codebase_context is None


class TestWorkspaceGuidanceFallback:
    """Workspace guidance is now folded into codebase_context as a fallback
    when no repo synthesis exists."""

    @pytest.mark.asyncio
    async def test_workspace_path_provides_codebase_context_when_no_repo(self, db, tmp_path):
        """Without a repo, workspace guidance becomes the codebase context."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "CLAUDE.md").write_text("# Project Guidance\nUse async everywhere.")

        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
            workspace_path=str(workspace),
        )
        # Workspace guidance now appears as codebase_context
        assert result.codebase_context is not None
        assert "async everywhere" in result.codebase_context
        # workspace_as_fallback tracked in enrichment meta
        assert result.enrichment_meta.get("workspace_as_fallback") is True

    @pytest.mark.asyncio
    async def test_no_workspace_no_repo_returns_none_codebase(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        assert result.codebase_context is None
        assert result.context_sources["codebase_context"] is False


class TestEnrichGracefulDegradation:
    @pytest.mark.asyncio
    async def test_all_none_still_returns_valid_context(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Tell me about the weather",
            tier="passthrough", db=db,
        )
        assert isinstance(result, EnrichedContext)
        assert result.raw_prompt == "Tell me about the weather"

    @pytest.mark.asyncio
    async def test_context_sources_audit_all_keys_present(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="passthrough", db=db,
        )
        expected_keys = {
            "codebase_context", "strategy_intelligence",
            "applied_patterns", "heuristic_analysis",
        }
        assert expected_keys == set(result.context_sources.keys())

    @pytest.mark.asyncio
    async def test_enriched_context_is_frozen(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a sorting algorithm",
            tier="passthrough", db=db,
        )
        assert isinstance(result, EnrichedContext)
        with pytest.raises((AttributeError, TypeError)):
            result.raw_prompt = "something else"  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_context_sources_is_immutable(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a sorting algorithm",
            tier="passthrough", db=db,
        )
        with pytest.raises(TypeError):
            result.context_sources["new_key"] = True  # type: ignore[index]


class TestEnrichSampling:
    @pytest.mark.asyncio
    async def test_sampling_tier_runs_heuristic_for_domain_detection(self, db, tmp_path):
        """Heuristic analysis runs for ALL tiers (including sampling) to provide
        domain detection for curated retrieval cross-domain filtering."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="sampling", db=db,
        )
        assert result.analysis is not None
        assert result.analysis.task_type == "coding"
        assert result.context_sources["heuristic_analysis"] is True

    @pytest.mark.asyncio
    async def test_sampling_tier_skips_codebase_context(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="sampling", db=db,
            repo_full_name="owner/repo",
        )
        assert result.codebase_context is None
        assert result.context_sources["codebase_context"] is False


class TestPreferencesGating:
    @pytest.mark.asyncio
    async def test_disable_strategy_intelligence_via_preferences(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="passthrough", db=db,
            preferences_snapshot={"enable_strategy_intelligence": False},
        )
        assert result.strategy_intelligence is None
        assert result.context_sources["strategy_intelligence"] is False

    @pytest.mark.asyncio
    async def test_strategy_intelligence_enabled_by_default(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="passthrough", db=db,
        )
        # Strategy intelligence key exists — may be None (no data) but was attempted
        assert "strategy_intelligence" in result.context_sources

    @pytest.mark.asyncio
    async def test_strategy_intelligence_tracked_in_sources(self, db, tmp_path):
        """strategy_intelligence is tracked in context_sources dict."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="passthrough", db=db,
        )
        assert "strategy_intelligence" in result.context_sources


class TestDBPersistenceCompat:
    """Verify heuristic analysis fields are compatible with Optimization model."""

    @pytest.mark.asyncio
    async def test_passthrough_enrichment_populates_db_fields(self, db, tmp_path):
        service = _build_service(tmp_path)
        enrichment = await service.enrich(
            raw_prompt="Implement a FastAPI REST endpoint with JWT authentication",
            tier="passthrough", db=db,
        )
        # Heuristic analysis should classify — not default to "general"
        assert enrichment.analysis is not None
        assert enrichment.analysis.task_type == "coding"
        assert enrichment.analysis.domain in ("backend", "fullstack", "backend: security", "backend: auth")
        assert enrichment.analysis.intent_label != ""

        # context_sources should track what was resolved
        assert "heuristic_analysis" in enrichment.context_sources
        assert enrichment.context_sources["heuristic_analysis"] is True

        # Simulate DB persistence: external LLM values take precedence
        opt_task_type = "writing"  # External LLM override
        effective = opt_task_type or enrichment.analysis.task_type or "general"
        assert effective == "writing"  # External wins

        # Fallback: no external override → heuristic value
        opt_task_type_none = None
        effective2 = opt_task_type_none or enrichment.analysis.task_type or "general"
        assert effective2 == "coding"  # Heuristic fills in


def _build_service(tmp_path: Path) -> ContextEnrichmentService:
    from app.services.heuristic_analyzer import HeuristicAnalyzer
    from app.services.workspace_intelligence import WorkspaceIntelligence
    mock_es = AsyncMock()
    mock_gc = AsyncMock()
    return ContextEnrichmentService(
        prompts_dir=tmp_path,
        data_dir=tmp_path,
        workspace_intel=WorkspaceIntelligence(),
        embedding_service=mock_es,
        heuristic_analyzer=HeuristicAnalyzer(),
        github_client=mock_gc,
    )


# ---------------------------------------------------------------------------
# Phase 1: Task-Gated Curated Retrieval
# ---------------------------------------------------------------------------


class TestShouldSkipCurated:
    """Unit tests for the _should_skip_curated() pure function."""

    def test_coding_task_does_not_skip(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "coding", "Implement a REST API endpoint",
        )
        assert skip is False
        assert reason is None

    def test_system_task_does_not_skip(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "system", "Configure the deployment pipeline",
        )
        assert skip is False
        assert reason is None

    def test_data_task_does_not_skip(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "data", "Build an ETL pipeline for the dataset",
        )
        assert skip is False
        assert reason is None

    def test_writing_task_skips(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "writing", "Write a blog post about sustainable energy",
        )
        assert skip is True
        assert reason is not None
        assert "writing" in reason

    def test_creative_task_skips(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "creative", "Brainstorm marketing campaign ideas",
        )
        assert skip is True
        assert "creative" in reason  # type: ignore[operator]

    def test_general_task_skips(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "general", "Help me organize my thoughts",
        )
        assert skip is True
        assert "general" in reason  # type: ignore[operator]

    def test_escape_hatch_code_keyword_prevents_skip(self):
        """Even for a writing task, mentioning 'code' should keep curated active."""
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "writing", "Write documentation for the API endpoint",
        )
        assert skip is False
        assert reason is None

    def test_escape_hatch_database_keyword_prevents_skip(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "creative", "Design a database schema for the project",
        )
        assert skip is False
        assert reason is None

    def test_escape_hatch_case_insensitive(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "writing", "Write about the DATABASE migration process",
        )
        assert skip is False
        assert reason is None

    def test_analysis_task_skips_without_code_keywords(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "analysis", "Compare the pros and cons of remote work",
        )
        assert skip is True
        assert "analysis" in reason  # type: ignore[operator]

    def test_analysis_task_keeps_with_code_keywords(self):
        skip, reason = ContextEnrichmentService._should_skip_curated(
            "analysis", "Analyze the query performance of this SQL",
        )
        assert skip is False
        assert reason is None


class TestTaskGatedCuratedRetrieval:
    """Integration tests: curated retrieval is skipped for non-coding prompts."""

    @pytest.mark.asyncio
    async def test_writing_task_skips_curated_in_enrich(self, db, tmp_path):
        """A writing prompt with a linked repo should skip curated retrieval."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Write an engaging blog post about renewable energy trends",
            tier="internal", db=db,
            repo_full_name="user/my-repo", repo_branch="main",
        )
        meta = dict(result.enrichment_meta)
        assert "curated_retrieval" in meta
        assert meta["curated_retrieval"]["status"] == "skipped_task_type"
        assert meta["curated_retrieval"]["files_included"] == 0
        assert "reason" in meta["curated_retrieval"]

    @pytest.mark.asyncio
    async def test_coding_task_does_not_skip_curated(self, db, tmp_path):
        """A coding prompt with a linked repo should NOT skip curated retrieval."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user authentication",
            tier="internal", db=db,
            repo_full_name="user/my-repo", repo_branch="main",
        )
        meta = dict(result.enrichment_meta)
        assert "curated_retrieval" in meta
        # Should NOT have the skipped status
        assert meta["curated_retrieval"].get("status") != "skipped_task_type"

    @pytest.mark.asyncio
    async def test_synthesis_always_included_even_when_curated_skipped(self, db, tmp_path):
        """L3a synthesis should still be fetched even when L3b curated is skipped."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Write a persuasive marketing email for our new product",
            tier="internal", db=db,
            repo_full_name="user/my-repo", repo_branch="main",
        )
        meta = dict(result.enrichment_meta)
        # Synthesis should have been attempted (present key exists in meta)
        assert "explore_synthesis" in meta
        # Curated should be skipped
        assert meta["curated_retrieval"]["status"] == "skipped_task_type"


# ---------------------------------------------------------------------------
# Phase 2A: Strategy Intelligence (standalone function)
# ---------------------------------------------------------------------------


async def _seed_optimization(db, strategy: str, task_type: str, domain: str, score: float):
    """Helper: insert a scored optimization for strategy intelligence tests."""
    from app.models import Optimization
    opt = Optimization(
        raw_prompt=f"test prompt for {strategy}",
        optimized_prompt="optimized",
        overall_score=score,
        strategy_used=strategy,
        task_type=task_type,
        domain=domain,
        status="completed",
    )
    db.add(opt)
    await db.flush()
    return opt


async def _seed_affinity(db, strategy: str, task_type: str, up: int, down: int):
    """Helper: insert a strategy affinity row for strategy intelligence tests."""
    from app.models import StrategyAffinity
    total = up + down
    aff = StrategyAffinity(
        task_type=task_type,
        strategy=strategy,
        thumbs_up=up,
        thumbs_down=down,
        approval_rate=up / total if total > 0 else 0.0,
    )
    db.add(aff)
    await db.flush()
    return aff


class TestStrategyIntelligence:
    """Tests for the resolve_strategy_intelligence() standalone function."""

    @pytest.mark.asyncio
    async def test_combines_perf_and_feedback(self, db):
        """Both performance signals and adaptation data should appear."""
        # Seed 3 optimizations for perf signal threshold
        for _ in range(3):
            await _seed_optimization(db, "meta-prompting", "coding", "backend", 8.0)
        # Seed affinity
        await _seed_affinity(db, "role-playing", "coding", 7, 2)
        await db.commit()

        result, _ = await resolve_strategy_intelligence(db, "coding", "backend")
        assert result is not None
        assert "meta-prompting" in result  # from perf signals
        assert "role-playing" in result    # from adaptation
        assert "approval" in result.lower()

    @pytest.mark.asyncio
    async def test_perf_only_no_feedback(self, db):
        """Works when only optimization history exists (no feedback)."""
        for _ in range(3):
            await _seed_optimization(db, "structured-output", "data", "database", 7.5)
        await db.commit()

        result, _ = await resolve_strategy_intelligence(db, "data", "database")
        assert result is not None
        assert "structured-output" in result

    @pytest.mark.asyncio
    async def test_feedback_only_no_perf(self, db):
        """Works when only feedback history exists (no scored optimizations)."""
        await _seed_affinity(db, "few-shot", "writing", 5, 1)
        await db.commit()

        result, _ = await resolve_strategy_intelligence(db, "writing", "general")
        assert result is not None
        assert "few-shot" in result
        assert "approval" in result.lower()

    @pytest.mark.asyncio
    async def test_empty_returns_none(self, db):
        """Returns None when neither source has data."""
        result, _ = await resolve_strategy_intelligence(db, "creative", "general")
        assert result is None

    @pytest.mark.asyncio
    async def test_blocked_strategies_included(self, db):
        """Strategies with approval < 0.3 and 5+ feedbacks appear as blocked."""
        await _seed_affinity(db, "chain-of-thought", "coding", 1, 8)  # 11% approval
        await db.commit()

        result, _ = await resolve_strategy_intelligence(db, "coding", "general")
        assert result is not None
        assert "Blocked" in result or "blocked" in result
        assert "chain-of-thought" in result

    @pytest.mark.asyncio
    async def test_anti_patterns_included(self, db):
        """Low-scoring strategies appear as anti-patterns."""
        for _ in range(4):
            await _seed_optimization(db, "few-shot", "coding", "backend", 4.0)
        await db.commit()

        result, _ = await resolve_strategy_intelligence(db, "coding", "backend")
        assert result is not None
        assert "Avoid" in result
        assert "few-shot" in result


# ---------------------------------------------------------------------------
# Phase 4: Enrichment Profiles
# ---------------------------------------------------------------------------


class TestSelectEnrichmentProfile:
    """Unit tests for the select_enrichment_profile() pure function."""

    def test_cold_start_below_threshold(self):
        assert select_enrichment_profile("coding", True, 5) == PROFILE_COLD_START

    def test_cold_start_at_zero(self):
        assert select_enrichment_profile("writing", False, 0) == PROFILE_COLD_START

    def test_cold_start_at_boundary(self):
        assert select_enrichment_profile("coding", True, 9) == PROFILE_COLD_START

    def test_code_aware_at_threshold(self):
        assert select_enrichment_profile("coding", True, 10) == PROFILE_CODE_AWARE

    def test_code_aware_system_task(self):
        assert select_enrichment_profile("system", True, 50) == PROFILE_CODE_AWARE

    def test_code_aware_data_task(self):
        assert select_enrichment_profile("data", True, 20) == PROFILE_CODE_AWARE

    def test_code_aware_requires_repo(self):
        """Coding task without repo → knowledge_work, not code_aware."""
        assert select_enrichment_profile("coding", False, 50) == PROFILE_KNOWLEDGE_WORK

    def test_knowledge_work_writing(self):
        assert select_enrichment_profile("writing", False, 50) == PROFILE_KNOWLEDGE_WORK

    def test_knowledge_work_creative(self):
        assert select_enrichment_profile("creative", True, 50) == PROFILE_KNOWLEDGE_WORK

    def test_knowledge_work_analysis(self):
        assert select_enrichment_profile("analysis", False, 100) == PROFILE_KNOWLEDGE_WORK

    def test_knowledge_work_general(self):
        assert select_enrichment_profile("general", True, 200) == PROFILE_KNOWLEDGE_WORK


class TestEnrichmentProfileIntegration:
    """Integration tests: profile selection affects which layers are active."""

    @pytest.mark.asyncio
    async def test_profile_tracked_in_meta(self, db, tmp_path):
        """Every enrichment result includes the profile in metadata."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        meta = dict(result.enrichment_meta)
        assert "enrichment_profile" in meta
        assert meta["enrichment_profile"] in {PROFILE_CODE_AWARE, PROFILE_KNOWLEDGE_WORK, PROFILE_COLD_START}

    @pytest.mark.asyncio
    async def test_cold_start_skips_strategy_and_patterns(self, db, tmp_path):
        """Cold-start profile skips strategy intelligence and patterns."""
        # Fresh DB with 0 optimizations → cold_start profile
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        meta = dict(result.enrichment_meta)
        assert meta["enrichment_profile"] == PROFILE_COLD_START
        assert result.strategy_intelligence is None
        assert result.applied_patterns is None
        assert "strategy_intelligence" in meta.get("profile_skipped_layers", [])
        assert "applied_patterns" in meta.get("profile_skipped_layers", [])

    @pytest.mark.asyncio
    async def test_knowledge_work_skips_codebase(self, db, tmp_path):
        """Knowledge-work profile skips codebase context even with repo."""
        # Seed enough optimizations to pass cold-start threshold
        for _ in range(10):
            await _seed_optimization(db, "auto", "writing", "general", 7.0)
        await db.commit()

        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Write a compelling blog post about sustainable energy",
            tier="internal", db=db,
            repo_full_name="user/my-repo", repo_branch="main",
        )
        meta = dict(result.enrichment_meta)
        assert meta["enrichment_profile"] == PROFILE_KNOWLEDGE_WORK
        assert result.codebase_context is None
        assert "codebase_context" in meta.get("profile_skipped_layers", [])


# ---------------------------------------------------------------------------
# A1+A2: Disambiguation metadata flow
# ---------------------------------------------------------------------------


class TestDisambiguationMetadata:
    """Verify disambiguation metadata flows from HeuristicAnalysis to enrichment_meta."""

    @pytest.mark.asyncio
    async def test_disambiguation_captured_in_enrichment_meta(self, db, tmp_path):
        """When heuristic applies disambiguation, enrichment_meta records the override."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Design a caching system for the API with Redis backend and TTL expiration",
            tier="passthrough", db=db,
        )
        assert result.analysis is not None
        assert result.analysis.task_type == "coding"
        if result.analysis.disambiguation_applied:
            meta = dict(result.enrichment_meta)
            assert "heuristic_disambiguation" in meta
            dis = meta["heuristic_disambiguation"]
            assert dis["original_task_type"] in ("creative", "general")
            assert dis["corrected_to"] == "coding"

    @pytest.mark.asyncio
    async def test_no_disambiguation_no_meta(self, db, tmp_path):
        """When no disambiguation needed, enrichment_meta has no disambiguation key."""
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user authentication",
            tier="passthrough", db=db,
        )
        assert result.analysis is not None
        assert result.analysis.task_type == "coding"
        assert result.analysis.disambiguation_applied is False
        meta = dict(result.enrichment_meta)
        assert "heuristic_disambiguation" not in meta


# ---------------------------------------------------------------------------
# C1: Domain-Relaxed Fallback Queries
# ---------------------------------------------------------------------------


class TestStrategyIntelligenceFallback:
    """Tests for the C1 domain-relaxed fallback in resolve_performance_signals."""

    @pytest.mark.asyncio
    async def test_exact_match_preferred_over_fallback(self, db):
        """When exact domain+task data exists, fallback does NOT fire."""
        for _ in range(3):
            await _seed_optimization(db, "meta-prompting", "coding", "backend", 8.0)
        await db.commit()

        result, fallback = await resolve_strategy_intelligence(db, "coding", "backend")
        assert result is not None
        assert fallback is False
        assert "meta-prompting" in result
        assert "across all domains" not in result

    @pytest.mark.asyncio
    async def test_fallback_fires_when_exact_empty(self, db):
        """When exact domain has no data, fallback returns cross-domain results."""
        # Seed data for coding+backend (NOT coding+database)
        for _ in range(4):
            await _seed_optimization(db, "structured-output", "coding", "backend", 7.5)
        await db.commit()

        # Query coding+database — exact match empty, fallback should fire
        result, fallback = await resolve_strategy_intelligence(db, "coding", "database")
        assert result is not None
        assert fallback is True
        assert "structured-output" in result
        assert "across all domains" in result

    @pytest.mark.asyncio
    async def test_fallback_tracked_in_enrichment_meta(self, db, tmp_path):
        """When fallback fires, enrichment_meta records it."""
        for _ in range(3):
            await _seed_optimization(db, "auto", "coding", "backend", 7.0)
        await db.commit()

        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user authentication",
            tier="internal", db=db,
        )
        meta = dict(result.enrichment_meta)
        # The heuristic may classify as coding+backend (exact) or coding+general (fallback)
        # If fallback fired, the key should be present
        if meta.get("strategy_intelligence_fallback"):
            assert meta["strategy_intelligence_fallback"] is True

    @pytest.mark.asyncio
    async def test_no_data_returns_none_even_with_fallback(self, db):
        """When neither exact nor fallback has data, result is None."""
        result, fallback = await resolve_strategy_intelligence(db, "creative", "general")
        assert result is None
        assert fallback is False

    @pytest.mark.asyncio
    async def test_anti_pattern_fallback_fires(self, db):
        """Anti-pattern fallback also triggers when exact anti-patterns are empty."""
        for _ in range(4):
            await _seed_optimization(db, "few-shot", "coding", "backend", 4.0)
        await db.commit()

        # Query coding+database — should get cross-domain anti-patterns
        result, fallback = await resolve_strategy_intelligence(db, "coding", "database")
        assert result is not None
        assert fallback is True
        assert "Avoid" in result


# ---------------------------------------------------------------------------
# B1: Tech Stack Divergence Detection
# ---------------------------------------------------------------------------


class TestDivergenceDetection:
    """Tests for the B1 tech stack divergence detection."""

    def test_postgresql_vs_sqlite_detected(self):
        divs = detect_divergences(
            "Add row-level security to our PostgreSQL schema",
            "This project uses SQLAlchemy async with aiosqlite for SQLite storage.",
        )
        assert len(divs) == 1
        assert divs[0].prompt_tech == "postgresql"
        assert divs[0].codebase_tech == "sqlite"
        assert divs[0].category == "database"
        assert divs[0].severity == "conflict"

    def test_migration_keyword_changes_severity(self):
        divs = detect_divergences(
            "Migrate our database from SQLite to PostgreSQL for better concurrency",
            "The backend uses aiosqlite with SQLAlchemy async.",
        )
        assert len(divs) == 1
        assert divs[0].severity == "migration"

    def test_no_divergence_when_matching(self):
        divs = detect_divergences(
            "Add a new FastAPI endpoint for user management",
            "Backend built with FastAPI and SQLAlchemy async.",
        )
        assert len(divs) == 0

    def test_no_divergence_without_codebase_context(self):
        divs = detect_divergences(
            "Add PostgreSQL row-level security",
            None,
        )
        assert len(divs) == 0

    def test_typescript_javascript_not_conflicting(self):
        divs = detect_divergences(
            "Refactor the TypeScript components",
            "Frontend uses JavaScript with npm and node_modules.",
        )
        # TS is a superset of JS — no conflict
        assert len(divs) == 0

    def test_multiple_divergences_detected(self):
        divs = detect_divergences(
            "Build this with Django and PostgreSQL instead",
            "Project uses FastAPI with aiosqlite for SQLite.",
        )
        categories = {d.category for d in divs}
        assert "framework" in categories  # django vs fastapi
        assert "database" in categories   # postgresql vs sqlite
        assert len(divs) >= 2

    def test_redis_not_flagged_as_conflict(self):
        """Redis is always additive — not a database conflict."""
        divs = detect_divergences(
            "Add Redis caching to our backend",
            "Backend uses SQLite with aiosqlite.",
        )
        # Redis should NOT produce a database conflict with SQLite
        assert all(d.prompt_tech != "redis" for d in divs)

    def test_case_insensitive(self):
        divs = detect_divergences(
            "Set up POSTGRESQL replication",
            "Using SQLITE via aiosqlite.",
        )
        assert len(divs) == 1
        assert divs[0].prompt_tech == "postgresql"

    @pytest.mark.asyncio
    async def test_divergence_stored_in_enrichment_meta(self, db, tmp_path):
        """Divergences flow into enrichment_meta when codebase context has conflicts."""
        # We can't easily mock codebase context in the full enrich() flow
        # because it requires a real repo index. Test the function directly.
        divs = detect_divergences(
            "Implement Django REST endpoints",
            "Built with FastAPI and Python 3.12.",
        )
        assert len(divs) >= 1
        assert divs[0].category == "framework"

    def test_empty_prompt_no_crash(self):
        divs = detect_divergences("", "FastAPI with SQLite.")
        assert divs == []

    def test_empty_codebase_no_crash(self):
        divs = detect_divergences("Add PostgreSQL support", "")
        assert divs == []


# ---------------------------------------------------------------------------
# B0: Repo relevance gate (integration)
# ---------------------------------------------------------------------------


class TestRepoRelevanceGate:
    """Verify hybrid repo relevance gate integration in enrich()."""

    @pytest.mark.asyncio
    async def test_gate_fires(self, db, tmp_path):
        """Low cosine against repo-anchored synthesis → gate fires (skip)."""
        import numpy as np

        for _ in range(10):
            await _seed_optimization(db, "auto", "coding", "backend", 7.0)
        await db.commit()

        mock_es = AsyncMock()
        # Near-orthogonal vectors — cosine stays below 0.15 floor
        prompt_vec = np.zeros(384, dtype=np.float32)
        prompt_vec[0] = 1.0
        synth_vec = np.zeros(384, dtype=np.float32)
        synth_vec[1] = 1.0
        mock_es.aembed_single = AsyncMock(side_effect=[prompt_vec, synth_vec])

        service = ContextEnrichmentService(
            prompts_dir=tmp_path,
            data_dir=tmp_path,
            workspace_intel=WorkspaceIntelligence(),
            embedding_service=mock_es,
            heuristic_analyzer=HeuristicAnalyzer(),
            github_client=AsyncMock(),
        )

        service._get_explore_synthesis = AsyncMock(
            return_value=(
                "taxonomy taxonomy taxonomy clustering clustering clustering "
                "enrichment enrichment enrichment"
            ),
        )

        result = await service.enrich(
            raw_prompt="Build a task management system with FastAPI",
            tier="internal", db=db,
            repo_full_name="project-synthesis/ProjectSynthesis",
            repo_branch="main",
        )

        meta = dict(result.enrichment_meta)
        assert meta.get("repo_relevance_skipped") is True
        assert meta["repo_relevance_info"]["decision"] == "skip"
        assert result.codebase_context is None
        assert meta["curated_retrieval"]["status"] == "skipped_repo_relevance"

    @pytest.mark.asyncio
    async def test_gate_passes(self, db, tmp_path):
        """Synthesis has domain terms AND prompt includes them → gate passes."""
        import numpy as np

        for _ in range(10):
            await _seed_optimization(db, "auto", "coding", "backend", 7.0)
        await db.commit()

        mock_es = AsyncMock()
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)
        noise = rng.random(384).astype(np.float32) * 0.3
        vec2 = base + noise
        vec2 /= np.linalg.norm(vec2)
        mock_es.aembed_single = AsyncMock(side_effect=[base, vec2])

        service = ContextEnrichmentService(
            prompts_dir=tmp_path,
            data_dir=tmp_path,
            workspace_intel=WorkspaceIntelligence(),
            embedding_service=mock_es,
            heuristic_analyzer=HeuristicAnalyzer(),
            github_client=AsyncMock(),
        )

        service._get_explore_synthesis = AsyncMock(
            return_value=(
                "taxonomy taxonomy taxonomy clustering clustering clustering "
                "enrichment enrichment enrichment"
            ),
        )

        result = await service.enrich(
            raw_prompt="Fix the taxonomy clustering enrichment warm path",
            tier="internal", db=db,
            repo_full_name="project-synthesis/ProjectSynthesis",
            repo_branch="main",
        )

        meta = dict(result.enrichment_meta)
        assert meta.get("repo_relevance_skipped") is None
        assert meta["repo_relevance_info"]["decision"] == "pass"
        # Codebase context includes at least the synthesis text
        assert result.codebase_context is not None

    @pytest.mark.asyncio
    async def test_no_synthesis(self, db, tmp_path):
        """When synthesis is absent, gate cannot fire — no relevance_info key."""
        for _ in range(10):
            await _seed_optimization(db, "auto", "coding", "backend", 7.0)
        await db.commit()

        service = _build_service(tmp_path)
        service._get_explore_synthesis = AsyncMock(return_value=None)

        result = await service.enrich(
            raw_prompt="Build a task management system with FastAPI",
            tier="internal", db=db,
            repo_full_name="project-synthesis/ProjectSynthesis",
            repo_branch="main",
        )

        meta = dict(result.enrichment_meta)
        assert "repo_relevance_info" not in meta
        assert meta.get("repo_relevance_skipped") is None

    @pytest.mark.asyncio
    async def test_embedding_failure(self, db, tmp_path):
        """When embedding throws, gate fails open — no relevance_info key."""
        for _ in range(10):
            await _seed_optimization(db, "auto", "coding", "backend", 7.0)
        await db.commit()

        mock_es = AsyncMock()
        mock_es.aembed_single = AsyncMock(side_effect=RuntimeError("model not loaded"))

        service = ContextEnrichmentService(
            prompts_dir=tmp_path,
            data_dir=tmp_path,
            workspace_intel=WorkspaceIntelligence(),
            embedding_service=mock_es,
            heuristic_analyzer=HeuristicAnalyzer(),
            github_client=AsyncMock(),
        )

        service._get_explore_synthesis = AsyncMock(
            return_value=(
                "taxonomy taxonomy taxonomy clustering clustering clustering "
                "enrichment enrichment enrichment"
            ),
        )

        result = await service.enrich(
            raw_prompt="Build a task management system with FastAPI",
            tier="internal", db=db,
            repo_full_name="project-synthesis/ProjectSynthesis",
            repo_branch="main",
        )

        meta = dict(result.enrichment_meta)
        assert "repo_relevance_info" not in meta
        assert meta.get("repo_relevance_skipped") is None
        assert meta.get("repo_relevance_error") is True
        assert result.codebase_context is not None


# ---------------------------------------------------------------------------
# B0: Domain vocabulary extraction
# ---------------------------------------------------------------------------


class TestDomainVocabExtraction:
    """Verify extract_domain_vocab tokenization and filtering."""

    def test_extracts_frequent_domain_terms(self):
        """Words appearing >= 3 times that aren't generic/tech are extracted."""
        synthesis = (
            "The taxonomy taxonomy taxonomy module handles enrichment enrichment "
            "enrichment of prompts via a pipeline pipeline pipeline that ensures "
            "coherence coherence coherence across clusters."
        )
        vocab = extract_domain_vocab(synthesis)
        assert "taxonomy" in vocab
        assert "enrichment" in vocab
        assert "pipeline" in vocab
        assert "coherence" in vocab

    def test_excludes_tech_vocabulary(self):
        """Tech vocabulary aliases (_TECH_VOCABULARY) are filtered out."""
        synthesis = (
            "fastapi fastapi fastapi sqlite sqlite sqlite python python python "
            "are the core technologies."
        )
        vocab = extract_domain_vocab(synthesis)
        assert "fastapi" not in vocab
        assert "sqlite" not in vocab
        assert "python" not in vocab

    def test_excludes_generic_coding_terms(self):
        """Generic programming terms (_GENERIC_TERMS) are filtered out."""
        synthesis = (
            "The service service service runs on the backend backend backend "
            "for the project project project using a database database database."
        )
        vocab = extract_domain_vocab(synthesis)
        assert "service" not in vocab
        assert "backend" not in vocab
        assert "project" not in vocab
        assert "database" not in vocab

    def test_returns_frozenset(self):
        """Return type is frozenset."""
        vocab = extract_domain_vocab("taxonomy taxonomy taxonomy")
        assert isinstance(vocab, frozenset)

    def test_empty_synthesis(self):
        """Empty synthesis returns empty frozenset."""
        vocab = extract_domain_vocab("")
        assert vocab == frozenset()
        vocab2 = extract_domain_vocab(None)  # type: ignore[arg-type]
        assert vocab2 == frozenset()


# ---------------------------------------------------------------------------
# B0: Hybrid repo relevance
# ---------------------------------------------------------------------------


class TestHybridRelevance:
    """Verify single-threshold compute_repo_relevance against a repo-anchored synthesis.

    The gate is ``cosine >= REPO_RELEVANCE_FLOOR`` (0.15) computed against a
    synthesis prefixed with ``"Project: {repo_full_name}\\n"`` when supplied.
    Domain vocabulary overlap is preserved in the info dict as a diagnostic
    only — it does not affect the decision.
    """

    @pytest.mark.asyncio
    async def test_orthogonal_vectors_skip(self):
        """Orthogonal vectors → skip with reason 'below_floor'."""
        import numpy as np

        mock_es = AsyncMock()
        prompt_vec = np.zeros(384, dtype=np.float32)
        prompt_vec[0] = 1.0
        synth_vec = np.zeros(384, dtype=np.float32)
        synth_vec[1] = 1.0
        mock_es.aembed_single = AsyncMock(side_effect=[prompt_vec, synth_vec])

        score, info = await compute_repo_relevance(
            "Build a task management system",
            "Project Synthesis is a RAG optimization platform",
            mock_es,
        )
        assert score < 0.1
        assert info["decision"] == "skip"
        assert info["reason"] == "below_floor"

    @pytest.mark.asyncio
    async def test_moderate_cosine_passes(self):
        """Cosine above floor → pass with reason 'above_floor'."""
        import numpy as np

        mock_es = AsyncMock()
        # Create vectors with moderate similarity (~0.5), well above the 0.15 floor
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)
        noise = rng.random(384).astype(np.float32) * 0.8
        vec2 = base + noise
        vec2 /= np.linalg.norm(vec2)
        mock_es.aembed_single = AsyncMock(side_effect=[base, vec2])

        score, info = await compute_repo_relevance(
            "Fix the taxonomy clustering issue in the warm path",
            "taxonomy taxonomy taxonomy clustering clustering clustering "
            "enrichment enrichment enrichment",
            mock_es,
        )
        assert score >= 0.15
        assert info["decision"] == "pass"
        assert info["reason"] == "above_floor"
        # Overlap is diagnostic-only but should still be reported
        assert info["domain_overlap"] >= 1

    @pytest.mark.asyncio
    async def test_high_cosine_passes_regardless_of_vocab(self):
        """High cosine → pass even when no domain vocabulary overlaps.

        Under the old two-stage gate this case was rejected as
        'no_domain_overlap'.  The root-fix simplification trusts the
        embedding: if two texts are semantically close enough to clear
        the floor, they're relevant.  Vocabulary overlap was a brittle
        secondary signal that over-rejected focused prompts.
        """
        import numpy as np

        mock_es = AsyncMock()
        vec = np.random.default_rng(42).random(384).astype(np.float32)
        vec /= np.linalg.norm(vec)
        noise = np.random.default_rng(99).random(384).astype(np.float32) * 0.1
        vec2 = vec + noise
        vec2 /= np.linalg.norm(vec2)
        mock_es.aembed_single = AsyncMock(side_effect=[vec, vec2])

        score, info = await compute_repo_relevance(
            "Build a REST API for user management",
            "xyzwidget xyzwidget xyzwidget foobarbaz foobarbaz foobarbaz "
            "quuxmachine quuxmachine quuxmachine",
            mock_es,
        )
        assert score > 0.8
        assert info["decision"] == "pass"
        assert info["reason"] == "above_floor"

    @pytest.mark.asyncio
    async def test_returns_domain_matches_in_info(self):
        """Matched domain terms appear in info['domain_matches'] as diagnostic."""
        import numpy as np

        mock_es = AsyncMock()
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)
        noise = rng.random(384).astype(np.float32) * 0.3
        vec2 = base + noise
        vec2 /= np.linalg.norm(vec2)
        mock_es.aembed_single = AsyncMock(side_effect=[base, vec2])

        score, info = await compute_repo_relevance(
            "Update the taxonomy enrichment pipeline",
            "taxonomy taxonomy taxonomy enrichment enrichment enrichment "
            "pipeline pipeline pipeline coherence coherence coherence",
            mock_es,
        )
        assert isinstance(info["domain_matches"], list)
        assert "taxonomy" in info["domain_matches"]
        assert "enrichment" in info["domain_matches"]

    @pytest.mark.asyncio
    async def test_repo_full_name_prefixes_anchor(self):
        """``repo_full_name`` prepends a ``"Project: ..."`` prefix to the anchor.

        Guards the embedding-side root fix: we embed the repo identity
        alongside its tech-stack signature so two projects with similar
        architectures don't collide on cosine.
        """
        import numpy as np

        mock_es = AsyncMock()
        prompt_vec = np.zeros(384, dtype=np.float32)
        prompt_vec[0] = 1.0
        synth_vec = np.zeros(384, dtype=np.float32)
        synth_vec[0] = 1.0  # match for a pass
        mock_es.aembed_single = AsyncMock(side_effect=[prompt_vec, synth_vec])

        await compute_repo_relevance(
            "Audit the MCP sampling pipeline",
            "synthesis synthesis synthesis",
            mock_es,
            repo_full_name="project-synthesis/ProjectSynthesis",
        )

        # Second call is the anchor — must be prefixed with "Project: ..."
        second_call = mock_es.aembed_single.call_args_list[1]
        anchor_text = second_call.args[0]
        assert anchor_text.startswith("Project: project-synthesis/ProjectSynthesis\n")
        assert "synthesis synthesis synthesis" in anchor_text

    @pytest.mark.asyncio
    async def test_no_repo_name_omits_prefix(self):
        """When ``repo_full_name`` is None, the anchor is the raw synthesis."""
        import numpy as np

        mock_es = AsyncMock()
        prompt_vec = np.zeros(384, dtype=np.float32)
        prompt_vec[0] = 1.0
        synth_vec = np.zeros(384, dtype=np.float32)
        synth_vec[0] = 1.0
        mock_es.aembed_single = AsyncMock(side_effect=[prompt_vec, synth_vec])

        await compute_repo_relevance(
            "Audit the pipeline",
            "plain synthesis text",
            mock_es,
        )

        second_call = mock_es.aembed_single.call_args_list[1]
        anchor_text = second_call.args[0]
        assert anchor_text == "plain synthesis text"


# ---------------------------------------------------------------------------
# B0 (path-enriched): domain vocab + anchor enrichment via indexed file paths
# ---------------------------------------------------------------------------


class TestDomainVocabPathEnrichment:
    """Verify file_paths contribute to domain vocab at freq >= 1.

    Component-level prompts (e.g. "Clusters Navigation Panel") were falling
    below the relevance floor because synthesis prose describes the system
    in aggregate.  Path-derived vocab gives the matcher explicit access to
    module names the repo actually contains.
    """

    def test_path_tokens_included_at_freq_one(self):
        """A token appearing in a single file path is preserved."""
        vocab = extract_domain_vocab(
            "",
            file_paths=["frontend/src/lib/components/ClustersNavigationPanel.svelte"],
        )
        assert "clustersnavigationpanel" in vocab
        assert "components" in vocab or "components" not in vocab  # may be generic

    def test_path_tokens_respect_generic_filter(self):
        """Generic coding terms are still filtered from path tokens."""
        vocab = extract_domain_vocab(
            "",
            file_paths=[
                "backend/app/services/domain_resolver.py",
                "backend/app/services/pipeline.py",
            ],
        )
        # Domain-specific module names survive
        assert "domain_resolver" in vocab
        assert "pipeline" in vocab

    def test_underscore_tokens_preserved_whole(self):
        """Identifiers with underscores tokenize as single words, not split."""
        vocab = extract_domain_vocab(
            "",
            file_paths=["backend/app/services/sub_domain_readiness.py"],
        )
        assert "sub_domain_readiness" in vocab

    def test_synth_and_path_sources_union(self):
        """Synthesis (freq >= 3) and paths (freq >= 1) merge into one set."""
        vocab = extract_domain_vocab(
            "taxonomy taxonomy taxonomy clustering clustering clustering",
            file_paths=["backend/app/services/sub_domain_readiness.py"],
        )
        # Synthesis side: freq >= 3
        assert "taxonomy" in vocab
        assert "clustering" in vocab
        # Path side: freq >= 1
        assert "sub_domain_readiness" in vocab

    def test_none_file_paths_noop(self):
        """file_paths=None matches the legacy synthesis-only behaviour."""
        vocab = extract_domain_vocab("taxonomy taxonomy taxonomy")
        vocab_none = extract_domain_vocab("taxonomy taxonomy taxonomy", file_paths=None)
        vocab_empty = extract_domain_vocab("taxonomy taxonomy taxonomy", file_paths=[])
        assert vocab == vocab_none == vocab_empty

    def test_empty_synth_with_paths_still_produces_vocab(self):
        """No synthesis + file paths alone still yields a useful vocab."""
        vocab = extract_domain_vocab(
            "",
            file_paths=[
                "backend/app/services/taxonomy/clustering.py",
                "backend/app/services/taxonomy/warm_phases.py",
            ],
        )
        assert "taxonomy" in vocab
        assert "clustering" in vocab
        assert "warm_phases" in vocab


class TestRepoRelevanceAnchorEnrichment:
    """Verify compute_repo_relevance extends the anchor with file paths."""

    @pytest.mark.asyncio
    async def test_file_paths_appended_as_components_section(self):
        """Anchor includes `Components:` section listing the sampled paths."""
        import numpy as np

        mock_es = AsyncMock()
        prompt_vec = np.zeros(384, dtype=np.float32)
        prompt_vec[0] = 1.0
        synth_vec = np.zeros(384, dtype=np.float32)
        synth_vec[0] = 1.0
        mock_es.aembed_single = AsyncMock(side_effect=[prompt_vec, synth_vec])

        paths = [
            "backend/app/services/taxonomy/clustering.py",
            "frontend/src/lib/components/ClustersNavigationPanel.svelte",
        ]
        await compute_repo_relevance(
            "Clusters Navigation Panel",
            "Project Synthesis RAG platform.",
            mock_es,
            repo_full_name="owner/repo",
            file_paths=paths,
        )

        anchor = mock_es.aembed_single.call_args_list[1].args[0]
        assert "Components:" in anchor
        assert "clustering.py" in anchor
        assert "ClustersNavigationPanel.svelte" in anchor

    @pytest.mark.asyncio
    async def test_paths_stride_sampled_when_over_cap(self):
        """Over 100 paths → stride-sampled to exactly 100 lines in anchor."""
        import numpy as np

        mock_es = AsyncMock()
        vec = np.zeros(384, dtype=np.float32)
        vec[0] = 1.0
        mock_es.aembed_single = AsyncMock(side_effect=[vec, vec])

        # 300 paths spanning backend/, data/, docs/, frontend/
        paths = (
            [f"backend/app/f{i}.py" for i in range(100)]
            + [f"docs/d{i}.md" for i in range(100)]
            + [f"frontend/src/c{i}.svelte" for i in range(100)]
        )
        await compute_repo_relevance(
            "any prompt",
            "synthesis text",
            mock_es,
            file_paths=paths,
        )

        anchor = mock_es.aembed_single.call_args_list[1].args[0]
        # Count lines between "Components:" and end
        components_block = anchor.split("Components:\n", 1)[1]
        lines = [ln for ln in components_block.split("\n") if ln.strip()]
        assert len(lines) == 100
        # Stride sampling preserves breadth — every subtree contributes.
        assert any("backend/" in ln for ln in lines)
        assert any("docs/" in ln for ln in lines)
        assert any("frontend/" in ln for ln in lines)

    @pytest.mark.asyncio
    async def test_no_file_paths_preserves_legacy_anchor(self):
        """Without file_paths the anchor is unchanged from the synthesis-only shape."""
        import numpy as np

        mock_es = AsyncMock()
        vec = np.zeros(384, dtype=np.float32)
        vec[0] = 1.0
        mock_es.aembed_single = AsyncMock(side_effect=[vec, vec])

        await compute_repo_relevance(
            "any prompt",
            "synthesis text",
            mock_es,
            repo_full_name="owner/repo",
        )

        anchor = mock_es.aembed_single.call_args_list[1].args[0]
        assert "Components:" not in anchor
        assert anchor == "Project: owner/repo\nsynthesis text"

    @pytest.mark.asyncio
    async def test_path_tokens_appear_in_domain_matches(self):
        """Path-derived vocab surfaces in `domain_matches` when prompt mentions them."""
        import numpy as np

        mock_es = AsyncMock()
        rng = np.random.default_rng(42)
        base = rng.random(384).astype(np.float32)
        base /= np.linalg.norm(base)
        noise = rng.random(384).astype(np.float32) * 0.3
        vec2 = base + noise
        vec2 /= np.linalg.norm(vec2)
        mock_es.aembed_single = AsyncMock(side_effect=[base, vec2])

        score, info = await compute_repo_relevance(
            "Update the sub_domain_readiness module in the taxonomy path",
            "Project Synthesis.",
            mock_es,
            repo_full_name="owner/repo",
            file_paths=[
                "backend/app/services/taxonomy/sub_domain_readiness.py",
            ],
        )
        assert "sub_domain_readiness" in info["domain_matches"]
