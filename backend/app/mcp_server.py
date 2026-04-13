"""Standalone MCP server — thin registration layer.

Tool implementations live in ``app.tools.*`` modules.  This file owns:
- FastMCP instance creation and tool registration
- ASGI middleware for capability detection
- Lifespan (routing / taxonomy init, state wiring)
- Monkey-patch for session-less SSE reconnection
- Entry point

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

import aiosqlite
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from app.config import DATA_DIR, PROMPTS_DIR
from app.providers.detector import detect_provider
from app.schemas.mcp_models import (
    AnalyzeOutput,
    ExplainResult,
    FeedbackOutput,
    HealthOutput,
    HistoryOutput,
    MatchOutput,
    OptimizationDetailOutput,
    OptimizeOutput,
    PrepareOutput,
    RefineOutput,
    SaveResultOutput,
    StrategiesOutput,
)
from app.schemas.seed import SeedOutput
from app.services.event_notification import notify_event_bus
from app.services.mcp_session_file import MCPSessionFile
from app.services.routing import RoutingManager
from app.tools import _shared

logger = logging.getLogger(__name__)

# Module-level session file helper — used by middleware and entry point.
_session_file = MCPSessionFile(DATA_DIR)

# ---------------------------------------------------------------------------
# Monkey-patch: allow session-less GETs for SSE reconnection after restart.
# ---------------------------------------------------------------------------
try:
    from mcp.server.streamable_http import StreamableHTTPServerTransport

    _orig_validate_session = StreamableHTTPServerTransport._validate_session

    async def _patched_validate_session(self, request, send):  # type: ignore[override]
        """Accept GET requests without session ID (SSE reconnection)."""
        from starlette.requests import Request as _Req

        if isinstance(request, _Req) and request.method == "GET":
            session_id = self._get_session_id(request)
            if not session_id and self.mcp_session_id:
                return True
        return await _orig_validate_session(self, request, send)

    StreamableHTTPServerTransport._validate_session = _patched_validate_session  # type: ignore[assignment]
    logger.debug("Patched StreamableHTTPServerTransport._validate_session for SSE reconnection")
except Exception:
    logger.warning("Could not patch StreamableHTTPServerTransport — SSE reconnection may not work", exc_info=True)

# ---------------------------------------------------------------------------
# Monkey-patch: release session creation lock before handling SSE streams.
#
# MCP SDK bug: StreamableHTTPSessionManager._handle_stateful_request holds
# _session_creation_lock while calling handle_request().  For GET/SSE streams
# handle_request() never returns, so the lock is held for the stream lifetime
# and all subsequent session creation deadlocks.
#
# Fix: create the transport under lock, then handle the request outside it.
# ---------------------------------------------------------------------------
try:
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    _orig_handle_stateful = StreamableHTTPSessionManager._handle_stateful_request

    async def _patched_handle_stateful(self, scope, receive, send):  # type: ignore[override]
        """Release _session_creation_lock before handle_request to prevent SSE deadlock."""
        from http import HTTPStatus
        from uuid import uuid4

        import anyio
        from mcp.server.streamable_http import StreamableHTTPServerTransport as _Transport
        from mcp.types import INVALID_REQUEST, ErrorData, JSONRPCError
        from starlette.requests import Request as _Req
        from starlette.responses import Response as _Resp

        if self._task_group is None:
            raise RuntimeError("Task group is not initialized. Make sure to use run().")

        request = _Req(scope, receive)
        request_mcp_session_id = request.headers.get("mcp-session-id")

        # Existing session — handle directly (no lock needed)
        if (
            request_mcp_session_id is not None
            and request_mcp_session_id in self._server_instances
        ):
            transport = self._server_instances[request_mcp_session_id]
            await transport.handle_request(scope, receive, send)
            return

        # New session — create under lock, handle OUTSIDE lock
        if request_mcp_session_id is None:
            http_transport: _Transport | None = None
            async with self._session_creation_lock:
                new_session_id = uuid4().hex
                http_transport = _Transport(
                    mcp_session_id=new_session_id,
                    is_json_response_enabled=self.json_response,
                    event_store=self.event_store,
                    security_settings=self.security_settings,
                    retry_interval=self.retry_interval,
                )
                assert http_transport.mcp_session_id is not None
                self._server_instances[http_transport.mcp_session_id] = http_transport
                _instances = self._server_instances  # closure ref

                async def run_server(
                    *, task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
                ) -> None:
                    async with http_transport.connect() as streams:  # type: ignore[union-attr]
                        read_stream, write_stream = streams
                        task_status.started()
                        try:
                            await self.app.run(
                                read_stream,
                                write_stream,
                                self.app.create_initialization_options(),
                                stateless=False,
                            )
                        except Exception as exc:
                            logger.error(
                                "Session %s crashed: %s",
                                http_transport.mcp_session_id,  # type: ignore[union-attr]
                                exc,
                                exc_info=True,
                            )
                        finally:
                            sid = http_transport.mcp_session_id  # type: ignore[union-attr]
                            if (
                                sid
                                and sid in _instances
                                and not http_transport.is_terminated  # type: ignore[union-attr]
                            ):
                                logger.info("Cleaning up crashed session %s", sid)
                                del _instances[sid]

                assert self._task_group is not None
                await self._task_group.start(run_server)

            # ── Handle request OUTSIDE the lock ─────────────────────────
            # SSE GET streams block here for the connection lifetime;
            # releasing the lock first lets other sessions be created.
            await http_transport.handle_request(scope, receive, send)
            return

        # Unknown / expired session ID — 404
        error_response = JSONRPCError(
            jsonrpc="2.0",
            id="server-error",
            error=ErrorData(code=INVALID_REQUEST, message="Session not found"),
        )
        response = _Resp(
            content=error_response.model_dump_json(by_alias=True, exclude_none=True),
            status_code=HTTPStatus.NOT_FOUND,
            media_type="application/json",
        )
        await response(scope, receive, send)

    StreamableHTTPSessionManager._handle_stateful_request = _patched_handle_stateful  # type: ignore[assignment]
    logger.debug("Patched StreamableHTTPSessionManager._handle_stateful_request — SSE lock fix")
except Exception:
    logger.warning(
        "Could not patch StreamableHTTPSessionManager — SSE lock contention may occur",
        exc_info=True,
    )

# ---------------------------------------------------------------------------
# Lifespan — process-level singletons, initialized once
# ---------------------------------------------------------------------------

# FastMCP's Streamable HTTP transport calls Server.run() per session, which
# enters the lifespan for each new client connection.  To prevent
# RoutingManager replacement (which destroys sampling state from other
# clients), all singletons are initialized exactly once on the first session
# and never torn down per-session.
#
# Stale session file is cleared in __main__ (process startup), NOT here —
# clearing per-session would race with the middleware writing the file.
#
# Safety invariant: the flag + guard below rely on asyncio's cooperative
# scheduling (no preemption between the check and assignment).  If the
# server were ever embedded in a threaded ASGI host, a threading.Lock
# would be needed instead.
_process_initialized = False


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Per-session lifespan — initializes process singletons on first call only."""
    global _process_initialized

    if not _process_initialized:
        _process_initialized = True

        # Enable WAL mode for SQLite
        db_path = DATA_DIR / "synthesis.db"
        if db_path.exists():
            async with aiosqlite.connect(str(db_path)) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")  # 30s — warm path can hold lock 10-20s
            logger.info("MCP lifespan: SQLite WAL mode enabled")

        # Initialize taxonomy event logger for scoring observability.
        # Without this, pipeline score events are silently skipped because
        # get_event_logger() raises RuntimeError in the MCP process.
        # cross_process=True forwards events to the backend's SSE via HTTP POST.
        from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger
        _tel = TaxonomyEventLogger(
            events_dir=DATA_DIR / "taxonomy_events",
            publish_to_bus=False,
            cross_process=True,
        )
        set_event_logger(_tel)
        logger.info("MCP lifespan: TaxonomyEventLogger initialized")

        # E1b: Enable cross-process forwarding for classification agreement
        from app.services.classification_agreement import get_classification_agreement
        get_classification_agreement()._cross_process = True
        logger.info("MCP lifespan: ClassificationAgreement cross-process forwarding enabled")

        # Initialize structured error logger for MCP process
        from app.services.error_logger import ErrorLogger as _ErrLogger
        from app.services.error_logger import set_error_logger as _set_err

        _set_err(_ErrLogger(DATA_DIR / "errors"))

        # Initialize routing with cross-process notification bridge.
        from app.services.event_bus import EventBus as _EventBus

        def _cross_process_notifier(event_type: str, payload: dict) -> None:
            try:
                asyncio.create_task(notify_event_bus(event_type, payload))
            except RuntimeError:
                logger.debug("Cross-process notifier: no running event loop (shutdown)")

        _mcp_event_bus = _EventBus()
        routing = RoutingManager(
            event_bus=_mcp_event_bus,
            data_dir=DATA_DIR,
            is_mcp_process=True,
            cross_process_notify=_cross_process_notifier,
        )
        _shared.set_routing(routing)

        _detected_provider = detect_provider()
        if _detected_provider:
            routing.set_provider(_detected_provider)
            logger.info("MCP routing: provider=%s tiers=%s", _detected_provider.name, routing.available_tiers)
        else:
            logger.warning("MCP routing: no provider, tiers=%s", routing.available_tiers)

        await routing.start_disconnect_checker()

        # Shared EmbeddingService singleton — reused by taxonomy engine and context service
        from app.services.embedding_service import EmbeddingService
        _mcp_embedding_service = EmbeddingService()

        # Hot-path-only taxonomy engine for domain mapping (Spec 6.7)
        try:
            from app.services.taxonomy import TaxonomyEngine

            engine = TaxonomyEngine(
                embedding_service=_mcp_embedding_service,
                provider_resolver=lambda: routing.state.provider,
            )
            _shared.set_taxonomy_engine(engine)

            # Load embedding index from disk cache so auto_inject_patterns()
            # can find relevant clusters. Without this, the MCP process's
            # embedding index is empty and all pattern injection returns nothing.
            _index_cache_path = DATA_DIR / "embedding_index.pkl"
            try:
                _cache_loaded = await engine.embedding_index.load_cache(_index_cache_path)
                if _cache_loaded:
                    logger.info(
                        "MCP server: EmbeddingIndex loaded from cache (%d entries)",
                        engine.embedding_index.size,
                    )
                else:
                    logger.info("MCP server: EmbeddingIndex cache not found — injection unavailable")
            except Exception as idx_exc:
                logger.warning("MCP server: EmbeddingIndex cache load failed (non-fatal): %s", idx_exc)

            # Periodic embedding index reload — picks up warm-path updates
            # so MCP pattern matching stays fresh (saved every ~5 min by backend).
            async def _refresh_embedding_index() -> None:
                while True:
                    await asyncio.sleep(600)  # 10 minutes
                    try:
                        loaded = await engine.embedding_index.load_cache(_index_cache_path)
                        if loaded:
                            logger.info(
                                "MCP: embedding index refreshed (%d entries)",
                                engine.embedding_index.size,
                            )
                    except Exception:
                        logger.debug("MCP: embedding index refresh failed", exc_info=True)

            asyncio.create_task(_refresh_embedding_index())

            logger.info("MCP server: TaxonomyEngine initialized (hot-path + injection + index refresh)")
        except Exception as exc:
            logger.warning("MCP server: TaxonomyEngine init failed (non-fatal): %s", exc)

        # Initialize unified context enrichment service
        try:
            from app.services.context_enrichment import ContextEnrichmentService
            from app.services.github_client import GitHubClient
            from app.services.heuristic_analyzer import HeuristicAnalyzer
            from app.services.workspace_intelligence import WorkspaceIntelligence

            _context_svc = ContextEnrichmentService(
                prompts_dir=PROMPTS_DIR,
                data_dir=DATA_DIR,
                workspace_intel=WorkspaceIntelligence(),
                embedding_service=_mcp_embedding_service,
                heuristic_analyzer=HeuristicAnalyzer(),
                github_client=GitHubClient(),
                taxonomy_engine=_shared.get_taxonomy_engine(),
            )
            _shared.set_context_service(_context_svc)
            logger.info("MCP server: ContextEnrichmentService initialized")
        except Exception as exc:
            logger.warning(
                "MCP server: ContextEnrichmentService init failed — passthrough "
                "and pattern resolution will be unavailable: %s", exc,
            )

        # Initialize domain services
        try:
            from app.services.domain_resolver import DomainResolver
            from app.services.domain_signal_loader import DomainSignalLoader
            from app.tools._shared import set_domain_resolver, set_signal_loader

            _domain_resolver = DomainResolver()
            _signal_loader = DomainSignalLoader()
            async with _shared.async_session_factory() as _init_db:
                await _domain_resolver.load(_init_db)
                await _signal_loader.load(_init_db)
            set_domain_resolver(_domain_resolver)
            set_signal_loader(_signal_loader)

            # Wire signal loader into heuristic analyzer
            from app.services.heuristic_analyzer import set_signal_loader as set_analyzer_signal_loader
            set_analyzer_signal_loader(_signal_loader)

            logger.info("MCP domain services initialized")
        except Exception as exc:
            logger.warning(
                "MCP server: domain services init failed (non-fatal): %s", exc,
            )

        # Load dynamic task-type signals from JSON cache (persisted by backend warm path)
        try:
            import json as _tt_json

            from app.services.heuristic_analyzer import set_task_type_signals
            _tt_cache = DATA_DIR / "task_type_signals.json"
            if _tt_cache.exists():
                _tt_raw = _tt_json.loads(_tt_cache.read_text())
                _tt_signals = {k: [(kw, w) for kw, w in v] for k, v in _tt_raw.items()}
                set_task_type_signals(_tt_signals)
                logger.info("MCP lifespan: TaskTypeSignals loaded from cache (%d types)", len(_tt_signals))
            else:
                logger.info("MCP lifespan: no task_type_signals.json — using static bootstrap")
        except Exception as _tt_exc:
            logger.warning("MCP lifespan: TaskTypeSignals cache load failed — static bootstrap: %s", _tt_exc)

        # Subscribe to domain events for cache invalidation
        async def _reload_domain_caches() -> None:
            try:
                from app.tools._shared import get_domain_resolver, get_signal_loader
                async with _shared.async_session_factory() as _reload_db:
                    resolver = get_domain_resolver()
                    await resolver.load(_reload_db)
                    loader = get_signal_loader()
                    if loader:
                        await loader.load(_reload_db)
                logger.info("MCP domain caches reloaded")
            except Exception:
                logger.error("MCP domain cache reload failed", exc_info=True)

        async def _domain_event_listener() -> None:
            """Background task: reload domain caches on taxonomy_changed / domain_created."""
            try:
                async for event in _mcp_event_bus.subscribe():
                    event_type = event.get("event", "")
                    if event_type in ("domain_created", "taxonomy_changed"):
                        asyncio.create_task(_reload_domain_caches())
            except Exception:
                logger.debug("MCP domain event listener exited", exc_info=True)

        asyncio.create_task(_domain_event_listener())

    yield {}
    # Drain any pending cross-process event forwarding tasks before
    # cleanup, so taxonomy_activity events aren't silently cancelled.
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        await get_event_logger().drain_pending(timeout=10.0)
    except RuntimeError:
        pass  # Event logger never initialized — nothing to drain

    # Clean up session file on shutdown so the next startup doesn't
    # see a stale file and trigger false reconnect_detected events.
    # Without this, `init.sh restart` leaves mcp_session.json from the
    # previous run, and the new backend's disconnect checker reads it as
    # evidence of a connected MCP client → spurious reconnect toasts.
    try:
        _session_file.delete()
        logger.info("Shutdown: deleted mcp_session.json")
    except Exception:
        pass


