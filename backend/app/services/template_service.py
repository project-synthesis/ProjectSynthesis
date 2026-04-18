"""Template service — fork immutable PromptTemplate snapshots from clusters.

See docs/superpowers/specs/2026-04-18-template-architecture-design.md.
"""
from __future__ import annotations

import logging

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Optimization,
    OptimizationPattern,
    PromptCluster,
    PromptTemplate,
)
from app.services.event_bus import event_bus
from app.services.taxonomy._constants import _utcnow
from app.services.taxonomy.domain_walk import root_domain_label

logger = logging.getLogger(__name__)


# Note: ``_utcnow`` is imported from ``taxonomy._constants`` above — it returns
# *naive* UTC to match SQLAlchemy ``DateTime()`` round-trip on SQLite.
# ``app.models._utcnow`` is tz-*aware* and therefore NOT a suitable substitute
# for DateTime columns (would raise on naive/aware comparison). ``warm_phases``
# follows the same precedent.


# Maximum parent hops when collecting the ancestor chain for root_domain_label.
# 2× safety margin over taxonomy.domain_walk._DOMAIN_WALK_HOP_CAP (8) — we need
# to collect at least as many nodes as that walker will consume, with headroom
# for any future intermediate grouping levels.
_ANCESTOR_WALK_HOP_CAP = 16


# Documented retire reasons per spec §Data model (Immutability / Soft delete
# row). Callers are free to pass arbitrary strings, but prefer these for
# observability consistency.
RETIRE_REASON_MANUAL = "manual"
RETIRE_REASON_SOURCE_DEGRADED = "source_degraded"
RETIRE_REASON_SOURCE_DISSOLVED = "source_dissolved"


async def _load_ancestor_chain(
    db: AsyncSession,
    leaf: PromptCluster,
) -> dict[str, PromptCluster]:
    """Walk the parent chain from ``leaf`` toward the root.

    Collects ancestors up to and including the first ``state='domain'`` node.
    Stops at the domain node so ``root_domain_label`` treats that domain as
    terminal (parent unreachable) regardless of whether it nests under a
    project node.

    Terminates early on: missing parent_id, cycle (id already collected),
    missing row (dangling FK), or hop-cap exhaustion.
    """
    lookup: dict[str, PromptCluster] = {}
    current_id: str | None = leaf.parent_id
    for _ in range(_ANCESTOR_WALK_HOP_CAP):
        if not current_id or current_id in lookup:
            break
        row = (
            await db.execute(
                select(PromptCluster).where(PromptCluster.id == current_id)
            )
        ).scalar_one_or_none()
        if row is None:
            break
        lookup[row.id] = row
        if row.state == "domain":
            break
        current_id = row.parent_id
    return lookup


