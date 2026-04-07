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


# ---------------------------------------------------------------------------
# Few-shot example retrieval
# ---------------------------------------------------------------------------

FEW_SHOT_MIN_SCORE = 7.5
FEW_SHOT_SIMILARITY_THRESHOLD = 0.50
FEW_SHOT_OUTPUT_SIMILARITY_THRESHOLD = 0.40  # lower: cross-space comparison is noisier
FEW_SHOT_MAX_EXAMPLES = 2
FEW_SHOT_MAX_CHARS_PER_EXAMPLE = 2000
FEW_SHOT_CANDIDATE_POOL = 20


@dataclass
class FewShotExample:
    """A high-scoring past optimization used as a concrete example."""

    raw_prompt: str
    optimized_prompt: str
    strategy_used: str
    overall_score: float
    similarity: float
    task_type: str
    intent_label: str = ""


# ---------------------------------------------------------------------------
# Intent label token bonus for few-shot ranking
# ---------------------------------------------------------------------------

from app.utils.text_cleanup import LABEL_STOP_WORDS


def _intent_label_bonus(prompt_text: str, candidate_label: str) -> float:
    """Jaccard token overlap bonus for intent-label similarity.

    Returns a small additive bonus (0.0-0.10) when the raw prompt
    shares keywords with the candidate's intent_label. Acts as a
    tiebreaker in few-shot ranking, not a dominant signal.
    """
    if not candidate_label:
        return 0.0
    prompt_tokens = {
        w for w in prompt_text.lower().split()[:20]
        if w not in LABEL_STOP_WORDS and len(w) > 1
    }
    label_tokens = {
        w for w in candidate_label.lower().split()
        if w not in LABEL_STOP_WORDS and len(w) > 1
    }
    if not prompt_tokens or not label_tokens:
        return 0.0
    jaccard = len(prompt_tokens & label_tokens) / len(prompt_tokens | label_tokens)
    return min(jaccard * 0.15, 0.10)  # cap at 0.10


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
        from app.services.taxonomy.fusion import resolve_fused_embedding
        search_embedding = await resolve_fused_embedding(
            raw_prompt, prompt_embedding, embedding_svc, taxonomy_engine, db,
            phase="pattern_injection",
        )

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
                    PromptCluster.state != "archived",
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


# ---------------------------------------------------------------------------
# Few-shot example retrieval
# ---------------------------------------------------------------------------


def _truncate_example(raw: str, optimized: str, max_chars: int) -> tuple[str, str]:
    """Truncate raw+optimized to fit within max_chars combined budget.

    Allocates 40% to raw, 60% to optimized (the optimized version is
    the more instructive part for the optimizer).
    """
    raw_budget = int(max_chars * 0.4)
    opt_budget = max_chars - raw_budget

    if len(raw) > raw_budget:
        raw = raw[: raw_budget - 15] + "\n...[truncated]"
    if len(optimized) > opt_budget:
        optimized = optimized[: opt_budget - 15] + "\n...[truncated]"
    return raw, optimized