mcp = FastMCP(
    "synthesis_mcp",
    host="127.0.0.1",
    port=8001,
    streamable_http_path="/mcp",
    lifespan=_mcp_lifespan,
)


# ---------------------------------------------------------------------------
# ASGI middleware: environment-gated bearer token authentication
# ---------------------------------------------------------------------------


class _MCPAuthMiddleware:
    """Environment-gated bearer token authentication for MCP server.

    When auth_token is None (MCP_AUTH_TOKEN not set), acts as a no-op.
    When set, requires Authorization: Bearer <token> on all HTTP requests.
    SSE fallback: accepts ?token=<value> when allow_query_token is True.
    """

    def __init__(self, app, auth_token: str | None = None, allow_query_token: bool = True) -> None:
        self.app = app
        self.auth_token = auth_token
        self.allow_query_token = allow_query_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.auth_token is None:
            # Pass through non-HTTP scopes (lifespan, websocket) and unauthenticated mode
            return await self.app(scope, receive, send)

        import hmac

        # Check Authorization header (constant-time comparison)
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()
        expected = f"Bearer {self.auth_token}"
        if hmac.compare_digest(auth_header, expected):
            return await self.app(scope, receive, send)

        # Check query param fallback for SSE (constant-time comparison)
        if self.allow_query_token:
            from urllib.parse import parse_qs
            qs = parse_qs(scope.get("query_string", b"").decode())
            candidate = qs.get("token", [""])[0]
            if candidate and hmac.compare_digest(candidate, self.auth_token):
                return await self.app(scope, receive, send)

        # Reject — send 401
        logger.warning("MCP auth failure from %s", scope.get("client", ("unknown",))[0])
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
        ]})
        await send({"type": "http.response.body", "body": b'{"error":"Unauthorized"}'})


