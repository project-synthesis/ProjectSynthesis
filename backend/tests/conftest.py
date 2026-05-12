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

# v0.4.22 T2 Cycle 8: relax the ``POST /api/probes`` per-IP rate limit
# for the test session so the race-stress test in
# ``test_probes_202_polling.py`` (50 dispatches per test) doesn't hit
# the production 5/minute budget mid-iteration. The router's
# ``reset_rate_limit_storage()`` autouse fixture handles inter-test
# isolation; this env var addresses intra-test budget exhaustion. No
# probe-specific rate-limit assertion tests exist in the suite, so the
# bump is observable only as a positive change in the 202+polling
# stress test. Must be set before ``app.config.settings`` instantiates.
os.environ.setdefault("PROBE_RATE_LIMIT", "10000/minute")

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

    # v0.4.22 T2 Cycle 5: services that route their reads through
    # ``app.database.async_session_factory()`` (e.g. ``ValidationSuiteService``
    # per the Foundation P4 detached-ORM-safe contract) need a context that
    # yields ``db_session`` so router-level tests see the seeded test data.
    #
    # Rationale for placing the patch on the fixture rather than inline in
    # ``test_routers_suites.py``: ``app_client`` is the canonical test-time
    # ASGI seam (everything that touches the FastAPI app goes through it).
    # Any router that delegates to a service which in turn opens short read
    # sessions via ``async_session_factory()`` — and that's the entire
    # Foundation P4 contract — needs identical wiring. Lifting the patch
    # here means a router test file never has to re-derive the seam; future
    # router tests get it for free. The pattern mirrors the inline
    # monkey-patch used by ``tests/test_routers_suites.py``'s
    # ``_seed_suite_via_service`` helper (and the canonical inline form in
    # ``tests/test_validation_suite_service.py``); both retain their inline
    # patches because they predate this fixture-level lift and the helper-
    # local scope is narrow enough not to need the fixture wiring.
    #
    # The prior factory is snapshotted and restored on teardown so non-
    # ``app_client`` tests (and any unrelated test ordering) cannot see a
    # patched factory across fixture boundaries.
    import app.database as _database_mod

    class _DbSessionContext:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    _prior_async_session_factory = _database_mod.async_session_factory
    _database_mod.async_session_factory = lambda: _DbSessionContext()  # type: ignore[assignment]

    # v0.4.13 cycle 8: install a synthetic write_queue that runs submit
    # callbacks against the same in-memory db_session so REST router tests
    # don't need a real WriteQueue worker. Cycle 9 lifespan installs the
    # real queue on app.state in production; tests use this stand-in to
    # exercise the same code path without spinning up a worker thread.
    #
    # v0.4.22 T2 Cycle 8: serialize concurrent submit() calls under a
    # single ``asyncio.Lock`` so the 202+polling race-stress (Test 3 of
    # ``test_probes_202_polling.py``) doesn't crash on
    # ``IllegalStateChangeError`` when two background tasks try to
    # ``commit()`` the shared ``db_session`` concurrently. Production
    # ``WriteQueue`` already serializes through its single-writer task +
    # ``pool_size=1`` writer engine; this lock mirrors that semantics in
    # the test stand-in. Same-task reentrancy still works (asyncio.Lock
    # is not reentrant, but no work_fn re-enters submit recursively in
    # the routes under test).
    class _TestWriteQueue:
        def __init__(self) -> None:
            self._lock = asyncio.Lock()

        async def submit(self, work, *, timeout=None, operation_label=None):
            # Serialize against the shared session so concurrent dispatchers
            # (e.g., the spawned ``_run_to_completion`` tasks from
            # ``RunOrchestrator.dispatch_async``) cannot interleave commits.
            async with self._lock:
                return await work(db_session)

    test_write_queue = _TestWriteQueue()
    app.state.write_queue = test_write_queue
    app.dependency_overrides[get_write_queue] = lambda: test_write_queue

    # v0.4.22 T2 Cycle 9: install a ValidationSuiteService singleton on the
    # test app.state so ``/api/health`` resolves the regression-alarm block
    # against a service whose 30s TTL cache + prior-state map persist
    # across requests within a single test. Production lifespan registers
    # the same singleton; this mirrors that contract for the ASGI seam.
    from app.services.validation_suite_service import ValidationSuiteService
    app.state.validation_suite_service = ValidationSuiteService()

    # v0.4.14 cycle 3e follow-up: cycle-3 router migrations import
    # ``get_write_queue`` from ``app.tools._shared`` (canonical for MCP-process
    # tools). Backend tests exercise those router code paths via the same
    # ASGI app, so we must seed the module-level singleton in addition to the
    # FastAPI dependency override above. Without this, real handlers raise
    # ``ValueError: WriteQueue not initialized`` when calling
    # ``tools._shared.get_write_queue()``.
    #
    # P4-integration-review (2026-05-10): snapshot the prior value before
    # mutating so the teardown restores it instead of clobbering it to None.
    # The pre-fix teardown set ``_write_queue = None`` unconditionally, which
    # leaked across the test session and broke any test executed AFTER an
    # ``app_client``-using test (the singleton stayed None, making
    # ``get_write_queue()`` raise ``WriteQueue not initialized``).
    #
    # P4-integration-review (2026-05-10) also seeds ``_shared._routing`` so
    # Cycle 2's restructured ``handle_refine`` (which dropped ``app.state.routing``
    # in favor of ``get_routing()``) resolves the test routing manager. Same
    # snapshot/restore pattern.
    from app.tools import _shared as _tools_shared
    _prior_write_queue = _tools_shared._write_queue
    _prior_routing = _tools_shared._routing
    _tools_shared.set_write_queue(test_write_queue)
    _tools_shared.set_routing(test_routing)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    # v0.4.22 T2 Cycle 8: drain any in-flight ``run-to-completion-*``
    # background tasks before tearing down the shared ``db_session``.
    # The 202+polling dispatch path (``RunOrchestrator.dispatch_async``)
    # spawns a task that outlives the HTTP response, so under the
    # synthetic test ``WriteQueue`` (which writes to ``db_session``)
    # that task's ``_persist_final`` can land AFTER the test ends —
    # which then races the session-close path and surfaces as a
    # teardown ``ResourceClosedError``. Cancelling pending spawn tasks
    # is the canonical cleanup: ``_run_to_completion``'s
    # ``CancelledError`` branch flips the row to ``status='failed'``
    # under ``asyncio.shield``, so the row state remains coherent for
    # any test that polls it post-teardown.
    pending_spawns = [
        t for t in asyncio.all_tasks()
        if (t.get_name() or "").startswith("run-to-completion-")
        and not t.done()
    ]
    for t in pending_spawns:
        t.cancel()
    if pending_spawns:
        # Wait briefly for cancellation to propagate; the shielded
        # ``_mark_failed`` write must reach the writer before the
        # session closes. ``return_exceptions=True`` so any
        # ``CancelledError`` is collected rather than propagated.
        await asyncio.gather(*pending_spawns, return_exceptions=True)

    app.dependency_overrides.clear()
    _tools_shared.set_write_queue(_prior_write_queue)
    _tools_shared.set_routing(_prior_routing)
    if hasattr(app.state, "write_queue"):
        del app.state.write_queue
    if hasattr(app.state, "validation_suite_service"):
        del app.state.validation_suite_service
    _database_mod.async_session_factory = _prior_async_session_factory  # type: ignore[assignment]
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


