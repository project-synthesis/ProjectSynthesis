"""Shared auto-injection logic for cluster meta-patterns.

Used by both the internal pipeline (``pipeline.py``) and the sampling-based
pipeline (``sampling_pipeline.py``) to discover and inject relevant patterns
from the taxonomy embedding index.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class InjectedPattern:
    """Structured metadata for an auto-injected meta-pattern."""

    pattern_text: str
    cluster_label: str
    domain: str
    similarity: float
    cluster_id: str = ""


def format_injected_patterns(
    auto_injected: list[InjectedPattern],
    existing_text: str | None = None,
) -> str | None:
    """Format InjectedPattern objects into the applied_patterns template variable.

    Merges with any existing applied_patterns_text from explicit pattern IDs.
    """
    if not auto_injected:
        return existing_text

    lines = []
    for ip in auto_injected:
        lines.append(
            f"- [{ip.domain} | {ip.similarity:.2f}] {ip.pattern_text}\n"
            f'  Source: "{ip.cluster_label}" cluster'
        )

    if existing_text:
        return existing_text + "\n" + "\n".join(lines)

    return (
        "The following proven patterns from past optimizations "
        "should be applied where relevant:\n"
        + "\n".join(lines)
    )


async def auto_inject_patterns(
    raw_prompt: str,
    taxonomy_engine: Any,
    db: AsyncSession,
    trace_id: str,
    optimization_id: str | None = None,
) -> tuple[list[InjectedPattern], list[str]]:
    """Auto-inject cluster meta-patterns based on prompt embedding similarity.

    Embeds the raw prompt, searches the taxonomy embedding index for the
    nearest active clusters (cosine >= 0.60), and fetches their associated
    ``MetaPattern`` texts with cluster metadata.

    Args:
        raw_prompt: The user's raw prompt text.
        taxonomy_engine: A ``TaxonomyEngine`` instance with an ``embedding_index``.
        db: Active async DB session for querying MetaPattern records.
        trace_id: Pipeline trace ID for log correlation.
        optimization_id: Optional optimization ID for persisting injection
            provenance as ``OptimizationPattern`` records with
            ``relationship="injected"``.  When ``None`` (default), no records
            are written — backward compatible with callers that lack an ID.

    Returns:
        ``(injected_patterns, cluster_ids)`` — both empty lists if no match or error.
    """
    from app.models import MetaPattern, PromptCluster
    from app.services.embedding_service import EmbeddingService

    embedding_svc = EmbeddingService()
    embedding_index = taxonomy_engine.embedding_index
    if embedding_index.size == 0:
        logger.info(
            "Taxonomy embedding index empty, skipping auto-injection. trace_id=%s",
            trace_id,
        )
        return [], []

    prompt_embedding = await embedding_svc.aembed_single(raw_prompt)
    # Threshold 0.45: broad clusters (post-cold-path merge) have averaged
    # centroids that score ~0.45-0.55 against specific prompts. The
    # optimizer prompt's precision instructions handle relevance filtering.
    matches = embedding_index.search(prompt_embedding, k=5, threshold=0.45)
    if not matches:
        logger.info(
            "No pattern matches above threshold (0.45). trace_id=%s",
            trace_id,
        )
        return [], []

    cluster_ids = [m[0] for m in matches]
    similarity_map = {m[0]: m[1] for m in matches}

    # Fetch cluster metadata (label, domain) for context
    cluster_result = await db.execute(
        select(PromptCluster.id, PromptCluster.label, PromptCluster.domain).where(
            PromptCluster.id.in_(cluster_ids)
        )
    )
    cluster_meta = {
        row.id: (row.label or "unnamed", row.domain or "general")
        for row in cluster_result
    }

    # Fetch meta-patterns
    result = await db.execute(
        select(MetaPattern).where(MetaPattern.cluster_id.in_(cluster_ids))
    )
    patterns = result.scalars().all()
    if not patterns:
        return [], cluster_ids

    injected = []
    for p in patterns:
        label, domain = cluster_meta.get(p.cluster_id, ("unnamed", "general"))
        sim = similarity_map.get(p.cluster_id, 0.0)
        injected.append(InjectedPattern(
            pattern_text=p.pattern_text,
            cluster_label=label,
            domain=domain,
            similarity=round(sim, 2),
            cluster_id=p.cluster_id,
        ))

    # Persist injection provenance when optimization_id is available.
    # Uses flush() to eagerly detect constraint violations — if provenance
    # fails, the pending objects are expunged so the main Optimization
    # commit is not affected.
    if optimization_id and cluster_ids:
        try:
            from app.models import OptimizationPattern

            pending: list[OptimizationPattern] = []
            for cid in cluster_ids:
                record = OptimizationPattern(
                    optimization_id=optimization_id,
                    cluster_id=cid,
                    relationship="injected",
                    similarity=similarity_map.get(cid),
                )
                db.add(record)
                pending.append(record)
            await db.flush()
            logger.info(
                "Injection provenance: %d records for opt=%s clusters=[%s]. trace_id=%s",
                len(pending), optimization_id[:8],
                ", ".join(cid[:8] for cid in cluster_ids), trace_id,
            )
        except Exception as exc:
            # Expunge failed records so they don't poison the main commit
            for record in pending:
                try:
                    db.expunge(record)
                except Exception:
                    pass
            logger.warning(
                "Injection provenance failed (non-fatal, expunged): %s trace_id=%s",
                exc, trace_id,
            )

    # Detailed injection chain log for observability
    cluster_summary = ", ".join(
        f"{label} ({domain}, sim={similarity_map.get(cid, 0):.2f})"
        for cid, (label, domain) in cluster_meta.items()
    )
    logger.info(
        "Auto-injected %d patterns from %d clusters [%s]. trace_id=%s",
        len(injected),
        len(cluster_ids),
        cluster_summary,
        trace_id,
    )
    return injected, cluster_ids
