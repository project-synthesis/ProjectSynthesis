"""FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

import aiosqlite
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app._version import __version__
from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.services.event_bus import event_bus
from app.services.file_watcher import watch_strategy_files

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
        logger.info("SQLite WAL mode enabled for %s", db_path)

    # Initialize routing service
    from app.providers.detector import detect_provider
    from app.services.routing import RoutingManager

    routing = RoutingManager(event_bus=event_bus, data_dir=DATA_DIR)
    try:
        provider = detect_provider()
    except Exception as exc:
        logger.warning("Provider detection failed: %s — starting without provider", exc)
        provider = None
    routing.set_provider(provider)
    app.state.routing = routing
    logger.info(
        "Routing initialized: provider=%s available_tiers=%s",
        routing.state.provider_name or "none",
        routing.available_tiers,
    )

    # Start background disconnect checker
    await routing.start_disconnect_checker()

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

    # Start strategy file watcher
    watcher_task = asyncio.create_task(
        watch_strategy_files(PROMPTS_DIR / "strategies")
    )
    app.state.watcher_task = watcher_task

    # Start pattern extraction subscriber
    async def _pattern_extraction_listener():
        """Subscribe to optimization_created events and extract patterns."""
        try:
            from app.services.pattern_extractor import PatternExtractorService
            extractor = PatternExtractorService(provider=app.state.routing.state.provider)
            logger.info("Pattern extraction listener started — subscribing to event bus")
            async for event in event_bus.subscribe():
                if event.get("event") == "optimization_created":
                    opt_id = event.get("data", {}).get("id")
                    if opt_id:
                        logger.info(
                            "Dispatching pattern extraction for optimization %s",
                            opt_id,
                        )

                        async def _run_extraction(oid: str) -> None:
                            """Wrapper to catch and log fire-and-forget task errors."""
                            try:
                                await extractor.process(oid)
                            except Exception as task_exc:
                                logger.error(
                                    "Background pattern extraction task failed for %s: %s",
                                    oid, task_exc, exc_info=True,
                                )

                        asyncio.create_task(
                            _run_extraction(opt_id),
                            name=f"pattern-extract-{opt_id}",
                        )
                    else:
                        logger.warning(
                            "optimization_created event missing 'id' in data: %s",
                            event.get("data"),
                        )
        except asyncio.CancelledError:
            logger.info("Pattern extraction listener shutting down")
        except Exception as exc:
            logger.error("Pattern extraction listener crashed: %s", exc, exc_info=True)

    extraction_task = asyncio.create_task(_pattern_extraction_listener())
    app.state.extraction_task = extraction_task

    yield

    # Stop routing disconnect checker
    if hasattr(app.state, "routing"):
        await app.state.routing.stop()

    # Stop pattern extraction listener
    if hasattr(app.state, "extraction_task"):
        app.state.extraction_task.cancel()
        try:
            await app.state.extraction_task
        except asyncio.CancelledError:
            pass

    # Stop strategy file watcher
    if hasattr(app.state, "watcher_task"):
        app.state.watcher_task.cancel()
        try:
            await app.state.watcher_task
        except asyncio.CancelledError:
            pass

    # Shutdown: mark in-flight optimizations as interrupted
    logger.info("Shutting down — marking in-flight optimizations as interrupted")
    try:
        from sqlalchemy import update

        from app.database import async_session_factory
        from app.models import Optimization
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

try:
    from app.routers.events import router as events_router
    app.include_router(events_router)
except ImportError:
    pass

try:
    from app.routers.preferences import router as preferences_router
    app.include_router(preferences_router)
except ImportError:
    pass

try:
    from app.routers.strategies import router as strategies_router
    app.include_router(strategies_router)
except ImportError:
    pass

try:
    from app.routers.patterns import router as patterns_router
    app.include_router(patterns_router)
except ImportError:
    pass

asgi_app = app