# ---------------------------------------------------------------------------
# ASGI middleware: detect sampling capability on MCP initialize handshake
# ---------------------------------------------------------------------------


class _CapabilityDetectionMiddleware:
    """Intercept MCP protocol messages to track client capabilities.

    Tracks two categories of MCP clients independently:

    1. **Sampling clients** (e.g., VS Code bridge) — declare ``sampling``
       in their ``initialize`` capabilities.  Their session IDs and SSE
       streams are tracked separately so disconnect is instant when they
       leave, regardless of other clients.

    2. **Non-sampling clients** (e.g., Claude Code) — do NOT affect
       sampling state.  Their activity keeps the general MCP connection
       alive but never refreshes the sampling timer.

    Design invariants:
    - ``routing.on_mcp_initialize(sampling_capable=True)`` fires ONLY
      when a sampling client sends ``initialize``.
    - ``routing.on_mcp_activity()`` fires ONLY from sampling client
      POST requests and SSE body chunks.
    - ``routing.on_mcp_disconnect()`` fires INSTANTLY when the last
      sampling SSE stream closes, OR when all SSE streams close.
    - Non-sampling clients cannot keep sampling alive or prevent
      disconnect detection.
    """

    _last_activity_write: float = 0.0
    _ACTIVITY_WRITE_THROTTLE: float = 10.0
    _active_sse_streams: int = 0
    _sampling_session_ids: set[str] = set()
    _sampling_sse_sessions: set[str] = set()

    def __init__(self, app):
        self.app = app

    # ── Request dispatch ──────────────────────────────────────────

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "")
        if method == "POST":
            await self._handle_post(scope, receive, send)
        elif method == "GET":
            await self._handle_get(scope, receive, send)
        else:
            await self.app(scope, receive, send)

    # ── POST handler (initialize + tool calls) ────────────────────

    async def _handle_post(self, scope, receive, send):
        body_chunks: list[bytes] = []
        response_status: int = 0
        is_initialize = False
        sampling_declared = False
        cls = _CapabilityDetectionMiddleware

        # Extract request session ID for per-client activity tracking
        req_headers = dict(
            (k.decode() if isinstance(k, bytes) else k,
             v.decode() if isinstance(v, bytes) else v)
            for k, v in scope.get("headers", [])
        )
        req_session_id = req_headers.get("mcp-session-id", "")
        is_sampling_client = req_session_id in cls._sampling_session_ids

        # Only refresh routing activity from sampling clients
        if is_sampling_client:
            self._touch_routing_activity()
        self._touch_session_file()

        async def _buffered_receive():
            nonlocal is_initialize, sampling_declared
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    body = b"".join(body_chunks)
                    try:
                        data = _json.loads(body)
                        if isinstance(data, dict) and data.get("method") == "initialize":
                            is_initialize = True
                            caps = data.get("params", {}).get("capabilities", {})
                            sampling_declared = caps.get("sampling") is not None
                    except Exception:
                        pass
                    self._inspect_initialize(body)
            return message

        async def _capture_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status", 0)
                if is_initialize and response_status == 200:
                    # Register session ID by capability
                    resp_headers = dict(
                        (k.decode() if isinstance(k, bytes) else k,
                         v.decode() if isinstance(v, bytes) else v)
                        for k, v in message.get("headers", [])
                    )
                    sid = resp_headers.get("mcp-session-id", "")
                    if sid:
                        if sampling_declared:
                            cls._sampling_session_ids.add(sid)
                            logger.info("Sampling session registered: %s", sid[:12])
                        else:
                            cls._sampling_session_ids.discard(sid)
            await send(message)

        await self.app(scope, _buffered_receive, _capture_send)

        if response_status in (400, 404):
            self._invalidate_stale_session()

    # ── GET handler (SSE streams) ─────────────────────────────────

    async def _handle_get(self, scope, receive, send):
        headers = dict(
            (k.decode() if isinstance(k, bytes) else k,
             v.decode() if isinstance(v, bytes) else v)
            for k, v in scope.get("headers", [])
        )
        session_id = headers.get("mcp-session-id", "")
        has_session_id = bool(session_id)
        is_sampling_stream = session_id in self._sampling_session_ids

        if not has_session_id:
            logger.info("GET /mcp without session ID — allowing for reconnection")

        get_handled = False
        get_is_sse = False
        cls = _CapabilityDetectionMiddleware

        async def _capture_get_send(message):
            nonlocal get_handled, get_is_sse
            if message["type"] == "http.response.start" and not get_handled:
                get_handled = True
                status = message.get("status", 0)
                if status in (400, 404):
                    cls._invalidate_stale_session()
                elif status == 200:
                    get_is_sse = True
                    cls._active_sse_streams += 1
                    if is_sampling_stream:
                        cls._sampling_sse_sessions.add(session_id)
                        logger.info("Sampling SSE opened: %s (total=%d, sampling=%d)",
                                    session_id[:12], cls._active_sse_streams,
                                    len(cls._sampling_sse_sessions))
                    if not has_session_id:
                        cls._write_optimistic_session()
                    elif is_sampling_stream:
                        # Sampling client reconnect — refresh routing
                        routing = _shared._routing
                        if routing:
                            routing.on_mcp_activity()
            elif message["type"] == "http.response.body" and get_is_sse:
                # SSE body chunk — only refresh routing from sampling streams
                if is_sampling_stream:
                    self._touch_routing_activity()
                self._touch_session_file()
            await send(message)

        try:
            await self.app(scope, receive, _capture_get_send)
        finally:
            if get_is_sse:
                cls._active_sse_streams = max(0, cls._active_sse_streams - 1)
                was_sampling = session_id in cls._sampling_sse_sessions
                if was_sampling:
                    cls._sampling_sse_sessions.discard(session_id)

                # Update session file
                try:
                    _session_file.update(sse_streams=cls._active_sse_streams)
                except Exception:
                    pass

                # Disconnect logic — fire instant routing state transitions
                # when the last sampling SSE closes.  The disconnect loop
                # (30s poll) is a fallback; this is the primary signal.
                routing = _shared._routing
                if was_sampling and not cls._sampling_sse_sessions and routing:
                    if cls._active_sse_streams == 0:
                        logger.info(
                            "All SSE streams closed — firing on_mcp_disconnect()"
                        )
                        routing.on_mcp_disconnect()
                    else:
                        logger.info(
                            "Last sampling SSE closed (non-sampling remain: %d) "
                            "— firing on_sampling_disconnect()",
                            cls._active_sse_streams,
                        )
                        routing.on_sampling_disconnect()
                elif cls._active_sse_streams == 0 and routing:
                    # All non-sampling streams closed — full disconnect
                    logger.info("All SSE streams closed — firing on_mcp_disconnect()")
                    routing.on_mcp_disconnect()

    # ── Activity tracking ─────────────────────────────────────────

    @classmethod
    def _touch_routing_activity(cls) -> None:
        """Refresh routing in-memory state (sampling clients only)."""
        routing = _shared._routing
        if routing:
            routing.on_mcp_activity()

    @classmethod
    def _touch_session_file(cls) -> None:
        """Write activity timestamp to session file (throttled, all clients)."""
        now_mono = time.monotonic()
        if now_mono - cls._last_activity_write < cls._ACTIVITY_WRITE_THROTTLE:
            return
        cls._last_activity_write = now_mono
        try:
            data = _session_file.read()
            if data is not None:
                data["last_activity"] = datetime.now(timezone.utc).isoformat()
                data["sse_streams"] = cls._active_sse_streams
                _session_file.write(data)
        except Exception:
            logger.debug("_touch_session_file failed", exc_info=True)

    # ── Optimistic session (reconnection) ─────────────────────────

    @classmethod
    def _write_optimistic_session(cls) -> None:
        """Session-less GET succeeded — client reconnecting after server restart."""
        try:
            routing = _shared._routing
            if routing:
                routing.on_mcp_initialize(sampling_capable=True)
            _session_file.write_session(True, sse_streams=cls._active_sse_streams)
            logger.info("Optimistic session write: sampling_capable=True (reconnection)")
        except Exception:
            logger.debug("Could not write optimistic session", exc_info=True)

    @staticmethod
    def _invalidate_stale_session() -> None:
        removed = _session_file.delete()
        if removed:
            routing = _shared._routing
            if routing:
                routing.on_session_invalidated()
            logger.info(
                "Stale session cleanup: removed mcp_session.json after failed "
                "client reconnection (400/404)",
            )

    @staticmethod
    def _inspect_initialize(body: bytes) -> None:
        try:
            data = _json.loads(body)
            if not isinstance(data, dict) or data.get("method") != "initialize":
                return
            params = data.get("params", {})
            caps = params.get("capabilities", {})
            client_info = params.get("clientInfo", {})
            sampling = caps.get("sampling") is not None

            logger.info(
                "Capability detection middleware: initialize from %s/%s — "
                "caps=%s, sampling=%s, protocolVersion=%s",
                client_info.get("name", "unknown"),
                client_info.get("version", "?"),
                list(caps.keys()),
                sampling,
                params.get("protocolVersion", "?"),
            )

            cls = _CapabilityDetectionMiddleware
            routing = _shared._routing
            if routing:
                # Don't downgrade sampling when a non-sampling client
                # connects while a sampling-capable client is already
                # active.  Multiple MCP clients (VS Code native + bridge)
                # can connect to the same server — only the bridge
                # declares sampling.  VS Code native reconnects
                # periodically and would clear sampling on each reconnect.
                if not sampling:
                    # Primary: authoritative RoutingManager state.
                    if routing.state.sampling_capable is True:
                        logger.info(
                            "Capability detection middleware: ignoring sampling=False "
                            "from %s (sampling already active from another client)",
                            client_info.get("name", "unknown"),
                        )
                        return
                    # Defense in depth: class-level SSE tracking proves a
                    # sampling client is connected even during brief startup
                    # races before RoutingManager state is fully populated.
                    if cls._sampling_sse_sessions:
                        logger.info(
                            "Capability detection middleware: ignoring sampling=False "
                            "from %s (sampling SSE streams still active)",
                            client_info.get("name", "unknown"),
                        )
                        return
                routing.on_mcp_initialize(sampling_capable=sampling)
            else:
                # RoutingManager not yet initialized (first-session race).
                # Don't write sampling=False if a sampling SSE is active.
                if not sampling and cls._sampling_sse_sessions:
                    logger.info(
                        "Capability detection middleware: ignoring sampling=False "
                        "from %s (sampling SSE active, routing not yet initialized)",
                        client_info.get("name", "unknown"),
                    )
                    return
                # Write to session file — RoutingManager._recover_state() will
                # read this during the singleton init moments later.
                _session_file.write_session(sampling)
        except Exception:
            logger.debug("Capability detection middleware: could not parse initialize", exc_info=True)