async def retrieve_few_shot_examples(
    raw_prompt: str,
    db: AsyncSession,
    trace_id: str,
    *,
    prompt_embedding=None,
    min_score: float = FEW_SHOT_MIN_SCORE,
    max_examples: int = FEW_SHOT_MAX_EXAMPLES,
) -> list[FewShotExample]:
    """Retrieve high-scoring past optimizations using dual-retrieval.

    Two retrieval paths are merged for richer example selection:

    1. **Input-similar** (existing): cosine similarity between raw embeddings.
       Finds past optimizations of similar prompts.
    2. **Output-similar** (new): cosine similarity between the current prompt
       embedding and past optimized_embeddings. Finds past optimizations
       whose *output* matches what we're trying to produce — a stronger
       signal for the optimizer.

    Both pools are merged, deduplicated, and re-ranked by
    ``max(input_sim, output_sim) * overall_score``.

    Args:
        raw_prompt: The user's raw prompt text.
        db: Active async DB session.
        trace_id: Pipeline trace ID for log correlation.
        prompt_embedding: Pre-computed embedding to avoid double-embedding.
        min_score: Minimum overall_score threshold (default 7.5).
        max_examples: Maximum number of examples to return (default 2).

    Returns:
        List of FewShotExample sorted by combined relevance descending.
        Empty list on cold start or no qualifying matches.
    """
    from app.models import Optimization
    from app.services.embedding_service import EmbeddingService

    try:
        if prompt_embedding is None:
            embedding_svc = EmbeddingService()
            prompt_embedding = await embedding_svc.aembed_single(raw_prompt)

        prompt_norm = float(np.linalg.norm(prompt_embedding))
        if prompt_norm < 1e-9:
            return []

        # Single query fetches both embedding types — avoids double round-trip
        result = await db.execute(
            select(
                Optimization.id,
                Optimization.raw_prompt,
                Optimization.optimized_prompt,
                Optimization.strategy_used,
                Optimization.overall_score,
                Optimization.task_type,
                Optimization.intent_label,
                Optimization.embedding,
                Optimization.optimized_embedding,
            ).where(
                Optimization.embedding.isnot(None),
                Optimization.overall_score >= min_score,
                Optimization.status == "completed",
                Optimization.optimized_prompt.isnot(None),
            ).order_by(
                Optimization.created_at.desc(),
            ).limit(FEW_SHOT_CANDIDATE_POOL)
        )

        # Collect candidates from both retrieval paths, keyed by opt ID
        # to deduplicate.  Store (input_sim, output_sim, FewShotExample).
        seen: dict[str, tuple[float, float, FewShotExample]] = {}

        for row in result.all():
            try:
                opt_id = row.id

                # Input similarity (raw embedding)
                input_sim = 0.0
                if row.embedding is not None:
                    emb = np.frombuffer(row.embedding, dtype=np.float32)
                    emb_norm = float(np.linalg.norm(emb))
                    if emb_norm > 1e-9:
                        input_sim = float(np.dot(prompt_embedding, emb) / (prompt_norm * emb_norm))

                # Output similarity (optimized embedding)
                output_sim = 0.0
                if row.optimized_embedding is not None:
                    opt_emb = np.frombuffer(row.optimized_embedding, dtype=np.float32)
                    opt_norm = float(np.linalg.norm(opt_emb))
                    if opt_norm > 1e-9:
                        output_sim = float(np.dot(prompt_embedding, opt_emb) / (prompt_norm * opt_norm))

                # Qualify via either threshold
                input_pass = input_sim >= FEW_SHOT_SIMILARITY_THRESHOLD
                output_pass = output_sim >= FEW_SHOT_OUTPUT_SIMILARITY_THRESHOLD
                if not (input_pass or output_pass):
                    continue

                raw_trunc, opt_trunc = _truncate_example(
                    row.raw_prompt or "",
                    row.optimized_prompt or "",
                    FEW_SHOT_MAX_CHARS_PER_EXAMPLE,
                )

                best_sim = max(input_sim, output_sim)
                example = FewShotExample(
                    raw_prompt=raw_trunc,
                    optimized_prompt=opt_trunc,
                    strategy_used=row.strategy_used or "auto",
                    overall_score=float(row.overall_score),
                    similarity=round(best_sim, 2),
                    task_type=row.task_type or "general",
                    intent_label=row.intent_label or "",
                )

                # Dedup: keep whichever has higher combined score
                if opt_id in seen:
                    prev_input, prev_output, _ = seen[opt_id]
                    if max(input_sim, output_sim) <= max(prev_input, prev_output):
                        continue
                seen[opt_id] = (input_sim, output_sim, example)
            except (ValueError, TypeError):
                continue

        # Rank by max(input_sim, output_sim) * overall_score + label overlap bonus
        ranked = sorted(
            seen.values(),
            key=lambda t: (
                max(t[0], t[1]) * t[2].overall_score
                + _intent_label_bonus(raw_prompt, t[2].intent_label)
            ),
            reverse=True,
        )
        examples = [ex for _, _, ex in ranked[:max_examples]]

        if examples:
            logger.info(
                "Few-shot retrieval: %d examples (max_sim=%.2f). trace_id=%s",
                len(examples), examples[0].similarity, trace_id,
            )

        return examples
    except Exception as exc:
        logger.warning("Few-shot retrieval failed (non-fatal): %s trace_id=%s", exc, trace_id)
        return []


def format_few_shot_examples(examples: list[FewShotExample]) -> str | None:
    """Format FewShotExample list into the few_shot_examples template variable.

    Returns None if no examples available (cold start graceful degradation).
    """
    if not examples:
        return None

    parts = []
    for i, ex in enumerate(examples, 1):
        parts.append(
            f'<example-{i} score="{ex.overall_score:.1f}" '
            f'strategy="{ex.strategy_used}" similarity="{ex.similarity:.2f}">\n'
            f"<before>\n{ex.raw_prompt}\n</before>\n"
            f"<after>\n{ex.optimized_prompt}\n</after>\n"
            f"</example-{i}>"
        )

    return (
        "\n\n".join(parts)
        + "\n\nStudy the transformation pattern — how the \"before\" prompt was "
        "restructured and enriched to become the \"after\" prompt — and apply "
        "similar techniques to the current prompt. Do NOT copy the content; "
        "adapt the transformation approach."
    )
