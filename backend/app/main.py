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

    # Clean up orphaned strategy affinities (strategies deleted from disk)
    try:
        from app.database import async_session_factory
        from app.services.adaptation_tracker import AdaptationTracker
        async with async_session_factory() as db:
            tracker = AdaptationTracker(db)
            await tracker.cleanup_orphaned_affinities()
    except Exception as exc:
        logger.debug("Strategy affinity cleanup skipped: %s", exc)

    # Start strategy file watcher
    watcher_task = asyncio.create_task(
        watch_strategy_files(PROMPTS_DIR / "strategies")
    )
    app.state.watcher_task = watcher_task

    # Track in-flight extraction tasks for graceful shutdown
    extraction_tasks: set[asyncio.Task[None]] = set()

    # Shared EmbeddingService singleton — reused by taxonomy engine and context service
    from app.services.embedding_service import EmbeddingService
    _shared_embedding_service = EmbeddingService()

    # Start taxonomy engine subscriber (replaces PatternExtractorService)
    async def _taxonomy_extraction_listener():
        """Subscribe to optimization_created events and run taxonomy hot path."""
        try:
            from app.database import async_session_factory
            from app.services.taxonomy import TaxonomyEngine, set_engine

            engine = TaxonomyEngine(
                embedding_service=_shared_embedding_service,
                provider_resolver=lambda: app.state.routing.state.provider,
            )
            app.state.taxonomy_engine = engine
            set_engine(engine)

            # Warm-load embedding index from active cluster centroids
            try:
                import numpy as _np
                from sqlalchemy import select as _select

                from app.models import PromptCluster

                async with async_session_factory() as _db:
                    _clusters = (
                        await _db.execute(
                            _select(PromptCluster).where(
                                PromptCluster.state != "archived"
                            )
                        )
                    ).scalars().all()
                    _centroids: dict[str, _np.ndarray] = {}
                    for _c in _clusters:
                        if _c.centroid_embedding:
                            try:
                                _emb = _np.frombuffer(
                                    _c.centroid_embedding, dtype=_np.float32
                                )
                                if _emb.shape[0] == 384:
                                    _centroids[_c.id] = _emb
                            except (ValueError, TypeError):
                                continue
                    await engine.embedding_index.rebuild(_centroids)
                    logger.info(
                        "EmbeddingIndex warm-loaded: %d centroids",
                        len(_centroids),
                    )
            except Exception as idx_exc:
                logger.warning(
                    "EmbeddingIndex warm-load failed (non-fatal): %s", idx_exc
                )

            # Startup: backfill orphan optimizations with null cluster_id
            try:
                from app.services.prompt_lifecycle import PromptLifecycleService
                async with async_session_factory() as _db:
                    lifecycle = PromptLifecycleService()
                    orphans_linked = await lifecycle.backfill_orphans(
                        _db, engine.embedding_index
                    )
                    await _db.commit()
                    logger.info(
                        "Backfill: %d orphan optimizations linked", orphans_linked
                    )
            except Exception as backfill_exc:
                logger.warning(
                    "Orphan backfill failed (non-fatal): %s", backfill_exc
                )

            logger.info("Taxonomy extraction listener started — subscribing to event bus")

            async for event in event_bus.subscribe():
                if event.get("event") == "optimization_created":
                    opt_id = event.get("data", {}).get("id")
                    if opt_id:
                        logger.info(
                            "Dispatching taxonomy extraction for optimization %s",
                            opt_id,
                        )

                        async def _run_extraction(oid: str) -> None:
                            try:
                                async with async_session_factory() as db:
                                    await engine.process_optimization(oid, db)
                                    # Hot path: check cluster promotion after
                                    # process_optimization writes OptimizationPattern
                                    from sqlalchemy import select as _sel

                                    from app.models import OptimizationPattern as _OptPat
                                    from app.services.prompt_lifecycle import (
                                        PromptLifecycleService,
                                    )
                                    _row = (await db.execute(
                                        _sel(_OptPat).where(
                                            _OptPat.optimization_id == oid,
                                            _OptPat.relationship == "source",
                                        )
                                    )).scalar_one_or_none()
                                    if _row is not None and _row.cluster_id:
                                        lifecycle = PromptLifecycleService()
                                        await lifecycle.check_promotion(
                                            db, _row.cluster_id
                                        )
                                        await lifecycle.update_strategy_affinity(
                                            db, _row.cluster_id
                                        )
                                        await db.commit()
                            except Exception as task_exc:
                                logger.error(
                                    "Background taxonomy extraction failed for %s: %s",
                                    oid, task_exc, exc_info=True,
                                )

                        task = asyncio.create_task(
                            _run_extraction(opt_id),
                            name=f"taxonomy-extract-{opt_id}",
                        )
                        extraction_tasks.add(task)
                        task.add_done_callback(extraction_tasks.discard)
                    else:
                        logger.warning(
                            "optimization_created event missing 'id' in data: %s",
                            event.get("data"),
                        )
        except asyncio.CancelledError:
            logger.info("Taxonomy extraction listener shutting down")
        except Exception as exc:
            logger.error("Taxonomy extraction listener crashed: %s", exc, exc_info=True)

    extraction_task = asyncio.create_task(_taxonomy_extraction_listener())
    app.state.extraction_task = extraction_task

    # Initialize unified context enrichment service
    try:
        from app.services.context_enrichment import ContextEnrichmentService
        from app.services.github_client import GitHubClient
        from app.services.heuristic_analyzer import HeuristicAnalyzer
        from app.services.workspace_intelligence import WorkspaceIntelligence

        app.state.context_service = ContextEnrichmentService(
            prompts_dir=PROMPTS_DIR,
            data_dir=DATA_DIR,
            workspace_intel=WorkspaceIntelligence(),
            embedding_service=_shared_embedding_service,
            heuristic_analyzer=HeuristicAnalyzer(),
            github_client=GitHubClient(),
            taxonomy_engine=getattr(app.state, "taxonomy_engine", None),
        )
        logger.info("ContextEnrichmentService initialized")
    except Exception as exc:
        logger.error(
            "ContextEnrichmentService init failed — passthrough and pattern "
            "resolution will be unavailable: %s", exc,
        )
        app.state.context_service = None

    # Start warm-path periodic timer (Spec Section 6.4 — every 5 minutes)
    async def _warm_path_timer():
        """Periodically trigger warm-path re-clustering."""
        try:
            await asyncio.sleep(60)  # Initial delay — let system stabilize
            while True:
                await asyncio.sleep(300)  # 5-minute interval
                try:
                    engine = getattr(app.state, "taxonomy_engine", None)
                    if engine:
                        from app.database import async_session_factory
                        from app.services.prompt_lifecycle import (
                            PromptLifecycleService,
                        )
                        async with async_session_factory() as db:
                            result = await engine.run_warm_path(db)
                            if result:
                                logger.info(
                                    "Warm path completed: q=%.4f ops=%d/%d snapshot=%s",
                                    result.q_system or 0.0,
                                    result.operations_accepted,
                                    result.operations_attempted,
                                    result.snapshot_id,
                                )
                            # Warm path: curation + usage decay after clustering
                            lifecycle = PromptLifecycleService()
                            await lifecycle.curate(db)
                            await lifecycle.decay_usage(db)
                            await db.commit()
                except Exception as exc:
                    logger.error("Warm path timer failed: %s", exc, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Warm path timer shutting down")

    warm_path_task = asyncio.create_task(_warm_path_timer())
    app.state.warm_path_task = warm_path_task

    yield

    # ── Shutdown (5-phase concurrent approach) ─────────────────────────

    # Phase 1: Drain SSE connections.  The sentinel unblocks all SSE
    # subscriber queues so persistent streams close *before* uvicorn's
    # graceful-shutdown timer kicks in (~0ms instead of waiting 3-5s).
    event_bus.shutdown()
    await asyncio.sleep(0.1)  # yield control so SSE generators return

    # Phase 2: Cancel all background tasks concurrently via gather().
    # Previous code awaited each sequentially (4 × cancel+await blocks).
    bg_tasks = [
        t for t in [
            getattr(app.state, "extraction_task", None),
            getattr(app.state, "warm_path_task", None),
            getattr(app.state, "watcher_task", None),
        ]
        if t is not None
    ]
    if hasattr(app.state, "routing"):
        await app.state.routing.stop()
    for t in bg_tasks:
        t.cancel()
    if bg_tasks:
        await asyncio.gather(*bg_tasks, return_exceptions=True)

    # Phase 3: Drain in-flight extraction tasks (may be mid-DB-write).
    pending = list(extraction_tasks)
    if pending:
        logger.info("Cancelling %d in-flight extraction tasks", len(pending))
        for t in pending:
            t.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            logger.warning("Timed out waiting for extraction tasks to finish")

    # Phase 4: Mark in-flight optimizations as interrupted + trace rotation.
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

    try:
        from app.services.trace_logger import TraceLogger
        tl = TraceLogger(DATA_DIR / "traces")
        deleted = tl.rotate(retention_days=settings.TRACE_RETENTION_DAYS)
        if deleted:
            logger.info("Trace rotation: deleted %d old files", deleted)
    except Exception as exc:
        logger.error("Trace rotation failed: %s", exc)

    # Phase 5: Clear taxonomy singleton + dispose database engine.
    try:
        from app.services.taxonomy import reset_engine
        reset_engine()
    except Exception:
        pass
    app.state.taxonomy_engine = None

    try:
        from app.database import dispose
        await dispose()
    except Exception as exc:
        logger.error("Database disposal failed: %s", exc)


app = FastAPI(
    title="Project Synthesis",
    version=__version__,
    lifespan=lifespan,
)

_cors_origins = [settings.FRONTEND_URL]
if settings.DEVELOPMENT_MODE and "http://localhost:5199" not in _cors_origins:
    _cors_origins.append("http://localhost:5199")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Cache-Control"],
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
    from app.routers.clusters import router as clusters_router
    app.include_router(clusters_router)
except ImportError:
    pass

asgi_app = app