# Patch streamable_http_app to inject the capability-detection middleware.
_original_streamable_http_app = mcp.streamable_http_app


def _patched_streamable_http_app(**kwargs):
    app = _original_streamable_http_app(**kwargs)
    app.add_middleware(_CapabilityDetectionMiddleware)
    # Auth middleware wraps outermost — checked before capability detection
    from app.config import settings
    app.add_middleware(
        _MCPAuthMiddleware,
        auth_token=settings.MCP_AUTH_TOKEN,
        allow_query_token=settings.MCP_ALLOW_QUERY_TOKEN,
    )
    return app


mcp.streamable_http_app = _patched_streamable_http_app


# ---------------------------------------------------------------------------
# Tool registrations — thin wrappers delegating to app.tools.* handlers
# ---------------------------------------------------------------------------

# Import handlers
from app.tools.analyze import handle_analyze  # noqa: E402
from app.tools.explain import handle_explain  # noqa: E402
from app.tools.feedback import handle_feedback  # noqa: E402
from app.tools.get_optimization import handle_get_optimization  # noqa: E402
from app.tools.health import handle_health  # noqa: E402
from app.tools.history import handle_history  # noqa: E402
from app.tools.match import handle_match  # noqa: E402
from app.tools.optimize import handle_optimize  # noqa: E402
from app.tools.prepare import handle_prepare  # noqa: E402
from app.tools.refine import handle_refine  # noqa: E402
from app.tools.save_result import handle_save_result  # noqa: E402
from app.tools.strategies import handle_strategies  # noqa: E402


