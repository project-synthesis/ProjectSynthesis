"""Persistence and diagnostic helpers for the sampling pipeline.

Extracted from ``sampling_pipeline``:

* ``resolve_applied_pattern_text`` — load ``MetaPattern`` rows for optimizer context
* ``increment_pattern_usage`` — bump cluster usage counts after a successful run
* ``check_intent_drift`` — cosine similarity gate between original and optimized prompts
* ``fetch_historical_stats`` — score distribution for z-score normalization
* ``track_applied_patterns`` — insert ``OptimizationPattern`` join rows

All previously leading-underscore private names are exported here without the
leading underscore so the package boundary is explicit.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.database import async_session_factory

logger = logging.getLogger(__name__)


async def resolve_applied_pattern_text(
    applied_pattern_ids: list[str],
) -> tuple[str | None, set[str]]:
    """Resolve meta-pattern texts (read-only — no usage increment).

    Returns:
        (applied_text, cluster_ids) — text for optimizer context + family IDs
        for deferred usage increment after successful completion.
    """
    try:
        from app.models import MetaPattern

        async with async_session_factory() as db:
            result = await db.execute(
                select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
            )
            patterns = result.scalars().all()
            if not patterns:
                return None, set()

            lines = [f"- {p.pattern_text}" for p in patterns]
            applied_text = (
                "The following proven patterns from past optimizations "
                "should be applied where relevant:\n"
                + "\n".join(lines)
            )

            cluster_ids = {p.cluster_id for p in patterns}
            logger.info(
                "Sampling: resolved %d applied patterns from %d families",
                len(patterns), len(cluster_ids),
            )
            return applied_text, cluster_ids
    except Exception as exc:
        logger.warning("Failed to resolve applied patterns in sampling: %s", exc)
        return None, set()


async def increment_pattern_usage(cluster_ids: set[str]) -> None:
    """Increment usage counts for applied pattern families (post-optimization)."""
    if not cluster_ids:
        return
    try:
        from app.models import PromptCluster
        from app.services.taxonomy import get_engine

        engine = get_engine()
        async with async_session_factory() as db:
            for fid in cluster_ids:
                try:
                    await engine.increment_usage(fid, db)
                except Exception as usage_exc:
                    logger.warning("Usage propagation failed for %s: %s", fid, usage_exc)
                    from sqlalchemy import update as sa_update
                    await db.execute(
                        sa_update(PromptCluster)
                        .where(PromptCluster.id == fid)
                        .values(usage_count=PromptCluster.usage_count + 1)
                    )
            await db.commit()
    except Exception as exc:
        logger.warning("Sampling usage increment failed: %s", exc)


async def check_intent_drift(
    original_prompt: str, optimized_prompt: str,
) -> str | None:
    """Check semantic similarity between original and optimized prompt.

    Returns a warning string if similarity is below 0.5, or None.
    """
    import numpy as np

    from app.services.embedding_service import EmbeddingService

    svc = EmbeddingService()
    orig_vec = await svc.aembed_single(original_prompt)
    opt_vec = await svc.aembed_single(optimized_prompt)
    similarity = float(
        np.dot(orig_vec, opt_vec)
        / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9)
    )

    if similarity < 0.5:
        logger.warning("Sampling intent drift detected: similarity=%.2f", similarity)
        return (
            f"Intent drift detected: semantic similarity {similarity:.2f} "
            f"between original and optimized prompt is below threshold (0.50)"
        )
    return None


async def fetch_historical_stats() -> dict | None:
    """Fetch score distribution for z-score normalization (non-fatal)."""
    try:
        from app.services.optimization_service import OptimizationService

        async with async_session_factory() as db:
            svc = OptimizationService(db)
            return await svc.get_score_distribution(
                exclude_scoring_modes=["heuristic"],
            )
    except Exception as exc:
        logger.debug("Historical stats unavailable for sampling normalization: %s", exc)
        return None


async def track_applied_patterns(
    db: Any, opt_id: str, applied_pattern_ids: list[str],
) -> None:
    """Record applied patterns in the OptimizationPattern join table."""
    try:
        from app.models import MetaPattern, OptimizationPattern

        for pid in applied_pattern_ids:
            mp_result = await db.execute(
                select(MetaPattern).where(MetaPattern.id == pid)
            )
            mp = mp_result.scalar_one_or_none()
            if mp:
                db.add(OptimizationPattern(
                    optimization_id=opt_id,
                    cluster_id=mp.cluster_id,
                    meta_pattern_id=mp.id,
                    relationship="applied",
                ))
    except Exception as exc:
        logger.warning("Failed to track applied patterns in sampling: %s", exc)


__all__ = [
    "check_intent_drift",
    "fetch_historical_stats",
    "increment_pattern_usage",
    "resolve_applied_pattern_text",
    "track_applied_patterns",
]
