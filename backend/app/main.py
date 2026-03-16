"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app._version import __version__
from app.config import settings, DATA_DIR, PROMPTS_DIR

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    settings.SECRET_KEY = settings.resolve_secret_key()

    db_path = DATA_DIR / "synthesis.db"
    if db_path.exists():
        async with aiosqlite.connect(str(db_path)) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=5000")

    # Detect LLM provider
    try:
        from app.providers.detector import detect_provider
        provider = detect_provider()
    except ImportError:
        provider = None
    app.state.provider = provider
    logger.info("Provider detected: %s", provider.name if provider else "none")

    # Validate prompt templates at startup
    from app.services.prompt_loader import PromptLoader
    from app.services.strategy_loader import StrategyLoader
    try:
        loader = PromptLoader(PROMPTS_DIR)
        loader.validate_all()
        StrategyLoader(PROMPTS_DIR / "strategies").validate()
    except RuntimeError as exc:
        logger.error("Template validation failed: %s", exc)
        # Don't prevent startup — log error but continue
        # (templates might be updated before first request)

    yield

    # Shutdown: mark in-flight optimizations as interrupted
    logger.info("Shutting down — marking in-flight optimizations as interrupted")
    try:
        from app.database import async_session_factory
        from app.models import Optimization
        from sqlalchemy import update
        async with async_session_factory() as db:
            await db.execute(
                update(Optimization)
                .where(Optimization.status == "running")
                .values(status="interrupted")
            )
            await db.commit()
    except Exception as exc:
        logger.error("Shutdown cleanup failed: %s", exc)

    # Run trace rotation
    try:
        from app.services.trace_logger import TraceLogger
        tl = TraceLogger(DATA_DIR / "traces")
        deleted = tl.rotate(retention_days=settings.TRACE_RETENTION_DAYS)
        if deleted:
            logger.info("Trace rotation: deleted %d old files", deleted)
    except Exception as exc:
        logger.error("Trace rotation failed: %s", exc)


app = FastAPI(
    title="Project Synthesis",
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:5199"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers (imported lazily — may not exist yet during phased development)
try:
    from app.routers.health import router as health_router
    app.include_router(health_router)
except ImportError:
    pass

try:
    from app.routers.optimize import router as optimize_router
    app.include_router(optimize_router)
except ImportError:
    pass

try:
    from app.routers.history import router as history_router
    app.include_router(history_router)
except ImportError:
    pass

try:
    from app.routers.feedback import router as feedback_router
    app.include_router(feedback_router)
except ImportError:
    pass

try:
    from app.routers.providers import router as providers_router
    app.include_router(providers_router)
except ImportError:
    pass

try:
    from app.routers.settings import router as settings_router
    app.include_router(settings_router)
except ImportError:
    pass

try:
    from app.routers.github_auth import router as github_auth_router
    app.include_router(github_auth_router)
except ImportError:
    pass

try:
    from app.routers.github_repos import router as github_repos_router
    app.include_router(github_repos_router)
except ImportError:
    pass

try:
    from app.routers.refinement import router as refinement_router
    app.include_router(refinement_router)
except ImportError:
    pass

asgi_app = app
