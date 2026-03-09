"""FastAPI application entry point for Project Synthesis.

Creates the FastAPI app with title="Project Synthesis API", version="2.0.0",
CORS middleware allowing http://localhost:5199, includes all routers
with /api prefix, lifespan handler that initializes database on startup,
and mounts /api/docs for Swagger UI.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import create_tables
from app.mcp_server import HAS_MCP, create_mcp_server, make_websocket_asgi
from app.services.cleanup import cleanup_loop
from app.providers.detector import ProviderNotAvailableError, detect_provider

# Import routers
from app.routers import github_auth, github_repos, health, history, optimize
from app.routers.auth import router as jwt_auth_router
from app.routers.github import router as github_router
from app.routers.providers import router as providers_router
from app.routers.providers import set_provider as providers_set_provider
from app.routers.settings import router as settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Lazy MCP ASGI wrappers ─────────────────────────────────────────────────
# Registered at module level (app.mount / app.add_websocket_route require the
# app to exist first), populated in lifespan after provider detection.

_mcp_http_app = None
_mcp_ws_asgi = None


class _LazyMCPHttpApp:
    """Defers to the real streamable-HTTP ASGI app once it is ready.

    Returns 503 for requests that arrive before MCP is initialized or when
    the mcp package is not installed, rather than silently hanging the client.
    """

    async def __call__(self, scope, receive, send):
        if _mcp_http_app is not None:
            await _mcp_http_app(scope, receive, send)
            return
        if scope["type"] == "http":
            await receive()  # consume http.request
            body = b'{"error":"MCP server not available"}'
            await send({
                "type": "http.response.start",
                "status": 503,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})


class _LazyMCPWSApp:
    """Defers to the real WebSocket ASGI callable once it is ready.

    Closes the WebSocket with code 1013 (try again later) when MCP is not yet
    initialized, rather than silently hanging the client.

    NOTE: This is NOT registered via app.add_websocket_route() because that
    routes through CORSMiddleware, which rejects WebSocket upgrades from
    Claude Code's Electron origin with HTTP 403. Instead it is wired in
    _SynthesisASGI below, which sits outside the middleware stack entirely.
    """

    async def __call__(self, scope, receive, send):
        if _mcp_ws_asgi is not None and scope["type"] == "websocket":
            await _mcp_ws_asgi(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await receive()  # consume websocket.connect
            await send({"type": "websocket.close", "code": 1013})  # try again later


# Module-level instance used by _SynthesisASGI before the FastAPI app is built.
_lazy_mcp_ws_app = _LazyMCPWSApp()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown.

    On startup:
    - Creates all database tables (acts as simple migration).
    - Detects the best available LLM provider.
    - Injects the provider into routers that need it.
    - Mounts MCP server (streamable-HTTP + WebSocket) if mcp is installed.

    On shutdown:
    - Performs any necessary cleanup.
    """
    global _mcp_http_app, _mcp_ws_asgi

    logger.info("Project Synthesis starting up...")

    # Create database tables
    await create_tables()
    logger.info("Database tables ready")

    # Detect LLM provider (raises ProviderNotAvailableError if none found)
    try:
        provider = await detect_provider()
    except ProviderNotAvailableError as e:
        logger.error("LLM Provider detection failed:\n%s", e)
        raise
    logger.info("LLM Provider: %s", provider.name)

    # Wire up provider to routers that need it
    health.set_provider(provider)
    optimize.set_provider(provider)
    providers_set_provider(provider)

    # Store provider on app state for access elsewhere
    app.state.provider = provider

    # Start background cleanup task
    cleanup_task = asyncio.create_task(cleanup_loop())
    app.state.cleanup_task = cleanup_task
    logger.info("Background cleanup task started")

    # B2: Mount MCP server — provider injected so tools never call detect_provider()
    if HAS_MCP:
        mcp = create_mcp_server(provider)
        _mcp_http_app = mcp.streamable_http_app()
        _mcp_ws_asgi = make_websocket_asgi(mcp)
        app.state.mcp = mcp
        logger.info("MCP server mounted at /mcp (streamable-HTTP) and /mcp/ws (WebSocket)")
        async with mcp.session_manager.run():
            logger.info("Project Synthesis ready")
            yield
    else:
        logger.info("Project Synthesis ready (MCP not available)")
        yield

    # Shutdown
    cleanup_task = getattr(app.state, "cleanup_task", None)
    if cleanup_task and not cleanup_task.done():
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass
        logger.info("Cleanup task stopped")
    logger.info("Project Synthesis shutting down...")


app = FastAPI(
    title="Project Synthesis API",
    version="2.0.0",
    description="Multi-Agent Development Platform",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# Streamable HTTP MCP — mounted on FastAPI so it shares the session_manager lifespan.
# HTTP clients (Claude SDK, curl) do not send an Origin header, so CORSMiddleware
# does not interfere.
app.mount("/mcp", _LazyMCPHttpApp())

# ── Middleware ─────────────────────────────────────────────────────────

# Session middleware (required for GitHub OAuth CSRF state)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="synthesis_session",
    max_age=86400 * 7,  # 7 days
)

# CORS middleware
cors_origins = [
    origin.strip()
    for origin in settings.CORS_ORIGINS.split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────

# Existing routers
app.include_router(health.router)
app.include_router(optimize.router)
app.include_router(history.router)
app.include_router(github_auth.router)
app.include_router(github_repos.router)

# New routers
app.include_router(providers_router)
app.include_router(github_router)
app.include_router(settings_router)
app.include_router(jwt_auth_router)


# ── Error Handlers ────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return JSON for unhandled exceptions instead of HTML stack traces."""
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": str(exc),
            "path": str(request.url.path),
        },
    )


@app.get("/")
async def root():
    """Root endpoint returning API info."""
    return {
        "app": "Project Synthesis API",
        "version": "2.0.0",
        "docs": "/api/docs",
    }


# ── Outer ASGI wrapper ─────────────────────────────────────────────────────
# WebSocket MCP connections from Claude Code (Electron) are rejected by
# CORSMiddleware with HTTP 403 because Electron sends a non-whitelisted Origin.
# Routing /mcp/ws here — outside the FastAPI middleware stack — bypasses CORS
# entirely. All other requests fall through to FastAPI as normal.

class _SynthesisASGI:
    """Top-level ASGI app: intercepts /mcp/ws WebSocket before FastAPI middleware."""

    def __init__(self, fastapi_app):
        self._app = fastapi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket" and scope.get("path") == "/mcp/ws":
            await _lazy_mcp_ws_app(scope, receive, send)
        else:
            await self._app(scope, receive, send)


# uvicorn is pointed at this module-level name in init.sh: app.main:asgi_app
asgi_app = _SynthesisASGI(app)
