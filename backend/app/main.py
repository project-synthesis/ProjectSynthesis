"""FastAPI application entry point for PromptForge v2.

Creates the FastAPI app with title="PromptForge API", version="2.0.0",
CORS middleware allowing http://localhost:5199, includes all routers
with /api prefix, lifespan handler that initializes database on startup,
and mounts /api/docs for Swagger UI.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.database import create_tables
from app.providers.detector import detect_provider, ProviderNotAvailableError

# Import routers
from app.routers import health, optimize, history, github_auth, github_repos
from app.routers.optimizations import router as optimizations_router, set_provider as optimizations_set_provider
from app.routers.providers import router as providers_router, set_provider as providers_set_provider
from app.routers.github import router as github_router
from app.routers.settings import router as settings_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown.

    On startup:
    - Creates all database tables (acts as simple migration).
    - Detects the best available LLM provider.
    - Injects the provider into routers that need it.

    On shutdown:
    - Performs any necessary cleanup.
    """
    logger.info("PromptForge v2 starting up...")

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
    optimizations_set_provider(provider)
    providers_set_provider(provider)

    # Store provider on app state for access elsewhere
    app.state.provider = provider

    logger.info("PromptForge v2 ready")
    yield

    # Shutdown
    logger.info("PromptForge v2 shutting down...")


app = FastAPI(
    title="PromptForge API",
    version="2.0.0",
    description="Intelligent Prompt Optimization Engine",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── Middleware ─────────────────────────────────────────────────────────

# Session middleware (required for GitHub OAuth CSRF state)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    session_cookie="promptforge_session",
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
app.include_router(optimizations_router)
app.include_router(providers_router)
app.include_router(github_router)
app.include_router(settings_router)


@app.get("/")
async def root():
    """Root endpoint returning API info."""
    return {
        "app": "PromptForge API",
        "version": "2.0.0",
        "docs": "/api/docs",
    }
