"""Shared auto-injection logic for cluster meta-patterns.

Used by both the internal pipeline (``pipeline.py``) and the sampling-based
pipeline (``sampling_pipeline.py``) to discover and inject relevant patterns
from the taxonomy embedding index.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Module-level counters for health check observability.
# Increment on provenance failures so the health endpoint can surface
# injection reliability without querying the DB.
_injection_provenance_failures: int = 0
_injection_provenance_successes: int = 0


def get_injection_stats() -> dict[str, int]:
    """Return injection provenance success/failure counts for health reporting."""
    return {
        "provenance_successes": _injection_provenance_successes,
        "provenance_failures": _injection_provenance_failures,
    }


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

    injected: list[InjectedPattern] = []
    cluster_ids: list[str] = []
    similarity_map: dict[str, float] = {}
    cluster_meta: dict[str, tuple[str, str]] = {}
    topic_pattern_ids: set[str] = set()
    prompt_embedding = None

    # ------------------------------------------------------------------
    # Topic-based injection: search embedding index for nearest clusters
    # ------------------------------------------------------------------
    if embedding_index.size == 0:
        logger.info(
            "Taxonomy embedding index empty, skipping topic-based injection. trace_id=%s",
            trace_id,
        )
    else:
        prompt_embedding = await embedding_svc.aembed_single(raw_prompt)

        # Phase 2: Composite fusion for cluster search
        search_embedding = prompt_embedding
        try:
            from app.services.taxonomy.fusion import PhaseWeights, build_composite_query
            composite = await build_composite_query(
                raw_prompt, embedding_svc, taxonomy_engine, db,
                topic_embedding=prompt_embedding,  # avoid double embed (E2-2)
            )
            # Load adapted weights from preferences if available, else defaults
            from app.services.preferences import PreferencesService
            prefs = PreferencesService().load()
            pw_dict = prefs.get("phase_weights", {}).get("pattern_injection", {})
            weights = PhaseWeights.from_dict(pw_dict) if pw_dict else PhaseWeights.for_phase("pattern_injection")
            search_embedding = composite.fuse(weights)
        except Exception:
            pass  # fallback to topic-only

        # Threshold 0.45: broad clusters (post-cold-path merge) have averaged
        # centroids that score ~0.45-0.55 against specific prompts. The
        # optimizer prompt's precision instructions handle relevance filtering.
        matches = embedding_index.search(search_embedding, k=5, threshold=0.45)
        if not matches:
            logger.info(
                "No pattern matches above threshold (0.45). trace_id=%s",
                trace_id,
            )
        else:
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
                topic_pattern_ids.add(p.id)

    # ------------------------------------------------------------------
    # Cross-cluster injection: fetch universal patterns by global_source_count
    # even when topic-based matching found nothing or few patterns.
    # ------------------------------------------------------------------
    try:
        from app.services.pipeline_constants import (
            CROSS_CLUSTER_MAX_PATTERNS,
            CROSS_CLUSTER_MIN_SOURCE_COUNT,
            CROSS_CLUSTER_RELEVANCE_FLOOR,
        )

        # Ensure we have a prompt embedding for relevance scoring
        if prompt_embedding is None:
            prompt_embedding = await embedding_svc.aembed_single(raw_prompt)

        if prompt_embedding is not None:
            cc_q = await db.execute(
                select(
                    MetaPattern,
                    PromptCluster.label,
                    PromptCluster.domain,
                    PromptCluster.avg_score,
                )
                .join(PromptCluster, MetaPattern.cluster_id == PromptCluster.id)
                .where(
                    MetaPattern.global_source_count >= CROSS_CLUSTER_MIN_SOURCE_COUNT,
                    MetaPattern.embedding.isnot(None),
                )
                .order_by(MetaPattern.global_source_count.desc())
                .limit(CROSS_CLUSTER_MAX_PATTERNS * 3)  # fetch extra for filtering
            )

            cc_count = 0
            for mp, cluster_label, cluster_domain, cluster_avg_score in cc_q.all():
                if cc_count >= CROSS_CLUSTER_MAX_PATTERNS:
                    break
                # Skip if already injected via topic match
                if mp.id in topic_pattern_ids:
                    continue
                try:
                    pat_emb = np.frombuffer(mp.embedding, dtype=np.float32)
                    sim = float(np.dot(prompt_embedding, pat_emb) / (
                        np.linalg.norm(prompt_embedding) * np.linalg.norm(pat_emb) + 1e-9
                    ))
                    # Full relevance formula with cluster_avg_score_factor
                    cluster_score_factor = max(0.1, (cluster_avg_score or 5.0) / 10.0)
                    relevance = sim * math.log2(1 + mp.global_source_count) * cluster_score_factor

                    if relevance >= CROSS_CLUSTER_RELEVANCE_FLOOR:
                        injected.append(InjectedPattern(
                            pattern_text=mp.pattern_text,
                            cluster_label=f"{cluster_label} (cross-cluster)",
                            domain=cluster_domain or "general",
                            similarity=round(relevance, 2),
                            cluster_id=mp.cluster_id,
                        ))
                        cc_count += 1
                except (ValueError, TypeError):
                    continue

            if cc_count:
                logger.info("Cross-cluster injection: added %d universal patterns. trace_id=%s", cc_count, trace_id)
    except Exception as cc_exc:
        logger.warning("Cross-cluster injection failed (non-fatal): %s trace_id=%s", cc_exc, trace_id)

    # ------------------------------------------------------------------
    # Persist injection provenance when optimization_id is available.
    # Uses flush() to eagerly detect constraint violations — if provenance
    # fails, the pending objects are expunged so the main Optimization
    # commit is not affected.
    # ------------------------------------------------------------------
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
            global _injection_provenance_successes  # noqa: PLW0603
            _injection_provenance_successes += 1
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
            global _injection_provenance_failures  # noqa: PLW0603
            _injection_provenance_failures += 1
            logger.warning(
                "Injection provenance failed (non-fatal, expunged): %s trace_id=%s",
                exc, trace_id,
            )

    # Detailed injection chain log for observability
    if cluster_meta:
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
    elif injected:
        logger.info(
            "Auto-injected %d patterns (cross-cluster only). trace_id=%s",
            len(injected),
            trace_id,
        )

    return injected, cluster_ids
