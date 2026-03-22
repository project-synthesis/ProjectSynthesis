"""Family operations — cluster assignment, meta-pattern extraction/merge,
centroid computation, and breadcrumb walking.

Extracted from engine.py (Task 2.2) to keep engine.py focused on
orchestration (hot/warm/cold paths + read API).

All functions accept explicit dependencies rather than referencing
engine state.  The TaxonomyEngine delegates to these functions from
``process_optimization()`` and ``map_domain()``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.taxonomy.embedding_index import EmbeddingIndex

import numpy as np
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    MetaPattern,
    Optimization,
    PromptCluster,
)
from app.providers.base import LLMProvider, call_provider_with_retry
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — imported from engine for consistency
# ---------------------------------------------------------------------------

FAMILY_MERGE_THRESHOLD = 0.78
PATTERN_MERGE_THRESHOLD = 0.82

# Warm path operational limits
MAX_META_PATTERNS_PER_EXTRACTION = 5
PROMPT_TRUNCATION_LIMIT = 2000

# ---------------------------------------------------------------------------
# Pydantic schema for extract_meta_patterns structured output
# ---------------------------------------------------------------------------


class _ExtractedPatterns(BaseModel):
    model_config = {"extra": "forbid"}
    patterns: list[str] = PydanticField(
        description=(
            "List of reusable meta-pattern descriptions extracted from the "
            "optimization (max 5)."
        ),
    )


# ---------------------------------------------------------------------------
# Breadcrumb helper
# ---------------------------------------------------------------------------


async def build_breadcrumb(
    db: AsyncSession, node: PromptCluster
) -> list[str]:
    """Walk parent_id chain upward and return labels from root to leaf.

    Args:
        db: Async SQLAlchemy session.
        node: The leaf PromptCluster to start from.

    Returns:
        List of label strings ordered from root to leaf.
    """
    labels: list[str] = []
    current: PromptCluster | None = node
    visited: set[str] = set()  # cycle guard

    while current is not None:
        if current.id in visited:
            logger.warning(
                "Breadcrumb cycle detected at node '%s' (id=%s) — stopping",
                current.label,
                current.id,
            )
            break
        visited.add(current.id)
        labels.append(current.label)

        if current.parent_id is None:
            break

        parent_result = await db.execute(
            select(PromptCluster).where(PromptCluster.id == current.parent_id)
        )
        current = parent_result.scalar_one_or_none()

    # Reverse so list goes root → leaf
    labels.reverse()
    return labels


# ---------------------------------------------------------------------------
# Cluster assignment
# ---------------------------------------------------------------------------


async def assign_cluster(
    db: AsyncSession,
    embedding: np.ndarray,
    label: str,
    domain: str,
    task_type: str,
    overall_score: float | None,
    embedding_index: EmbeddingIndex | None = None,
) -> PromptCluster:
    """Find nearest PromptCluster or create a new one.

    Nearest centroid search with FAMILY_MERGE_THRESHOLD guard and
    cross-domain merge prevention.  Updates centroid as running mean
    ``(old * n + new) / (n+1)`` on merge.

    Args:
        db: Async SQLAlchemy session.
        embedding: Unit-norm embedding of the raw prompt.
        label: Analyzer intent label.
        domain: Free-text domain string from the analyzer (via domain_raw).
        task_type: Analyzer task type.
        overall_score: Pipeline overall score (may be None).

    Returns:
        Existing (updated) or newly-created PromptCluster.
    """

    # Only merge into non-archived clusters.  Archived clusters are
    # effectively tombstoned and should never absorb new members.
    result = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state.in_(["candidate", "active", "mature", "template"])
        )
    )
    clusters = result.scalars().all()

    if clusters:
        valid_clusters: list[PromptCluster] = []
        centroids: list[np.ndarray] = []

        for c_row in clusters:
            try:
                c = np.frombuffer(c_row.centroid_embedding, dtype=np.float32)
                if c.shape[0] != embedding.shape[0]:
                    logger.warning(
                        "Skipping cluster '%s' — centroid dim %d != expected %d",
                        c_row.label,
                        c.shape[0],
                        embedding.shape[0],
                    )
                    continue
                centroids.append(c)
                valid_clusters.append(c_row)
            except (ValueError, TypeError) as exc:
                logger.warning(
                    "Skipping cluster '%s' — corrupt centroid: %s",
                    c_row.label,
                    exc,
                )

        if centroids:
            matches = EmbeddingService.cosine_search(embedding, centroids, top_k=1)
            if matches and matches[0][1] >= FAMILY_MERGE_THRESHOLD:
                idx, score = matches[0]
                matched = valid_clusters[idx]

                # Cross-domain merge prevention
                if matched.domain != domain:
                    logger.info(
                        "Cross-domain merge prevented: cluster '%s' domain=%s != "
                        "incoming domain=%s (cosine=%.3f). Creating new cluster.",
                        matched.label,
                        matched.domain,
                        domain,
                        score,
                    )
                    # Fall through to creation
                else:
                    # Merge: update centroid as running mean, re-normalize
                    old_centroid = np.frombuffer(
                        matched.centroid_embedding, dtype=np.float32
                    )
                    new_centroid = (old_centroid * matched.member_count + embedding) / (
                        matched.member_count + 1
                    )
                    # Re-normalize to unit norm — running mean drifts
                    # from unit sphere without this (critical for cosine
                    # similarity accuracy on subsequent merges).
                    c_norm = np.linalg.norm(new_centroid)
                    if c_norm > 0:
                        new_centroid = new_centroid / c_norm
                    matched.centroid_embedding = new_centroid.astype(
                        np.float32
                    ).tobytes()
                    matched.member_count += 1

                    # avg_score tracks the running mean over members that
                    # have a score.  Members with overall_score=None are
                    # excluded intentionally — we cannot average with None.
                    # When the first scored member arrives, avg_score is
                    # seeded with that single score.
                    if overall_score is not None and matched.avg_score is not None:
                        matched.avg_score = round(
                            (
                                matched.avg_score * (matched.member_count - 1)
                                + overall_score
                            )
                            / matched.member_count,
                            2,
                        )
                    elif overall_score is not None:
                        matched.avg_score = overall_score

                    # Update embedding index with new centroid
                    if embedding_index is not None:
                        await embedding_index.upsert(
                            matched.id, new_centroid
                        )

                    logger.debug(
                        "Merged into cluster '%s' (cosine=%.3f, members=%d)",
                        matched.label,
                        score,
                        matched.member_count,
                    )
                    return matched

    # No match — create new cluster
    new_cluster = PromptCluster(
        label=label,
        domain=domain,
        task_type=task_type,
        centroid_embedding=embedding.astype(np.float32).tobytes(),
        member_count=1,
        usage_count=0,
        avg_score=overall_score,
    )
    db.add(new_cluster)
    await db.flush()  # populate ID

    # Update embedding index with new centroid
    if embedding_index is not None:
        await embedding_index.upsert(new_cluster.id, embedding)

    logger.debug(
        "Created new PromptCluster: id=%s label='%s' domain=%s",
        new_cluster.id,
        label,
        domain,
    )
    return new_cluster


# ---------------------------------------------------------------------------
# Meta-pattern extraction
# ---------------------------------------------------------------------------


async def extract_meta_patterns(
    opt: Optimization,
    db: AsyncSession,
    provider: LLMProvider | None,
    prompt_loader: PromptLoader,
) -> list[str]:
    """Call Haiku to extract meta-patterns from a completed optimization.

    Renders extract_patterns.md template, calls provider.complete_parsed()
    with _ExtractedPatterns structured output.  Caps at 5 patterns.
    Returns empty list on any error (non-fatal).

    Args:
        opt: Completed Optimization row with prompt text and metadata.
        db: Async SQLAlchemy session (used for taxonomy node lookup).
        provider: LLM provider for Haiku calls. None disables extraction.
        prompt_loader: PromptLoader instance for template rendering.

    Returns:
        List of meta-pattern strings (at most 5).
    """
    if not provider:
        logger.debug("No LLM provider — skipping meta-pattern extraction")
        return []

    try:
        # Build taxonomy context string (Spec 7.6)
        taxonomy_context = ""
        if opt.cluster_id:
            try:
                node_result = await db.execute(
                    select(PromptCluster).where(PromptCluster.id == opt.cluster_id)
                )
                tax_node = node_result.scalar_one_or_none()
                if tax_node:
                    breadcrumb = await build_breadcrumb(db, tax_node)
                    taxonomy_context = (
                        f'This prompt belongs to the "{tax_node.label}" pattern cluster '
                        f"({' > '.join(breadcrumb)}).\n"
                    )
            except Exception as ctx_exc:
                logger.warning("Taxonomy context build failed (non-fatal): %s", ctx_exc)

        template = prompt_loader.render(
            "extract_patterns.md",
            {
                "raw_prompt": opt.raw_prompt[:PROMPT_TRUNCATION_LIMIT],
                "optimized_prompt": (opt.optimized_prompt or "")[:PROMPT_TRUNCATION_LIMIT],
                "intent_label": opt.intent_label or "general",
                "domain_raw": opt.domain_raw or opt.domain or "general",
                "strategy_used": opt.strategy_used or "auto",
                "taxonomy_context": taxonomy_context,
            },
        )

        response = await call_provider_with_retry(
            provider,
            model=settings.MODEL_HAIKU,
            system_prompt=(
                "You are a prompt engineering analyst. "
                "Extract reusable meta-patterns."
            ),
            user_message=template,
            output_format=_ExtractedPatterns,
        )

        patterns = [
            str(p) for p in response.patterns if isinstance(p, str)
        ][:MAX_META_PATTERNS_PER_EXTRACTION]
        logger.debug(
            "Haiku returned %d meta-patterns for opt=%s", len(patterns), opt.id
        )
        return patterns

    except Exception as exc:
        logger.warning(
            "Meta-pattern extraction failed (non-fatal) for opt=%s: %s",
            opt.id,
            exc,
        )
        return []


# ---------------------------------------------------------------------------
# Meta-pattern merge
# ---------------------------------------------------------------------------


async def merge_meta_pattern(
    db: AsyncSession,
    cluster_id: str,
    pattern_text: str,
    embedding_service: EmbeddingService,
) -> bool:
    """Merge a meta-pattern into a cluster — enrich existing or create new.

    Cosine search against existing MetaPatterns for the cluster.  If the
    best match is >= PATTERN_MERGE_THRESHOLD: increment source_count and
    update text if new version is longer.  Otherwise create a new row.

    Args:
        db: Async SQLAlchemy session.
        cluster_id: PromptCluster PK.
        pattern_text: Meta-pattern text extracted by Haiku.
        embedding_service: EmbeddingService for embedding pattern text.

    Returns:
        True if merged into existing pattern, False if new pattern created.
    """
    try:
        result = await db.execute(
            select(MetaPattern).where(MetaPattern.cluster_id == cluster_id)
        )
        existing = result.scalars().all()

        pattern_embedding = await embedding_service.aembed_single(pattern_text)

        if existing:
            embeddings: list[np.ndarray] = []
            for mp in existing:
                if mp.embedding:
                    embeddings.append(
                        np.frombuffer(mp.embedding, dtype=np.float32)
                    )
                else:
                    embeddings.append(
                        np.zeros(embedding_service.dimension, dtype=np.float32)
                    )

            matches = EmbeddingService.cosine_search(
                pattern_embedding, embeddings, top_k=1
            )
            if matches and matches[0][1] >= PATTERN_MERGE_THRESHOLD:
                idx, score = matches[0]
                mp = existing[idx]
                mp.source_count += 1
                if len(pattern_text) > len(mp.pattern_text):
                    mp.pattern_text = pattern_text
                    mp.embedding = pattern_embedding.astype(np.float32).tobytes()
                logger.debug(
                    "Enriched meta-pattern '%s' (cosine=%.3f, count=%d)",
                    mp.pattern_text[:50],
                    score,
                    mp.source_count,
                )
                return True

        # No match — create new MetaPattern
        mp = MetaPattern(
            cluster_id=cluster_id,
            pattern_text=pattern_text,
            embedding=pattern_embedding.astype(np.float32).tobytes(),
            source_count=1,
        )
        db.add(mp)
        logger.debug(
            "Created new MetaPattern for cluster=%s: '%s'",
            cluster_id,
            pattern_text[:50],
        )
        return False

    except Exception as exc:
        logger.warning(
            "Failed to merge meta-pattern into cluster=%s: %s",
            cluster_id,
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Pattern centroid computation
# ---------------------------------------------------------------------------


async def compute_pattern_centroid(
    db: AsyncSession, pattern_ids: list[str]
) -> np.ndarray | None:
    """Compute mean centroid of PromptClusters linked via MetaPattern -> PromptCluster.

    Looks up MetaPatterns by ID, gets their cluster_id,
    loads the corresponding PromptCluster centroids, and returns the mean.

    Args:
        db: Async SQLAlchemy session.
        pattern_ids: List of MetaPattern PKs.

    Returns:
        Mean centroid as float32 ndarray, or None if no valid nodes found.
    """
    if not pattern_ids:
        return None

    result = await db.execute(
        select(MetaPattern).where(MetaPattern.id.in_(pattern_ids))
    )
    meta_patterns = result.scalars().all()

    if not meta_patterns:
        return None

    # Collect unique cluster IDs
    cluster_ids = list({mp.cluster_id for mp in meta_patterns if mp.cluster_id})
    if not cluster_ids:
        return None

    # Load clusters referenced by these meta-patterns
    cluster_result = await db.execute(
        select(PromptCluster).where(PromptCluster.id.in_(cluster_ids))
    )
    clusters = cluster_result.scalars().all()

    # Prefer parent (broader topic) centroids when available.
    # Fall back to the cluster's own centroid for root-level clusters
    # so they still contribute to the Bayesian prior.
    parent_ids = list({c.parent_id for c in clusters if c.parent_id})

    # Collect parent centroids
    vecs: list[np.ndarray] = []
    if parent_ids:
        parent_result = await db.execute(
            select(PromptCluster).where(PromptCluster.id.in_(parent_ids))
        )
        for p in parent_result.scalars().all():
            try:
                c = np.frombuffer(p.centroid_embedding, dtype=np.float32)
                vecs.append(c)
            except (ValueError, TypeError):
                continue

    # Root-level clusters (no parent) — use their own centroids
    root_clusters = [c for c in clusters if not c.parent_id]
    for rc in root_clusters:
        try:
            c = np.frombuffer(rc.centroid_embedding, dtype=np.float32)
            vecs.append(c)
        except (ValueError, TypeError):
            continue

    if not vecs:
        return None

    mean = np.mean(np.stack(vecs, axis=0), axis=0).astype(np.float32)
    norm = np.linalg.norm(mean)
    if norm > 0:
        mean = mean / norm
    return mean
