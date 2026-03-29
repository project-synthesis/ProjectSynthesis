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
from app.utils.text_cleanup import parse_domain

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
                matched_primary, _ = parse_domain(matched.domain)
                incoming_primary, _ = parse_domain(domain)
                if matched_primary != incoming_primary:
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
    # Find the parent domain node to link under
    domain_node_q = await db.execute(
        select(PromptCluster).where(
            PromptCluster.state == "domain",
            PromptCluster.label == domain,
        )
    )
    domain_node = domain_node_q.scalar_one_or_none()

    new_cluster = PromptCluster(
        label=label,
        domain=domain,
        task_type=task_type,
        parent_id=domain_node.id if domain_node else None,
        centroid_embedding=embedding.astype(np.float32).tobytes(),
        member_count=1,
        usage_count=0,
        avg_score=overall_score,
    )
    db.add(new_cluster)
    await db.flush()  # populate ID

    # Recount domain node's members (all clusters with matching domain, not just direct children)
    if domain_node is not None:
        from sqlalchemy import func as _func
        count_q = await db.execute(
            select(_func.count()).where(
                PromptCluster.state != "domain",
                PromptCluster.domain == domain,
            )
        )
        domain_node.member_count = count_q.scalar() or 0

    # Update embedding index with new centroid
    if embedding_index is not None:
        await embedding_index.upsert(new_cluster.id, embedding)

    logger.debug(
        "Created new PromptCluster: id=%s label='%s' domain=%s parent=%s",
        new_cluster.id,
        label,
        domain,
        domain_node.label if domain_node else None,
    )
    return new_cluster


# ---------------------------------------------------------------------------
# Meta-pattern extraction
# ---------------------------------------------------------------------------


def extract_structural_patterns(
    raw_prompt: str,
    optimized_prompt: str,
) -> list[str]:
    """Extract meta-patterns from structural diff between raw and optimized prompts.

    Zero-LLM alternative to Haiku-based extraction.  Detects formatting
    additions, score dimension improvements, and structural transformations
    using the same regex patterns as :class:`HeuristicScorer`.

    Two detection mechanisms applied in sequence:

    **Mechanism A — Score delta**: Score both prompts on 5 dimensions,
    emit a pattern when improvement crosses a threshold.

    **Mechanism B — Structural regex**: Detect formatting elements
    present in the optimized prompt but absent from the raw prompt.

    Args:
        raw_prompt: Original user prompt text.
        optimized_prompt: Cleaned optimization output.

    Returns:
        List of 1-5 pattern description strings.
    """
    import re

    from app.services.heuristic_scorer import HeuristicScorer

    patterns: list[str] = []

    def _add(text: str) -> None:
        """Append pattern if not a substring duplicate of an existing one."""
        for existing in patterns:
            if text in existing or existing in text:
                return
        if len(patterns) < 5:
            patterns.append(text)

    # --- Mechanism A: Score delta detection ---
    raw_scores = HeuristicScorer.score_prompt(raw_prompt)
    opt_scores = HeuristicScorer.score_prompt(optimized_prompt, original=raw_prompt)

    delta_rules: list[tuple[str, float, str]] = [
        ("structure", 1.5, (
            "Organize prompts with hierarchical headers and numbered "
            "step sequences for clear task decomposition"
        )),
        ("specificity", 2.0, (
            "Add explicit constraints and type-level specifications to "
            "transform vague requests into precise instructions"
        )),
        ("clarity", 1.5, (
            "Simplify sentence structure and eliminate ambiguous "
            "references to improve readability and reduce misinterpretation"
        )),
        ("conciseness", 1.5, (
            "Remove filler phrases and redundant qualifiers to increase "
            "information density without losing essential detail"
        )),
    ]

    for dim, threshold, pattern_text in delta_rules:
        delta = opt_scores.get(dim, 0.0) - raw_scores.get(dim, 0.0)
        if delta >= threshold:
            _add(pattern_text)

    # Faithfulness drop — cautionary pattern
    faith_delta = opt_scores.get("faithfulness", 0.0) - raw_scores.get("faithfulness", 0.0)
    if faith_delta <= -1.5:
        _add(
            "Preserve original intent by anchoring optimizations to the "
            "user's stated requirements and avoiding unsolicited scope expansion"
        )

    # --- Mechanism B: Structural regex detection ---
    re_headers = re.compile(r"(?m)^#{1,6}\s+\S")
    re_lists = re.compile(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S")
    re_xml = re.compile(r"</?[A-Za-z][A-Za-z0-9_-]*\s*/?>")
    re_format = re.compile(r"\b(?:format|schema|json|yaml|xml|csv|markdown)\b", re.IGNORECASE)
    re_examples = re.compile(r"\bfor example\b|\be\.g\.\b|\bsuch as\b|\bexample:", re.IGNORECASE)
    re_modals = re.compile(r"\b(?:must|shall|should)\b", re.IGNORECASE)

    raw_lower = raw_prompt.lower()
    opt_lower = optimized_prompt.lower()

    # Headers added
    if len(re_headers.findall(optimized_prompt)) >= 2 and len(re_headers.findall(raw_prompt)) == 0:
        _add(
            "Use markdown headers to create clear visual hierarchy "
            "and separate distinct sections of the prompt"
        )

    # Lists added
    if len(re_lists.findall(optimized_prompt)) >= 2 and len(re_lists.findall(raw_prompt)) == 0:
        _add(
            "Structure requirements as bulleted or numbered lists to "
            "make individual items scannable and unambiguous"
        )

    # XML tags added
    if len(re_xml.findall(optimized_prompt)) >= 2 and len(re_xml.findall(raw_prompt)) == 0:
        _add(
            "Wrap semantic sections in XML tags to create "
            "machine-parseable boundaries between context, "
            "instructions, and output format"
        )

    # Format keywords added
    if re_format.search(opt_lower) and not re_format.search(raw_lower):
        _add(
            "Specify an explicit output format (JSON schema, YAML "
            "template, or markdown structure) to constrain response shape"
        )

    # Example keywords added
    if re_examples.search(opt_lower) and not re_examples.search(raw_lower):
        _add(
            "Include concrete examples to anchor the expected output "
            "format and reduce interpretation ambiguity"
        )

    # Constraint modals added
    raw_modals = len(re_modals.findall(raw_prompt))
    opt_modals = len(re_modals.findall(optimized_prompt))
    if opt_modals > raw_modals + 1:
        _add(
            "Add modal obligation keywords (must, shall, should) to "
            "enforce non-negotiable requirements"
        )

    # Fallback: always return at least 1 pattern
    if not patterns:
        patterns.append(
            "Apply targeted structural improvements based on the "
            "prompt's weakest quality dimension"
        )

    return patterns


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
        logger.debug("No LLM provider — using structural pattern extraction")
        return extract_structural_patterns(
            raw_prompt=opt.raw_prompt[:PROMPT_TRUNCATION_LIMIT],
            optimized_prompt=(opt.optimized_prompt or "")[:PROMPT_TRUNCATION_LIMIT],
        )

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
