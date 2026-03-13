"""Framework composite scoring for strategy selection."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from math import exp

from app.config import settings


def compute_framework_composite_score(
    avg_overall: float,
    user_rating_avg: float,
    last_updated: datetime,
    user_weights: dict[str, float] | None,
    avg_scores: str | dict | None,
) -> float:
    """Compute composite score for framework ranking.
    composite = weighted_avg * satisfaction_factor * recency_decay"""
    if user_weights and avg_scores:
        scores = json.loads(avg_scores) if isinstance(avg_scores, str) else avg_scores
        total_w = sum(user_weights.get(d, 0.2) for d in scores)
        if total_w > 0:
            base = sum(scores[d] * user_weights.get(d, 0.2) for d in scores) / total_w
        else:
            base = avg_overall
    else:
        base = avg_overall

    satisfaction = 1.0 + 0.3 * user_rating_avg

    if last_updated:
        now = datetime.now(timezone.utc)
        if last_updated.tzinfo is None:
            last_updated = last_updated.replace(tzinfo=timezone.utc)
        days = (now - last_updated).total_seconds() / 86400
        recency = exp(-settings.FRAMEWORK_PERF_RECENCY_DECAY * days)
    else:
        recency = 0.5

    return base * satisfaction * recency
