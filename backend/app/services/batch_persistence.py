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

import logging
import time
import uuid
from typing import TYPE_CHECKING, TypedDict

import numpy as np
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, OptimizationPattern
from app.services.event_bus import event_bus
from app.services.taxonomy import get_engine
from app.services.taxonomy.cluster_meta import write_meta
from app.services.taxonomy.event_logger import get_event_logger
from app.services.taxonomy.family_ops import assign_cluster
from app.services.write_queue import WriteQueue

if TYPE_CHECKING:
    from app.services.batch_pipeline import PendingOptimization, SessionFactory

logger = logging.getLogger(__name__)


class TaxonomyAssignSummary(TypedDict):
    """Return shape of ``batch_taxonomy_assign``.

    Stable contract:

    * ``clusters_assigned`` — count of pendings successfully wired to a
      cluster + ``OptimizationPattern(relationship='source')`` row.
    * ``clusters_created`` — subset of ``clusters_assigned`` where the
      target cluster was net-new (``member_count == 1`` after assign).
    * ``domains_touched`` — sorted, de-duplicated list of domain labels
      affected by this batch (input source: ``pending.domain``).

    The post-queue ``seed_taxonomy_complete`` decision event and
    ``taxonomy_changed`` event-bus payload index by these keys verbatim;
    keep them stable across cycles.
    """

    clusters_assigned: int
    clusters_created: int
    domains_touched: list[str]