@mcp.tool(structured_output=True)
async def synthesis_optimize(
    prompt: Annotated[str, Field(description="The raw prompt text to optimize (20–200k chars).")],
    strategy: Annotated[str | None, Field(
        default=None,
        description="Optimization strategy name (e.g. 'auto', 'chain-of-thought', 'few-shot', "
        "'meta-prompting', 'role-playing', 'structured-output'). Defaults to user preference or 'auto'.",
    )] = None,
    repo_full_name: Annotated[str | None, Field(
        default=None, description="GitHub repo in 'owner/repo' format for codebase-aware optimization.",
    )] = None,
    workspace_path: Annotated[str | None, Field(
        default=None, description="Absolute path to the workspace root for context injection.",
    )] = None,
    applied_pattern_ids: Annotated[list[str] | None, Field(
        default=None, description="List of MetaPattern IDs to inject into the optimizer context. "
        "Get these from synthesis_match results.",
    )] = None,
    ctx: Context | None = None,
) -> OptimizeOutput:
    """Run the full optimization pipeline on a prompt.

    Call this to optimize any prompt. Returns the improved prompt, quality scores,
    and follow-up suggestions.

    Five execution paths (auto-selected by routing):
    1. force_passthrough → returns assembled_prompt for your LLM to process
    2. force_sampling + sampling capable → full pipeline via IDE's LLM
    3. Local provider → full internal pipeline
    4. No provider + MCP sampling → full pipeline via IDE's LLM
    5. Fallback → returns assembled_prompt for your LLM to process

    If status='pending_external': process the assembled_prompt with your LLM,
    then call synthesis_save_result(trace_id=<returned trace_id>,
    optimized_prompt=<your result>, scores={clarity, specificity, structure,
    faithfulness, conciseness — each 1.0-10.0}).

    Chain: Call synthesis_match BEFORE this tool to get applied_pattern_ids.
    Call synthesis_feedback AFTER using the result to close the learning loop.
    """
    return await handle_optimize(prompt, strategy, repo_full_name, workspace_path, applied_pattern_ids, ctx)


