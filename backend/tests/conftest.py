"""Shared test fixtures + helpers."""

import asyncio
import logging
import os
from collections.abc import AsyncGenerator, Generator
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.providers.base import LLMProvider

# v0.4.13 cycle 9.5: force the read-engine audit hook into RAISE mode
# for the entire test session. Any INSERT/UPDATE/DELETE that hits the
# read engine outside the ``migration_mode`` / ``cold_path_mode``
# allow-list now fails the test instead of silently warning. Must be
# set at module import time so it precedes ``app.config.settings``
# instantiation (Pydantic snapshots env at construction).
os.environ.setdefault("WRITE_QUEUE_AUDIT_HOOK_RAISE", "true")

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
    from app.dependencies.write_queue import get_write_queue
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

    # v0.4.13 cycle 8: install a synthetic write_queue that runs submit
    # callbacks against the same in-memory db_session so REST router tests
    # don't need a real WriteQueue worker. Cycle 9 lifespan installs the
    # real queue on app.state in production; tests use this stand-in to
    # exercise the same code path without spinning up a worker thread.
    class _TestWriteQueue:
        async def submit(self, work, *, timeout=None, operation_label=None):
            # Run the callback against db_session directly. The db_session
            # is the same session the routes read from, so commits are
            # immediately visible. Mirrors the production semantics where
            # the queue worker uses the writer engine but tests collapse
            # the read+write to one in-memory DB.
            return await work(db_session)

    test_write_queue = _TestWriteQueue()
    app.state.write_queue = test_write_queue
    app.dependency_overrides[get_write_queue] = lambda: test_write_queue

    # v0.4.14 cycle 3e follow-up: cycle-3 router migrations import
    # ``get_write_queue`` from ``app.tools._shared`` (canonical for MCP-process
    # tools). Backend tests exercise those router code paths via the same
    # ASGI app, so we must seed the module-level singleton in addition to the
    # FastAPI dependency override above. Without this, real handlers raise
    # ``ValueError: WriteQueue not initialized`` when calling
    # ``tools._shared.get_write_queue()``.
    from app.tools import _shared as _tools_shared
    _tools_shared.set_write_queue(test_write_queue)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    _tools_shared.set_write_queue(None)
    if hasattr(app.state, "write_queue"):
        del app.state.write_queue
    _cfg.DATA_DIR = original_data_dir


# WriteQueue fixtures (v0.4.13 — see docs/specs/sqlite-writer-queue-2026-05-02.md §9.10)


