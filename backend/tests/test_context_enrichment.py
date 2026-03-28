"""Tests for ContextEnrichmentService."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from app.services.context_enrichment import ContextEnrichmentService, EnrichedContext
from app.services.domain_signal_loader import DomainSignalLoader
from app.services.heuristic_analyzer import set_signal_loader

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
        # Adaptation state is resolved (may be None if no data, but key exists)
        assert "adaptation" in result.context_sources


class TestEnrichInternal:
    @pytest.mark.asyncio
    async def test_internal_skips_heuristic_analysis(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        assert result.analysis is None
        assert result.context_sources["heuristic_analysis"] is False

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


class TestEnrichWorkspaceGuidance:
    @pytest.mark.asyncio
    async def test_workspace_path_resolves_guidance(self, db, tmp_path):
        # Create a workspace with CLAUDE.md
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        (workspace / "CLAUDE.md").write_text("# Project Guidance\nUse async everywhere.")

        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="passthrough", db=db,
            workspace_path=str(workspace),
        )
        assert result.workspace_guidance is not None
        assert "async everywhere" in result.workspace_guidance

    @pytest.mark.asyncio
    async def test_no_workspace_path_returns_none_guidance(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint for user login",
            tier="internal", db=db,
        )
        assert result.workspace_guidance is None
        assert result.context_sources["workspace_guidance"] is False


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
            "workspace_guidance", "codebase_context", "adaptation",
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
    async def test_sampling_tier_skips_heuristic(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="sampling", db=db,
        )
        assert result.analysis is None
        assert result.context_sources["heuristic_analysis"] is False

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
    async def test_disable_adaptation_via_preferences(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="passthrough", db=db,
            preferences_snapshot={"enable_adaptation": False},
        )
        assert result.adaptation_state is None
        assert result.context_sources["adaptation"] is False

    @pytest.mark.asyncio
    async def test_adaptation_enabled_by_default(self, db, tmp_path):
        service = _build_service(tmp_path)
        result = await service.enrich(
            raw_prompt="Implement a REST API endpoint",
            tier="passthrough", db=db,
        )
        # Adaptation key exists — may be None (no data) but was attempted
        assert "adaptation" in result.context_sources


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
        assert enrichment.analysis.domain in ("backend", "fullstack", "backend: security")
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
