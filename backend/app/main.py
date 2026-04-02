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

# Configure root logger so app.services.* INFO messages reach stderr/log file.
# Uvicorn sets up its own loggers but doesn't propagate to third-party loggers.
# Must be called after imports but before any logger.getLogger() usage.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

logger = logging.getLogger(__name__)

# Module-level event: set to trigger an early warm-path run (e.g. after
# a new optimization is clustered).  The warm-path timer awaits this event
# with a timeout equal to the configured interval, so it runs either on
# schedule or immediately when signaled.
_warm_path_pending = asyncio.Event()


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

            # Initialize domain services
            from app.services.domain_resolver import (
                DomainResolver,
                set_domain_resolver,
            )
            from app.services.domain_signal_loader import DomainSignalLoader

            domain_resolver = DomainResolver()
            signal_loader = DomainSignalLoader()
            async with async_session_factory() as _init_db:
                await domain_resolver.load(_init_db)
                await signal_loader.load(_init_db)
            app.state.domain_resolver = domain_resolver
            app.state.signal_loader = signal_loader
            set_domain_resolver(domain_resolver)

            # Wire signal loader into heuristic analyzer for dynamic domain signals
            from app.services.heuristic_analyzer import set_signal_loader as set_analyzer_signal_loader
            set_analyzer_signal_loader(signal_loader)

            logger.info("Domain services initialized")

            # Warm-load embedding index from disk cache or active cluster centroids
            _index_cache_path = DATA_DIR / "embedding_index.pkl"
            try:
                _cache_loaded = await engine.embedding_index.load_cache(_index_cache_path)

                # Validate cache size: if the cache has far fewer entries
                # than active clusters, it's stale and needs a full rebuild.
                # A stale cache causes the orphan backfill to miss matches.
                if _cache_loaded:
                    from sqlalchemy import func as _func_check
                    from sqlalchemy import select as _sel_check

                    from app.models import PromptCluster as _PC_check

                    async with async_session_factory() as _check_db:
                        _active_count = (await _check_db.execute(
                            _sel_check(_func_check.count()).where(
                                _PC_check.state != "archived",
                                _PC_check.centroid_embedding.isnot(None),
                            )
                        )).scalar() or 0
                    if engine.embedding_index.size < _active_count * 0.5:
                        logger.info(
                            "Embedding index cache stale (%d entries, %d active clusters) — rebuilding",
                            engine.embedding_index.size, _active_count,
                        )
                        _cache_loaded = False  # Force rebuild below

                if not _cache_loaded:
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
                        await engine.embedding_index.save_cache(_index_cache_path)
                        logger.info(
                            "EmbeddingIndex warm-loaded: %d centroids",
                            len(_centroids),
                        )
            except Exception as idx_exc:
                logger.warning(
                    "EmbeddingIndex warm-load failed (non-fatal): %s", idx_exc
                )

            # Startup: ensure routing_tier column exists (SQLite ALTER TABLE)
            # SQLAlchemy create_all() only creates new tables, not new columns
            # on existing tables. This is idempotent — duplicate ADD COLUMN
            # raises OperationalError which we catch and ignore.
            try:
                from sqlalchemy import text as _text_rt
                async with async_session_factory() as _alt_db:
                    await _alt_db.execute(
                        _text_rt("ALTER TABLE optimizations ADD COLUMN routing_tier VARCHAR")
                    )
                    await _alt_db.commit()
                    logger.info("Added routing_tier column to optimizations table")
            except Exception:
                pass  # Column already exists — expected on subsequent startups

            # Startup: backfill routing_tier on legacy records (idempotent)
            try:
                from sqlalchemy import update as _upd_rt

                from app.models import Optimization as _Opt_rt

                async with async_session_factory() as _rt_db:
                    await _rt_db.execute(
                        _upd_rt(_Opt_rt)
                        .where(_Opt_rt.routing_tier.is_(None), _Opt_rt.provider == "mcp_sampling")
                        .values(routing_tier="sampling")
                    )
                    await _rt_db.execute(
                        _upd_rt(_Opt_rt)
                        .where(_Opt_rt.routing_tier.is_(None), _Opt_rt.provider.like("%passthrough%"))
                        .values(routing_tier="passthrough")
                    )
                    await _rt_db.execute(
                        _upd_rt(_Opt_rt)
                        .where(_Opt_rt.routing_tier.is_(None))
                        .values(routing_tier="internal")
                    )
                    await _rt_db.commit()
                    logger.info("Routing tier backfill complete")
            except Exception as rt_exc:
                logger.warning("Routing tier backfill failed (non-fatal): %s", rt_exc)

            # Startup: ensure global_source_count column exists on meta_patterns
            try:
                async with async_session_factory() as _gsc_db:
                    from sqlalchemy import text as _text_gsc
                    await _gsc_db.execute(
                        _text_gsc("ALTER TABLE meta_patterns ADD COLUMN global_source_count INTEGER NOT NULL DEFAULT 0")
                    )
                    await _gsc_db.commit()
                    logger.info("Added global_source_count column to meta_patterns table")
            except Exception:
                pass  # Column already exists

            # Startup: ensure optimized_embedding + transformation_embedding columns exist
            try:
                async with async_session_factory() as _emb_db:
                    await _emb_db.execute(
                        _text_gsc("ALTER TABLE optimizations ADD COLUMN optimized_embedding BLOB")
                    )
                    await _emb_db.commit()
            except Exception:
                pass
            try:
                async with async_session_factory() as _emb_db2:
                    await _emb_db2.execute(
                        _text_gsc("ALTER TABLE optimizations ADD COLUMN transformation_embedding BLOB")
                    )
                    await _emb_db2.commit()
            except Exception:
                pass
            # Startup: ensure weighted_member_sum column exists on prompt_cluster
            try:
                async with async_session_factory() as _wms_db:
                    await _wms_db.execute(
                        _text_gsc("ALTER TABLE prompt_cluster ADD COLUMN weighted_member_sum REAL NOT NULL DEFAULT 0.0")
                    )
                    await _wms_db.commit()
            except Exception:
                pass

            # One-time backfill: embed optimized_prompt + transformation for existing rows
            import numpy as np
            from sqlalchemy import select as _bf_select

            from app.models import Optimization as _bf_Opt

            _backfill_marker = DATA_DIR / ".embedding_backfill_done"
            if not _backfill_marker.exists():
                try:
                    async with async_session_factory() as _bf_db:
                        from sqlalchemy import func as _bf_func
                        count = (await _bf_db.execute(
                            _bf_select(_bf_func.count()).where(
                                _bf_Opt.embedding.isnot(None),
                                _bf_Opt.optimized_embedding.is_(None),
                                _bf_Opt.optimized_prompt.isnot(None),
                            )
                        )).scalar() or 0

                        if count > 0:
                            logger.info("Backfilling %d optimized embeddings...", count)
                            rows = (await _bf_db.execute(
                                _bf_select(_bf_Opt).where(
                                    _bf_Opt.embedding.isnot(None),
                                    _bf_Opt.optimized_embedding.is_(None),
                                    _bf_Opt.optimized_prompt.isnot(None),
                                )
                            )).scalars().all()

                            # Batch embed for efficiency (errata E1-4)
                            texts = [opt.optimized_prompt for opt in rows]
                            if texts:
                                embeddings = await _shared_embedding_service.aembed_texts(texts)
                                for opt, opt_emb in zip(rows, embeddings):
                                    try:
                                        raw_emb = np.frombuffer(opt.embedding, dtype=np.float32)
                                        opt.optimized_embedding = opt_emb.astype(np.float32).tobytes()
                                        transform = opt_emb - raw_emb
                                        t_norm = np.linalg.norm(transform)
                                        if t_norm > 1e-9:
                                            transform = transform / t_norm
                                        opt.transformation_embedding = transform.astype(np.float32).tobytes()
                                    except Exception:
                                        continue
                            await _bf_db.commit()
                            logger.info("Backfill complete: %d rows processed", count)

                    _backfill_marker.touch()
                except Exception as bf_exc:
                    logger.warning("Embedding backfill failed (non-fatal): %s", bf_exc)

            # Build transformation index from cluster mean transformation vectors
            try:
                async with async_session_factory() as _ti_db:
                    from sqlalchemy import func as _ti_func
                    # Find clusters with transformation data
                    ti_q = await _ti_db.execute(
                        _bf_select(
                            _bf_Opt.cluster_id,
                            _ti_func.count().label("ct"),
                        ).where(
                            _bf_Opt.cluster_id.isnot(None),
                            _bf_Opt.transformation_embedding.isnot(None),
                        ).group_by(_bf_Opt.cluster_id)
                    )
                    cluster_ids_with_transforms = [row[0] for row in ti_q.all() if row[1] >= 1]

                    transform_vectors: dict[str, np.ndarray] = {}
                    for cid in cluster_ids_with_transforms:
                        emb_q = await _ti_db.execute(
                            _bf_select(_bf_Opt.transformation_embedding).where(
                                _bf_Opt.cluster_id == cid,
                                _bf_Opt.transformation_embedding.isnot(None),
                            )
                        )
                        embs = []
                        for row in emb_q.scalars().all():
                            try:
                                embs.append(np.frombuffer(row, dtype=np.float32))
                            except (ValueError, TypeError):
                                continue
                        if embs:
                            mean_vec = np.mean(np.stack(embs), axis=0).astype(np.float32)
                            norm = np.linalg.norm(mean_vec)
                            if norm > 1e-9:
                                transform_vectors[cid] = mean_vec / norm

                    if transform_vectors:
                        await engine._transformation_index.rebuild(transform_vectors)
                        logger.info(
                            "TransformationIndex loaded with %d vectors",
                            len(transform_vectors),
                        )
            except Exception as ti_exc:
                logger.warning("TransformationIndex build failed (non-fatal): %s", ti_exc)

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
                # Reload domain caches when taxonomy or domain events fire
                elif event.get("event") in ("domain_created", "taxonomy_changed"):
                    try:
                        async with async_session_factory() as _reload_db:
                            await app.state.domain_resolver.load(_reload_db)
                            await app.state.signal_loader.load(_reload_db)
                        logger.info(
                            "Domain caches reloaded on %s event",
                            event.get("event"),
                        )
                    except Exception:
                        logger.error("Domain cache reload failed", exc_info=True)
                    # Signal warm-path timer to run early — but ONLY for
                    # hot-path events (new optimization clustered) and domain
                    # creation.  Warm/cold path events must NOT re-trigger
                    # the warm path or it creates an infinite cascade loop.
                    trigger = event.get("data", {}).get("trigger", "")
                    if trigger not in ("warm_path", "cold_path"):
                        _warm_path_pending.set()
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

    # Start warm-path periodic timer (Spec Section 6.4 — adaptive interval)
    async def _warm_path_timer():
        """Periodically trigger warm-path re-clustering.

        Runs on a configurable interval (default 5 min) OR immediately when
        ``_warm_path_pending`` is set (e.g. after a new optimization is
        clustered), whichever comes first.
        """
        try:
            await asyncio.sleep(60)  # Initial delay — let system stabilize
            while True:
                try:
                    await asyncio.wait_for(
                        _warm_path_pending.wait(),
                        timeout=settings.WARM_PATH_INTERVAL_SECONDS,
                    )
                    _warm_path_pending.clear()  # Reset for next cycle
                except asyncio.TimeoutError:
                    pass  # Normal timeout — run warm path on schedule
                try:
                    engine = getattr(app.state, "taxonomy_engine", None)
                    if engine:
                        from app.database import async_session_factory
                        from app.services.prompt_lifecycle import (
                            PromptLifecycleService,
                        )
                        result = await engine.run_warm_path(async_session_factory)
                        if result:
                            logger.info(
                                "Warm path completed: q=%.4f baseline=%.4f ops=%d/%d snapshot=%s",
                                result.q_system or 0.0,
                                result.q_baseline or 0.0,
                                result.operations_accepted,
                                result.operations_attempted,
                                result.snapshot_id,
                            )
                        # Lifecycle service gets its own session
                        async with async_session_factory() as lifecycle_db:
                            lifecycle = PromptLifecycleService()
                            await lifecycle.curate(lifecycle_db, embedding_index=engine.embedding_index)
                            await lifecycle.decay_usage(lifecycle_db)
                            await lifecycle_db.commit()

                        # Auto-trigger cold path when:
                        # 1. Deadlock breaker signaled _cold_path_needed, OR
                        # 2. Active nodes lack UMAP coordinates (hot-path
                        #    creates clusters with NULL umap_x/y/z).
                        try:
                            need_cold = getattr(engine, "_cold_path_needed", False)

                            if not need_cold:
                                from sqlalchemy import func, select

                                from app.models import PromptCluster

                                async with async_session_factory() as umap_db:
                                    no_umap = (await umap_db.execute(
                                        select(func.count()).where(
                                            PromptCluster.state == "active",
                                            PromptCluster.umap_x.is_(None),
                                        )
                                    )).scalar() or 0
                                    need_cold = no_umap >= 5
                                    if need_cold:
                                        logger.info(
                                            "Auto cold-path: %d active nodes lack UMAP coordinates",
                                            no_umap,
                                        )

                            if need_cold:
                                if getattr(engine, "_cold_path_needed", False):
                                    logger.info("Auto cold-path: deadlock breaker requested rebuild")
                                async with async_session_factory() as cold_db:
                                    await engine.run_cold_path(cold_db)
                        except Exception as cold_exc:
                            logger.warning("Auto cold-path check failed: %s", cold_exc)
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

try:
    from app.routers.domains import router as domains_router
    app.include_router(domains_router)
except ImportError:
    pass

asgi_app = app
