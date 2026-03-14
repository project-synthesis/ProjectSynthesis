"""Framework composite scoring for strategy selection."""
from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from math import exp

from app.config import settings
from app.utils.json_fields import parse_json_column


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
        try:
            scores = json.loads(avg_scores) if isinstance(avg_scores, str) else avg_scores
        except (json.JSONDecodeError, TypeError):
            scores = None
        if not scores:
            base = avg_overall  # fall back to raw average
        else:
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


def build_performance_prompt_section(
    task_type: str,
    perf_rows: list[dict],
    user_weights: dict[str, float] | None = None,
) -> str:
    """Build a prompt section with framework performance stats for strategy.

    Accepts pre-formatted performance dicts (from ``format_framework_performance``
    or ``_load_all_framework_perfs``).  Returns empty string when no data.

    Frameworks are ranked by composite score (weighted quality * satisfaction *
    recency) so the strategy LLM sees the most promising options first.

    The section is injected alongside affinity data so the strategy LLM can
    consider historical quality when selecting a framework.
    """
    if not perf_rows:
        return ""

    # Rank frameworks by composite score (best first)
    scored_rows: list[tuple[float, dict]] = []
    for p in perf_rows:
        count = p.get("sample_count", 0)
        if count < 1:
            continue
        avg_scores = p.get("avg_scores")
        if avg_scores and isinstance(avg_scores, dict):
            avg_overall = sum(avg_scores.values()) / max(len(avg_scores), 1)
        else:
            avg_overall = 5.0
        rating = p.get("user_rating_avg", 0) or 0
        last_updated = p.get("last_updated")
        if isinstance(last_updated, str):
            try:
                last_updated = datetime.fromisoformat(last_updated)
            except (ValueError, TypeError):
                last_updated = None
        if not isinstance(last_updated, datetime):
            last_updated = datetime.now(timezone.utc)
        composite = compute_framework_composite_score(
            avg_overall=avg_overall,
            user_rating_avg=rating,
            last_updated=last_updated,
            user_weights=user_weights,
            avg_scores=avg_scores,
        )
        scored_rows.append((composite, p))

    scored_rows.sort(key=lambda x: x[0], reverse=True)

    lines = [f"\n## Framework Performance History ({task_type})"]
    for _composite, p in scored_rows:
        fw = p.get("framework", "unknown")
        count = p.get("sample_count", 0)
        rating = p.get("user_rating_avg", 0)
        avg_scores = p.get("avg_scores")
        parts = [f"- **{fw}**: {count} sample(s)"]
        if avg_scores and isinstance(avg_scores, dict):
            overall = sum(avg_scores.values()) / max(len(avg_scores), 1)
            parts.append(f"avg {overall:.1f}/10")
        if rating and rating != 0:
            label = "positive" if rating > 0.3 else (
                "negative" if rating < -0.3 else "neutral"
            )
            parts.append(f"user sentiment: {label}")
        lines.append(", ".join(parts))

    if len(lines) <= 1:
        return ""

    lines.append(
        "Consider these results when selecting a framework — "
        "higher-rated frameworks with more samples are more reliable."
    )
    return "\n".join(lines)


def format_framework_performance(rows: Sequence, *, include_last_updated: bool = True) -> list[dict]:
    """Format FrameworkPerformance ORM rows into API-ready dicts.

    Shared by the REST ``/api/framework-performance`` endpoint and the
    ``synthesis_get_framework_performance`` MCP tool.

    Args:
        rows: Iterable of ``FrameworkPerformance`` ORM instances.
        include_last_updated: Whether to include ``last_updated`` in each
            item (REST endpoint includes it; MCP tool omits it).

    Returns:
        List of dicts with framework, avg_scores, user_rating_avg,
        issue_frequency, sample_count, and elasticity_snapshot.
    """
    items: list[dict] = []
    for row in rows:
        item: dict = {
            "framework": row.framework,
            "avg_scores": parse_json_column(row.avg_scores) if row.avg_scores else None,
            "user_rating_avg": row.user_rating_avg,
            "issue_frequency": parse_json_column(row.issue_frequency) if row.issue_frequency else None,
            "sample_count": row.sample_count,
            "elasticity_snapshot": (
                parse_json_column(row.elasticity_snapshot)
                if row.elasticity_snapshot else None
            ),
        }
        if include_last_updated:
            item["last_updated"] = row.last_updated.isoformat() if row.last_updated else None
        items.append(item)
    return items
