"""Standalone MCP server with 4 optimization tools.

When no local LLM provider is available, synthesis_optimize uses MCP sampling
(ctx.session.create_message) to run the full pipeline through the IDE's
LLM via ``sampling_pipeline.py``.  If the client doesn't support sampling,
falls back to single-shot passthrough.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import json as _json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import aiosqlite
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field
from sqlalchemy import select

from app.config import (
    DATA_DIR,
    MCP_ACTIVITY_STALENESS_SECONDS,
    MCP_CAPABILITY_STALENESS_MINUTES,
    PROMPTS_DIR,
    settings,
)
from app.database import async_session_factory
from app.models import Optimization
from app.providers.detector import detect_provider
from app.schemas.mcp_models import (
    AnalyzeOutput,
    OptimizeOutput,
    PrepareOutput,
    SaveResultOutput,
)
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    ScoreResult,
)
from app.services.event_notification import notify_event_bus
from app.services.heuristic_scorer import HeuristicScorer
from app.services.passthrough import assemble_passthrough_prompt
from app.services.pipeline import PipelineOrchestrator
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.sampling_pipeline import run_sampling_analyze, run_sampling_pipeline
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.services.workspace_intelligence import WorkspaceIntelligence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Monkey-patch: allow session-less GETs for SSE reconnection after restart.
#
# When the MCP server restarts, all sessions are lost.  VS Code's MCP client
# detects the broken SSE stream and sends GET /mcp *without* an Mcp-Session-Id
# header (it lost the ID when the session was destroyed).  FastMCP's session
# manager creates a new transport for this GET (session_id=None → new session),
# but the transport's _validate_session then rejects the GET with 400 because
# the *request* doesn't carry the session ID.
#
# This patch makes _validate_session accept GETs that lack a session ID by
# returning True (skipping the check).  The response will include the new
# Mcp-Session-Id header, allowing the client to use it for subsequent POSTs
# (initialize + tool calls).
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
                # Let the GET through — the response will carry the session ID.
                return True
        return await _orig_validate_session(self, request, send)

    StreamableHTTPServerTransport._validate_session = _patched_validate_session  # type: ignore[assignment]
    logger.debug("Patched StreamableHTTPServerTransport._validate_session for SSE reconnection")
except Exception:
    logger.warning("Could not patch StreamableHTTPServerTransport — SSE reconnection may not work", exc_info=True)

# Module-level provider cache — set once by the lifespan, read by tools.
_provider = None

# Shared workspace intelligence instance — caches profiles by root set.
_workspace_intel = WorkspaceIntelligence()


async def _resolve_workspace_guidance(
    ctx: Context | None, workspace_path: str | None
) -> str | None:
    """Resolve workspace guidance: try roots/list first, fall back to workspace_path."""
    roots: list[Path] = []

    # Try MCP roots/list (zero-config)
    if ctx:
        try:
            roots_result = await ctx.session.list_roots()
            for root in roots_result.roots:
                uri = str(root.uri)
                if uri.startswith("file://"):
                    roots.append(Path(uri.removeprefix("file://")))
            if roots:
                logger.debug("Resolved %d workspace roots via MCP roots/list", len(roots))
        except Exception:
            logger.debug("Client does not support roots/list — will try workspace_path fallback")

    # Fallback: explicit workspace_path
    if not roots and workspace_path:
        roots = [Path(workspace_path)]
        logger.debug("Using explicit workspace_path fallback: %s", workspace_path)

    if not roots:
        logger.debug("No workspace roots resolved — skipping guidance injection")
        return None

    profile = _workspace_intel.analyze(roots)
    if profile:
        logger.info("Workspace guidance resolved: %d chars from %d roots", len(profile), len(roots))
    return profile


def _write_mcp_session_caps(ctx: Context | None) -> None:
    """Detect sampling capability from MCP client and persist to mcp_session.json.

    Called at the start of every MCP tool invocation so the health endpoint
    reflects the latest client state.  Uses the same optimistic strategy as the
    ASGI middleware: never downgrades a fresh ``True`` to ``False``.
    """
    try:
        sampling_capable = (
            ctx is not None
            and hasattr(ctx, "session")
            and ctx.session is not None
            and ctx.session.client_params is not None
            and getattr(ctx.session.client_params.capabilities, "sampling", None) is not None
        )
        path = DATA_DIR / "mcp_session.json"

        # Optimistic: never downgrade a fresh True to False
        if not sampling_capable and path.exists():
            try:
                existing = _json.loads(path.read_text(encoding="utf-8"))
                if existing.get("sampling_capable") is True:
                    written = datetime.fromisoformat(existing["written_at"])
                    if datetime.now(timezone.utc) - written <= timedelta(minutes=MCP_CAPABILITY_STALENESS_MINUTES):
                        logger.debug("_write_mcp_session_caps: skipping False — fresh True on file")
                        return
            except Exception:
                pass

        now = datetime.now(timezone.utc).isoformat()
        path.write_text(
            _json.dumps({
                "sampling_capable": sampling_capable,
                "written_at": now,
                "last_activity": now,
            }),
            encoding="utf-8",
        )
        logger.debug("mcp_session.json written: sampling_capable=%s", sampling_capable)
    except Exception:
        logger.debug("Could not write mcp_session.json", exc_info=True)


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Detect the LLM provider once at startup and expose it to tools."""
    global _provider
    # Enable WAL mode for SQLite (same as main.py)
    db_path = DATA_DIR / "synthesis.db"
    if db_path.exists():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
        logger.info("MCP lifespan: SQLite WAL mode enabled")

    _provider = detect_provider()
    if _provider:
        logger.info("MCP lifespan: provider detected — %s", _provider.name)
    else:
        logger.warning("MCP lifespan: no LLM provider available")

    yield {"provider": _provider}

    _provider = None


mcp = FastMCP(
    "synthesis_mcp",
    host="127.0.0.1",
    port=8001,
    streamable_http_path="/mcp",
    lifespan=_mcp_lifespan,
)


# ---------------------------------------------------------------------------
# ASGI middleware: detect sampling capability on MCP initialize handshake
# ---------------------------------------------------------------------------


