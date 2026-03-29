"""Shared test fixtures."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.providers.base import LLMProvider

# No custom event_loop fixture needed — pytest-asyncio manages it
# automatically with asyncio_mode = "auto" in pyproject.toml.


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory SQLite session for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Import models to register them
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def mock_provider():
    provider = AsyncMock(spec=LLMProvider)
    provider.name = "mock"
    # Streaming delegates to non-streaming (mirrors base class default),
    # so tests that set side_effect on complete_parsed work for both paths.
    async def _streaming_delegate(**kw):
        return await provider.complete_parsed(**kw)

    provider.complete_parsed_streaming.side_effect = _streaming_delegate
    return provider


@pytest_asyncio.fixture
async def app_client(mock_provider, db_session, tmp_path):
    import app.config as _cfg
    from app.config import PROMPTS_DIR
    from app.database import get_db
    from app.main import app
    from app.services.context_enrichment import ContextEnrichmentService
    from app.services.event_bus import EventBus
    from app.services.heuristic_analyzer import HeuristicAnalyzer
    from app.services.routing import RoutingManager
    from app.services.workspace_intelligence import WorkspaceIntelligence

    # Isolate DATA_DIR to tmp_path so tests never read the user's real
    # preferences (e.g. force_passthrough=true from a previous session).
    original_data_dir = _cfg.DATA_DIR
    _cfg.DATA_DIR = tmp_path

    # Create a test RoutingManager with mock provider
    test_routing = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
    test_routing.set_provider(mock_provider)
    app.state.routing = test_routing

    # Seed domain nodes and create a test DomainResolver
    from app.models import PromptCluster
    from app.services.domain_resolver import DomainResolver

    for domain_label in ("backend", "frontend", "database", "devops", "security", "fullstack", "general"):
        db_session.add(PromptCluster(
            label=domain_label,
            state="domain",
            domain=domain_label,
        ))
    await db_session.commit()

    domain_resolver = DomainResolver()
    await domain_resolver.load(db_session)
    app.state.domain_resolver = domain_resolver

    # Create a test ContextEnrichmentService
    app.state.context_service = ContextEnrichmentService(
        prompts_dir=PROMPTS_DIR,
        data_dir=tmp_path,
        workspace_intel=WorkspaceIntelligence(),
        embedding_service=None,
        heuristic_analyzer=HeuristicAnalyzer(),
        github_client=None,
    )

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    _cfg.DATA_DIR = original_data_dir