@mcp.tool(structured_output=True)
async def synthesis_analyze(
    prompt: Annotated[str, Field(description="The raw prompt text to analyze (min 20 chars).")],
    ctx: Context | None = None,
) -> AnalyzeOutput:
    """Analyze a prompt and generate baseline quality scores.

    Call BEFORE synthesis_optimize to understand prompt weaknesses and get a
    strategy recommendation. Returns task classification, strengths, weaknesses,
    recommended strategy, and baseline scores (5 dimensions).

    Chain: Use the returned selected_strategy when calling synthesis_optimize.
    """
    return await handle_analyze(prompt, ctx)


@mcp.tool(structured_output=True)
async def synthesis_prepare_optimization(
    prompt: Annotated[str, Field(
        description="The raw prompt text to prepare for external optimization (min 20 chars).",
    )],
    strategy: Annotated[str | None, Field(
        default=None,
        description="Optimization strategy name. Defaults to user preference or 'auto'.",
    )] = None,
    max_context_tokens: Annotated[int, Field(
        default=128000, description="Maximum context window budget in tokens.",
    )] = 128000,
    workspace_path: Annotated[str | None, Field(
        default=None, description="Absolute path to the workspace root for context injection.",
    )] = None,
    repo_full_name: Annotated[str | None, Field(
        default=None, description="GitHub repo in 'owner/repo' format for codebase-aware optimization.",
    )] = None,
    ctx: Context | None = None,
) -> PrepareOutput:
    """Assemble the full optimization prompt for processing by YOUR LLM.

    This is step 1 of the passthrough workflow:
    (1) Call this tool to get assembled_prompt and trace_id.
    (2) Process assembled_prompt with your LLM to produce an optimized version.
    (3) Call synthesis_save_result with the trace_id and the optimized output.

    Use when you want your own LLM to perform the optimization instead of
    the server's provider.
    """
    return await handle_prepare(prompt, strategy, max_context_tokens, workspace_path, repo_full_name, ctx)