class _CapabilityDetectionMiddleware:
    """Intercept the JSON-RPC ``initialize`` request to detect client capabilities.

    The ``initialize`` message is the *first* thing an MCP client sends. Its
    ``params.capabilities.sampling`` field tells us whether the client supports
    ``sampling/createMessage``.  By peeking at the raw HTTP body we can write
    ``mcp_session.json`` at handshake time — **before** any tool call — so the
    frontend health poll picks up the new state within seconds.

    The middleware re-assembles the request body transparently; downstream
    handlers (FastMCP) receive the original bytes untouched.
    """

    # Throttle activity writes: at most once per 10 seconds
    _last_activity_write: float = 0.0
    _ACTIVITY_WRITE_THROTTLE: float = 10.0
    # Track active SSE streams — proof that a client is connected even
    # when no POSTs are happening (idle SSE has no body chunks).
    _active_sse_streams: int = 0

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        if method == "POST":
            reconnected = self._touch_activity()  # throttled last_activity update
            body_chunks: list[bytes] = []
            body_complete = False
            initialize_result: dict | None = None  # set by _inspect_initialize
            response_status: int = 0

            async def _buffered_receive():
                nonlocal body_complete, initialize_result
                message = await receive()
                if message["type"] == "http.request":
                    body_chunks.append(message.get("body", b""))
                    if not message.get("more_body", False):
                        body_complete = True
                        initialize_result = self._inspect_initialize(b"".join(body_chunks))
                return message

            async def _capture_send(message):
                nonlocal response_status
                if message["type"] == "http.response.start":
                    response_status = message.get("status", 0)
                await send(message)

            await self.app(scope, _buffered_receive, _capture_send)

            # Detect failed reconnection: client sent a POST that got 404
            # (stale Mcp-Session-Id) or 400 (missing session ID after restart).
            # Invalidate stale mcp_session.json so health stops claiming a
            # client is connected.
            if response_status in (400, 404) and initialize_result is None:
                invalidated = self._invalidate_stale_session()
                if invalidated:
                    try:
                        await notify_event_bus("mcp_session_changed", {
                            "sampling_capable": False,
                            "reconnected": False,
                        })
                    except Exception:
                        pass
                    return  # skip normal event logic

            # Fire SSE events *after* the request completes so we don't block it.
            # Two triggers: reconnection (activity gap) or fresh initialize handshake.
            event_payload: dict | None = None
            if initialize_result is not None:
                event_payload = {
                    "sampling_capable": initialize_result["sampling_capable"],
                    "reconnected": False,
                }
            if reconnected:
                event_payload = {
                    "sampling_capable": True,
                    "reconnected": True,
                }
            if event_payload is not None:
                try:
                    await notify_event_bus("mcp_session_changed", event_payload)
                except Exception:
                    pass  # best-effort — backend SSE may not be reachable

        elif method == "GET":
            # GET /mcp establishes the SSE stream.  After a server restart all
            # sessions are lost; VS Code retries with a session-less GET.  The
            # monkey-patch above lets this through so the client gets a fresh
            # SSE stream with a new Mcp-Session-Id in the response headers.
            headers = dict(
                (k.decode() if isinstance(k, bytes) else k, v.decode() if isinstance(v, bytes) else v)
                for k, v in scope.get("headers", [])
            )
            has_session_id = "mcp-session-id" in headers

            if not has_session_id:
                logger.info(
                    "GET /mcp without Mcp-Session-Id — allowing SSE stream "
                    "for seamless reconnection after server restart",
                )

            # Note: for SSE streams, ``await self.app()`` blocks until the
            # client disconnects.  We must react in the send wrapper when
            # we see the response status, not after the await returns.
            get_handled = False

            get_is_sse = False  # set True once we confirm a 200 SSE stream

            async def _capture_get_send(message):
                nonlocal get_handled, get_is_sse
                if message["type"] == "http.response.start" and not get_handled:
                    get_handled = True
                    status = message.get("status", 0)
                    if status in (400, 404):
                        invalidated = _CapabilityDetectionMiddleware._invalidate_stale_session()
                        if invalidated:
                            try:
                                await notify_event_bus("mcp_session_changed", {
                                    "sampling_capable": False,
                                    "reconnected": False,
                                })
                            except Exception:
                                pass
                    elif status == 200:
                        get_is_sse = True
                        _CapabilityDetectionMiddleware._active_sse_streams += 1
                        if not has_session_id:
                            # Successful session-less GET = client reconnecting
                            # after server restart.  Optimistically assume
                            # sampling-capable (the client was previously
                            # connected and is re-establishing its SSE stream).
                            _CapabilityDetectionMiddleware._write_optimistic_session()
                            try:
                                await notify_event_bus("mcp_session_changed", {
                                    "sampling_capable": True,
                                    "reconnected": True,
                                })
                            except Exception:
                                pass
                elif message["type"] == "http.response.body" and get_is_sse:
                    # SSE stream is alive — keep last_activity fresh so the
                    # health endpoint doesn't report a false disconnect.
                    # _touch_activity is throttled (10s) so this is cheap.
                    _CapabilityDetectionMiddleware._touch_activity()
                await send(message)

            # Track the SSE stream lifetime. get_is_sse is set to True
            # inside _capture_get_send when we see a 200 response start.
            # We increment _after_ the response starts (inside the wrapper)
            # and decrement when the stream ends (self.app returns).
            try:
                await self.app(scope, receive, _capture_get_send)
            finally:
                if get_is_sse:
                    _CapabilityDetectionMiddleware._active_sse_streams = max(
                        0, _CapabilityDetectionMiddleware._active_sse_streams - 1,
                    )
                    # Write updated count to disk so health sees sse_streams=0.
                    # When the last stream closes, also fire a disconnect event
                    # so the frontend reacts immediately.
                    _CapabilityDetectionMiddleware._flush_sse_streams()
                    if _CapabilityDetectionMiddleware._active_sse_streams == 0:
                        logger.info("Last SSE stream closed — client disconnected")
                        try:
                            await notify_event_bus("mcp_session_changed", {
                                "sampling_capable": True,
                                "reconnected": False,
                                "disconnected": True,
                            })
                        except Exception:
                            pass
        else:
            await self.app(scope, receive, send)

    @classmethod
    def _flush_sse_streams(cls) -> None:
        """Write current ``_active_sse_streams`` count to ``mcp_session.json``.

        Called when an SSE stream closes so the health endpoint immediately
        sees the updated count.  When the last stream closes (count → 0),
        we do NOT update ``last_activity`` — letting it go stale so the
        health endpoint detects the disconnect via the normal staleness check.
        """
        try:
            path = DATA_DIR / "mcp_session.json"
            if not path.exists():
                return
            data = _json.loads(path.read_text(encoding="utf-8"))
            data["sse_streams"] = cls._active_sse_streams
            # Only refresh activity if streams are still active; otherwise
            # let staleness detection handle the disconnect.
            if cls._active_sse_streams > 0:
                data["last_activity"] = datetime.now(timezone.utc).isoformat()
            path.write_text(_json.dumps(data), encoding="utf-8")
        except Exception:
            logger.debug("_flush_sse_streams: could not update mcp_session.json", exc_info=True)

    @classmethod
    def _write_optimistic_session(cls) -> None:
        """Write ``mcp_session.json`` with ``sampling_capable=True`` optimistically.

        Called when a session-less GET succeeds (200) — the client is reconnecting
        its SSE stream after a server restart.  We assume sampling capability
        because the client was previously connected.  If the client later sends
        POST ``initialize`` the middleware will overwrite with the actual value.
        """
        try:
            path = DATA_DIR / "mcp_session.json"
            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                _json.dumps({
                    "sampling_capable": True,
                    "written_at": now,
                    "last_activity": now,
                    "sse_streams": cls._active_sse_streams,
                }),
                encoding="utf-8",
            )
            logger.info(
                "Optimistic session write: sampling_capable=True "
                "(session-less GET succeeded — client reconnecting)",
            )
        except Exception:
            logger.debug("Could not write optimistic mcp_session.json", exc_info=True)

    @staticmethod
    def _invalidate_stale_session() -> bool:
        """Remove ``mcp_session.json`` if it exists (failed reconnection cleanup).

        Called when a client receives a 400/404 from the MCP transport layer,
        indicating a stale or missing session ID.  Removing the file ensures
        the health endpoint immediately reports ``sampling_capable=null`` instead
        of keeping stale ``True`` until the 30-minute window expires.

        Returns ``True`` if a file was actually removed.
        """
        path = DATA_DIR / "mcp_session.json"
        if not path.exists():
            return False
        try:
            path.unlink()
            logger.info(
                "Stale session cleanup: removed mcp_session.json after failed "
                "client reconnection (400/404)",
            )
            return True
        except Exception:
            logger.debug("Could not remove stale mcp_session.json", exc_info=True)
            return False

    @classmethod
    def _touch_activity(cls) -> bool:
        """Update ``last_activity`` in mcp_session.json (throttled to avoid I/O spam).

        Also writes the ``sse_streams`` count so the health endpoint can see
        that a client is connected even when the SSE stream is idle (no POSTs).

        Returns ``True`` if this write represents a reconnection (previous
        activity was older than the staleness window).
        """
        now_mono = time.monotonic()
        if now_mono - cls._last_activity_write < cls._ACTIVITY_WRITE_THROTTLE:
            return False
        cls._last_activity_write = now_mono
        try:
            path = DATA_DIR / "mcp_session.json"
            if not path.exists():
                return False
            data = _json.loads(path.read_text(encoding="utf-8"))
            now_utc = datetime.now(timezone.utc)

            # Detect reconnection: previous activity was stale AND session was sampling-capable
            reconnected = False
            prev_activity_str = data.get("last_activity")
            if prev_activity_str and data.get("sampling_capable") is True:
                prev_activity = datetime.fromisoformat(prev_activity_str)
                gap = (now_utc - prev_activity).total_seconds()
                if gap > MCP_ACTIVITY_STALENESS_SECONDS:
                    reconnected = True
                    logger.info(
                        "MCP client reconnected after %ds inactivity (threshold %.0fs)",
                        int(gap),
                        MCP_ACTIVITY_STALENESS_SECONDS,
                    )

            data["last_activity"] = now_utc.isoformat()
            data["sse_streams"] = cls._active_sse_streams
            path.write_text(_json.dumps(data), encoding="utf-8")
            return reconnected
        except Exception:
            logger.debug("_touch_activity: could not update mcp_session.json", exc_info=True)
            return False

    @staticmethod
    def _inspect_initialize(body: bytes) -> dict | None:
        """If the body is a JSON-RPC ``initialize`` request, write session caps.

        Uses an **optimistic** strategy: a ``True`` detection is always written
        immediately.  A ``False`` detection only writes if the file doesn't
        already contain a fresh ``True`` value (within the staleness window).
        This prevents multi-session clients like VS Code from downgrading the
        detection when one of their internal sessions lacks sampling capability.

        Returns a ``{"sampling_capable": bool}`` dict when a file was written
        (caller uses this to fire an SSE event for instant frontend detection),
        or ``None`` if the body was not an ``initialize`` request or no write occurred.
        """
        try:
            data = _json.loads(body)
            if not isinstance(data, dict) or data.get("method") != "initialize":
                return None
            params = data.get("params", {})
            caps = params.get("capabilities", {})
            client_info = params.get("clientInfo", {})
            sampling = caps.get("sampling") is not None
            path = DATA_DIR / "mcp_session.json"

            logger.info(
                "Capability detection middleware: initialize from %s/%s — "
                "caps=%s, sampling=%s, protocolVersion=%s",
                client_info.get("name", "unknown"),
                client_info.get("version", "?"),
                list(caps.keys()),
                sampling,
                params.get("protocolVersion", "?"),
            )

            # Optimistic: never downgrade a fresh True to False
            if not sampling and path.exists():
                try:
                    existing = _json.loads(path.read_text(encoding="utf-8"))
                    if existing.get("sampling_capable") is True:
                        written = datetime.fromisoformat(existing["written_at"])
                        if datetime.now(timezone.utc) - written <= timedelta(minutes=MCP_CAPABILITY_STALENESS_MINUTES):
                            logger.info(
                                "Capability detection middleware: ignoring False — "
                                "fresh True already on file (age=%ds, client=%s)",
                                (datetime.now(timezone.utc) - written).total_seconds(),
                                client_info.get("name", "unknown"),
                            )
                            return None
                except Exception:
                    pass  # corrupt file — overwrite it

            now = datetime.now(timezone.utc).isoformat()
            path.write_text(
                _json.dumps({
                    "sampling_capable": sampling,
                    "written_at": now,
                    "last_activity": now,
                }),
                encoding="utf-8",
            )
            logger.info(
                "Capability detection middleware: wrote sampling_capable=%s (client=%s/%s)",
                sampling,
                client_info.get("name", "unknown"),
                client_info.get("version", "?"),
            )
            return {"sampling_capable": sampling}
        except Exception:
            logger.debug("Capability detection middleware: could not parse initialize", exc_info=True)
            return None


