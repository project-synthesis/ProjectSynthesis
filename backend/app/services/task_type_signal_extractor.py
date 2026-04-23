"""Task-type signal extractor -- mines TF-IDF keywords from optimizations grouped by task_type.

Queries all optimizations, groups by task_type, and for each type with
sufficient samples computes discriminative keywords using a simplified
TF-IDF approach: tokens that appear frequently in a task type's prompts
but rarely across all prompts are strong task-type indicators.

Returns ``{task_type: [(keyword, weight), ...]}`` with top keywords per type,
or an empty dict on failure (callers keep existing signals).

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SAMPLES = 30       # Minimum optimizations per task type to extract signals
TOP_K = 15             # Maximum keywords per task type
MIN_TASK_FREQ = 0.30   # Token must appear in >= 30% of the type's prompts
MAX_GLOBAL_FREQ = 0.70 # Token must appear in <= 70% of ALL prompts
GLOBAL_SAMPLE_CAP = 500  # Cap global frequency sample to avoid full table scan

# Stopwords -- common English words that are never useful as task-type signals.
# Copied from domain_signal_extractor.py (private, not imported) and extended.
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
    # Extended for task-type extraction
    "good", "better", "best", "example", "following", "given", "based",
})

# Regex to extract tokens (alphanumeric + hyphens for tech terms like "ci-cd")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9\-]{2,}")


async def extract_task_type_signals(
    db: AsyncSession,
) -> dict[str, list[tuple[str, float]]]:
    """Extract discriminative keywords for each task type from optimization history.

    For each task_type with >= ``MIN_SAMPLES`` completed optimizations, computes
    a simplified TF-IDF score and returns the top ``TOP_K`` keywords with
    weights normalized to ``[0.5, 1.0]``.

    Returns:
        Mapping of task_type -> list of (keyword, weight) tuples, sorted by
        discriminative score. Empty dict on failure.
    """
    t0 = time.monotonic()

    try:
        # 1. Count optimizations per task type
        type_counts_q = await db.execute(
            select(Optimization.task_type, func.count(Optimization.id))
            .where(
                Optimization.task_type.isnot(None),
                Optimization.raw_prompt.isnot(None),
                Optimization.status == "completed",
            )
            .group_by(Optimization.task_type)
        )
        type_counts: dict[str, int] = {
            row[0]: row[1] for row in type_counts_q.all()
        }

        # 1b. Count telemetry samples per task type
        from app.models import TaskTypeTelemetry
        telemetry_counts_q = await db.execute(
            select(TaskTypeTelemetry.task_type, func.count(TaskTypeTelemetry.id))
            .where(TaskTypeTelemetry.task_type.isnot(None))
            .group_by(TaskTypeTelemetry.task_type)
        )
        telemetry_counts: dict[str, int] = {
            row[0]: row[1] for row in telemetry_counts_q.all()
        }

        total_samples = sum(type_counts.values()) + sum(telemetry_counts.values())
        all_task_types = set(type_counts.keys()) | set(telemetry_counts.keys())

        logger.info(
            "extract_task_type_signals: start — %d total samples across %d task types",
            total_samples, len(all_task_types),
        )

        # 2. Fetch global sample for background frequency
        global_q = await db.execute(
            select(Optimization.raw_prompt)
            .where(Optimization.raw_prompt.isnot(None))
            .limit(GLOBAL_SAMPLE_CAP)
        )
        global_prompts = [r[0] for r in global_q.all()]
        total_global = max(len(global_prompts), 1)

        global_doc_count: Counter[str] = Counter()
        for prompt in global_prompts:
            tokens = set(_TOKEN_RE.findall(prompt.lower()))
            tokens -= _STOPWORDS
            for token in tokens:
                global_doc_count[token] += 1

        # 3. Process each task type
        result: dict[str, list[tuple[str, float]]] = {}
        dynamic_count = 0
        static_count = 0

        for task_type in all_task_types:
            opt_count = type_counts.get(task_type, 0)
            tel_count = telemetry_counts.get(task_type, 0)
            effective_count = opt_count + tel_count * 5  # 5x weight for gold-standard telemetry

            if effective_count < MIN_SAMPLES:
                logger.info(
                    "extract_task_type_signals: task_type='%s' bootstrap — %d effective samples < %d threshold",
                    task_type, effective_count, MIN_SAMPLES,
                )
                static_count += 1
                try:
                    from app.services.taxonomy.event_logger import get_event_logger
                    get_event_logger().log_decision(
                        path="warm",
                        op="task_type_signal_enrichment",
                        decision="task_type_signals_bootstrap",
                        context={
                            "task_type": task_type,
                            "sample_count": effective_count,
                            "threshold": MIN_SAMPLES,
                        },
                    )
                except RuntimeError:
                    pass
                continue

            # Fetch prompts for this task type
            try:
                type_q = await db.execute(
                    select(Optimization.raw_prompt).where(
                        Optimization.task_type == task_type,
                        Optimization.raw_prompt.isnot(None),
                        Optimization.status == "completed",
                    )
                )
                type_prompts = [r[0] for r in type_q.all()]

                tel_q = await db.execute(
                    select(TaskTypeTelemetry.raw_prompt).where(
                        TaskTypeTelemetry.task_type == task_type,
                        TaskTypeTelemetry.raw_prompt.isnot(None),
                    )
                )
                tel_prompts = [r[0] for r in tel_q.all()]

                # Multiply telemetry prompts by 5 in the corpus to enforce the weight!
                type_prompts.extend(tel_prompts * 5)

                # Compute task-type term frequency
                type_doc_count: Counter[str] = Counter()
                for prompt in type_prompts:
                    tokens = set(_TOKEN_RE.findall(prompt.lower()))
                    tokens -= _STOPWORDS
                    for token in tokens:
                        type_doc_count[token] += 1

                total_type = len(type_prompts)

                # Score: tokens frequent in this type but rare globally
                scored: list[tuple[str, float]] = []
                for token, type_count_val in type_doc_count.items():
                    task_freq = type_count_val / total_type
                    global_freq = global_doc_count.get(token, 0) / total_global

                    if task_freq < MIN_TASK_FREQ:
                        continue
                    if global_freq > MAX_GLOBAL_FREQ:
                        continue

                    score = task_freq / max(global_freq, 0.01)
                    scored.append((token, score))

                # Sort by score, take top_k, normalize weights to [0.5, 1.0]
                scored.sort(key=lambda x: x[1], reverse=True)
                top = scored[:TOP_K]

                if not top:
                    logger.info(
                        "extract_task_type_signals: task_type='%s' — no discriminative keywords found (%d prompts)",
                        task_type, total_type,
                    )
                    static_count += 1
                    continue

                max_score = top[0][1]
                min_score = top[-1][1] if len(top) > 1 else max_score
                score_range = max(max_score - min_score, 0.01)

                normalized = [
                    (token, round(0.5 + 0.5 * (score - min_score) / score_range, 2))
                    for token, score in top
                ]

                result[task_type] = normalized
                dynamic_count += 1

                logger.info(
                    "extract_task_type_signals: task_type='%s' — extracted %d keywords from %d prompts [%s]",
                    task_type, len(normalized), total_type,
                    ", ".join(f"{kw}({w})" for kw, w in normalized[:5]),
                )

                try:
                    from app.services.taxonomy.event_logger import get_event_logger
                    get_event_logger().log_decision(
                        path="warm",
                        op="task_type_signal_enrichment",
                        decision="task_type_signals_replaced",
                        context={
                            "task_type": task_type,
                            "sample_count": total_type,
                            "keywords_extracted": len(normalized),
                            "top_keywords": [kw for kw, _ in normalized[:5]],
                        },
                    )
                except RuntimeError:
                    pass

            except Exception:
                logger.warning(
                    "extract_task_type_signals: failed for task_type='%s'",
                    task_type, exc_info=True,
                )
                static_count += 1

                try:
                    from app.services.taxonomy.event_logger import get_event_logger
                    get_event_logger().log_decision(
                        path="warm",
                        op="task_type_signal_enrichment",
                        decision="task_type_signals_failed",
                        context={
                            "task_type": task_type,
                            "error": "per-type extraction failed, see logs",
                        },
                    )
                except RuntimeError:
                    pass
                continue

        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "extract_task_type_signals: complete — %d dynamic, %d static/bootstrap in %.1fms",
            dynamic_count, static_count, elapsed_ms,
        )

        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="warm",
                op="task_type_signal_enrichment",
                decision="task_type_signals_extracted",
                context={
                    "total_samples": total_samples,
                    "task_types_total": len(type_counts),
                    "dynamic_count": dynamic_count,
                    "static_count": static_count,
                    "elapsed_ms": elapsed_ms,
                },
            )
        except RuntimeError:
            pass

        return result

    except Exception:
        logger.warning(
            "extract_task_type_signals: global failure", exc_info=True,
        )

        try:
            from app.services.taxonomy.event_logger import get_event_logger
            get_event_logger().log_decision(
                path="warm",
                op="task_type_signal_enrichment",
                decision="task_type_signals_failed",
                context={"error": "global extraction failed, see logs"},
            )
        except RuntimeError:
            pass

        return {}
