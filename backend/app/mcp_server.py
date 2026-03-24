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
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _mcp_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Detect the LLM provider and initialize routing at startup."""
    _clear_stale_session()

    # Enable WAL mode for SQLite
    db_path = DATA_DIR / "synthesis.db"
    if db_path.exists():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")
        logger.info("MCP lifespan: SQLite WAL mode enabled")

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
        logger.info("MCP server: TaxonomyEngine initialized (hot-path only)")
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

    yield {}

    _shared.set_context_service(None)
    await routing.stop()
    _shared.set_taxonomy_engine(None)
    _shared.set_routing(None)

    try:
        from app.database import dispose
        await dispose()
    except Exception as exc:
        logger.warning("MCP database disposal failed: %s", exc)


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
    """Intercept JSON-RPC ``initialize`` to detect client capabilities."""

    _last_activity_write: float = 0.0
    _ACTIVITY_WRITE_THROTTLE: float = 10.0
    _active_sse_streams: int = 0

    def __init__(self, app):
        self.app = app

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

    async def _handle_post(self, scope, receive, send):
        self._touch_activity()
        body_chunks: list[bytes] = []
        response_status: int = 0

        async def _buffered_receive():
            message = await receive()
            if message["type"] == "http.request":
                body_chunks.append(message.get("body", b""))
                if not message.get("more_body", False):
                    self._inspect_initialize(b"".join(body_chunks))
            return message

        async def _capture_send(message):
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message.get("status", 0)
            await send(message)

        await self.app(scope, _buffered_receive, _capture_send)

        if response_status in (400, 404):
            self._invalidate_stale_session()

    async def _handle_get(self, scope, receive, send):
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
                    if not has_session_id:
                        cls._write_optimistic_session()
            elif message["type"] == "http.response.body" and get_is_sse:
                cls._touch_activity()
            await send(message)

        try:
            await self.app(scope, receive, _capture_get_send)
        finally:
            if get_is_sse:
                cls._active_sse_streams = max(0, cls._active_sse_streams - 1)
                cls._flush_sse_streams()
                if cls._active_sse_streams == 0:
                    logger.info("Last SSE stream closed — client disconnected")
                    routing = _shared._routing
                    if routing:
                        routing.on_mcp_disconnect()

    @classmethod
    def _flush_sse_streams(cls) -> None:
        try:
            fields: dict = {"sse_streams": cls._active_sse_streams}
            if cls._active_sse_streams > 0:
                fields["last_activity"] = datetime.now(timezone.utc).isoformat()
                routing = _shared._routing
                if routing:
                    routing.on_mcp_activity()
            _session_file.update(**fields)
        except Exception:
            logger.debug("_flush_sse_streams: could not update mcp_session.json", exc_info=True)

    @classmethod
    def _write_optimistic_session(cls) -> None:
        try:
            routing = _shared._routing
            if routing:
                routing.on_mcp_initialize(sampling_capable=True)
            _session_file.write_session(True, sse_streams=cls._active_sse_streams)
            logger.info(
                "Optimistic session write: sampling_capable=True "
                "(session-less GET succeeded — client reconnecting)",
            )
        except Exception:
            logger.debug("Could not write optimistic mcp_session.json", exc_info=True)

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

    @classmethod
    def _touch_activity(cls) -> None:
        now_mono = time.monotonic()
        if now_mono - cls._last_activity_write < cls._ACTIVITY_WRITE_THROTTLE:
            return
        cls._last_activity_write = now_mono
        routing = _shared._routing
        if routing:
            routing.on_mcp_activity()
        else:
            try:
                data = _session_file.read()
                if data is not None:
                    data["last_activity"] = datetime.now(timezone.utc).isoformat()
                    data["sse_streams"] = cls._active_sse_streams
                    _session_file.write(data)
            except Exception:
                logger.debug("_touch_activity: could not update mcp_session.json", exc_info=True)

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

            routing = _shared._routing
            if routing:
                routing.on_mcp_initialize(sampling_capable=sampling)
            else:
                if not sampling and _session_file.should_skip_downgrade():
                    return
                _session_file.write_session(sampling)
        except Exception:
            logger.debug("Capability detection middleware: could not parse initialize", exc_info=True)


# Patch streamable_http_app to inject the capability-detection middleware.
_original_streamable_http_app = mcp.streamable_http_app


def _patched_streamable_http_app(**kwargs):
    app = _original_streamable_http_app(**kwargs)
    app.add_middleware(_CapabilityDetectionMiddleware)
    return app


mcp.streamable_http_app = _patched_streamable_http_app


# ---------------------------------------------------------------------------
# Tool registrations — thin wrappers delegating to app.tools.* handlers
# ---------------------------------------------------------------------------

# Import handlers
from app.tools.analyze import handle_analyze  # noqa: E402
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
    1. force_passthrough → assembled template for manual processing
    2. force_sampling + sampling capable → full pipeline via IDE's LLM
    3. Local provider → full internal pipeline
    4. No provider + MCP sampling → full pipeline via IDE's LLM
    5. Fallback → assembled template for manual processing

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
        "faithfulness, conciseness (0-10 float).",
    )] = None,
    model: Annotated[str | None, Field(
        default=None, description="Model ID that produced the optimization.",
    )] = None,
    codebase_context: Annotated[str | None, Field(
        default=None, description="IDE-provided codebase context snapshot to store alongside the result.",
    )] = None,
    domain: Annotated[str | None, Field(
        default=None,
        description="Domain category: 'backend', 'frontend', 'database', 'devops', "
        "'security', 'fullstack', or 'general'. Defaults to 'general' if not provided.",
    )] = None,
    intent_label: Annotated[str | None, Field(
        default=None, description="Short 3-6 word intent classification label. Defaults to 'general'.",
    )] = None,
    ctx: Context | None = None,
) -> SaveResultOutput:
    """Persist an optimization result from an external LLM with bias correction.

    This is step 3 of the passthrough workflow (after synthesis_prepare_optimization).
    Applies heuristic bias correction to self-rated scores and computes score deltas
    against the original prompt.

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