# Patch streamable_http_app to inject the capability-detection middleware.
# FastMCP creates a new Starlette app on each call, so we wrap the method.
_original_streamable_http_app = mcp.streamable_http_app


def _patched_streamable_http_app(**kwargs):
    app = _original_streamable_http_app(**kwargs)
    app.add_middleware(_CapabilityDetectionMiddleware)
    return app


mcp.streamable_http_app = _patched_streamable_http_app


# ---- Tool 1: synthesis_optimize ----


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
        default=None, description="List of MetaPattern IDs to inject into the optimizer context.",
    )] = None,
    ctx: Context | None = None,
) -> OptimizeOutput:
    """Run the full optimization pipeline on a prompt.

    Five execution paths (checked in order):
    1. force_passthrough=True → assembled template returned immediately (manual processing)
    2. force_sampling=True + client supports sampling → full pipeline via IDE's LLM
    3. Local provider exists → full internal pipeline
    4. No provider + client supports MCP sampling → full pipeline via IDE's LLM
    5. No provider + no sampling → assembled template for manual processing

    pipeline.force_passthrough and pipeline.force_sampling are mutually exclusive.
    """
    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )
    if len(prompt) > 200000:
        raise ValueError(
            "Prompt too long (%d chars). Maximum is 200,000 characters." % len(prompt)
        )

    provider = _provider

    # ---- Hoist: single PreferencesService + workspace resolution for all paths ----
    prefs = PreferencesService(DATA_DIR)
    effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)

    # ---- Detect and persist MCP client sampling capability (before routing) ----
    _write_mcp_session_caps(ctx)

    # ---- Force-passthrough short-circuit (explicit manual override, checked first) ----
    if prefs.get("pipeline.force_passthrough"):
        logger.info("synthesis_optimize: force_passthrough=True — returning passthrough template directly")
        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=prompt,
            strategy_name=effective_strategy,
            codebase_guidance=guidance,
        )
        trace_id = str(uuid.uuid4())
        async with async_session_factory() as db:
            pending = Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="pending",
                trace_id=trace_id,
                provider="mcp_passthrough",
                strategy_used=strategy_name,
                task_type="general",
            )
            db.add(pending)
            await db.commit()
        return OptimizeOutput(
            status="pending_external",
            pipeline_mode="passthrough",
            strategy_used=strategy_name,
            trace_id=trace_id,
            assembled_prompt=assembled,
            instructions=(
                "force_passthrough=True. Process the assembled_prompt with your LLM, "
                "then call synthesis_save_result with the trace_id and the optimized output. "
                "Include optimized_prompt, changes_summary, task_type, strategy_used, and "
                "optionally scores (clarity, specificity, structure, faithfulness, conciseness — each 1-10)."
            ),
        )

    # ---- Force-sampling short-circuit (overrides local provider when enabled) ----
    _sampling_already_attempted = False
    if prefs.get("pipeline.force_sampling"):
        if ctx and hasattr(ctx, "session") and ctx.session:
            logger.info("synthesis_optimize: force_sampling=True — attempting sampling pipeline")
            _sampling_already_attempted = True
            try:
                result = await run_sampling_pipeline(
                    ctx, prompt,
                    effective_strategy if effective_strategy != "auto" else None,
                    guidance,
                    repo_full_name=repo_full_name,
                    applied_pattern_ids=applied_pattern_ids,
                )
                return _sampling_result_to_output(result)
            except Exception as exc:
                logger.error(
                    "force_sampling requested but sampling failed: %s",
                    exc, exc_info=True,
                )
                error_msg = await _persist_sampling_failure(prompt, effective_strategy, exc)
                return OptimizeOutput(
                    status="error",
                    pipeline_mode="sampling",
                    strategy_used=effective_strategy,
                    warnings=[error_msg],
                )
        else:
            logger.info(
                "synthesis_optimize: force_sampling=True but no sampling-capable session — using normal routing"
            )

    # ---- No local provider: try sampling, then fall back to passthrough ----
    if not provider:
        # Try MCP sampling (full pipeline via IDE's LLM)
        if not _sampling_already_attempted and ctx and hasattr(ctx, "session") and ctx.session:
            try:
                logger.info("synthesis_optimize: no provider — attempting sampling pipeline")
                result = await run_sampling_pipeline(
                    ctx, prompt,
                    effective_strategy if effective_strategy != "auto" else None,
                    guidance,
                    repo_full_name=repo_full_name,
                    applied_pattern_ids=applied_pattern_ids,
                )
                return _sampling_result_to_output(result)
            except Exception as exc:
                logger.error(
                    "Sampling failed, falling back to passthrough: %s",
                    exc, exc_info=True,
                )
                await _persist_sampling_failure(prompt, effective_strategy, exc)

        # Fallback: single-shot passthrough template
        logger.info("synthesis_optimize: no provider — using passthrough template")

        assembled, strategy_name = assemble_passthrough_prompt(
            prompts_dir=PROMPTS_DIR,
            raw_prompt=prompt,
            strategy_name=effective_strategy,
            codebase_guidance=guidance,
        )

        trace_id = str(uuid.uuid4())

        async with async_session_factory() as db:
            pending = Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="pending",
                trace_id=trace_id,
                provider="mcp_passthrough",
                strategy_used=strategy_name,
                task_type="general",
            )
            db.add(pending)
            await db.commit()

        return OptimizeOutput(
            status="pending_external",
            pipeline_mode="passthrough",
            strategy_used=strategy_name,
            trace_id=trace_id,
            assembled_prompt=assembled,
            instructions=(
                "No local LLM provider detected. Process the assembled_prompt "
                "with your LLM, then call synthesis_save_result with the trace_id "
                "and the optimized output. Include optimized_prompt, changes_summary, "
                "task_type, strategy_used, and optionally scores "
                "(clarity, specificity, structure, faithfulness, conciseness — each 1-10)."
            ),
        )

    start = time.monotonic()

    logger.info(
        "synthesis_optimize called: prompt_len=%d strategy=%s repo=%s",
        len(prompt), effective_strategy, repo_full_name,
    )

    async with async_session_factory() as db:
        orchestrator = PipelineOrchestrator(prompts_dir=PROMPTS_DIR)

        result = None
        async for event in orchestrator.run(
            raw_prompt=prompt,
            provider=provider,
            db=db,
            strategy_override=effective_strategy if effective_strategy != "auto" else None,
            codebase_guidance=guidance,
            repo_full_name=repo_full_name,
            applied_pattern_ids=applied_pattern_ids,
        ):
            if event.event == "optimization_complete":
                result = event.data
            elif event.event == "error":
                error_msg = event.data.get("error", "Pipeline failed")
                logger.error("synthesis_optimize pipeline error: %s", error_msg)
                raise ValueError(error_msg)

        if not result:
            raise ValueError(
                "Pipeline completed but produced no result. Check server logs for details."
            )

        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "synthesis_optimize completed in %dms: optimization_id=%s strategy=%s",
            elapsed_ms, result.get("id", ""), result.get("strategy_used", ""),
        )

        # Notify backend event bus (MCP runs in a separate process)
        await notify_event_bus("optimization_created", {
            "id": result.get("id", ""),
            "task_type": result.get("task_type", ""),
            "strategy_used": result.get("strategy_used", ""),
            "overall_score": result.get("overall_score"),
            "provider": provider.name,
            "status": "completed",
        })

        return OptimizeOutput(
            status="completed",
            pipeline_mode="internal",
            optimization_id=result.get("id", ""),
            optimized_prompt=result.get("optimized_prompt", ""),
            task_type=result.get("task_type", ""),
            strategy_used=result.get("strategy_used", ""),
            changes_summary=result.get("changes_summary", ""),
            scores=result.get("optimized_scores", result.get("scores", {})),
            original_scores=result.get("original_scores", {}),
            score_deltas=result.get("score_deltas", {}),
            scoring_mode=result.get("scoring_mode", "independent"),
            suggestions=result.get("suggestions", []),
            warnings=result.get("warnings", []),
            model_used=result.get("model_used"),
            intent_label=result.get("intent_label"),
            domain=result.get("domain"),
            trace_id=result.get("trace_id"),
        )


