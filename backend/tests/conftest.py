"""Shared test fixtures + helpers."""

import asyncio
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.providers.base import LLMProvider

# No custom event_loop fixture needed — pytest-asyncio manages it
# automatically with asyncio_mode = "auto" in pyproject.toml.


def drain_events_nonblocking(queue: asyncio.Queue) -> list[dict]:
    """Drain all events currently in an ``asyncio.Queue`` without awaiting.

    ``event_bus.publish`` is sync even though subscribers are async — events
    are already in the queue by the time a sync-style publisher returns, so
    pulling with ``get_nowait()`` until ``QueueEmpty`` is the deterministic
    way to collect everything that was emitted during a unit-test arrangement.
    Callers filter by ``event`` type themselves at the call site.

    Shared helper used by ``test_bulk_delete_router.py`` and
    ``test_optimization_service_delete.py`` (both subscribe queues directly
    to ``event_bus._subscribers`` for deterministic registration — see the
    comment chain in the ``event_bus.subscribe()`` definition for why the
    public async-generator API would race under test timing).
    """
    events: list[dict] = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create an in-memory SQLite session for testing.

    Note: this fixture intentionally does NOT apply the production PRAGMA
    hook — many existing tests insert orphan FK rows (e.g. cluster_id
    references to never-created clusters) for unit-test isolation, and
    enabling FK enforcement globally would require a coordinated cleanup
    well outside any single refactor's scope. Tests that need
    FK-enforcement assertions opt in via the
    ``enable_sqlite_foreign_keys`` fixture (see below) — single source of
    truth replacing five inline ``PRAGMA foreign_keys=ON`` calls.
    """
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
async def enable_sqlite_foreign_keys(db_session: AsyncSession) -> AsyncSession:
    """Enable SQLite FK enforcement on ``db_session`` for the current test.

    Replaces the inline ``await db_session.execute(text("PRAGMA foreign_keys=ON"))``
    incantation that previously lived in five delete-related tests + the
    cycle 2 ProbeRun FK test. Production ``app/database.py`` applies this
    PRAGMA via an event hook on every pool checkout; the conftest engine
    omits it because many existing tests insert orphan FK rows (see
    ``db_session`` docstring).

    Usage::

        async def test_fk_constraint(enable_sqlite_foreign_keys):
            db = enable_sqlite_foreign_keys  # is the same db_session
            ...

    Returns the same ``db_session`` instance for ergonomic single-arg use.
    """
    from sqlalchemy import text

    await db_session.execute(text("PRAGMA foreign_keys=ON"))
    return db_session


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

    for domain_label in ("backend", "frontend", "database", "data", "devops", "security", "fullstack", "general"):
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


# WriteQueue fixtures (v0.4.13 — see docs/specs/sqlite-writer-queue-2026-05-02.md §9.10)


@pytest_asyncio.fixture
async def writer_engine_inmem(tmp_path):
    """In-memory writer engine for unit tests (NOT for WAL semantics — use
    writer_engine_file for those).

    Uses ``StaticPool`` (the SQLAlchemy default for SQLite memory URLs), which
    is implicitly single-connection — so ``pool_size``/``max_overflow`` are
    not applicable here and SQLAlchemy raises ``TypeError`` if passed. The
    single-writer semantic is preserved by the pool topology itself.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:memdb_writer_unit?mode=memory&cache=shared&uri=true",
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def writer_engine_file(tmp_path):
    """File-mode writer engine for WAL semantics tests."""
    db_path = tmp_path / "writer_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def write_queue_inmem(writer_engine_inmem):
    """Started write queue, no audit hook."""
    from app.services.write_queue import WriteQueue
    queue = WriteQueue(writer_engine_inmem)
    await queue.start()
    try:
        yield queue
    finally:
        await queue.stop(drain_timeout=2.0)