def _apply_writer_pragmas(engine):
    """Apply production PRAGMAs (journal_mode=WAL, busy_timeout, synchronous,
    cache_size, foreign_keys) to a test engine so contention tests exercise
    real WAL semantics rather than the SQLAlchemy-default rollback journaling.
    Mirrors ``backend/app/database.py:_set_writer_pragmas``.

    SQLite limitation: in-memory databases (including ``cache=shared`` URIs)
    silently report ``journal_mode=memory`` and reject WAL — the lock topology
    differs from production but no other journal mode is available for memory
    DBs. This helper is a no-op on the journal_mode line for in-memory
    engines; the other PRAGMAs (busy_timeout, foreign_keys, etc.) still apply.
    Use ``writer_engine_file`` for tests that depend on WAL contention
    semantics (e.g. ``test_bulk_persist_n5_concurrent_callers_serialize_via_queue``).
    """
    from sqlalchemy import event

    from app.config import settings

    @event.listens_for(engine.sync_engine, "connect")
    def _set_pragmas(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={settings.DB_LOCK_TIMEOUT_SECONDS * 1000}")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA cache_size={settings.DB_CACHE_SIZE_KB}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


@pytest_asyncio.fixture
async def writer_engine_inmem(tmp_path):
    """In-memory writer engine for unit tests (NOT for WAL semantics — use
    writer_engine_file for those).

    Uses ``StaticPool`` (the SQLAlchemy default for SQLite memory URLs), which
    is implicitly single-connection — so ``pool_size``/``max_overflow`` are
    not applicable here and SQLAlchemy raises ``TypeError`` if passed. The
    single-writer semantic is preserved by the pool topology itself.

    Production PRAGMAs (busy_timeout, synchronous, cache_size, foreign_keys)
    are applied via ``_apply_writer_pragmas``. ``journal_mode=WAL`` is
    requested but SQLite silently downgrades to ``journal_mode=memory`` on
    in-memory DBs — verified empirically. Tests that depend on real WAL
    writer contention must use ``writer_engine_file``.

    The schema (``Base.metadata.create_all``) is materialized so tests that
    submit ORM work to ``WriteQueue`` (e.g. v0.4.13 cycle 2's
    ``test_bulk_persist_routes_through_write_queue``) can insert into the
    canonical tables. Tests that don't touch ORM tables are unaffected — the
    extra DDL is one-shot and idempotent.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///file:memdb_writer_unit?mode=memory&cache=shared&uri=true",
    )
    _apply_writer_pragmas(engine)
    from app.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def writer_engine_file(tmp_path):
    """File-mode writer engine for WAL semantics tests.

    Production PRAGMAs (journal_mode=WAL, busy_timeout, synchronous,
    cache_size, foreign_keys) applied via ``_apply_writer_pragmas`` so
    contention tests (e.g. N=10 concurrent submits) exercise the same lock
    topology as production. Without WAL, file-mode SQLite would default to
    rollback journaling and the "no database is locked" assertion could pass
    for the wrong reason.
    """
    db_path = tmp_path / "writer_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        pool_size=1,
        max_overflow=0,
    )
    _apply_writer_pragmas(engine)
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


# Shared TaxonomyEngine reset fixture (v0.4.13 — see docs/specs/sqlite-writer-queue-2026-05-02.md)
#
# Promoted from the cycle 3/4/5 OPERATE-class autouse copies. Sync ``@pytest.fixture``
# (no autouse) so consumers opt-in via class-level
# ``pytestmark = pytest.mark.usefixtures("reset_taxonomy_engine")`` — keeps the
# autouse semantic at class scope without slowing every unrelated test in the
# broader suite.

import pytest  # noqa: E402  (intentional: kept after pytest_asyncio imports)


@pytest.fixture
def reset_taxonomy_engine():
    """Reset the singleton TaxonomyEngine before/after each test that needs
    a fresh engine (e.g., cycle 3+ taxonomy/persist tests).

    Used by tests that touch get_engine() under concurrent load — without
    reset, prior tests' state leaks into the next test's engine instance.
    """
    from app.services.taxonomy import reset_engine
    reset_engine()
    yield
    reset_engine()


# ============================================================
# Foundation P3 fixtures (added 2026-05-06)
# ============================================================
#
# These fixtures support Cycles 4, 6, 7, 9, 11, 12 of the Foundation P3
# substrate-unification plan. They isolate per-test side effects on the
# global event bus, taxonomy event logger, and Sonnet provider so the
# RunOrchestrator/RunGenerator integration tests can assert on observable
# state (warnings, events, decisions) without bleeding across tests.

@dataclass
class _AuditHookCapture:
    warnings: list = field(default_factory=list)
    _caplog: Any = None

    def reset(self) -> None:
        self.warnings.clear()

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def populate_from_caplog(self) -> None:
        """Refresh warnings from the underlying caplog records."""
        if self._caplog is None:
            return
        self.warnings = [
            str(r.message) for r in self._caplog.records
            if "audit" in r.name.lower() or "[AUDIT-HOOK]" in str(r.message)
        ]


@pytest.fixture
def audit_hook(caplog) -> Generator[_AuditHookCapture, None, None]:
    """Captures audit-hook WARN records from logger output.

    The real audit hook (database.py event listener for direct read-engine writes)
    emits via ``logging.warning("[AUDIT-HOOK] direct write detected: ...")``. This
    fixture lets tests assert no such warnings fired during a code path. Tests
    call ``audit_hook.populate_from_caplog()`` then check ``audit_hook.warnings``.
    """
    cap = _AuditHookCapture(_caplog=caplog)
    caplog.set_level(logging.WARNING)
    yield cap
    cap.populate_from_caplog()


@dataclass
class _BusEvent:
    kind: str
    payload: dict


@dataclass
class _EventBusCapture:
    events: list[_BusEvent] = field(default_factory=list)

    def events_for_run(self, run_id: str) -> list[_BusEvent]:
        return [e for e in self.events if e.payload.get("run_id") == run_id]


@pytest_asyncio.fixture
async def event_bus_capture(monkeypatch) -> AsyncGenerator[_EventBusCapture, None]:
    """Captures every event published to event_bus during the test.
    Hooks publish() directly, parallel to existing subscribers."""
    from app.services.event_bus import event_bus
    cap = _EventBusCapture()
    real_publish = event_bus.publish

    def _wrapped(event_type, data):
        cap.events.append(_BusEvent(
            kind=event_type, payload=data if isinstance(data, dict) else {},
        ))
        return real_publish(event_type, data)

    monkeypatch.setattr(event_bus, "publish", _wrapped)
    yield cap


@dataclass
class _TaxDecision:
    path: str
    op: str
    decision: str
    context: dict


@dataclass
class _TaxonomyEventCapture:
    decisions: list[_TaxDecision] = field(default_factory=list)

    def decisions_with_op(self, op: str) -> list[_TaxDecision]:
        return [d for d in self.decisions if d.op == op]


@pytest.fixture
def taxonomy_event_capture(monkeypatch) -> Generator[_TaxonomyEventCapture, None, None]:
    """Captures every taxonomy_event_logger.log_decision call."""
    from app.services.taxonomy import event_logger as el_mod
    cap = _TaxonomyEventCapture()
    real_logger_class = el_mod.TaxonomyEventLogger
    real_log = real_logger_class.log_decision

    def _wrapped(self, path, op, decision, context):
        cap.decisions.append(_TaxDecision(
            path=path, op=op, decision=decision, context=context,
        ))
        return real_log(self, path, op, decision, context)

    monkeypatch.setattr(real_logger_class, "log_decision", _wrapped)
    yield cap


@pytest.fixture
def provider_mock() -> Any:
    """Default Sonnet provider mock returning a 'completed' response."""
    p = AsyncMock()
    p.complete_parsed.return_value = AsyncMock(
        result_text="optimized prompt",
        model="claude-sonnet-4-6",
    )
    return p


@pytest.fixture
def provider_partial_mock() -> Any:
    """Simulates 1 success + 1 failure across N prompts."""
    p = AsyncMock()
    counter = {"n": 0}

    async def _call(*args, **kwargs):
        counter["n"] += 1
        if counter["n"] % 2 == 0:
            raise RuntimeError("partial failure simulation")
        return AsyncMock(result_text="ok", model="claude-sonnet-4-6")

    p.complete_parsed = _call
    return p


@pytest.fixture
def provider_all_fail_mock() -> Any:
    p = AsyncMock()
    p.complete_parsed.side_effect = RuntimeError("all fail simulation")
    return p


@pytest.fixture
def provider_429_then_ok_mock() -> Any:
    """First call raises 429, subsequent calls succeed."""
    p = AsyncMock()
    counter = {"n": 0}

    async def _call(*args, **kwargs):
        counter["n"] += 1
        if counter["n"] == 1:
            err = RuntimeError("HTTP 429: rate limited")
            raise err
        return AsyncMock(result_text="ok", model="claude-sonnet-4-6")

    p.complete_parsed = _call
    return p


@pytest.fixture
def provider_hanging_mock() -> Any:
    """Provider that never returns — used for cancellation tests."""
    p = AsyncMock()

    async def _hang(*args, **kwargs):
        await asyncio.sleep(60)

    p.complete_parsed = _hang
    return p


@pytest.fixture
def seed_orchestrator_mock() -> Any:
    """Mock SeedOrchestrator returning a successful generation."""
    orch = MagicMock()
    gen_result = MagicMock()
    gen_result.prompts = ["prompt 1", "prompt 2", "prompt 3"]
    orch.generate = AsyncMock(return_value=gen_result)
    return orch


@pytest.fixture
def seed_orchestrator_failing_mock() -> Any:
    orch = MagicMock()
    orch.generate = AsyncMock(side_effect=RuntimeError("generation failed"))
    return orch


@pytest.fixture
def repo_index_mock() -> Any:
    rix = MagicMock()
    rix.query_curated_context = AsyncMock(return_value=MagicMock(
        relevant_files=[], explore_synthesis_excerpt="", known_domains=[],
    ))
    return rix


@pytest.fixture
def taxonomy_mock() -> Any:
    return MagicMock()


@pytest_asyncio.fixture
async def mcp_test_client():
    """Real MCP client connected to the in-process MCP server.

    Uses ``fastmcp.Client`` for actual MCP SDK round-trip — exercises the same
    schema-validation path Claude Code + VSCode bridge use, NOT the FastAPI
    test client. Required for spec § 11 risk: MCP SDK strict-validation of
    additive run_id field.
    """
    from fastmcp import Client

    from app.mcp_server import mcp
    async with Client(mcp) as client:
        yield client