async def _persist_sampling_failure(
    prompt: str, strategy: str, exc: Exception,
) -> str:
    """Persist a failed sampling Optimization record and notify event bus.

    Returns the formatted error message.  Non-fatal: swallows DB errors so
    the caller can still return a response.
    """
    error_msg = f"Sampling pipeline failed: {type(exc).__name__}: {exc}"
    try:
        async with async_session_factory() as db:
            db.add(Optimization(
                id=str(uuid.uuid4()),
                raw_prompt=prompt,
                status="failed",
                provider="mcp_sampling",
                strategy_used=strategy,
                task_type="general",
                changes_summary=error_msg,
            ))
            await db.commit()
    except Exception:
        logger.debug("Failed to persist sampling failure record", exc_info=True)
    await notify_event_bus("optimization_failed", {
        "error": error_msg,
        "provider": "mcp_sampling",
        "pipeline_mode": "sampling",
    })
    return error_msg


def _sampling_result_to_output(result: dict) -> OptimizeOutput:
    """Convert a sampling pipeline result dict to OptimizeOutput."""
    return OptimizeOutput(
        status="completed",
        pipeline_mode="sampling",
        optimization_id=result.get("optimization_id", ""),
        optimized_prompt=result.get("optimized_prompt", ""),
        task_type=result.get("task_type", ""),
        strategy_used=result.get("strategy_used", ""),
        changes_summary=result.get("changes_summary", ""),
        scores=result.get("scores", {}),
        original_scores=result.get("original_scores", {}),
        score_deltas=result.get("score_deltas", {}),
        scoring_mode=result.get("scoring_mode", ""),
        suggestions=result.get("suggestions", []),
        warnings=result.get("warnings", []),
        model_used=result.get("model_used"),
        intent_label=result.get("intent_label"),
        domain=result.get("domain"),
        trace_id=result.get("trace_id"),
    )


