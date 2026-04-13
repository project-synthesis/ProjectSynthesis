"""Domain signal extractor — auto-enriches heuristic signals from taxonomy discoveries.

When the warm path discovers a new domain, this module extracts the most
discriminative keywords from that domain's member prompts and registers
them as domain signals on the DomainSignalLoader singleton.

Uses a simplified TF-IDF approach: tokens that appear frequently in the
domain's prompts but rarely across all prompts are strong domain indicators.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, PromptCluster

logger = logging.getLogger(__name__)

# Stopwords — common English words that are never useful as domain signals
_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "that", "this",
    "these", "those", "it", "its", "i", "you", "he", "she", "we", "they",
    "my", "your", "our", "their", "me", "him", "her", "us", "them",
    "not", "no", "so", "if", "as", "up", "out", "about", "into", "over",
    "then", "than", "too", "very", "just", "also", "how", "what", "when",
    "where", "which", "who", "why", "all", "each", "every", "both",
    "few", "more", "most", "other", "some", "such", "any", "only", "own",
    "same", "new", "use", "using", "used", "make", "like", "need", "want",
    "get", "set", "add", "create", "write", "include", "provide", "ensure",
    "implement", "build", "design", "please", "help", "give", "show",
})

# Minimum token length to consider
_MIN_TOKEN_LENGTH = 3

# Regex to extract words (alphanumeric + hyphens for tech terms like "ci-cd")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")


async def extract_domain_signals(
    db: AsyncSession,
    domain_label: str,
    min_members: int = 5,
    min_coherence: float = 0.4,
    top_k: int = 8,
) -> list[tuple[str, float]]:
    """Extract top discriminative keywords from a domain's member prompts.

    Only fires when the domain has sufficient members and coherence.
    Returns (keyword, weight) tuples sorted by discriminative score,
    or an empty list if the domain doesn't qualify.

    Args:
        db: Async database session.
        domain_label: The domain node's label (e.g., "infrastructure").
        min_members: Minimum optimization count to extract signals.
        min_coherence: Minimum domain node coherence to trust signals.
        top_k: Maximum number of keywords to return.
    """
    try:
        # 1. Find the domain node and check quality gates
        domain_q = await db.execute(
            select(PromptCluster).where(
                PromptCluster.state == "domain",
                PromptCluster.label == domain_label,
            ).limit(1)
        )
        domain_node = domain_q.scalar_one_or_none()
        if not domain_node:
            logger.debug("extract_domain_signals: domain '%s' not found", domain_label)
            return []

        coherence = domain_node.coherence or 0.0
        if coherence < min_coherence:
            logger.info(
                "extract_domain_signals: domain '%s' skipped — coherence %.2f < %.2f",
                domain_label, coherence, min_coherence,
            )
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                get_event_logger().log_decision(
                    path="warm", op="signal_enrichment", decision="skipped_coherence",
                    context={"domain": domain_label, "coherence": round(coherence, 2),
                             "threshold": min_coherence},
                )
            except RuntimeError:
                pass
            return []

        # 2. Find all clusters parented under this domain
        child_q = await db.execute(
            select(PromptCluster.id).where(
                PromptCluster.parent_id == domain_node.id,
                PromptCluster.state.in_(["active", "mature", "candidate"]),
            )
        )
        child_ids = [r[0] for r in child_q.all()]
        if not child_ids:
            return []

        # 3. Fetch raw prompts from optimizations in these clusters
        opt_q = await db.execute(
            select(Optimization.raw_prompt).where(
                Optimization.cluster_id.in_(child_ids),
                Optimization.raw_prompt.isnot(None),
            )
        )
        prompts = [r[0] for r in opt_q.all()]

        if len(prompts) < min_members:
            logger.info(
                "extract_domain_signals: domain '%s' skipped — %d members < %d threshold",
                domain_label, len(prompts), min_members,
            )
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                get_event_logger().log_decision(
                    path="warm", op="signal_enrichment", decision="skipped_sparse",
                    context={"domain": domain_label, "member_count": len(prompts),
                             "threshold": min_members},
                )
            except RuntimeError:
                pass
            return []

        # 4. Tokenize and compute domain term frequency
        domain_doc_count: Counter[str] = Counter()  # how many prompts contain each token
        for prompt in prompts:
            tokens = set(_TOKEN_RE.findall(prompt.lower()))
            tokens -= _STOPWORDS
            for token in tokens:
                domain_doc_count[token] += 1

        # 5. Compute global term frequency (across ALL prompts, not just this domain)
        global_q = await db.execute(
            select(Optimization.raw_prompt).where(
                Optimization.raw_prompt.isnot(None),
            ).limit(500)  # sample cap to avoid scanning entire table
        )
        global_prompts = [r[0] for r in global_q.all()]
        global_doc_count: Counter[str] = Counter()
        for prompt in global_prompts:
            tokens = set(_TOKEN_RE.findall(prompt.lower()))
            tokens -= _STOPWORDS
            for token in tokens:
                global_doc_count[token] += 1

        total_domain = len(prompts)
        total_global = max(len(global_prompts), 1)

        # 6. Score: tokens that appear often in domain but rarely globally
        scored: list[tuple[str, float]] = []
        for token, domain_count in domain_doc_count.items():
            domain_freq = domain_count / total_domain
            global_freq = global_doc_count.get(token, 0) / total_global

            # Filter: present in >= 30% of domain prompts AND <= 70% of all prompts
            if domain_freq < 0.3:
                continue
            if global_freq > 0.7:
                continue

            # Discriminative score: how much more common in domain than globally
            # Avoid division by zero; add smoothing
            score = domain_freq / max(global_freq, 0.01)
            scored.append((token, score))

        # 7. Sort by score, normalize weights to [0.5, 1.0], return top_k
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[:top_k]
        if not top:
            return []

        max_score = top[0][1]
        min_score = top[-1][1] if len(top) > 1 else max_score
        score_range = max(max_score - min_score, 0.01)

        result = [
            (token, round(0.5 + 0.5 * (score - min_score) / score_range, 2))
            for token, score in top
        ]

        logger.info(
            "extract_domain_signals: domain='%s' members=%d extracted=%d keywords=[%s]",
            domain_label, total_domain, len(result),
            ", ".join(f"{kw}({w})" for kw, w in result[:5]),
        )

        # Taxonomy observability — log to Activity Panel
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="warm",
                op="signal_enrichment",
                decision="signals_extracted",
                context={
                    "domain": domain_label,
                    "members_scanned": total_domain,
                    "candidates_scored": len(scored),
                    "keywords_extracted": len(result),
                    "top_keywords": [kw for kw, _ in result[:5]],
                    "coherence": round(coherence, 2),
                },
            )
        except RuntimeError:
            pass  # Event logger not initialized

        return result

    except Exception:
        logger.warning(
            "extract_domain_signals failed for domain '%s'",
            domain_label, exc_info=True,
        )

        # Log failure to taxonomy Activity Panel
        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="warm",
                op="signal_enrichment",
                decision="extraction_failed",
                context={
                    "domain": domain_label,
                    "error": "see logs for details",
                },
            )
        except RuntimeError:
            pass

        return []