class TemplateService:
    """Service for creating and managing PromptTemplate snapshots."""

    async def fork_from_cluster(
        self,
        cluster_id: str,
        db: AsyncSession,
        *,
        auto: bool = True,
    ) -> PromptTemplate | None:
        """Fork an immutable PromptTemplate from a cluster's top optimization.

        Returns the existing live template if one already exists for the
        (cluster, top_optimization) pair. Returns ``None`` if the cluster
        does not exist or has no scored optimizations.
        """
        cluster = (
            await db.execute(
                select(PromptCluster).where(PromptCluster.id == cluster_id)
            )
        ).scalar_one_or_none()
        if cluster is None:
            logger.warning("fork_from_cluster: cluster %s not found", cluster_id)
            return None

        top_opt = (
            await db.execute(
                select(Optimization)
                .where(Optimization.cluster_id == cluster_id)
                .order_by(
                    Optimization.overall_score.desc(),
                    Optimization.created_at.desc(),
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if top_opt is None:
            logger.info(
                "fork_from_cluster: cluster %s has no optimizations", cluster_id
            )
            return None

        existing = (
            await db.execute(
                select(PromptTemplate).where(
                    PromptTemplate.source_cluster_id == cluster_id,
                    PromptTemplate.source_optimization_id == top_opt.id,
                    PromptTemplate.retired_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        domain_lookup = await _load_ancestor_chain(db, cluster)
        domain_label = root_domain_label(cluster, domain_lookup)

        pattern_ids = [
            pid
            for pid in (
                await db.execute(
                    select(OptimizationPattern.meta_pattern_id).where(
                        OptimizationPattern.optimization_id == top_opt.id,
                        OptimizationPattern.meta_pattern_id.is_not(None),
                    )
                )
            ).scalars().all()
        ]

        # Snapshot scalar fields up front. On IntegrityError below we call
        # db.rollback(), which detaches top_opt; any subsequent attribute
        # access would trigger a lazy-load and raise MissingGreenlet under
        # async SQLAlchemy. Reading scalars here keeps the race-loss path
        # greenlet-free.
        top_opt_id = top_opt.id
        top_opt_project_id = top_opt.project_id
        top_opt_prompt = top_opt.optimized_prompt or top_opt.raw_prompt
        top_opt_strategy = top_opt.strategy_used
        top_opt_score = top_opt.overall_score or 0.0

        tpl = PromptTemplate(
            source_cluster_id=cluster_id,
            source_optimization_id=top_opt_id,
            project_id=top_opt_project_id,
            label=cluster.label,
            prompt=top_opt_prompt,
            strategy=top_opt_strategy,
            score=top_opt_score,
            pattern_ids=pattern_ids,
            domain_label=domain_label,
        )
        db.add(tpl)
        try:
            await db.flush()
        except IntegrityError:
            # Race loss: a concurrent session committed the live template
            # first (enforced by the partial unique index). Roll back our
            # pending INSERT and return the winner.
            await db.rollback()
            winner = (
                await db.execute(
                    select(PromptTemplate).where(
                        PromptTemplate.source_cluster_id == cluster_id,
                        PromptTemplate.source_optimization_id == top_opt_id,
                        PromptTemplate.retired_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            logger.debug(
                "fork_from_cluster: race lost for cluster %s, returning winner %s",
                cluster_id,
                winner.id if winner else None,
            )
            return winner

        # Counter bump runs AFTER the INSERT flushes cleanly. If we queued
        # it before the flush, SQLAlchemy autoflush would execute the
        # pending INSERT inside the UPDATE's try-block and surface any
        # IntegrityError from the wrong call site, bypassing the race
        # recovery above.
        await db.execute(
            update(PromptCluster)
            .where(PromptCluster.id == cluster_id)
            .values(template_count=PromptCluster.template_count + 1)
        )
        await db.flush()

        try:
            event_bus.publish(
                "template_forked",
                {
                    "template_id": tpl.id,
                    "cluster_id": cluster_id,
                    "auto": auto,
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning("template_forked event publish failed for %s", tpl.id)

        return tpl

    async def retire(
        self,
        template_id: str,
        db: AsyncSession,
        *,
        reason: str = RETIRE_REASON_MANUAL,
    ) -> bool:
        """Retire a live template.

        Sets ``retired_at`` and ``retired_reason``, then decrements the
        source cluster's ``template_count`` (guarded against underflow).

        Returns ``True`` on successful retirement. Returns ``False`` for
        both "template not found" and "already retired" — callers that
        need to distinguish these cases should do the lookup themselves
        before dispatching. ``reason`` is free-form but prefer the
        ``RETIRE_REASON_*`` module constants for observability.
        """
        tpl = (
            await db.execute(
                select(PromptTemplate).where(PromptTemplate.id == template_id)
            )
        ).scalar_one_or_none()
        if tpl is None:
            logger.warning("retire: template %s not found", template_id)
            return False
        if tpl.retired_at is not None:
            logger.debug("retire: template %s already retired", template_id)
            return False

        tpl.retired_at = _utcnow()
        tpl.retired_reason = reason

        if tpl.source_cluster_id is not None:
            await db.execute(
                update(PromptCluster)
                .where(
                    PromptCluster.id == tpl.source_cluster_id,
                    PromptCluster.template_count > 0,
                )
                .values(template_count=PromptCluster.template_count - 1)
            )

        await db.flush()
        logger.info("retire: template %s retired (reason=%s)", template_id, reason)
        return True

    async def increment_usage(
        self,
        template_id: str,
        db: AsyncSession,
    ) -> None:
        """Atomically bump ``usage_count`` and ``last_used_at`` for a template.

        No-op if the template does not exist; callers should pre-check with
        ``get()`` if 404 semantics are required.
        """
        await db.execute(
            update(PromptTemplate)
            .where(PromptTemplate.id == template_id)
            .values(
                usage_count=PromptTemplate.usage_count + 1,
                last_used_at=_utcnow(),
            )
        )
        await db.flush()