# ---- Tool 2: synthesis_analyze ----


@mcp.tool(structured_output=True)
async def synthesis_analyze(
    prompt: Annotated[str, Field(description="The raw prompt text to analyze (min 20 chars).")],
    ctx: Context | None = None,
) -> AnalyzeOutput:
    """Analyze a prompt and score it.

    Returns task type, weaknesses, strengths, strategy recommendation,
    and baseline quality scores (5 dimensions). Persists to history as an
    'analyzed' entry. Use the returned optimization_ready params to run
    synthesis_optimize if the analysis suggests improvement is worthwhile.

    When no local LLM provider is available but the client supports MCP
    sampling, runs analysis via the IDE's LLM.
    """
    _write_mcp_session_caps(ctx)

    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )

    provider = _provider

    # ---- Sampling fallback: no local provider but client supports sampling ----
    if not provider:
        if ctx and hasattr(ctx, "session") and ctx.session:
            try:
                result = await run_sampling_analyze(ctx, prompt)
                return AnalyzeOutput(**result)
            except Exception as exc:
                logger.info(
                    "Sampling analyze failed, no fallback available: %s",
                    type(exc).__name__,
                )
        raise ValueError(
            "No LLM provider available. Set ANTHROPIC_API_KEY or install the Claude CLI."
        )

    start = time.monotonic()
    logger.info("synthesis_analyze called: prompt_len=%d", len(prompt))

    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()

    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    # --- Phase 1: Analyze ---
    system_prompt = loader.load("agent-guidance.md")
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": strategy_loader.format_available(),
    })

    try:
        analysis: AnalysisResult = await provider.complete_parsed(
            model=prefs.resolve_model("analyzer", prefs_snapshot),
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            max_tokens=16384,
            effort="medium",
        )
    except Exception as exc:
        logger.error("synthesis_analyze Phase 1 (analyze) failed: %s", exc)
        raise ValueError("Analysis failed: %s" % exc) from exc

    analyze_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "synthesis_analyze Phase 1 complete in %dms: task_type=%s strategy=%s confidence=%.2f",
        analyze_ms, analysis.task_type, analysis.selected_strategy, analysis.confidence,
    )

    # --- Phase 2: Score original prompt ---
    # Send the same prompt as both A and B — scorer evaluates it on its own merits.
    scoring_system = loader.load("scoring.md")
    scorer_msg = (
        f"<prompt-a>\n{prompt}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt}\n</prompt-b>"
    )

    try:
        score_result: ScoreResult = await provider.complete_parsed(
            model=prefs.resolve_model("scorer", prefs_snapshot),
            system_prompt=scoring_system,
            user_message=scorer_msg,
            output_format=ScoreResult,
            max_tokens=16384,
            effort="medium",
        )
    except Exception as exc:
        logger.error("synthesis_analyze Phase 2 (score) failed: %s", exc)
        raise ValueError("Scoring failed: %s" % exc) from exc

    # Both A and B are the same prompt — use prompt_a_scores as baseline
    # Apply hybrid scoring for consistency with main pipeline
    heur_scores = HeuristicScorer.score_prompt(prompt)
    blended = blend_scores(score_result.prompt_a_scores, heur_scores)
    baseline = blended.to_dimension_scores()
    overall = baseline.overall

    total_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        "synthesis_analyze Phase 2 complete: overall=%.1f total_ms=%d",
        overall, total_ms,
    )

    # --- Persist to DB ---
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    async with async_session_factory() as db:
        opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt="",
            task_type=analysis.task_type,
            intent_label=getattr(analysis, "intent_label", None) or "general",
            domain=getattr(analysis, "domain", None) or "general",
            strategy_used=analysis.selected_strategy,
            changes_summary="",
            score_clarity=baseline.clarity,
            score_specificity=baseline.specificity,
            score_structure=baseline.structure,
            score_faithfulness=baseline.faithfulness,
            score_conciseness=baseline.conciseness,
            overall_score=overall,
            provider=provider.name,
            model_used=prefs.resolve_model("analyzer", prefs_snapshot),
            scoring_mode="baseline",
            status="analyzed",
            trace_id=trace_id,
            duration_ms=total_ms,
        )
        db.add(opt)
        await db.commit()

    logger.info(
        "synthesis_analyze persisted: optimization_id=%s trace_id=%s",
        opt_id, trace_id,
    )

    # --- Notify event bus ---
    await notify_event_bus("optimization_analyzed", {
        "id": opt_id,
        "trace_id": trace_id,
        "task_type": analysis.task_type,
        "strategy": analysis.selected_strategy,
        "overall_score": overall,
        "provider": provider.name,
        "status": "analyzed",
    })

    # --- Build actionable next steps ---
    next_steps = [
        "Run `synthesis_optimize(prompt=..., strategy='%s')` to improve this prompt"
        % analysis.selected_strategy,
    ]
    # Add weakness-specific suggestions
    for weakness in analysis.weaknesses[:3]:
        next_steps.append("Address: %s" % weakness)

    # Find lowest-scoring dimension for targeted advice
    dim_scores = {
        "clarity": baseline.clarity,
        "specificity": baseline.specificity,
        "structure": baseline.structure,
        "faithfulness": baseline.faithfulness,
        "conciseness": baseline.conciseness,
    }
    weakest_dim = min(dim_scores, key=dim_scores.get)  # type: ignore[arg-type]
    weakest_val = dim_scores[weakest_dim]
    if weakest_val < 7.0:
        next_steps.append(
            "Focus on %s (scored %.1f/10) — this is the biggest opportunity for improvement"
            % (weakest_dim, weakest_val)
        )

    return AnalyzeOutput(
        optimization_id=opt_id,
        task_type=analysis.task_type,
        weaknesses=analysis.weaknesses,
        strengths=analysis.strengths,
        selected_strategy=analysis.selected_strategy,
        strategy_rationale=analysis.strategy_rationale,
        confidence=analysis.confidence,
        baseline_scores=dim_scores,
        overall_score=overall,
        duration_ms=total_ms,
        next_steps=next_steps,
        optimization_ready={
            "prompt": prompt,
            "strategy": analysis.selected_strategy,
        },
        intent_label=getattr(analysis, "intent_label", None) or "general",
        domain=getattr(analysis, "domain", None) or "general",
    )