@pytest.fixture(autouse=True)
def _cleanup_stopped_write_queue_singleton():
    """Autouse cleanup: drop the ``_shared._write_queue`` singleton if the
    previous test left a stopped ``WriteQueue`` behind.

    Background (P4-integration-review 2026-05-10): ``test_main.py``'s
    lifespan tests run the real production lifespan, which calls
    ``set_write_queue(queue)`` on startup and ``set_write_queue(None)`` is
    NOT in the shutdown path (the lifespan only clears
    ``app.state.write_queue`` + ``register_process_write_queue(None)`` —
    the ``_shared`` singleton is left dangling with a stopped queue).

    The previous fix (snapshot/restore in ``app_client``) cured the prior
    "WriteQueue not initialized" failure but uncovered this second leak:
    tests after ``test_lifespan_startup_and_shutdown`` would find a
    stopped queue in the singleton and raise ``WriteQueueStoppedError``
    when calling ``.submit()``.

    Cleanest pure-test fix: a function-scoped autouse fixture that runs
    BEFORE every test (yield comes after setup), checks whether the
    singleton is a stopped real ``WriteQueue``, and clears it so the test
    starts from a known clean state. Tests that need a working queue
    install their own via ``app_client`` / explicit patches.
    """
    from app.tools import _shared as _ts
    wq = _ts._write_queue
    if wq is not None:
        # Detect stopped real WriteQueue. The synthetic test queue from
        # ``app_client`` is a ``_TestWriteQueue`` class instance — it has
        # no ``_stopping`` / ``_dead`` attrs. Real WriteQueue instances
        # use ``_stopping: bool`` (set in ``stop()``). Use getattr with
        # default so the synthetic queue passes through untouched.
        is_stopped = (
            getattr(wq, "_stopping", False)
            or getattr(wq, "_dead", False)
        )
        if is_stopped:
            _ts.set_write_queue(None)
    yield


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

    def _wrapped(self, *, path, op, decision, context=None, **kwargs):
        # ``log_decision`` is keyword-only on the real method
        # (event_logger.py:130). Mirror that signature here so ordering
        # interactions across test files (e.g., a prior test that
        # initialised the real TaxonomyEventLogger via the MCP lifespan)
        # don't break the fixture's wrap. Forward any optional fields
        # (cluster_id, optimization_id, duration_ms) as kwargs to the
        # real method.
        cap.decisions.append(_TaxDecision(
            path=path, op=op, decision=decision, context=context or {},
        ))
        return real_log(
            self, path=path, op=op, decision=decision,
            context=context, **kwargs,
        )

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


