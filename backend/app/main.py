"""FastAPI application entry point."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app._version import __version__
from app.config import DATA_DIR, PROJECT_ROOT, PROMPTS_DIR, settings
from app.services.event_bus import event_bus
from app.services.file_watcher import watch_strategy_files
from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES

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


def _apply_cross_process_dirty_marks(engine, event_data) -> None:
    """Gap B bridge: mark clusters dirty from cross-process ``taxonomy_changed``.

    ``OptimizationService.delete_optimizations()`` marks clusters dirty on
    the live engine in its own process. When the producer is another
    process (MCP, CLI, tests, future admin tools) the in-process
    ``mark_dirty`` call is a no-op because ``get_engine()`` raises — only
    the cross-process HTTP event carries the affected clusters forward.
    Without this bridge the warm-path timer would fire on
    ``taxonomy_changed`` but Phase 0 would skip with
    ``decision="no_dirty_clusters"`` and stale ``member_count`` rows would
    survive until the next maintenance cadence hit.

    Pure-ish: reads from ``event_data``, mutates only the supplied engine.
    Never raises — this is called from the event-bus consumer loop and an
    exception here would tear the listener down.
    """
    if engine is None or not isinstance(event_data, dict):
        return
    affected = event_data.get("affected_clusters")
    if not affected:
        return
    for cid in affected:
        if not isinstance(cid, str) or not cid:
            continue
        try:
            engine.mark_dirty(cid)
        except Exception:
            logger.debug(
                "mark_dirty(%s) raised from cross-process bridge — skipping",
                cid, exc_info=True,
            )


async def _backfill_project_ids(db) -> None:
    """Backfill Optimization.project_id via repo_full_name → LinkedRepo (Hybrid).

    Hybrid taxonomy: projects are sibling roots alongside global domains, so
    project_id cannot be derived from cluster ancestry anymore. Resolution
    path: Optimization.repo_full_name → LinkedRepo.project_node_id, falling
    back to the Legacy project node for repo-less optimizations.
    """
    from sqlalchemy import select as _sel

    from app.models import LinkedRepo, Optimization, PromptCluster

    # Resolve Legacy project (fallback for repo-less optimizations).
    legacy = (await db.execute(
        _sel(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == "Legacy",
        ).limit(1)
    )).scalar_one_or_none()
    legacy_id = legacy.id if legacy else None

    # Bulk-load repo → project_node_id map (typically small).
    repo_map: dict[str, str] = {}
    lr_rows = (await db.execute(
        _sel(LinkedRepo.full_name, LinkedRepo.project_node_id)
        .where(LinkedRepo.project_node_id.isnot(None))
    )).all()
    for row in lr_rows:
        repo_map[row[0]] = row[1]

    total_filled = 0
    while True:
        missing = (await db.execute(
            _sel(Optimization).where(Optimization.project_id.is_(None)).limit(500)
        )).scalars().all()
        if not missing:
            break

        filled = 0
        for opt in missing:
            resolved: str | None = None
            if opt.repo_full_name and opt.repo_full_name in repo_map:
                resolved = repo_map[opt.repo_full_name]
            elif legacy_id:
                resolved = legacy_id
            if resolved:
                opt.project_id = resolved
                filled += 1

        if filled == 0:
            break  # No fillable rows remaining — prevent infinite loop.

        total_filled += filled
        await db.flush()

    if total_filled:
        logger.info(
            "Hybrid migration: backfilled project_id on %d optimizations",
            total_filled,
        )


async def _run_adr005_migration(db) -> None:
    """Hybrid taxonomy migration: ensure Legacy, detach domain tree, backfill project_id.

    Hybrid architecture (2026-04-19): projects and global domains are sibling
    root nodes (``parent_id IS NULL``). Projects isolate via
    ``Optimization.project_id`` FK, not via tree ancestry. Any legacy rows
    where a domain is parented under a project are detached here.

    Step 2.5 auto-provisions a project node per LinkedRepo that is missing
    ``project_node_id`` — one repo earns one project, never bulk-assigned to
    Legacy. This preserves the "each project learns its own domain"
    invariant under resets and pre-ADR-005 migrations.

    Idempotent — safe to run on every startup.
    """
    from sqlalchemy import select as _sel

    from app.models import LinkedRepo, PromptCluster
    from app.services.project_service import ensure_project_for_repo

    # Step 1: Find or create the canonical Legacy project node.
    legacy_q = await db.execute(
        _sel(PromptCluster).where(
            PromptCluster.state == "project",
            PromptCluster.label == "Legacy",
        ).limit(1)
    )
    legacy = legacy_q.scalar_one_or_none()

    if legacy is None:
        legacy = PromptCluster(
            label="Legacy",
            state="project",
            domain="general",
            task_type="general",
            member_count=0,
        )
        db.add(legacy)
        await db.flush()
        logger.info(
            "Hybrid migration: created Legacy project node %s", legacy.id,
        )

    # Step 2: Detach top-level domains from any project parent.
    # Domains must live at the taxonomy root so projects and domains are
    # sibling roots. Sub-domains (parent.state == "domain") are preserved.
    project_ids_q = await db.execute(
        _sel(PromptCluster.id).where(PromptCluster.state == "project")
    )
    project_ids = {row[0] for row in project_ids_q.all()}
    detached_count = 0
    if project_ids:
        parented = (await db.execute(
            _sel(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.parent_id.in_(project_ids),
            )
        )).scalars().all()
        for d in parented:
            d.parent_id = None
            detached_count += 1
        if detached_count:
            await db.flush()
            logger.info(
                "Hybrid migration: detached %d domain nodes from project tree",
                detached_count,
            )

    # Step 2.5: Auto-provision a project node per LinkedRepo with NULL
    # project_node_id. Creates one project per repo (or re-attaches to an
    # existing project whose label matches the repo name). This replaces
    # the legacy bulk-assign-to-Legacy behavior that collapsed every
    # linked repo into a single shared project.
    try:
        unlinked_repos = (await db.execute(
            _sel(LinkedRepo).where(LinkedRepo.project_node_id.is_(None))
        )).scalars().all()
    except Exception:
        # Column may not exist yet on very old schemas — handled by the
        # ALTER TABLE block in lifespan. Safe to skip here.
        unlinked_repos = []

    provisioned = 0
    for lr in unlinked_repos:
        if not lr.full_name:
            continue
        await ensure_project_for_repo(db, lr.full_name)
        provisioned += 1
    if provisioned:
        await db.flush()
        logger.info(
            "Hybrid migration: auto-provisioned %d project nodes for linked repos",
            provisioned,
        )

    # Step 3: Backfill project_id on Optimizations via repo_full_name.
    # Runs AFTER Step 2.5 so the LinkedRepo → project_node_id map is
    # populated before we attempt to resolve optimization project_ids.
    await _backfill_project_ids(db)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    _lifespan_start = time.monotonic()
    app.state.startup_wall = time.time()
    settings.SECRET_KEY = settings.resolve_secret_key()

    # Initialize structured error logger (before anything else that might fail)
    from app.services.error_logger import ErrorLogger, set_error_logger

    _error_logger = ErrorLogger(DATA_DIR / "errors")
    set_error_logger(_error_logger)

    # SQLite PRAGMAs (WAL, busy_timeout, foreign_keys, synchronous, cache_size)
    # are applied to every pool checkout by the event hook in app.database.
    # The previous throwaway aiosqlite.connect() only set WAL+busy_timeout on a
    # single connection — busy_timeout is per-connection and was silently lost
    # for the pool. See app/database.py for the hook definition.

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

    # Initialize rate-limit store and startup probe
    from app.services.rate_limit_state import get_rate_limit_store, probe_rate_limit
    rate_limit_store = get_rate_limit_store()

    # Synchronize initial routing state
    # Iterate through all active rate limits in the store and sync them
    active_state = rate_limit_store._state
    for provider_name in active_state:
        routing.sync_rate_limit(provider_name, True)

    # Start the proactive expiration watcher
    rate_limit_store.start_watcher(event_bus, provider)

    async def _rate_limit_event_consumer():
        try:
            async for payload in event_bus.subscribe():
                evt = payload.get("event")
                if evt == "rate_limit_active":
                    data = payload.get("data", {})
                    rate_limit_store.handle_rate_limit_active(data)
                    routing.sync_rate_limit(data.get("provider"), True)
                elif evt == "rate_limit_cleared":
                    data = payload.get("data", {})
                    rate_limit_store.handle_rate_limit_cleared(data)
                    routing.sync_rate_limit(data.get("provider"), False)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("Rate limit event consumer failed: %s", exc)

    app.state.rate_limit_consumer_task = asyncio.create_task(_rate_limit_event_consumer())

    async def _run_rate_limit_probe():
        try:
            result = await probe_rate_limit(provider, rate_limit_store)
            logger.info("Startup rate limit probe completed: %s", result)
        except Exception as exc:
            logger.warning("Startup rate limit probe failed: %s", exc)

    asyncio.create_task(_run_rate_limit_probe())

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
        async with async_session_factory() as db:  # type: ignore[assignment]
            tracker = AdaptationTracker(db)  # type: ignore[arg-type]
            await tracker.cleanup_orphaned_affinities()
    except Exception as exc:
        logger.debug("Strategy affinity cleanup skipped: %s", exc)

    # Startup garbage collection — clean dead records from the DB
    try:
        from app.database import async_session_factory
        from app.services.gc import run_startup_gc
        async with async_session_factory() as db:  # type: ignore[assignment]
            await run_startup_gc(db)  # type: ignore[arg-type]
    except Exception as exc:
        logger.debug("Startup GC skipped: %s", exc)

    # Recurring garbage collection — hourly sweep of expired tokens and
    # orphan LinkedRepo rows. Complements run_startup_gc (which handles
    # cold-start dead records). Task handle lives on app.state and is
    # cancelled during shutdown.
    #
    # v0.4.13 cycle 9 (MED-6 / HIGH-8): pulls the WriteQueue from
    # ``app.state.write_queue`` lazily so the queue is guaranteed
    # initialized by the time the first iteration fires. The submit path
    # surfaces ``WriteQueueStoppedError`` if shutdown beats us to the
    # punch — that's the canonical signal to exit cleanly without
    # logging an exception trace.
    async def _recurring_gc_task() -> None:
        from app.database import async_session_factory
        from app.services.gc import run_recurring_gc
        from app.services.write_queue import (
            WriteQueueDeadError,
            WriteQueueStoppedError,
        )

        while True:
            try:
                wq = getattr(app.state, "write_queue", None)
                async with async_session_factory() as db:  # type: ignore[assignment]
                    await run_recurring_gc(db, write_queue=wq)  # type: ignore[arg-type]
            except asyncio.CancelledError:
                raise
            except (WriteQueueStoppedError, WriteQueueDeadError) as exc:
                logger.info(
                    "recurring_gc: queue not available (%s) — exiting cleanly",
                    type(exc).__name__,
                )
                return
            except Exception:
                logger.exception("recurring_gc sweep failed")
            await asyncio.sleep(3600)  # 1h

    app.state.recurring_gc_task = asyncio.create_task(_recurring_gc_task())

    # Start strategy file watcher
    watcher_task = asyncio.create_task(
        watch_strategy_files(PROMPTS_DIR / "strategies")
    )
    app.state.watcher_task = watcher_task

    # Start seed agent file watcher
    from app.services.file_watcher import watch_seed_agent_files
    agent_watcher_task = asyncio.create_task(
        watch_seed_agent_files(PROMPTS_DIR / "seed-agents")
    )
    app.state.agent_watcher_task = agent_watcher_task

    # Start update checker (background — non-blocking)
    from app.services.update_service import UpdateService
    _update_svc = UpdateService(project_root=PROJECT_ROOT)
    app.state.update_service = _update_svc

    async def _run_update_check():
        try:
            await _update_svc.check_for_updates()
            if _update_svc.status and _update_svc.status.update_available:
                logger.info(
                    "Update available: %s -> %s",
                    _update_svc.status.current_version,
                    _update_svc.status.latest_version,
                )
        except Exception as exc:
            logger.warning("Update check failed: %s", exc)

    asyncio.create_task(_run_update_check())

    # Track in-flight extraction tasks for graceful shutdown
    extraction_tasks: set[asyncio.Task[None]] = set()

    # v0.4.13 cycle 9: signaled when the in-listener migration block
    # finishes so the lifespan can install the audit hook + start the
    # WriteQueue at the correct ordering. See spec §3.3.
    _migrations_done = asyncio.Event()

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

            # Initialize taxonomy event logger
            from app.services.taxonomy.event_logger import TaxonomyEventLogger, set_event_logger
            taxonomy_event_logger = TaxonomyEventLogger(
                events_dir=DATA_DIR / "taxonomy_events",
                publish_to_bus=True,
            )
            set_event_logger(taxonomy_event_logger)

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

            # Extract dynamic task-type signals from optimization history
            try:
                from app.services.heuristic_analyzer import set_task_type_signals
                from app.services.task_type_signal_extractor import extract_task_type_signals
                async with async_session_factory() as _tt_db:
                    tt_signals = await extract_task_type_signals(_tt_db)
                    if tt_signals:
                        # A4: keys of the extractor output are exactly the
                        # task_types whose sample count crossed MIN_SAMPLES
                        # — hand them over so HeuristicAnalysis can report
                        # signal_source="dynamic" only for genuinely live
                        # extraction (vs a cache-only warmup).
                        set_task_type_signals(
                            tt_signals,
                            extracted_task_types=set(tt_signals.keys()),
                        )
                        # Persist for MCP cold-start
                        import json as _tt_json
                        _tt_cache = DATA_DIR / "task_type_signals.json"
                        try:
                            _tt_cache.write_text(_tt_json.dumps(
                                {k: [[kw, w] for kw, w in v] for k, v in tt_signals.items()},
                                indent=2,
                            ))
                        except Exception:
                            logger.debug("Failed to persist task_type_signals.json")
                    else:
                        logger.info("TaskTypeSignals: no dynamic signals — using static bootstrap")
            except Exception as _tt_exc:
                logger.warning("TaskTypeSignals: startup extraction failed — static bootstrap: %s", _tt_exc)

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
                                _PC_check.state.notin_(EXCLUDED_STRUCTURAL_STATES),
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
                                    PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
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

            # Warm-load TransformationIndex + OptimizedEmbeddingIndex from disk cache
            await engine.load_index_caches(DATA_DIR)

            # v0.4.13 cycle 9: enter migration_mode so the audit hook
            # (installed later in lifespan) does NOT fire on the
            # idempotent ALTER TABLE / DML migrations below. Cleared
            # immediately before the event-consumption ``async for``
            # loop and signaled via ``_migrations_done``.
            from app.database import read_engine_meta
            read_engine_meta.migration_mode = True

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
            # Startup: ensure phase_weights_json column exists on optimizations
            try:
                async with async_session_factory() as _pw_db:
                    await _pw_db.execute(
                        _text_gsc("ALTER TABLE optimizations ADD COLUMN phase_weights_json TEXT")
                    )
                    await _pw_db.commit()
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
            # Startup: ensure created_at index on taxonomy_snapshots
            try:
                async with async_session_factory() as _idx_db:
                    await _idx_db.execute(
                        _text_gsc(
                            "CREATE INDEX IF NOT EXISTS ix_taxonomy_snapshot_created_at "
                            "ON taxonomy_snapshots (created_at DESC)"
                        )
                    )
                    await _idx_db.commit()
            except Exception:
                pass

            # ADR-005: ensure project_id column on optimizations
            try:
                async with async_session_factory() as _pid_db:
                    from sqlalchemy import text as _text_pid
                    await _pid_db.execute(
                        _text_pid("ALTER TABLE optimizations ADD COLUMN project_id VARCHAR(36)")
                    )
                    await _pid_db.commit()
                    logger.info("Added project_id column to optimizations")
            except Exception:
                pass  # Column already exists

            # ADR-005: ensure project_id index on optimizations
            try:
                async with async_session_factory() as _pidx_db:
                    from sqlalchemy import text as _text_pidx
                    await _pidx_db.execute(
                        _text_pidx(
                            "CREATE INDEX IF NOT EXISTS ix_optimizations_project_id"
                            " ON optimizations (project_id)"
                        )
                    )
                    await _pidx_db.commit()
            except Exception:
                pass

            # ADR-005: ensure global_patterns table exists
            try:
                async with async_session_factory() as _gp_db:
                    from sqlalchemy import text as _text_gp
                    await _gp_db.execute(_text_gp("""
                        CREATE TABLE IF NOT EXISTS global_patterns (
                            id VARCHAR(36) PRIMARY KEY,
                            pattern_text TEXT NOT NULL,
                            embedding BLOB,
                            source_cluster_ids TEXT NOT NULL DEFAULT '[]',
                            source_project_ids TEXT NOT NULL DEFAULT '[]',
                            cross_project_count INTEGER NOT NULL DEFAULT 0,
                            global_source_count INTEGER NOT NULL DEFAULT 0,
                            avg_cluster_score REAL,
                            promoted_at DATETIME NOT NULL,
                            last_validated_at DATETIME NOT NULL,
                            state VARCHAR(20) NOT NULL DEFAULT 'active'
                        )
                    """))
                    await _gp_db.commit()
            except Exception:
                pass

            # ADR-005 Phase 2A: ensure project_node_id column on linked_repos
            # (must run BEFORE _run_adr005_migration because Step 2.5 reads
            # and writes LinkedRepo.project_node_id to auto-provision projects).
            try:
                async with async_session_factory() as _pnid_db:
                    from sqlalchemy import text as _text_pnid
                    await _pnid_db.execute(
                        _text_pnid("ALTER TABLE linked_repos ADD COLUMN project_node_id VARCHAR(36)")
                    )
                    await _pnid_db.commit()
                    logger.info("Added project_node_id column to linked_repos")
            except Exception:
                pass  # Column already exists

            # ADR-005: Legacy project node + domain detach + per-repo project
            # provisioning + project_id backfill. See _run_adr005_migration.
            try:
                async with async_session_factory() as _adr005_db:
                    await _run_adr005_migration(_adr005_db)
                    await _adr005_db.commit()
            except Exception as adr005_exc:
                logger.warning("ADR-005 migration failed (non-fatal): %s", adr005_exc)

            # B1: Cache Legacy project_id on app.state for zero-query pipeline
            # fallback. Resolved after _run_adr005_migration so the Legacy
            # node is guaranteed to exist. Also primes the module-level
            # project_service cache so helpers that don't have app.state
            # access (sampling/passthrough, MCP tool handlers, batch
            # pipeline) share the same zero-query fallback. A fresh DB
            # where migration failed degrades gracefully to None — the
            # startup _backfill_project_ids sweep will repair NULL rows on
            # the next boot.
            try:
                from app.services.project_service import (
                    get_legacy_project_id,
                    prime_legacy_project_id_cache,
                )
                async with async_session_factory() as _lpid_db:
                    app.state.legacy_project_id = await get_legacy_project_id(_lpid_db)
                prime_legacy_project_id_cache(app.state.legacy_project_id)
                logger.info(
                    "Legacy project_id cached: %s",
                    (app.state.legacy_project_id or "none")[:8],
                )
            except Exception as _lpid_exc:
                logger.warning("Failed to cache legacy_project_id: %s", _lpid_exc)
                app.state.legacy_project_id = None

            # ADR-005 B8: one-shot startup repair.  Demote/retire any
            # GlobalPatterns admitted under the old single-project gate
            # that no longer meet ``GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS``.
            try:
                from app.services.taxonomy.global_patterns import (
                    repair_legacy_only_promotions,
                )
                async with async_session_factory() as _b8_db:
                    _b8_stats = await repair_legacy_only_promotions(_b8_db)
                if _b8_stats.get("demoted") or _b8_stats.get("retired"):
                    logger.info(
                        "B8 repair applied: demoted=%d retired=%d",
                        _b8_stats.get("demoted", 0),
                        _b8_stats.get("retired", 0),
                    )
            except Exception as _b8_exc:
                logger.warning("B8 startup repair failed (non-fatal): %s", _b8_exc)

            # ADR-005 Phase 2B: ensure global_pattern_id column on optimization_patterns
            try:
                async with async_session_factory() as _gpid_db:
                    from sqlalchemy import text as _text_gpid
                    await _gpid_db.execute(
                        _text_gpid("ALTER TABLE optimization_patterns ADD COLUMN global_pattern_id VARCHAR(36)")
                    )
                    await _gpid_db.commit()
            except Exception:
                pass  # Column already exists

            # Ensure explore_synthesis column on repo_index_meta
            try:
                async with async_session_factory() as _es_db:
                    from sqlalchemy import text as _text_es
                    await _es_db.execute(
                        _text_es("ALTER TABLE repo_index_meta ADD COLUMN explore_synthesis TEXT")
                    )
                    await _es_db.commit()
            except Exception:
                pass  # Column already exists

            # Ensure synthesis_status column on repo_index_meta
            try:
                async with async_session_factory() as _ss_db:
                    from sqlalchemy import text as _text_ss
                    await _ss_db.execute(
                        _text_ss(
                            "ALTER TABLE repo_index_meta "
                            "ADD COLUMN synthesis_status VARCHAR DEFAULT 'pending' NOT NULL"
                        )
                    )
                    await _ss_db.commit()
            except Exception:
                pass  # Column already exists

            # Ensure synthesis_error column on repo_index_meta
            try:
                async with async_session_factory() as _se_db:
                    from sqlalchemy import text as _text_se
                    await _se_db.execute(
                        _text_se("ALTER TABLE repo_index_meta ADD COLUMN synthesis_error TEXT")
                    )
                    await _se_db.commit()
            except Exception:
                pass  # Column already exists

            # Backfill: mark existing rows with synthesis as ready
            try:
                async with async_session_factory() as _bf_synth_db:
                    from sqlalchemy import text as _text_bf_synth
                    await _bf_synth_db.execute(
                        _text_bf_synth(
                            "UPDATE repo_index_meta SET synthesis_status = 'ready' "
                            "WHERE explore_synthesis IS NOT NULL AND synthesis_status = 'pending'"
                        )
                    )
                    await _bf_synth_db.commit()
            except Exception:
                pass

            # Ensure content column on repo_file_index (full source for curated context)
            try:
                async with async_session_factory() as _rc_db:
                    from sqlalchemy import text as _text_rc
                    await _rc_db.execute(
                        _text_rc("ALTER TABLE repo_file_index ADD COLUMN content TEXT")
                    )
                    await _rc_db.commit()
            except Exception:
                pass  # Column already exists

            # Ensure unique index on repo_file_index (repo, branch, path) for incremental upserts
            try:
                async with async_session_factory() as _rfi_idx_db:
                    from sqlalchemy import text as _text_rfi_idx
                    await _rfi_idx_db.execute(
                        _text_rfi_idx(
                            "CREATE UNIQUE INDEX IF NOT EXISTS "
                            "idx_repo_file_index_repo_branch_path "
                            "ON repo_file_index (repo_full_name, branch, file_path)"
                        )
                    )
                    await _rfi_idx_db.commit()
            except Exception:
                pass  # Index already exists

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

            # Startup: rebuild OptimizedEmbeddingIndex from stored optimized_embeddings
            try:
                async with async_session_factory() as _oi_db:
                    from sqlalchemy import func as _oi_func
                    oi_q = await _oi_db.execute(
                        _bf_select(
                            _bf_Opt.cluster_id,
                            _oi_func.count().label("ct"),
                        ).where(
                            _bf_Opt.cluster_id.isnot(None),
                            _bf_Opt.optimized_embedding.isnot(None),
                        ).group_by(_bf_Opt.cluster_id)
                    )
                    cluster_ids_with_opt_embs = [row[0] for row in oi_q.all() if row[1] >= 1]

                    optimized_vectors: dict[str, np.ndarray] = {}
                    for cid in cluster_ids_with_opt_embs:
                        emb_q = await _oi_db.execute(
                            _bf_select(_bf_Opt.optimized_embedding).where(
                                _bf_Opt.cluster_id == cid,
                                _bf_Opt.optimized_embedding.isnot(None),
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
                                optimized_vectors[cid] = mean_vec / norm

                    if optimized_vectors:
                        await engine._optimized_index.rebuild(optimized_vectors)
                        logger.info(
                            "OptimizedEmbeddingIndex loaded with %d vectors",
                            len(optimized_vectors),
                        )
            except Exception as oi_exc:
                logger.warning("OptimizedEmbeddingIndex build failed (non-fatal): %s", oi_exc)

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

            # Cold-start bootstrap: if orphans remain AND no active clusters
            # exist (fresh install, post-reset, or migrated DB), drive each
            # orphan through ``engine.process_optimization()`` which creates
            # the first clusters organically. Without this, ``backfill_orphans``
            # can't link orphans to anything (nothing to link to) and the
            # system stays frozen on an empty taxonomy tree forever.
            try:
                from sqlalchemy import func as _cs_func
                from sqlalchemy import select as _cs_select

                from app.models import Optimization as _CS_Opt
                from app.models import PromptCluster as _CS_Cluster
                async with async_session_factory() as _cs_db:
                    active_count = (await _cs_db.execute(
                        _cs_select(_cs_func.count(_CS_Cluster.id)).where(
                            _CS_Cluster.state.in_(
                                ["active", "candidate", "mature"]
                            )
                        )
                    )).scalar() or 0
                    orphan_ids: list[str] = []
                    if active_count == 0:
                        orphan_q = await _cs_db.execute(
                            _cs_select(_CS_Opt.id).where(
                                _CS_Opt.status == "completed",
                                _CS_Opt.raw_prompt.isnot(None),
                                _CS_Opt.cluster_id.is_(None),
                            )
                        )
                        orphan_ids = [row[0] for row in orphan_q.all()]

                if orphan_ids:
                    logger.info(
                        "Cold-start bootstrap: seeding taxonomy from %d orphan "
                        "optimizations (empty cluster tree detected)",
                        len(orphan_ids),
                    )
                    seeded = 0
                    for _opt_id in orphan_ids:
                        try:
                            async with async_session_factory() as _seed_db:
                                await engine.process_optimization(_opt_id, _seed_db)
                                await _seed_db.commit()
                            seeded += 1
                        except Exception as _seed_exc:
                            logger.warning(
                                "Cold-start seed failed for %s: %s",
                                _opt_id, _seed_exc,
                            )
                    logger.info(
                        "Cold-start bootstrap complete: seeded %d/%d optimizations",
                        seeded, len(orphan_ids),
                    )
            except Exception as cs_exc:
                logger.warning(
                    "Cold-start bootstrap failed (non-fatal): %s", cs_exc
                )

            # v0.4.13 cycle 9: migrations done, exit migration_mode +
            # signal lifespan. Audit hook + WriteQueue come online next.
            read_engine_meta.migration_mode = False
            _migrations_done.set()

            logger.info("Taxonomy extraction listener started — subscribing to event bus")

            _recently_dispatched: set[str] = set()  # dedup window

            async for event in event_bus.subscribe():
                if event.get("event") == "optimization_created":
                    opt_id = event.get("data", {}).get("id")
                    if opt_id:
                        # Dedup: skip if already dispatched (cross-process
                        # events can arrive multiple times for the same opt)
                        if opt_id in _recently_dispatched:
                            logger.debug(
                                "Skipping duplicate taxonomy extraction dispatch for %s",
                                opt_id,
                            )
                            continue
                        _recently_dispatched.add(opt_id)
                        # Bound the set to prevent unbounded growth
                        if len(_recently_dispatched) > 500:
                            # Discard oldest half (set is unordered, but this
                            # is a best-effort dedup, not a strict window)
                            _to_remove = list(_recently_dispatched)[:250]
                            _recently_dispatched.difference_update(_to_remove)
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
                    # candidate_evaluation fires from within warm path Phase
                    # 0.5 — excluding it prevents self-re-triggering.
                    event_data = event.get("data") or {}
                    trigger = event_data.get("trigger", "") if isinstance(event_data, dict) else ""
                    if trigger not in ("warm_path", "cold_path", "candidate_evaluation"):
                        # Gap B: propagate ``affected_clusters`` into the
                        # resident engine's dirty set so the warm path's
                        # Phase 0 reconciliation actually runs. Without this
                        # bridge, cross-process deletes fire ``taxonomy_changed``
                        # but Phase 0 sees an empty dirty set and skips with
                        # ``decision="no_dirty_clusters"`` — leaving stale
                        # ``member_count`` on domain nodes forever.
                        _apply_cross_process_dirty_marks(engine, event_data)
                        _warm_path_pending.set()
        except asyncio.CancelledError:
            # v0.4.13 cycle 9: ensure lifespan can proceed even if the
            # listener is cancelled mid-migration.
            from app.database import read_engine_meta
            read_engine_meta.migration_mode = False
            _migrations_done.set()
            logger.info("Taxonomy extraction listener shutting down")
        except Exception as exc:
            from app.database import read_engine_meta
            read_engine_meta.migration_mode = False
            _migrations_done.set()
            logger.error("Taxonomy extraction listener crashed: %s", exc, exc_info=True)

    extraction_task = asyncio.create_task(_taxonomy_extraction_listener())
    app.state.extraction_task = extraction_task

    # v0.4.13 cycle 9 — Wait for the listener's migration block to
    # complete (or to surface) before installing the audit hook + the
    # WriteQueue. Spec §3.3 mandates: migrations → audit hook install →
    # queue start → recurring tasks → yield.
    #
    # Telemetry: ``app.state.lifespan_order`` records each ordering
    # checkpoint so integration tests can pin the spec-required
    # sequence (test_lifespan_starts_write_queue_after_alter_table_migrations).
    app.state.lifespan_order = []
    try:
        await asyncio.wait_for(_migrations_done.wait(), timeout=120.0)
    except asyncio.TimeoutError:
        logger.error(
            "Lifespan migrations did not complete within 120s — "
            "starting WriteQueue with audit hook in degraded mode",
        )
    app.state.lifespan_order.append("migrations_complete")

    # Install audit hook on the read engine. Any INSERT/UPDATE/DELETE
    # that hits the read engine outside of migration_mode/cold_path_mode
    # will now WARN (dev/prod) or RAISE (CI WRITE_QUEUE_AUDIT_HOOK_RAISE=True).
    try:
        from app.database import engine as _read_engine
        from app.database import install_read_engine_audit_hook

        install_read_engine_audit_hook(_read_engine)
        app.state.lifespan_order.append("audit_hook_installed")
        logger.info("Read-engine audit hook installed")
    except RuntimeError as _hook_exc:
        logger.warning(
            "Audit hook install skipped (already installed): %s",
            _hook_exc,
        )

    # Start the WriteQueue worker bound to the writer engine.
    try:
        from app.database import writer_engine as _writer_engine
        from app.services.write_queue import WriteQueue

        write_queue = WriteQueue(_writer_engine)
        await write_queue.start()
        app.state.write_queue = write_queue
        app.state.lifespan_order.append("write_queue_started")
        logger.info(
            "WriteQueue started: max_depth=%d default_timeout=%.1fs",
            settings.WRITE_QUEUE_MAX_QUEUE_DEPTH,
            settings.WRITE_QUEUE_DEFAULT_TIMEOUT_SECONDS,
        )
    except Exception as _wq_exc:
        logger.error("Failed to start WriteQueue: %s", _wq_exc, exc_info=True)
        app.state.write_queue = None

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

    # Start background repo index refresh loop (incremental staleness detection)
    async def _repo_index_refresh_loop():
        """Periodically check linked repos for file changes and incrementally
        re-embed only the diffs.  Runs on a configurable interval (default
        10 min).  Each repo is checked independently — one failure never
        blocks other repos or crashes the loop.

        Cycle summary is logged at INFO level after each pass with per-repo
        breakdown.  Publishes ``index_refreshed`` event on the event bus
        when files change so the frontend can update.
        """
        interval = settings.REPO_INDEX_REFRESH_INTERVAL
        if interval <= 0:
            logger.info("Repo index refresh disabled (REPO_INDEX_REFRESH_INTERVAL=0)")
            return

        # Wait for services to be ready (poll, not sleep)
        for _ in range(60):
            if getattr(app.state, "context_service", None):
                break
            await asyncio.sleep(1)

        logger.info(
            "index_refresh_loop: started (interval=%ds, concurrency=%d)",
            interval, settings.REPO_INDEX_REFRESH_CONCURRENCY,
        )

        from sqlalchemy import select as _sel_refresh

        from app.database import async_session_factory
        from app.models import GitHubToken, LinkedRepo
        from app.services.github_client import GitHubClient
        from app.services.github_service import GitHubService
        from app.services.repo_index_service import (
            RepoIndexService,
            invalidate_curated_cache,
        )

        cycle_number = 0
        try:
            while True:
                await asyncio.sleep(interval)
                cycle_number += 1
                t_cycle = time.monotonic()
                try:
                    async with async_session_factory() as db:
                        # Find all linked repos
                        repos = (await db.execute(
                            _sel_refresh(LinkedRepo)
                        )).scalars().all()

                        if not repos:
                            logger.debug("index_refresh_cycle: cycle=%d no linked repos", cycle_number)
                            continue

                        # Collect session_ids and decrypt tokens
                        session_ids = {r.session_id for r in repos}
                        tokens_q = await db.execute(
                            _sel_refresh(GitHubToken).where(
                                GitHubToken.session_id.in_(session_ids)
                            )
                        )
                        token_rows = {
                            t.session_id: t for t in tokens_q.scalars().all()
                        }

                        github_svc = GitHubService(secret_key=settings.resolve_secret_key())
                        gc = GitHubClient()

                        # Per-cycle counters
                        repos_checked = 0
                        repos_updated = 0
                        repos_unchanged = 0
                        repos_skipped = 0
                        repos_failed = 0
                        total_changed = 0
                        total_added = 0
                        total_removed = 0
                        updated_repos: list[str] = []

                        for repo in repos:
                            token_row = token_rows.get(repo.session_id)
                            if not token_row:
                                repos_skipped += 1
                                continue

                            try:
                                token = github_svc.decrypt_token(token_row.token_encrypted)
                            except Exception as decrypt_exc:
                                repos_skipped += 1
                                logger.warning(
                                    "index_refresh: %s token decrypt failed: %s",
                                    repo.full_name, decrypt_exc,
                                )
                                continue

                            branch = repo.branch or repo.default_branch or "main"
                            repos_checked += 1
                            try:
                                index_svc = RepoIndexService(
                                    db=db,
                                    github_client=gc,
                                    embedding_service=_shared_embedding_service,
                                )
                                result = await index_svc.incremental_update(
                                    repo_full_name=repo.full_name,
                                    branch=branch,
                                    token=token,
                                    concurrency=settings.REPO_INDEX_REFRESH_CONCURRENCY,
                                )
                                file_changes = (
                                    result["changed"]
                                    + result["added"]
                                    + result["removed"]
                                )
                                if file_changes > 0:
                                    repos_updated += 1
                                    total_changed += result["changed"]
                                    total_added += result["added"]
                                    total_removed += result["removed"]
                                    updated_repos.append(f"{repo.full_name}@{branch}")
                                elif result["skipped_reason"]:
                                    repos_unchanged += 1
                                else:
                                    repos_unchanged += 1
                            except Exception as repo_exc:
                                repos_failed += 1
                                logger.warning(
                                    "index_refresh: %s@%s unhandled error: %s",
                                    repo.full_name, branch, repo_exc,
                                )
                                continue

                        # Invalidate curated cache if any repo had changes
                        if repos_updated > 0:
                            evicted = invalidate_curated_cache()
                            logger.info(
                                "index_refresh: curated cache invalidated (%d entries evicted)",
                                evicted,
                            )
                            # Notify frontend via event bus
                            try:
                                event_bus.publish({
                                    "event": "index_refreshed",
                                    "data": {
                                        "repos": updated_repos,
                                        "changed": total_changed,
                                        "added": total_added,
                                        "removed": total_removed,
                                    },
                                })
                            except Exception:
                                pass  # Event bus publish is best-effort

                    cycle_ms = (time.monotonic() - t_cycle) * 1000
                    logger.info(
                        "index_refresh_cycle: cycle=%d checked=%d updated=%d "
                        "unchanged=%d skipped=%d failed=%d "
                        "files_changed=%d files_added=%d files_removed=%d "
                        "elapsed=%.0fms",
                        cycle_number, repos_checked, repos_updated,
                        repos_unchanged, repos_skipped, repos_failed,
                        total_changed, total_added, total_removed,
                        cycle_ms,
                    )
                except Exception as cycle_exc:
                    cycle_ms = (time.monotonic() - t_cycle) * 1000
                    logger.error(
                        "index_refresh_cycle: cycle=%d failed after %.0fms: %s",
                        cycle_number, cycle_ms, cycle_exc,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            logger.info("index_refresh_loop: shutting down after %d cycles", cycle_number)

    refresh_task = asyncio.create_task(_repo_index_refresh_loop())
    app.state.refresh_task = refresh_task

    # Start warm-path periodic timer (Spec Section 6.4 — adaptive interval)
    async def _warm_path_timer():
        """Periodically trigger warm-path re-clustering.

        Runs on a configurable interval (default 5 min) OR immediately when
        ``_warm_path_pending`` is set (e.g. after a new optimization is
        clustered), whichever comes first.
        """
        debounce_seconds = 30  # Wait 30s after last event before running

        try:
            # Wait for taxonomy engine + provider to be ready (poll, not sleep)
            for _ in range(60):  # up to 60s
                engine = getattr(app.state, "taxonomy_engine", None)
                routing = getattr(app.state, "routing", None)
                if engine and routing and routing.state.provider:
                    break
                await asyncio.sleep(1)
            else:
                logger.warning("Warm path timer: engine/provider not ready after 60s")

            # Run one immediate warm cycle on startup to refresh stale
            # clusters (pattern_stale=True from splits while server was down).
            # Without this, clusters show "No meta-patterns" for 5+ minutes.
            if engine:
                try:
                    from app.database import async_session_factory
                    await engine.run_warm_path(async_session_factory)
                    logger.info("Startup warm path completed")
                except Exception as startup_exc:
                    logger.warning("Startup warm path failed (non-fatal): %s", startup_exc)

            while True:
                try:
                    await asyncio.wait_for(
                        _warm_path_pending.wait(),
                        timeout=settings.WARM_PATH_INTERVAL_SECONDS,
                    )
                    _warm_path_pending.clear()  # Reset for next cycle
                    # Debounce: wait 30s, restart if more events arrive
                    while True:
                        try:
                            await asyncio.wait_for(
                                _warm_path_pending.wait(),
                                timeout=debounce_seconds,
                            )
                            _warm_path_pending.clear()  # Another event — restart debounce
                            logger.debug("Warm path debounce reset — more events arriving")
                        except asyncio.TimeoutError:
                            break  # 30s of silence — proceed to run warm path
                except asyncio.TimeoutError:
                    pass  # Normal interval timeout — run warm path on schedule
                try:
                    engine = getattr(app.state, "taxonomy_engine", None)
                    if engine:
                        from app.database import async_session_factory
                        from app.services.prompt_lifecycle import (
                            PromptLifecycleService,
                        )
                        result = await engine.run_warm_path(async_session_factory)
                        if result is None:
                            logger.debug("Warm path skipped — lock held")
                        elif result.snapshot_id == "skipped":
                            logger.debug("Warm path skipped — no dirty clusters, maintenance off-cadence")
                        elif result.q_baseline is None and result.snapshot_id != "skipped":
                            # Maintenance-only cycle (no Phase 0, so no q_baseline)
                            logger.info(
                                "Warm path maintenance-only: q=%.4f snapshot=%s",
                                result.q_system or 0.0,
                                result.snapshot_id,
                            )
                        else:
                            logger.info(
                                "Warm path completed: q=%.4f baseline=%.4f ops=%d/%d snapshot=%s",
                                result.q_system or 0.0,
                                result.q_baseline or 0.0,
                                result.operations_accepted,
                                result.operations_attempted,
                                result.snapshot_id,
                            )
                        # Cache injection effectiveness for health endpoint
                        eff = getattr(engine, "_injection_effectiveness", None)
                        if eff:
                            app.state.injection_effectiveness = eff
                        # Lifecycle service gets its own session
                        async with async_session_factory() as lifecycle_db:
                            lifecycle = PromptLifecycleService()
                            await lifecycle.curate(lifecycle_db, embedding_index=engine.embedding_index)
                            await lifecycle.decay_usage(lifecycle_db)
                            await lifecycle_db.commit()

                        # Orphan recovery — piggyback on warm-path timer
                        try:
                            from app.services.orphan_recovery import recovery_service
                            recovery_stats = await recovery_service.scan_and_recover(
                                async_session_factory, engine,
                            )
                            if recovery_stats.get("recovered"):
                                app.state.recovery_metrics = recovery_service.get_metrics()
                        except Exception:
                            logger.debug("Orphan recovery failed (non-fatal)", exc_info=True)

                        # Auto-trigger UMAP projection or cold path:
                        # 1. Deadlock breaker → full cold path (HDBSCAN refit)
                        # 2. UMAP-less nodes → UMAP-only projection (no refit)
                        try:
                            need_full_refit = getattr(engine, "_cold_path_needed", False)

                            if need_full_refit:
                                logger.info("Auto cold-path: deadlock breaker requested rebuild")
                                async with async_session_factory() as cold_db:
                                    cold_result = await engine.run_cold_path(cold_db)
                                    if cold_result and not cold_result.accepted:
                                        logger.info("Cold path rejected (Q regression)")
                            else:
                                # UMAP-only: project clusters that lack 3D coordinates.
                                # No HDBSCAN, no Q-gate, no rollback risk.
                                from sqlalchemy import func, select

                                from app.models import PromptCluster

                                async with async_session_factory() as umap_db:
                                    no_umap = (await umap_db.execute(
                                        select(func.count()).where(
                                            PromptCluster.state == "active",
                                            PromptCluster.umap_x.is_(None),
                                        )
                                    )).scalar() or 0

                                if no_umap >= 3:
                                    logger.info(
                                        "UMAP projection: %d active nodes lack coordinates",
                                        no_umap,
                                    )
                                    async with async_session_factory() as proj_db:
                                        await engine.run_umap_projection(proj_db)
                        except Exception as cold_exc:
                            logger.warning("Auto cold/UMAP check failed: %s", cold_exc)
                except Exception as exc:
                    logger.error("Warm path timer failed: %s", exc, exc_info=True)
        except asyncio.CancelledError:
            logger.info("Warm path timer shutting down")

    warm_path_task = asyncio.create_task(_warm_path_timer())
    app.state.warm_path_task = warm_path_task

    # Record cold start time (process spawn to fully ready)
    app.state.startup_monotonic = _lifespan_start
    app.state.cold_start_ms = round((time.monotonic() - _lifespan_start) * 1000, 1)

    yield

    # ── Shutdown (5-phase concurrent approach) ─────────────────────────

    # Phase 1: Drain SSE connections.  The sentinel unblocks all SSE
    # subscriber queues so persistent streams close *before* uvicorn's
    # graceful-shutdown timer kicks in (~0ms instead of waiting 3-5s).
    event_bus.shutdown()
    # Allow enough time for SSE generators to detect the sentinel,
    # yield their final bytes, and exit cleanly.  0.1s was too tight
    # under load — the StreamingResponse wrapper needs a full event-loop
    # turn plus network flush before the generator is truly done.
    await asyncio.sleep(0.5)

    # Phase 2: Cancel all background tasks concurrently via gather().
    # Previous code awaited each sequentially (4 × cancel+await blocks).
    bg_tasks = [
        t for t in [
            getattr(app.state, "extraction_task", None),
            getattr(app.state, "warm_path_task", None),
            getattr(app.state, "refresh_task", None),
            getattr(app.state, "watcher_task", None),
            getattr(app.state, "agent_watcher_task", None),
            getattr(app.state, "recurring_gc_task", None),
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
    #
    # v0.4.13 cycle 9: route the UPDATE through the WriteQueue while it
    # is still alive (we stop it after this phase). Falls back to direct
    # write under migration_mode bypass if the queue is unavailable —
    # that path is idempotent and only fires on shutdown.
    logger.info("Shutting down — marking in-flight optimizations as interrupted")
    try:
        from sqlalchemy import update

        from app.database import async_session_factory, read_engine_meta
        from app.models import Optimization

        async def _mark_interrupted(db):  # type: ignore[no-untyped-def]
            await db.execute(
                update(Optimization)
                .where(Optimization.status == "running")
                .values(status="interrupted")
            )
            await db.commit()

        wq = getattr(app.state, "write_queue", None)
        if wq is not None and wq.worker_alive:
            try:
                await wq.submit(
                    _mark_interrupted,
                    operation_label="shutdown_mark_interrupted",
                )
            except Exception as wq_exc:
                logger.warning(
                    "Shutdown queue submit failed (%s) — falling back to direct write",
                    wq_exc,
                )
                read_engine_meta.migration_mode = True
                try:
                    async with async_session_factory() as db:  # type: ignore[assignment]
                        await _mark_interrupted(db)
                finally:
                    read_engine_meta.migration_mode = False
        else:
            read_engine_meta.migration_mode = True
            try:
                async with async_session_factory() as db:  # type: ignore[assignment]
                    await _mark_interrupted(db)
            finally:
                read_engine_meta.migration_mode = False
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

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        tel = get_event_logger()
        tel_deleted = tel.rotate(retention_days=settings.TRACE_RETENTION_DAYS)
        if tel_deleted:
            logger.info("Rotated %d old taxonomy event files", tel_deleted)
    except RuntimeError:
        pass  # Logger not initialized (unlikely during shutdown)
    except Exception as exc:
        logger.error("Taxonomy event rotation failed: %s", exc)

    # Phase 4c: Rotate error logs (same retention as traces/taxonomy events).
    try:
        err_deleted = _error_logger.rotate(retention_days=settings.TRACE_RETENTION_DAYS)
        if err_deleted:
            logger.info("Error log rotation: deleted %d old files", err_deleted)
    except Exception as exc:
        logger.error("Error log rotation failed: %s", exc)

    # Phase 5: Clear taxonomy singleton + dispose database engine.
    try:
        from app.services.taxonomy import reset_engine
        reset_engine()
    except Exception:
        pass
    app.state.taxonomy_engine = None

    # Phase 4.5: Drain active request-scoped database sessions
    try:
        if hasattr(app.state, "request_tracker"):
            logger.info("Draining active HTTP requests...")
            drained = await app.state.request_tracker.wait_for_drain(timeout=3.0)
            if not drained:
                logger.warning("Proceeding to dispose database while requests are still in flight!")
    except Exception as exc:
        logger.error("Error while waiting for request drain: %s", exc)

    # v0.4.13 cycle 9 — Phase 5a: stop the WriteQueue with drain budget.
    # Recurring tasks have already been cancelled in Phase 2 so no new
    # submits arrive; we drain in-flight + pending under
    # WRITE_QUEUE_DRAIN_TIMEOUT_SECONDS, then fail any leftover Futures
    # with WriteQueueDeadError. Audit hook is uninstalled afterwards.
    wq = getattr(app.state, "write_queue", None)
    if wq is not None:
        try:
            logger.info(
                "Stopping WriteQueue (drain_timeout=%.1fs)",
                settings.WRITE_QUEUE_DRAIN_TIMEOUT_SECONDS,
            )
            await wq.stop(
                drain_timeout=settings.WRITE_QUEUE_DRAIN_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.error("WriteQueue stop failed: %s", exc, exc_info=True)
        finally:
            app.state.write_queue = None

    # v0.4.13 cycle 9 — Phase 5b: uninstall the audit hook so subsequent
    # disposal (which may issue PRAGMA wal_checkpoint) does not race
    # against a registered listener.
    try:
        from app.database import uninstall_read_engine_audit_hook
        uninstall_read_engine_audit_hook()
    except Exception as exc:
        logger.error("Audit hook uninstall failed: %s", exc)

    try:
        from app.database import dispose
        await dispose()
    except Exception as exc:
        logger.error("Database disposal failed: %s", exc)

    # v0.4.13 cycle 9 — Phase 5c: dispose the writer engine pool.
    try:
        from app.database import dispose_writer
        await dispose_writer()
    except Exception as exc:
        logger.error("Writer engine disposal failed: %s", exc)


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

# ruff: noqa: E402, I001 — request_tracker import below MUST come after CORS
# middleware registration so the tracker wraps user requests correctly.
from app.services.request_tracker import (  # noqa: E402, I001
    RequestTracker,
    RequestTrackerMiddleware,
)

_request_tracker = RequestTracker()
app.state.request_tracker = _request_tracker
app.add_middleware(RequestTrackerMiddleware, tracker=_request_tracker)

# Global exception handler — captures unhandled 500s to structured error JSONL
@app.exception_handler(Exception)
async def _global_exception_handler(request, exc):
    import traceback as _tb

    from fastapi.responses import JSONResponse

    try:
        from app.services.error_logger import get_error_logger

        get_error_logger().log_error(
            service="backend",
            level="error",
            module=type(exc).__module__ or "unknown",
            error_type=type(exc).__name__,
            message=str(exc),
            traceback=_tb.format_exc(),
            request_context={
                "method": request.method,
                "url": str(request.url),
                "client": request.client.host if request.client else None,
            },
        )
    except Exception:
        pass  # Error logger itself failed — don't recurse

    # CORS-safe error response. FastAPI's exception_handler runs OUTSIDE
    # the CORSMiddleware in Starlette's stack, so a bare JSONResponse here
    # ships without ``access-control-allow-origin`` and the browser
    # rejects it with "Failed to fetch" + ERR_FAILED. The user sees a
    # generic CORS error instead of the actual problem (the underlying
    # exception that caused the 500). Echo the request's Origin back
    # when it's in our allowlist so the frontend gets a proper 500
    # response with a useful body.
    headers: dict[str, str] = {}
    origin = request.headers.get("origin")
    if origin and origin in _cors_origins:
        headers["access-control-allow-origin"] = origin
        headers["access-control-allow-credentials"] = "true"
    # Surface a stable error type the frontend can match on for
    # category-aware UI handling (e.g., contention vs auth vs validation).
    body = {
        "detail": "Internal server error",
        "error_type": type(exc).__name__,
    }
    return JSONResponse(status_code=500, content=body, headers=headers)


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

try:
    from app.routers.patterns import router as patterns_router
    app.include_router(patterns_router)
except ImportError:
    pass

try:
    from app.routers.seed import router as seed_router
    app.include_router(seed_router)
except ImportError:
    pass

try:
    from app.routers.monitoring import router as monitoring_router
    app.include_router(monitoring_router)
except ImportError:
    pass

try:
    from app.routers.templates import router as templates_router
    app.include_router(templates_router)
except ImportError:
    pass

try:
    from app.routers.taxonomy_insights import router as taxonomy_insights_router
    app.include_router(taxonomy_insights_router)
except ImportError:
    pass

try:
    from app.routers.update import router as update_router
    app.include_router(update_router)
except ImportError:
    pass

try:
    from app.routers.projects import router as projects_router
    app.include_router(projects_router)
except ImportError:
    pass

try:
    from app.routers.probes import router as probes_router
    app.include_router(probes_router)
except ImportError:
    pass

asgi_app = app