# ---- Tool 3: synthesis_prepare_optimization ----


@mcp.tool(structured_output=True)
async def synthesis_prepare_optimization(
    prompt: Annotated[str, Field(
        description="The raw prompt text to prepare for external optimization (min 20 chars).",
    )],
    strategy: Annotated[str | None, Field(
        default=None,
        description="Optimization strategy name (e.g. 'auto', 'chain-of-thought', 'few-shot', "
        "'meta-prompting', 'role-playing', 'structured-output'). Defaults to user preference or 'auto'.",
    )] = None,
    max_context_tokens: Annotated[int, Field(
        default=128000, description="Maximum context window budget in tokens. Assembled prompt is truncated to fit.",
    )] = 128000,
    workspace_path: Annotated[str | None, Field(
        default=None, description="Absolute path to the workspace root for context injection.",
    )] = None,
    repo_full_name: Annotated[str | None, Field(
        default=None, description="GitHub repo in 'owner/repo' format for codebase-aware optimization.",
    )] = None,
    ctx: Context | None = None,
) -> PrepareOutput:
    """Assemble the full optimization prompt with context for an external LLM.

    Call synthesis_save_result with the output.
    """
    _write_mcp_session_caps(ctx)

    if len(prompt) < 20:
        raise ValueError(
            "Prompt too short (%d chars). Minimum is 20 characters." % len(prompt)
        )

    # Resolve strategy: explicit param → user preference → auto
    prefs = PreferencesService(DATA_DIR)
    effective_strategy = strategy or prefs.get("defaults.strategy") or "auto"

    logger.info(
        "synthesis_prepare_optimization called: prompt_len=%d strategy=%s",
        len(prompt), effective_strategy,
    )

    # Auto-discover workspace roots (zero-config) or fall back to workspace_path
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)

    assembled, strategy_name = assemble_passthrough_prompt(
        prompts_dir=PROMPTS_DIR,
        raw_prompt=prompt,
        strategy_name=effective_strategy,
        codebase_guidance=guidance,
    )

    # Enforce max_context_tokens budget
    estimated_tokens = len(assembled) // 4
    if estimated_tokens > max_context_tokens:
        max_chars = max_context_tokens * 4
        assembled = assembled[:max_chars]
        context_size_tokens = max_context_tokens
    else:
        context_size_tokens = estimated_tokens

    trace_id = str(uuid.uuid4())

    # Store pending optimization with raw_prompt for later save_result linkage
    async with async_session_factory() as db:
        pending = Optimization(
            id=str(uuid.uuid4()),
            raw_prompt=prompt,
            status="pending",
            trace_id=trace_id,
            provider="mcp_passthrough",
            strategy_used=strategy_name,
            task_type="general",
        )
        db.add(pending)
        await db.commit()

    logger.info(
        "synthesis_prepare_optimization completed: trace_id=%s strategy=%s tokens=%d",
        trace_id, strategy_name, context_size_tokens,
    )

    return PrepareOutput(
        trace_id=trace_id,
        assembled_prompt=assembled,
        context_size_tokens=context_size_tokens,
        strategy_requested=strategy_name,
    )


