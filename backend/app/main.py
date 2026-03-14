"""FastAPI application entry point for Project Synthesis.

Creates the FastAPI app with title="Project Synthesis API",
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

from app._version import __version__
from app.config import settings
from app.database import create_tables
from app.mcp_server import HAS_MCP, create_mcp_server, make_websocket_asgi
from app.providers.detector import ProviderNotAvailableError, detect_provider

# Import routers
from app.routers import github_auth, github_repos, health, history, optimize
from app.routers.auth import router as jwt_auth_router
from app.routers.feedback import router as feedback_router
from app.routers.framework import router as framework_router
from app.routers.github import router as github_router
from app.routers.github_config import router as github_config_router
from app.routers.onboarding import router as onboarding_router
from app.routers.provider_config import router as provider_config_router
from app.routers.providers import router as providers_router
from app.routers.refinement import router as refinement_router
from app.routers.settings import router as settings_router
from app.services.cleanup import cleanup_loop
from app.services.github_credentials_service import load_credentials_from_file

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
    - Connects to Redis (graceful degradation if unavailable).
    - Initializes rate limiter and cache service.
    - Detects the best available LLM provider.
    - Injects the provider into routers that need it.
    - Mounts MCP server (streamable-HTTP + WebSocket) if mcp is installed.

    On shutdown:
    - Closes Redis connection.
    - Performs any necessary cleanup.
    """
    global _mcp_http_app, _mcp_ws_asgi

    logger.info("Project Synthesis starting up...")

    # Create database tables
    await create_tables()
    logger.info("Database tables ready")

    # Initialize Redis (graceful degradation)
    from app.services.redis_service import RedisService

    redis_svc = RedisService(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
        password=settings.REDIS_PASSWORD,
    )
    connected = await redis_svc.connect()
    if not connected:
        logger.critical("Redis unavailable — falling back to in-memory rate limiting and caching")
    app.state.redis = redis_svc

    # Initialize rate limiter (uses Redis if available, else in-memory)
    from app.dependencies.rate_limit import init_rate_limiter

    await init_rate_limiter(redis_svc)

    # Initialize cache service
    from app.services.cache_service import init_cache

    app.state.cache = init_cache(redis_svc)

    # Load persisted GitHub App credentials (hot-reload before first request)
    load_credentials_from_file()

    # Load persisted API key (if any) before provider detection
    from app.services.api_credentials_service import load_api_key_from_file

    load_api_key_from_file()

    # Detect LLM provider — graceful degradation if none available.
    # The pipeline returns 503 when provider is None (optimize.py:58),
    # health reports provider: "none", and providers router shows "unavailable".
    try:
        provider = await detect_provider()
        logger.info("LLM Provider: %s", provider.name)
    except ProviderNotAvailableError:
        logger.warning(
            "No LLM provider available — pipeline disabled until "
            "an API key is configured via UI or environment variable"
        )
        provider = None

    # Store provider on app state for access elsewhere
    app.state.provider = provider

    # Start background cleanup task
    cleanup_task = asyncio.create_task(cleanup_loop())
    app.state.cleanup_task = cleanup_task
    logger.info("Background cleanup task started")

    # B4: Always mount MCP when the package is available — tools resolve the
    # provider dynamically so hot-reloaded keys are picked up immediately.
    if HAS_MCP:
        mcp = create_mcp_server(
            provider_getter=lambda: getattr(app.state, "provider", None),
        )
        _mcp_http_app = mcp.streamable_http_app()
        _mcp_ws_asgi = make_websocket_asgi(mcp)
        app.state.mcp = mcp
        logger.info("MCP server mounted at /mcp (streamable-HTTP) and /mcp/ws (WebSocket)")
        if settings.MCP_HOST not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "SECURITY: MCP_HOST=%s — MCP WebSocket endpoint bypasses auth/CORS middleware. "
                "Bind to 127.0.0.1 or add authentication to the WebSocket handshake.",
                settings.MCP_HOST,
            )
        async with mcp.session_manager.run():
            logger.info("Project Synthesis ready")
            yield
    else:
        logger.info(
            "Project Synthesis ready (MCP not available — "
            "install fastmcp to enable)"
        )
        yield

    # Shutdown
    await redis_svc.close()
    logger.info("Redis connection closed")

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
    version=__version__,
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

# Session cache middleware (mirrors session data to Redis for server-side visibility).
# Registered before SessionMiddleware so it runs AFTER it in the ASGI stack
# (Starlette middleware wraps in reverse registration order).
from app.middleware.session_cache import SessionCacheMiddleware  # noqa: E402

app.add_middleware(SessionCacheMiddleware)

# Session middleware (required for GitHub OAuth CSRF state)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="synthesis_session",
    max_age=86400 * 7,  # 7 days
    https_only=settings.JWT_COOKIE_SECURE,
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
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept", "X-Requested-With"],
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
app.include_router(github_config_router)
app.include_router(provider_config_router)
app.include_router(feedback_router)
app.include_router(framework_router)
app.include_router(onboarding_router)
app.include_router(refinement_router)

if settings.TESTING:
    from app.routers.test_helpers import router as test_helpers_router
    app.include_router(test_helpers_router)
    logger.warning("TESTING mode: test-helpers router mounted — never use in production")


# ── Error Handlers ────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Return JSON for unhandled exceptions instead of HTML stack traces."""
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal server error",
            "detail": "An unexpected error occurred.",
            "path": str(request.url.path),
        },
    )


@app.get("/")
async def root():
    """Root endpoint returning API info."""
    return {
        "app": "Project Synthesis API",
        "version": __version__,
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
