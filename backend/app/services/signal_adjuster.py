"""Signal adjuster — close the loop on ``TaskTypeTelemetry``.

Since v0.4.2 the A4 Haiku LLM fallback persists every ambiguous-prompt
classification to ``TaskTypeTelemetry``.  Nothing consumed those rows
until now — they accumulated as write-only state, visible only via
ad-hoc SQL.

**Active-learning oracle design (plan item #3).**  Haiku only fires
when the heuristic's confidence is below the gate AND the margin is
tight.  That means every telemetry row is, by construction, a prompt
the heuristic struggled with.  If a particular keyword appears in
many of Haiku's classifications for the same task_type, the heuristic
*should* have classified those prompts itself — we should teach the
heuristic that association.

The adjuster reads the last N days of telemetry, tokenizes each
prompt, tallies ``(token, task_type)`` pairs, and — for tokens that
cross ``SIGNAL_ADJUSTER_MIN_FREQUENCY`` hits on the same task_type —
merges the token into ``_TASK_TYPE_SIGNALS[task_type]`` at weight
``SIGNAL_ADJUSTER_WEIGHT`` (0.5: below compound weights, above filler
singles — nudges without overpowering).

Runs once per maintenance cycle in Phase 4.75 (right after the TF-IDF
extraction).  Emits a ``signal_adjusted`` taxonomy event per change.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

# Module-level so tests can ``patch("app.services.signal_adjuster.get_event_logger", ...)``.
# Runtime behaviour unchanged — the observability emit is still wrapped in
# try/except RuntimeError for the uninitialised-logger case.
from app.services.taxonomy.event_logger import get_event_logger

logger = logging.getLogger(__name__)

# ── Tunable constants ──────────────────────────────────────────────────────

#: Days of telemetry to scan per adjustment pass.
SIGNAL_ADJUSTER_LOOKBACK_DAYS = 7

#: Minimum (token, task_type) pair count before the token is elevated to a
#: heuristic signal.  Low enough to catch emerging jargon fast, high enough
#: to skip one-off Haiku calls on genuinely ambiguous prompts.
SIGNAL_ADJUSTER_MIN_FREQUENCY = 3

#: Weight assigned to active-learned signals.  Lower than compound signals
#: (1.0-1.5 range) so learned singles nudge ranking without overpowering
#: structural compound matches.  Higher than filler singles (0.3-0.5 range)
#: so genuine discriminative keywords earn their influence.
SIGNAL_ADJUSTER_WEIGHT = 0.5

#: Stopwords excluded from tokenisation.  A small list — we intentionally
#: keep *domain* words ("backend", "auth", etc.) eligible so the oracle
#: can strengthen domain-coupled task-type signals.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "when",
    "where", "how", "why", "what", "who", "which", "is", "are", "was",
    "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "may", "might", "must",
    "shall", "can", "of", "in", "to", "for", "on", "at", "by", "with",
    "as", "from", "that", "this", "these", "those", "i", "you", "we",
    "they", "it", "me", "us", "them", "my", "your", "our", "their",
    "some", "any", "all", "each", "every", "no", "not", "so", "than",
    "too", "very", "just", "only", "also", "into", "through", "during",
    "about", "over", "under", "between", "up", "down", "out", "off",
})

_TOKEN_RE = re.compile(r"\b[a-z][a-z0-9_-]{2,}\b")


# ── Result dataclass ───────────────────────────────────────────────────────


class AdjustmentResult:
    """Tally of signals added and telemetry rows processed."""

    __slots__ = ("rows_processed", "signals_added", "task_types_touched")

    def __init__(self) -> None:
        self.rows_processed: int = 0
        self.signals_added: int = 0
        self.task_types_touched: set[str] = set()


# ── Public API ─────────────────────────────────────────────────────────────


async def adjust_signals_from_telemetry(
    db: AsyncSession,
    *,
    lookback_days: int = SIGNAL_ADJUSTER_LOOKBACK_DAYS,
    min_frequency: int = SIGNAL_ADJUSTER_MIN_FREQUENCY,
    weight: float = SIGNAL_ADJUSTER_WEIGHT,
) -> AdjustmentResult:
    """Scan recent telemetry and merge active-learned signals.

    Reads ``TaskTypeTelemetry`` rows from the last ``lookback_days``,
    tallies ``(token, task_type)`` pairs, and merges tokens that cross
    ``min_frequency`` hits into ``_TASK_TYPE_SIGNALS[task_type]`` at
    ``weight`` — but only for tokens that aren't already present.

    Emits one ``signal_adjusted`` taxonomy event per merged token.

    Degrades gracefully: ``OperationalError`` (table missing — fresh
    install before migration) returns an empty result.  Signal-loader
    singleton absence raises ``RuntimeError`` if called before
    ``set_task_type_signals`` has ever been called; callers in the
    warm-path hook wrap in try/except.
    """
    result = AdjustmentResult()

    # Lazy import — avoid import cycles in warm_phases test contexts.
    from app.models import TaskTypeTelemetry
    from app.services.task_type_classifier import (
        get_task_type_signals,
        set_task_type_signals,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    try:
        rows_q = await db.execute(
            select(TaskTypeTelemetry.raw_prompt, TaskTypeTelemetry.task_type)
            .where(
                TaskTypeTelemetry.created_at >= cutoff,
                TaskTypeTelemetry.raw_prompt.isnot(None),
                TaskTypeTelemetry.task_type.isnot(None),
            )
        )
        rows: list[tuple[str, str]] = list(rows_q.all())
    except OperationalError as op_exc:
        logger.warning(
            "adjust_signals_from_telemetry: task_type_telemetry table "
            "unavailable (%s) — skipping this cycle",
            op_exc.orig if hasattr(op_exc, "orig") else op_exc,
        )
        return result

    result.rows_processed = len(rows)
    if not rows:
        return result

    # ── Tally (token, task_type) pairs ──
    pair_counts: Counter[tuple[str, str]] = Counter()
    for raw_prompt, task_type in rows:
        tokens = {
            t for t in _TOKEN_RE.findall(raw_prompt.lower())
            if t not in _STOPWORDS
        }
        for token in tokens:
            pair_counts[(token, task_type)] += 1

    # ── Identify candidates crossing threshold ──
    candidates: dict[str, list[tuple[str, float]]] = {}
    for (token, task_type), count in pair_counts.items():
        if count >= min_frequency:
            candidates.setdefault(task_type, []).append((token, weight))

    if not candidates:
        return result

    # ── Merge with current signal table — only ADD tokens not present ──
    current_signals = get_task_type_signals()
    # Build set of keywords already registered per task_type so we don't
    # blindly overwrite adapted weights from warm-path TF-IDF.
    existing_keywords: dict[str, set[str]] = {
        tt: {kw for kw, _w in kws} for tt, kws in current_signals.items()
    }

    adjustments_for_event: list[tuple[str, str, int]] = []
    updated_signals = {tt: list(kws) for tt, kws in current_signals.items()}

    for task_type, new_kws in candidates.items():
        for token, w in new_kws:
            if token in existing_keywords.get(task_type, set()):
                # Token already known for this task_type — warm-path TF-IDF
                # or manual tuning owns the weight; don't touch it.
                continue
            updated_signals.setdefault(task_type, []).append((token, w))
            result.signals_added += 1
            result.task_types_touched.add(task_type)
            adjustments_for_event.append(
                (token, task_type, pair_counts[(token, task_type)])
            )

    if result.signals_added == 0:
        return result

    # ── Persist merged table and emit events ──
    set_task_type_signals(updated_signals)

    try:
        logger_ = get_event_logger()
        for token, task_type, count in adjustments_for_event:
            logger_.log_decision(
                path="warm",
                op="signal_adjuster",
                decision="signal_adjusted",
                context={
                    "token": token,
                    "task_type": task_type,
                    "telemetry_count": count,
                    "weight": weight,
                    "lookback_days": lookback_days,
                },
            )
    except RuntimeError:
        # Event logger not initialised in test/CLI contexts — silent.
        pass

    logger.info(
        "signal_adjuster: %d token→task_type signals added from %d telemetry "
        "rows (task_types touched: %s)",
        result.signals_added, result.rows_processed,
        sorted(result.task_types_touched),
    )
    return result


__all__ = [
    "SIGNAL_ADJUSTER_LOOKBACK_DAYS",
    "SIGNAL_ADJUSTER_MIN_FREQUENCY",
    "SIGNAL_ADJUSTER_WEIGHT",
    "AdjustmentResult",
    "adjust_signals_from_telemetry",
]
