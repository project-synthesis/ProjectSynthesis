"""Bulk persistence + taxonomy assignment for batch seed results.

Extracted from ``batch_pipeline`` in Phase 3B of the code-quality sweep:

* ``bulk_persist`` — single-transaction insert of completed
  ``PendingOptimization`` rows with quality gate, idempotency check, and
  transient-failure retry. Emits one ``optimization_created`` event per
  inserted row.
* ``batch_taxonomy_assign`` — post-persist cluster assignment: embeds each
  row into the taxonomy via ``family_ops.assign_cluster()``, writes back
  ``cluster_id`` on the ``Optimization`` row, inserts the ``source``
  ``OptimizationPattern`` join record, and defers pattern extraction to the
  warm path via ``pattern_stale=True``.

Both helpers are idempotent: re-running ``bulk_persist`` for the same
``batch_id`` skips rows that already exist, and ``batch_taxonomy_assign``
never creates duplicate ``source`` join rows because it runs once per batch.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import numpy as np
from sqlalchemy import select as sa_select

from app.models import Optimization, OptimizationPattern
from app.services.event_bus import event_bus
from app.services.taxonomy import get_engine
from app.services.taxonomy.cluster_meta import write_meta
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import assign_cluster

if TYPE_CHECKING:
    from app.services.batch_pipeline import PendingOptimization, SessionFactory

logger = logging.getLogger(__name__)

# Pool-aware serialization lock. With ``pool_size=1``, concurrent
# ``bulk_persist`` calls (from ``probe_service._persist_one()`` per-prompt
# concurrent invocations and the batch pipeline's post-batch flush) race
# on pool checkout: each caller waits up to 30s for the sole connection,
# then retries with exponential backoff — 5 attempts x 30s timeout = 150s
# stall storms. This asyncio.Lock serializes callers in-process, so they
# queue cleanly instead of racing to pool timeout. The
# ``WriterLockedAsyncSession`` handles intra-session write serialization;
# this lock prevents inter-call contention at the function boundary before
# the session is even opened.
_persist_lock = asyncio.Lock()


async def bulk_persist(
    results: list[PendingOptimization],
    session_factory: SessionFactory,
    batch_id: str,
) -> int:
    """Persist all completed optimizations in a single transaction.

    Returns count of rows inserted. Skips failed optimizations.
    Idempotent: skips prompts already persisted for this batch_id.
    Includes retry logic — one retry after 5s on transient failures.
    """
    t0 = time.monotonic()
    # ID-shape gate: reject test-fixture-pattern IDs.  Production rows
    # always use ``uuid4()`` (length 36, hyphens at positions 8/13/18/23).
    # Test fixtures can leak `opt-NN-XX` / `tr-NN` shapes into production
    # if the test harness's session-factory monkeypatch fails to reach
    # the writer (in-process import-binding edge case observed in
    # v0.4.12 — see docs/audits/test-leak-2026-04-29.md). Rejecting at
    # the persistence boundary is the simplest belt-and-suspenders
    # against future test-isolation regressions.
    import uuid as _uuid
    completed_raw = [r for r in results if r.status == "completed"]
    id_rejected = 0
    quality_rejected = 0
    completed = []
    seed_min_score = 5.0
    for r in completed_raw:
        try:
            _uuid.UUID(r.id)  # raises if not a valid uuid
        except (ValueError, AttributeError, TypeError):
            id_rejected += 1
            logger.warning(
                "Bulk persist ID-shape gate: rejected non-uuid id %r "
                "(batch_id=%s) -- this typically means a test fixture "
                "leaked into production; investigate the caller.",
                r.id, batch_id,
            )
            continue
        # Quality gate: filter out low-quality seeds before persisting.
        # Seeds with overall_score < 5.0 add noise to the taxonomy +
        # few-shot pool without providing value.
        if r.overall_score is not None and r.overall_score < seed_min_score:
            quality_rejected += 1
            logger.info(
                "Seed quality gate: rejected %s (score=%.2f, improvement=%.2f)",
                r.id[:8], r.overall_score, r.improvement_score or 0.0,
            )
            continue
        completed.append(r)
    if id_rejected:
        logger.warning(
            "Bulk persist: rejected %d/%d rows on ID-shape gate",
            id_rejected, len(completed_raw),
        )
    if quality_rejected:
        logger.info("Seed quality gate: %d/%d rejected (min_score=%.1f)",
                     quality_rejected, len(completed_raw), seed_min_score)

    if not completed:
        return 0

    inserted = 0
    inserted_pendings: list[PendingOptimization] = []
    # Writer serialization is handled automatically by
    # ``WriterLockedAsyncSession`` (see app/database.py). The retry loop
    # is defense-in-depth for CROSS-PROCESS contention -- the asyncio
    # writer lock only serializes within ONE process. Other processes
    # (MCP server, pytest test runner during dev workflows, anything
    # using the same SQLite file) compete at the file lock layer where
    # only ``busy_timeout=30s`` applies. Generous retry sizing here
    # absorbs realistic contention windows (a long-held warm-engine
    # write or a test-suite batch insert) without losing the batch.
    # 5 attempts x exponential backoff (5/10/20/40s = ~75s total) is
    # tuned for the worst case observed in v0.4.12: a full backend
    # test suite (5min) running concurrently with a live probe.
    #
    # ``_persist_lock`` serializes in-process callers at the function
    # boundary so they queue cleanly instead of racing to pool timeout.
    _MAX_PERSIST_ATTEMPTS = 5  # noqa: N806 — local constant
    _PERSIST_BACKOFF_SECS = 5.0  # noqa: N806 — local constant
    async with _persist_lock:
        for attempt in range(_MAX_PERSIST_ATTEMPTS):
            try:
                async with session_factory() as db:
                    # Idempotency check: find already-persisted IDs for this batch
                    existing_ids_result = await db.execute(
                        sa_select(Optimization.id).where(
                            Optimization.context_sources.op("->>")(
                                "batch_id"
                            ) == batch_id
                        )
                    )
                    existing_ids: set[str] = {row[0] for row in existing_ids_result}
                    inserted = 0  # Reset for retry
                    inserted_pendings = []  # Reset for retry
                    for pending in completed:
                        if pending.id in existing_ids:
                            logger.debug(
                                "Skipping already-persisted optimization %s (batch_id=%s)",
                                pending.id[:8], batch_id,
                            )
                            continue

                        db_opt = Optimization(
                            id=pending.id,
                            trace_id=pending.trace_id,
                            raw_prompt=pending.raw_prompt,
                            optimized_prompt=pending.optimized_prompt,
                            task_type=pending.task_type,
                            strategy_used=pending.strategy_used,
                            changes_summary=pending.changes_summary,
                            score_clarity=pending.score_clarity,
                            score_specificity=pending.score_specificity,
                            score_structure=pending.score_structure,
                            score_faithfulness=pending.score_faithfulness,
                            score_conciseness=pending.score_conciseness,
                            overall_score=pending.overall_score,
                            improvement_score=pending.improvement_score,
                            scoring_mode=pending.scoring_mode,
                            intent_label=pending.intent_label,
                            domain=pending.domain,
                            domain_raw=pending.domain_raw,
                            embedding=pending.embedding,
                            optimized_embedding=pending.optimized_embedding,
                            transformation_embedding=pending.transformation_embedding,
                            models_by_phase=pending.models_by_phase,
                            original_scores=pending.original_scores,
                            score_deltas=pending.score_deltas,
                            duration_ms=pending.duration_ms,
                            status=pending.status,
                            provider=pending.provider,
                            model_used=pending.model_used,
                            routing_tier=pending.routing_tier,
                            heuristic_flags=pending.heuristic_flags,
                            suggestions=pending.suggestions,
                            repo_full_name=pending.repo_full_name,
                            project_id=pending.project_id,
                            context_sources=pending.context_sources,
                        )
                        db.add(db_opt)
                        inserted += 1
                        inserted_pendings.append(pending)

                    await db.commit()

                    # Post-commit injection provenance (task #97). Probe and
                    # seed rows used to land with ZERO ``relationship='injected'``
                    # join rows because ``auto_inject_patterns`` ran during
                    # enrichment (BEFORE this commit) and its in-line SAVEPOINT
                    # silently rolled back on the FK-on-Optimization miss.
                    # Now that the parent rows are durable, replay the
                    # provenance write per row for any pending that captured
                    # injected patterns. Mirrors what
                    # ``pipeline_phases.persist_and_propagate`` does for the
                    # canonical pipeline.py path.
                    from app.services.pattern_injection import (
                        record_injection_provenance,
                    )
                    provenance_written = 0
                    provenance_failed = 0
                    for pending in inserted_pendings:
                        inj = pending.auto_injected_patterns
                        cids = pending.auto_injected_cluster_ids or []
                        sim_map = pending.auto_injected_similarity_map
                        if not inj and not cids:
                            continue
                        try:
                            await record_injection_provenance(
                                db,
                                optimization_id=pending.id,
                                cluster_ids=list(cids),
                                injected=list(inj or []),
                                similarity_map=sim_map,
                                trace_id=pending.trace_id,
                            )
                            provenance_written += 1
                        except Exception as _prov_exc:
                            provenance_failed += 1
                            logger.warning(
                                "Post-commit injection provenance failed for "
                                "%s (non-fatal): %s",
                                pending.id[:8], _prov_exc,
                            )
                    if provenance_written or provenance_failed:
                        try:
                            await db.commit()
                        except Exception as _c_exc:
                            logger.warning(
                                "Provenance commit failed (non-fatal): %s",
                                _c_exc,
                            )
                        logger.info(
                            "Bulk persist provenance: %d rows written, %d failed",
                            provenance_written, provenance_failed,
                        )
                break  # success
            except Exception as exc:
                if attempt < _MAX_PERSIST_ATTEMPTS - 1:
                    delay = _PERSIST_BACKOFF_SECS * (2 ** attempt)
                    logger.warning(
                        "Bulk persist attempt %d/%d failed (%s); retry in %.0fs",
                        attempt + 1, _MAX_PERSIST_ATTEMPTS, exc, delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    raise

    # Per-prompt event emission — parallels the regular pipeline contract so
    # frontend history refresh and cross-process MCP bridge fire reliably.
    # `source="batch_seed"` lets consumers distinguish seed-originated rows
    # from text-editor optimizations while batch-level `seed_*` events still
    # stream the coarser batch progress view.
    if inserted_pendings:
        # Rate-limit auto-clear: when a batch successfully persists rows
        # whose routing_tier is NOT the passthrough fallback (i.e. real
        # LLM calls succeeded against a previously-limited provider), the
        # rate limit has lifted. Publish ``rate_limit_cleared`` so the
        # frontend banner clears without waiting for the stale reset_at
        # countdown.  Idempotent at the consumer layer (the store
        # ignores clear events for providers it doesn't track).
        cleared_providers: set[str] = set()
        for pending in inserted_pendings:
            tier_val = pending.routing_tier or ""
            prov = pending.provider or ""
            if tier_val != "passthrough_fallback" and prov:
                cleared_providers.add(prov)
        for prov in cleared_providers:
            try:
                event_bus.publish("rate_limit_cleared", {
                    "provider": prov,
                    "source": "batch_seed",
                    "batch_id": batch_id,
                })
            except Exception:
                logger.debug(
                    "rate_limit_cleared publish failed", exc_info=True,
                )
        try:
            for pending in inserted_pendings:
                event_bus.publish("optimization_created", {
                    "id": pending.id,
                    "trace_id": pending.trace_id,
                    "task_type": pending.task_type,
                    "intent_label": pending.intent_label or "general",
                    "domain": pending.domain,
                    "domain_raw": pending.domain_raw,
                    "strategy_used": pending.strategy_used,
                    "overall_score": pending.overall_score,
                    "provider": pending.provider,
                    "status": pending.status,
                    "routing_tier": pending.routing_tier,
                    "source": "batch_seed",
                    "batch_id": batch_id,
                })
        except Exception:
            logger.debug("Event bus publish failed", exc_info=True)

    duration_ms = int((time.monotonic() - t0) * 1000)

    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_persist_complete",
            context={
                "batch_id": batch_id,
                "rows_inserted": inserted,
                "rows_skipped_idempotent": len(completed) - inserted,
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    logger.info("Bulk persist: %d rows in %dms", inserted, duration_ms)
    return inserted


async def batch_taxonomy_assign(
    results: list[PendingOptimization],
    session_factory: SessionFactory,
    batch_id: str,
) -> dict[str, Any]:
    """Assign clusters for all persisted optimizations in one transaction.

    Pattern extraction is deferred (pattern_stale=True) — the warm path
    handles it after the batch completes.

    Returns summary dict with clusters_assigned, clusters_created, domains_touched.
    """
    t0 = time.monotonic()
    completed = [r for r in results if r.status == "completed" and r.embedding]
    clusters_created = 0
    domains_touched: set[str] = set()

    if not completed:
        return {"clusters_assigned": 0, "clusters_created": 0, "domains_touched": []}

    engine = get_engine()
    assigned = 0

    # Writer serialization is automatic via ``WriterLockedAsyncSession``
    # (see app/database.py) -- the session acquires ``db_writer_lock`` at
    # first flush and releases at commit/rollback/close.
    async with session_factory() as db:
        for pending in completed:
            try:
                embedding = np.frombuffer(pending.embedding, dtype=np.float32)  # type: ignore[arg-type]
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=pending.intent_label or "general",
                    domain=pending.domain or "general",
                    task_type=pending.task_type or "general",
                    overall_score=pending.overall_score,
                    embedding_index=engine._embedding_index,
                )

                # Write cluster_id back to the Optimization row (matches engine.py hot path)
                opt_row = await db.execute(
                    sa_select(Optimization).where(Optimization.id == pending.id)
                )
                opt = opt_row.scalar_one_or_none()
                if opt is not None:
                    opt.cluster_id = cluster.id

                # Create OptimizationPattern join record so downstream consumers
                # (history, detail view, lifecycle, pattern injection) can find this
                # optimization's cluster. Matches engine.py hot path step 5.
                db.add(OptimizationPattern(
                    optimization_id=pending.id,
                    cluster_id=cluster.id,
                    relationship="source",
                ))

                # Track what was created
                if cluster.member_count == 1:
                    clusters_created += 1
                domains_touched.add(pending.domain or "general")
                assigned += 1

                # Defer pattern extraction to warm path
                cluster.cluster_metadata = write_meta(
                    cluster.cluster_metadata, pattern_stale=True,
                )

            except Exception as exc:
                logger.warning(
                    "Taxonomy assign failed for %s: %s",
                    pending.id[:8], exc,
                )

        await db.commit()

    duration_ms = int((time.monotonic() - t0) * 1000)
    domains_list = sorted(domains_touched)

    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_taxonomy_complete",
            context={
                "batch_id": batch_id,
                "clusters_assigned": assigned,
                "clusters_created": clusters_created,
                "domains_touched": domains_list,
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    # Trigger warm path (single event — debounce handles the rest)
    try:
        event_bus.publish("taxonomy_changed", {
            "trigger": "batch_seed",
            "batch_id": batch_id,
            "clusters_created": clusters_created,
        })
    except Exception as _bus_exc:
        logger.warning("taxonomy_changed publish failed after batch seed: %s", _bus_exc)

    logger.info(
        "Taxonomy assign: %d clusters (%d new), domains=%s (%dms)",
        assigned, clusters_created, domains_list, duration_ms,
    )

    return {
        "clusters_assigned": assigned,
        "clusters_created": clusters_created,
        "domains_touched": domains_list,
    }


__all__ = ["batch_taxonomy_assign", "bulk_persist"]
