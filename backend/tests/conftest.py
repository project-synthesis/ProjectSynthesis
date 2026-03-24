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
    from app.config import DATA_DIR, PROMPTS_DIR
    from app.database import get_db
    from app.main import app
    from app.services.context_enrichment import ContextEnrichmentService
    from app.services.event_bus import EventBus
    from app.services.heuristic_analyzer import HeuristicAnalyzer
    from app.services.routing import RoutingManager
    from app.services.workspace_intelligence import WorkspaceIntelligence

    # Create a test RoutingManager with mock provider
    test_routing = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
    test_routing.set_provider(mock_provider)
    app.state.routing = test_routing

    # Create a test ContextEnrichmentService
    app.state.context_service = ContextEnrichmentService(
        prompts_dir=PROMPTS_DIR,
        data_dir=DATA_DIR,
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