@pytest_asyncio.fixture
async def async_session_factory_override(tmp_path, monkeypatch):
    """In-memory SQLite session factory for Foundation P4 integration tests.

    Wires both the read engine and the writer engine to the same temp-file
    SQLite DB so persists made through the WriteQueue are visible on
    subsequent SELECTs through the read engine.

    Patches every import-time rebound name of `async_session_factory`
    (top-level `from app.database import async_session_factory` in each
    consumer module binds a SEPARATE module-level reference, so patching
    only `app.database.async_session_factory` does NOT redirect those
    consumers). Foundation P4 consumers: `app.tools.save_result`,
    `app.tools._shared`. Cycles 2/3 add more — extend this fixture's
    monkeypatch list when those land.
    """
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.models import Base
    from app.services.write_queue import WriteQueue

    shared_url = f"sqlite+aiosqlite:///{tmp_path}/shared.db"
    test_engine = create_async_engine(shared_url, future=True)

    # Create schema
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False,
    )

    # Patch every consumer's locally rebound `async_session_factory`.
    monkeypatch.setattr("app.database.async_session_factory", factory)
    monkeypatch.setattr("app.tools.refine.async_session_factory", factory)
    monkeypatch.setattr("app.tools.save_result.async_session_factory", factory)
    monkeypatch.setattr("app.tools._shared.async_session_factory", factory)
    monkeypatch.setattr("app.tools.optimize.async_session_factory", factory)

    # DEFENSIVE: patch `writer_engine` + `writer_session_factory` even though
    # the fixture's own `WriteQueue(test_engine)` builds its internal
    # sessionmaker against `test_engine` directly (not via these names).
    # Reason for defense: lifecycle tasks (`gc.py`, `_recurring_gc_task`,
    # ad-hoc writer-engine access patterns) may read these names directly
    # during test runs; if they do, they must hit `test_engine`, not the
    # production writer. Cycles 2/3 may also add direct readers — keep
    # these patches here, not removed.
    monkeypatch.setattr("app.database.writer_engine", test_engine)
    monkeypatch.setattr(
        "app.database.writer_session_factory",
        async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False),
    )

    # Start a WriteQueue against the shared engine
    test_wq = WriteQueue(test_engine)
    await test_wq.start()
    monkeypatch.setattr(
        "app.tools._shared.get_write_queue",
        lambda: test_wq,
    )
    # Foundation P4 Cycle 1 GREEN: `app.tools.save_result` now imports
    # `get_write_queue` from `_shared` at module load, so patching ONLY
    # `_shared.get_write_queue` doesn't reach the rebound name in
    # `save_result`. Patch the save_result-module-level binding too.
    # Cycles 2/3: extend with their respective consumers when they land.
    monkeypatch.setattr(
        "app.tools.save_result.get_write_queue",
        lambda: test_wq,
    )
    # Foundation P4 Cycle 3 review-round-2: `app.tools.optimize` imports
    # `get_write_queue` from `_shared` at module load (same pattern as
    # save_result). Patch the optimize-module-level binding so integration
    # tests driving `handle_optimize` end-to-end see the test queue.
    monkeypatch.setattr(
        "app.tools.optimize.get_write_queue",
        lambda: test_wq,
    )

    yield factory

    await test_wq.stop()
    await test_engine.dispose()
