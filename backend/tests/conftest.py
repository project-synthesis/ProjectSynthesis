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
    return provider


@pytest_asyncio.fixture
async def app_client(mock_provider, db_session, tmp_path):
    from app.database import get_db
    from app.main import app
    from app.services.event_bus import EventBus
    from app.services.routing import RoutingManager

    # Create a test RoutingManager with mock provider
    test_routing = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
    test_routing.set_provider(mock_provider)
    app.state.routing = test_routing

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