# ---- Tool 4: synthesis_save_result ----


@mcp.tool(structured_output=True)
async def synthesis_save_result(
    trace_id: Annotated[str, Field(description="Trace ID from synthesis_prepare_optimization to link this result.")],
    optimized_prompt: Annotated[str, Field(description="The optimized prompt text produced by the external LLM.")],
    changes_summary: Annotated[str | None, Field(
        default=None, description="Brief summary of changes made during optimization.",
    )] = None,
    task_type: Annotated[str | None, Field(
        default=None,
        description="Task classification: 'coding', 'writing', 'analysis', 'creative', 'data', 'system', or 'general'.",
    )] = None,
    strategy_used: Annotated[str | None, Field(
        default=None,
        description="Strategy name used (e.g. 'auto', 'chain-of-thought'). Normalized to known strategies if verbose.",
    )] = None,
    scores: Annotated[dict | None, Field(
        default=None,
        description="Self-rated scores dict with keys: clarity, specificity, structure, "
        "faithfulness, conciseness (0-10 float).",
    )] = None,
    model: Annotated[str | None, Field(
        default=None, description="Model ID that produced the optimization (e.g. 'claude-sonnet-4-6').",
    )] = None,
    codebase_context: Annotated[str | None, Field(
        default=None, description="IDE-provided codebase context snapshot to store alongside the result.",
    )] = None,
    ctx: Context | None = None,
) -> SaveResultOutput:
    """Persist an optimization result from an external LLM.

    Applies bias correction to self-rated scores.
    Optionally stores IDE-provided codebase context snapshot.
    """
    _write_mcp_session_caps(ctx)
    logger.info("synthesis_save_result called: trace_id=%s model=%s", trace_id, model)

    # Normalize strategy_used — external LLMs often return verbose rationales
    # instead of the short identifier. Match against known strategies.
    if strategy_used:
        strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
        known = strategy_loader.list_strategies()
        if strategy_used not in known:
            # Try to extract a known strategy name from the verbose string
            normalized = "auto"
            lower = strategy_used.lower()
            for name in known:
                if name != "auto" and name in lower:
                    normalized = name
                    break
            logger.info(
                "Strategy normalized: '%s' → '%s'",
                strategy_used[:80], normalized,
            )
            strategy_used = normalized

    # Check scoring preference
    prefs = PreferencesService(DATA_DIR)
    scoring_enabled = prefs.get("pipeline.enable_scoring")
    if scoring_enabled is None:
        scoring_enabled = True  # default on

    # Determine scoring mode and compute final scores
    clean_scores: dict[str, float] = {}
    heuristic_flags: list[str] = []
    scoring_mode = "skipped" if not scoring_enabled else "heuristic"

    if scores and scoring_enabled:
        # IDE provided self-rated scores — clean and validate
        scoring_mode = "hybrid_passthrough"
        for k, v in scores.items():
            try:
                clean_scores[k] = float(v)
            except (ValueError, TypeError):
                clean_scores[k] = 5.0  # default

    # Persist — look up pending optimization created by prepare, or create new
    async with async_session_factory() as db:
        # Look up pending optimization created by synthesis_prepare_optimization
        result = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        opt = result.scalar_one_or_none()

        # Determine strategy compliance by comparing prepare vs save
        strategy_compliance = "unknown"
        if opt and opt.strategy_used and strategy_used:
            if opt.strategy_used == strategy_used:
                strategy_compliance = "matched"
            else:
                strategy_compliance = "partial"
                logger.info(
                    "Strategy mismatch: requested=%s, used=%s",
                    opt.strategy_used,
                    strategy_used,
                )
        elif strategy_used:
            strategy_compliance = "matched"  # no prepare to compare against

        # Compute scores — hybrid blending matching the internal pipeline
        heuristic_scores: dict[str, float] = {}
        final_scores: dict[str, float] = {}
        overall: float | None = None
        original_scores: dict[str, float] | None = None
        deltas: dict[str, float] | None = None

        if scoring_enabled:
            # Compute heuristic scores for the optimized prompt
            heuristic_scores = HeuristicScorer.score_prompt(
                optimized_prompt,
                original=opt.raw_prompt if opt and opt.raw_prompt else None,
            )

            if clean_scores:
                # IDE provided scores — blend with heuristics (same as internal pipeline)
                try:
                    # Apply bias correction BEFORE blending (discount self-rating inflation)
                    corrected = HeuristicScorer.apply_bias_correction(clean_scores)
                    ide_scores_corrected = DimensionScores(
                        clarity=corrected.get("clarity", 5.0),
                        specificity=corrected.get("specificity", 5.0),
                        structure=corrected.get("structure", 5.0),
                        faithfulness=corrected.get("faithfulness", 5.0),
                        conciseness=corrected.get("conciseness", 5.0),
                    )

                    # Fetch historical stats for z-score normalization (non-fatal)
                    historical_stats: dict | None = None
                    try:
                        from app.services.optimization_service import OptimizationService
                        opt_svc = OptimizationService(db)
                        historical_stats = await opt_svc.get_score_distribution(
                            exclude_scoring_modes=["heuristic"],
                        )
                    except Exception:
                        pass

                    # Hybrid blend: bias-corrected IDE scores + heuristics
                    blended = blend_scores(
                        ide_scores_corrected, heuristic_scores, historical_stats,
                    )
                    blended_dims = blended.to_dimension_scores()
                    final_scores = {
                        "clarity": blended_dims.clarity,
                        "specificity": blended_dims.specificity,
                        "structure": blended_dims.structure,
                        "faithfulness": blended_dims.faithfulness,
                        "conciseness": blended_dims.conciseness,
                    }

                    # Divergence flags
                    heuristic_flags = blended.divergence_flags or []

                    scoring_mode = "hybrid_passthrough"

                except Exception as exc:
                    logger.warning("Hybrid blending failed, falling back to heuristic: %s", exc)
                    final_scores = heuristic_scores
                    scoring_mode = "heuristic"
            else:
                # No IDE scores — pure heuristic (same as before)
                final_scores = heuristic_scores
                scoring_mode = "heuristic"

            overall = round(
                sum(final_scores.values()) / max(len(final_scores), 1), 2,
            )

            # Compute original prompt scores + deltas when raw_prompt is available
            if opt and opt.raw_prompt:
                original_heur = HeuristicScorer.score_prompt(opt.raw_prompt)
                original_scores = original_heur
                deltas = {
                    dim: round(final_scores[dim] - original_scores[dim], 2)
                    for dim in final_scores
                    if dim in original_scores
                }

        # Truncate codebase context if provided
        context_snapshot = None
        if codebase_context:
            context_snapshot = codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]

        if opt:
            # Update existing pending record from prepare
            opt.optimized_prompt = optimized_prompt
            opt.task_type = task_type or opt.task_type or "general"
            opt.strategy_used = strategy_used or opt.strategy_used or "auto"
            opt.changes_summary = changes_summary or ""
            opt.score_clarity = final_scores.get("clarity")
            opt.score_specificity = final_scores.get("specificity")
            opt.score_structure = final_scores.get("structure")
            opt.score_faithfulness = final_scores.get("faithfulness")
            opt.score_conciseness = final_scores.get("conciseness")
            opt.overall_score = overall
            opt.original_scores = original_scores
            opt.score_deltas = deltas
            opt.model_used = model or "external"
            opt.scoring_mode = scoring_mode
            opt.status = "completed"
            if context_snapshot:
                opt.codebase_context_snapshot = context_snapshot
            opt_id = opt.id
        else:
            # No prepare was called — create new record (standalone save).
            # No raw_prompt available, so no original_scores or deltas.
            opt_id = str(uuid.uuid4())
            opt = Optimization(
                id=opt_id,
                raw_prompt="",
                optimized_prompt=optimized_prompt,
                task_type=task_type or "general",
                strategy_used=strategy_used or "auto",
                changes_summary=changes_summary or "",
                score_clarity=final_scores.get("clarity"),
                score_specificity=final_scores.get("specificity"),
                score_structure=final_scores.get("structure"),
                score_faithfulness=final_scores.get("faithfulness"),
                score_conciseness=final_scores.get("conciseness"),
                overall_score=overall,
                provider="mcp_passthrough",
                model_used=model or "external",
                scoring_mode=scoring_mode,
                status="completed",
                trace_id=trace_id,
                codebase_context_snapshot=context_snapshot,
            )
            db.add(opt)

        await db.commit()

        # Notify backend event bus (MCP runs in a separate process)
        await notify_event_bus("optimization_created", {
            "id": opt_id,
            "trace_id": trace_id,
            "task_type": opt.task_type,
            "strategy_used": opt.strategy_used,
            "overall_score": overall,
            "provider": opt.provider,
            "status": "completed",
        })

    logger.info(
        "synthesis_save_result completed: optimization_id=%s strategy_compliance=%s flags=%d",
        opt_id, strategy_compliance, len(heuristic_flags),
    )

    return SaveResultOutput(
        optimization_id=opt_id,
        scoring_mode=scoring_mode,
        scores={k: round(v, 2) for k, v in final_scores.items()} if final_scores else {},
        original_scores=original_scores,
        score_deltas=deltas,
        overall_score=overall,
        strategy_compliance=strategy_compliance,
        heuristic_flags=heuristic_flags,
    )


# ---- Entry point ----


def create_mcp_server() -> FastMCP:
    """Return the configured MCP server instance."""
    return mcp


def _clear_stale_session() -> None:
    """Remove stale ``mcp_session.json`` on server startup.

    All MCP sessions live in-memory — a server restart invalidates every
    session.  Without this cleanup, the health endpoint would keep reporting
    ``sampling_capable=true`` from the old session until the 30-minute
    staleness window expires.
    """
    mcp_session_path = DATA_DIR / "mcp_session.json"
    if mcp_session_path.exists():
        try:
            mcp_session_path.unlink()
            logger.info("Startup: cleared stale mcp_session.json")
        except Exception:
            logger.debug("Startup: could not remove mcp_session.json", exc_info=True)


if __name__ == "__main__":
    _clear_stale_session()
    mcp.run(transport="streamable-http")