@mcp.tool(structured_output=True)
async def synthesis_save_result(
    trace_id: Annotated[str, Field(description="Trace ID from synthesis_prepare_optimization.")],
    optimized_prompt: Annotated[str, Field(description="The optimized prompt text produced by your LLM.")],
    changes_summary: Annotated[str | None, Field(
        default=None, description="Brief summary of changes made during optimization.",
    )] = None,
    task_type: Annotated[str | None, Field(
        default=None,
        description="Task classification: 'coding', 'writing', 'analysis', 'creative', 'data', 'system', or 'general'.",
    )] = None,
    strategy_used: Annotated[str | None, Field(
        default=None, description="Strategy name used. Normalized to known strategies if verbose.",
    )] = None,
    scores: Annotated[dict | None, Field(
        default=None,
        description="Self-rated scores dict with keys: clarity, specificity, structure, "
        "faithfulness, conciseness (1.0-10.0 float, clamped to this range).",
    )] = None,
    model: Annotated[str | None, Field(
        default=None, description="Model ID that produced the optimization.",
    )] = None,
    codebase_context: Annotated[str | None, Field(
        default=None, description="IDE-provided codebase context snapshot to store alongside the result.",
    )] = None,
    domain: Annotated[str | None, Field(
        default=None,
        description="Domain from known domain nodes (e.g., 'backend', 'frontend', "
        "'database', 'devops', 'security'). Defaults to 'general' if not provided.",
    )] = None,
    intent_label: Annotated[str | None, Field(
        default=None, description="Short 3-6 word intent classification label. Defaults to 'general'.",
    )] = None,
    ctx: Context | None = None,
) -> SaveResultOutput:
    """Persist an optimization result from an external LLM with hybrid scoring.

    This is step 3 of the passthrough workflow (after synthesis_prepare_optimization
    or after synthesis_optimize returns status='pending_external').
    Blends self-rated scores with model-independent heuristics (z-score normalization
    + dimension-specific weights) and computes score deltas against the original prompt.

    Chain: Call synthesis_feedback AFTER using the optimized prompt to report quality.
    """
    return await handle_save_result(
        trace_id, optimized_prompt, changes_summary, task_type,
        strategy_used, scores, model, codebase_context, ctx,
        domain=domain, intent_label=intent_label,
    )


# ---- New tools (MCP tool chain expansion) ----


@mcp.tool(structured_output=True)
async def synthesis_health(
    ctx: Context | None = None,
) -> HealthOutput:
    """Check system capabilities and health before starting a workflow.

    Call at the START of any optimization session. Returns available routing
    tiers (internal/sampling/passthrough), active provider, and loaded strategies.
    Use this to decide whether synthesis_optimize will work or if you need
    synthesis_prepare_optimization (passthrough fallback).
    """
    return await handle_health()


@mcp.tool(structured_output=True)
async def synthesis_strategies(
    ctx: Context | None = None,
) -> StrategiesOutput:
    """List available optimization strategies with descriptions.

    Call BEFORE synthesis_optimize to choose the best strategy for your prompt
    instead of defaulting to 'auto'. Returns strategy names, taglines, and
    descriptions. Pass the returned name to synthesis_optimize's strategy parameter.
    """
    return await handle_strategies()


@mcp.tool(structured_output=True)
async def synthesis_history(
    limit: Annotated[int, Field(
        default=10, description="Number of results to return (1-50).",
    )] = 10,
    offset: Annotated[int, Field(
        default=0, description="Pagination offset.",
    )] = 0,
    sort_by: Annotated[str, Field(
        default="created_at",
        description="Sort column: 'created_at', 'overall_score', 'task_type', 'strategy_used', 'duration_ms'.",
    )] = "created_at",
    sort_order: Annotated[str, Field(
        default="desc", description="Sort direction: 'asc' or 'desc'.",
    )] = "desc",
    task_type: Annotated[str | None, Field(
        default=None,
        description="Filter by task type: 'coding', 'writing', 'analysis', 'creative', 'data', 'system', 'general'.",
    )] = None,
    status: Annotated[str | None, Field(
        default=None, description="Filter by status: 'completed', 'failed', 'analyzed', 'pending'.",
    )] = None,
    ctx: Context | None = None,
) -> HistoryOutput:
    """Query optimization history with filtering and sorting.

    Use to find past optimizations by task type, check which strategies
    performed well (sort by overall_score desc), or find an optimization
    to refine. Returns paginated summaries with 200-char prompt previews.

    Chain: Call synthesis_get_optimization with a returned ID to get full details.
    """
    return await handle_history(limit, offset, sort_by, sort_order, task_type, status)


@mcp.tool(structured_output=True)
async def synthesis_get_optimization(
    optimization_id: Annotated[str, Field(description="ID of the optimization to retrieve.")],
    ctx: Context | None = None,
) -> OptimizationDetailOutput:
    """Retrieve full details of a specific optimization by ID.

    Call after browsing synthesis_history to get the complete optimized prompt
    text, or before synthesis_refine to understand what needs improving.
    Returns full prompt texts, scores, feedback status, and refinement count.
    """
    return await handle_get_optimization(optimization_id)