async def bulk_persist(
    results: list[PendingOptimization],
    queue_or_session_factory: WriteQueue | SessionFactory,
    batch_id: str,
) -> int:
    """Persist all completed optimizations in a single transaction.

    v0.4.13 cycle 2: writes go through ``WriteQueue.submit`` when a
    ``WriteQueue`` is supplied. The queue serializes against every other
    backend writer, so the v0.4.12 ``_persist_lock`` and 5-attempt retry
    loop are removed — contention is eliminated by construction.

    The second positional argument is a transitional union:

    * ``WriteQueue`` — canonical form. Used by callers already migrated to
      the single-writer queue (v0.4.13 batch_pipeline + new probe paths).
    * ``async_sessionmaker``-style ``SessionFactory`` — **legacy**, retained
      only so cycles 3–6 can land without breaking the still-unmigrated
      callers ``app/tools/seed.py`` and ``app/services/probe_service.py``.
      Slated for removal in **cycle 7** when those callers inject the
      queue. After cycle 7, the parameter type collapses to ``WriteQueue``.

    Detection is ``isinstance(queue_or_session_factory, WriteQueue)``;
    mypy narrows the parameter to ``WriteQueue`` in the ``if`` branch and
    to ``SessionFactory`` in the ``else``.

    Returns count of rows inserted. Skips failed optimizations.
    Idempotent: skips prompts already persisted for this ``batch_id``.

    Failure semantics:
        If ``submit()`` raises (e.g. ``WriteQueueOverloadedError``,
        ``WriteQueueDeadError``, ``WriteQueueStoppedError``,
        ``asyncio.TimeoutError``), the exception propagates to the caller
        WITHOUT emitting ``optimization_created`` or ``rate_limit_cleared``
        events. This is intentional: events represent durable persistence,
        so a failed submit cannot fire them. Callers handle batch-level
        error recovery (e.g. retry the whole submit, log and skip).

        Future maintainers: do NOT wrap ``submit()`` in a try/except that
        swallows the exception and continues to the event-emission block —
        that would fire phantom events for rows that never persisted.
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
    completed_raw = [r for r in results if r.status == "completed"]
    id_rejected = 0
    quality_rejected = 0
    completed: list[PendingOptimization] = []
    seed_min_score = 5.0
    for r in completed_raw:
        try:
            uuid.UUID(r.id)  # raises if not a valid uuid
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

    async def _do_persist(db: AsyncSession) -> tuple[int, list[PendingOptimization]]:
        # Idempotency check: find already-persisted IDs for this batch
        existing_ids_result = await db.execute(
            sa_select(Optimization.id).where(
                Optimization.context_sources.op("->>")(
                    "batch_id"
                ) == batch_id
            )
        )
        existing_ids: set[str] = {row[0] for row in existing_ids_result}
        inserted_local = 0
        inserted_pendings_local: list[PendingOptimization] = []
        for pending in completed:
            if pending.id in existing_ids:
                logger.debug(
                    "Skipping already-persisted optimization %s (batch_id=%s)",
                    pending.id[:8], batch_id,
                )
                continue

            db.add(Optimization(
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
            ))
            inserted_local += 1
            inserted_pendings_local.append(pending)

        # CRITICAL (CRIT-6 / spec § 3.4): commit BEFORE
        # ``record_injection_provenance``. The provenance writer wraps each
        # row's join insert in ``begin_nested()`` SAVEPOINT which enforces
        # the FK on ``Optimization.id`` — that FK requires the parent
        # row to be durable. v0.4.5 invariant.
        await db.commit()

        from app.services.pattern_injection import (
            record_injection_provenance,
        )
        for pending in inserted_pendings_local:
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
            except Exception as _prov_exc:
                logger.warning(
                    "Post-commit injection provenance failed for "
                    "%s (non-fatal): %s",
                    pending.id[:8], _prov_exc,
                )
        # NO second db.commit() — record_injection_provenance commits its
        # own SAVEPOINTs per call (v0.4.5 pattern).
        return inserted_local, inserted_pendings_local

    if isinstance(queue_or_session_factory, WriteQueue):
        # Canonical path: the queue serializes ``_do_persist`` against
        # every other backend writer. ``operation_label`` surfaces in
        # ``WriteQueueMetrics`` snapshots and ``write_queue.complete``
        # decision events so health-endpoint consumers can attribute
        # latency to the bulk-persist op.
        inserted, inserted_pendings = await queue_or_session_factory.submit(
            _do_persist, operation_label="bulk_persist",
        )
    else:
        # Legacy ``SessionFactory`` path — retired in cycle 7 once
        # ``app/tools/seed.py`` + ``app/services/probe_service.py`` inject
        # the queue. Single-attempt commit semantics: the v0.4.12
        # ``_persist_lock`` + 5-attempt retry loop are gone — the canonical
        # contention solution lives on the queue path now. **Known
        # transitional risk between cycles 2 and 7**: real production
        # callers (``tools/seed.py`` + ``probe_service.py``) still use this
        # branch with the ``async_session_factory``; under heavy concurrent
        # writer pressure, ``database is locked`` exceptions will propagate
        # to the caller instead of retrying. Mitigated by:
        # (a) ``probe_service._persist_lock`` still serializes per-prompt
        # persists in-process for that orchestrator, and
        # (b) cycle 7 migrates both callers, fully closing the gap.
        # Until cycle 7 lands, avoid running probes + concurrent /api/seed
        # batches simultaneously in production.
        session_factory = queue_or_session_factory
        async with session_factory() as db:
            inserted, inserted_pendings = await _do_persist(db)

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
    queue_or_session_factory: WriteQueue | SessionFactory,
    batch_id: str,
) -> TaxonomyAssignSummary:
    """Assign clusters for all persisted optimizations in one transaction.

    v0.4.13 cycle 3: writes go through ``WriteQueue.submit`` when a
    ``WriteQueue`` is supplied. Mirrors the cycle 2 ``bulk_persist``
    Option C dual-typed pattern — same isinstance dispatch, same
    transitional ``SessionFactory`` retention until cycle 7.

    The second positional argument is a transitional union:

    * ``WriteQueue`` — canonical form. The queue serializes the
      assign-loop callback against every other backend writer, so
      file-mode WAL contention is eliminated by construction.
    * ``async_sessionmaker``-style ``SessionFactory`` — **legacy**, retained
      so cycles 3–6 can land without breaking the still-unmigrated
      callers ``app/tools/seed.py`` and ``app/services/probe_service.py``.
      Slated for removal in **cycle 7**. After cycle 7 the parameter
      type collapses to ``WriteQueue``.

    Detection is ``isinstance(queue_or_session_factory, WriteQueue)``;
    mypy narrows the parameter to ``WriteQueue`` in the ``if`` branch and
    to ``SessionFactory`` in the ``else``.

    Pattern extraction is deferred (``pattern_stale=True``) — the warm
    path handles it after the batch completes.

    Returns ``TaxonomyAssignSummary`` (``TypedDict``) with
    ``clusters_assigned``, ``clusters_created``, ``domains_touched``.
    Empty/no-embedding inputs short-circuit and return zeros without
    calling ``submit()`` — no queue work, no events, no log decision.
    Per-pending failures inside ``_do_assign`` are absorbed (warning
    logged, counters NOT incremented), so partial-batch progress is
    durable.

    Failure semantics:
        If ``submit()`` raises (e.g. ``WriteQueueOverloadedError``,
        ``WriteQueueDeadError``, ``WriteQueueStoppedError``,
        ``asyncio.TimeoutError``), the exception propagates to the caller
        WITHOUT publishing the ``taxonomy_changed`` event or logging
        ``seed_taxonomy_complete``. This is intentional: those events
        represent durable taxonomy state, so a failed submit cannot fire
        them. Callers handle batch-level error recovery (the probe path
        already wraps the call in try/except and logs a non-fatal warning).

        Future maintainers: do NOT wrap ``submit()`` in a try/except that
        swallows the exception and continues to the event-emission block —
        that would fire phantom ``taxonomy_changed`` events for taxonomy
        state that never persisted.
    """
    t0 = time.monotonic()
    completed = [r for r in results if r.status == "completed" and r.embedding]

    if not completed:
        return TaxonomyAssignSummary(
            clusters_assigned=0, clusters_created=0, domains_touched=[],
        )

    engine = get_engine()

    async def _do_assign(db: AsyncSession) -> TaxonomyAssignSummary:
        clusters_created = 0
        domains_touched: set[str] = set()
        assigned = 0
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
        return TaxonomyAssignSummary(
            clusters_assigned=assigned,
            clusters_created=clusters_created,
            domains_touched=sorted(domains_touched),
        )

    if isinstance(queue_or_session_factory, WriteQueue):
        # Canonical path: the queue serializes ``_do_assign`` against
        # every other backend writer. ``operation_label`` surfaces in
        # ``WriteQueueMetrics`` snapshots and ``write_queue.complete``
        # decision events so health-endpoint consumers can attribute
        # latency to the taxonomy-assign op.
        summary = await queue_or_session_factory.submit(
            _do_assign, operation_label="batch_taxonomy_assign",
        )
    else:
        # Legacy ``SessionFactory`` path — retired in cycle 7 once
        # ``app/tools/seed.py`` + ``app/services/probe_service.py`` inject
        # the queue. Single-attempt commit semantics: writer serialization
        # is automatic via ``WriterLockedAsyncSession`` (see
        # app/database.py) -- the session acquires ``db_writer_lock`` at
        # first flush and releases at commit/rollback/close. Same
        # transitional risk window as ``bulk_persist`` (see its docstring);
        # closes when both callers migrate in cycle 7.
        session_factory = queue_or_session_factory
        async with session_factory() as db:
            summary = await _do_assign(db)

    duration_ms = int((time.monotonic() - t0) * 1000)

    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_taxonomy_complete",
            context={
                "batch_id": batch_id,
                "clusters_assigned": summary["clusters_assigned"],
                "clusters_created": summary["clusters_created"],
                "domains_touched": summary["domains_touched"],
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
            "clusters_created": summary["clusters_created"],
        })
    except Exception as _bus_exc:
        logger.warning("taxonomy_changed publish failed after batch seed: %s", _bus_exc)

    logger.info(
        "Taxonomy assign: %d clusters (%d new), domains=%s (%dms)",
        summary["clusters_assigned"],
        summary["clusters_created"],
        summary["domains_touched"],
        duration_ms,
    )

    return summary


__all__ = ["TaxonomyAssignSummary", "batch_taxonomy_assign", "bulk_persist"]