@mcp.tool(structured_output=True)
async def synthesis_match(
    prompt_text: Annotated[str, Field(
        description="Prompt text to match against the knowledge graph (min 10 chars).",
    )],
    ctx: Context | None = None,
) -> MatchOutput:
    """Search the knowledge graph for clusters and reusable patterns similar to a prompt.

    Call BEFORE synthesis_optimize to leverage past optimization knowledge.
    Returns match quality (family/cluster/none), similarity score, cluster info,
    and meta-pattern IDs.

    Chain: Pass returned meta_patterns[].id values as applied_pattern_ids to
    synthesis_optimize for knowledge-informed optimization.
    """
    return await handle_match(prompt_text)


@mcp.tool(structured_output=True)
async def synthesis_feedback(
    optimization_id: Annotated[str, Field(description="ID of the optimization to rate.")],
    rating: Annotated[str, Field(
        description="Quality rating: 'thumbs_up' if the optimized prompt worked well, "
        "'thumbs_down' if it underperformed.",
    )],
    comment: Annotated[str | None, Field(
        default=None, description="Optional explanation of what worked or didn't.",
    )] = None,
    ctx: Context | None = None,
) -> FeedbackOutput:
    """Submit quality feedback on a completed optimization to drive strategy adaptation.

    Call AFTER using an optimized prompt and observing its effectiveness.
    Use 'thumbs_up' when the result produced good outcomes, 'thumbs_down'
    when it underperformed. The system learns which strategies work best
    for each task type based on accumulated feedback.
    """
    return await handle_feedback(optimization_id, rating, comment)


@mcp.tool(structured_output=True)
async def synthesis_refine(
    optimization_id: Annotated[str, Field(description="ID of the optimization to refine.")],
    refinement_request: Annotated[str, Field(
        description="Specific refinement instruction (e.g., 'add more concrete examples', "
        "'strengthen the error handling section', 'make it more concise').",
    )],
    branch_id: Annotated[str | None, Field(
        default=None, description="Branch ID to refine on. Omit to use the latest branch.",
    )] = None,
    workspace_path: Annotated[str | None, Field(
        default=None, description="Absolute path to workspace root for codebase context.",
    )] = None,
    ctx: Context | None = None,
) -> RefineOutput:
    """Iteratively improve an optimized prompt with specific instructions.

    Call after synthesis_optimize when the result needs targeted improvement.
    Each call is a fresh pipeline invocation (not multi-turn accumulation).
    Returns the refined prompt, updated scores, score deltas, and suggestions.

    Chain: Call synthesis_feedback after the final refinement to close the loop.
    """
    return await handle_refine(optimization_id, refinement_request, branch_id, workspace_path, ctx)


@mcp.tool(structured_output=True)
async def synthesis_seed(
    project_description: Annotated[str, Field(
        description="Project description for prompt generation (20+ chars).",
    )],
    workspace_path: Annotated[str | None, Field(
        default=None,
        description="Absolute path to workspace root for context extraction.",
    )] = None,
    repo_full_name: Annotated[str | None, Field(
        default=None,
        description="GitHub repo in 'owner/repo' format for explore phase.",
    )] = None,
    prompt_count: Annotated[int, Field(
        default=30,
        description="Target total prompts (5-100).",
    )] = 30,
    agents: Annotated[list[str] | None, Field(
        default=None,
        description="Specific agent names. None = all enabled.",
    )] = None,
    prompts: Annotated[list[str] | None, Field(
        default=None,
        description="User-provided prompts (bypasses generation).",
    )] = None,
    ctx: Context | None = None,
) -> SeedOutput:
    """Seed the taxonomy by generating and optimizing diverse prompts.

    Two modes:
    1. Generated (default): Provide project_description. Agents generate
       diverse prompts which are optimized through the full pipeline.
    2. Provided: Supply prompts list directly for batch optimization.

    The taxonomy discovers clusters, domains, and patterns organically
    from the optimized results. No structure is forced — the pipeline
    and taxonomy engine handle everything.
    """
    from app.tools.seed import handle_seed
    # MCP context: routing resolved via get_routing() inside handle_seed fallback
    return await handle_seed(
        project_description=project_description,
        workspace_path=workspace_path,
        repo_full_name=repo_full_name,
        prompt_count=prompt_count,
        agents=agents,
        prompts=prompts,
        ctx=ctx,
        routing=None,  # MCP path: handle_seed falls back to get_routing()
    )


@mcp.tool(structured_output=True)
async def synthesis_explain(
    optimization_id: Annotated[str, Field(
        description="Optimization ID or trace_id to explain.",
    )],
    ctx: Context | None = None,
) -> ExplainResult:
    """Get a plain-English explanation of what an optimization changed and why.

    Call after synthesis_optimize or synthesis_get_optimization to understand
    the result in non-technical terms. Returns a brief summary, specific
    change descriptions, the strategy used, and the score improvement.

    Chain: Call synthesis_get_optimization first to browse results,
    then this tool to explain one.
    """
    return await handle_explain(optimization_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def create_mcp_server() -> FastMCP:
    """Return the configured MCP server instance."""
    return mcp


def _clear_stale_session() -> None:
    """Remove stale ``mcp_session.json`` on server startup."""
    if _session_file.delete():
        logger.info("Startup: cleared stale mcp_session.json")


if __name__ == "__main__":
    _clear_stale_session()
    mcp.run(transport="streamable-http")
