"""Adaptation engine: feedback → pipeline parameter tuning.

Computes user-specific dimension weights, retry thresholds, and
strategy affinities from accumulated feedback and pairwise preferences.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import UserAdaptation
from app.services.prompt_diff import SCORE_DIMENSIONS
from app.utils.json_fields import parse_json_column

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
DEFAULT_WEIGHTS: dict[str, float] = {
    "clarity_score": 0.20,
    "specificity_score": 0.20,
    "structure_score": 0.15,
    "faithfulness_score": 0.25,
    "conciseness_score": 0.20,
}

WEIGHT_LOWER_BOUND = 0.05
WEIGHT_UPPER_BOUND = 0.40
MAX_DAMPING = 0.15
THRESHOLD_BOUNDS = (3.0, 8.0)
MIN_FEEDBACKS_FOR_ADAPTATION = 3
MIN_SAMPLES_PER_DIMENSION = 3
MIN_SAMPLES_PER_STRATEGY = 2
STRATEGY_DECAY_DAYS = 90
PAIRWISE_WEIGHT_MULTIPLIER = 2.0

# Concurrency guard: per-user locks with skip-if-busy semantics
_user_locks: dict[str, asyncio.Lock] = {}
_user_busy: dict[str, bool] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def compute_override_deltas(feedbacks: list[dict]) -> dict[str, float]:
    """Compute average delta between user overrides and validator scores.

    Positive delta = user thinks dimension is underscored.
    Negative delta = user thinks dimension is overscored.
    """
    deltas: dict[str, list[float]] = {}
    for fb in feedbacks:
        overrides = fb.get("dimension_overrides")
        scores = fb.get("scores", {})
        if not overrides:
            continue
        for dim, user_val in overrides.items():
            validator_val = scores.get(dim)
            if validator_val is not None:
                deltas.setdefault(dim, []).append(user_val - validator_val)
    return {k: sum(v) / len(v) for k, v in deltas.items() if v}


def adjust_weights_from_deltas(
    base_weights: dict[str, float],
    deltas: dict[str, float],
    damping: float = MAX_DAMPING,
    min_samples: int = MIN_SAMPLES_PER_DIMENSION,
) -> dict[str, float]:
    """Adjust dimension weights based on user override patterns.

    If a user consistently overrides a dimension upward, its weight increases
    (the pipeline should care more about what the user values).

    Invariants: weights sum to 1.0, each within [0.05, 0.40], max shift 15%.
    """
    if not deltas:
        return dict(base_weights)

    adjusted = dict(base_weights)
    for dim, delta in deltas.items():
        if dim not in adjusted:
            continue
        # Normalize delta to a shift: positive delta → increase weight
        # Scale: delta of 3 points → full damping shift
        shift = (delta / 3.0) * damping
        shift = max(-damping, min(damping, shift))
        adjusted[dim] = adjusted[dim] + shift

    # Iterative clamp-then-normalize: normalization after clamping can push
    # values back outside bounds, so repeat until convergence (typically 2-3
    # iterations).  After the loop, apply one final hard clamp + renormalize
    # to guarantee invariants hold even in adversarial float edge cases.
    for _ in range(20):
        for dim in adjusted:
            adjusted[dim] = max(WEIGHT_LOWER_BOUND, min(WEIGHT_UPPER_BOUND, adjusted[dim]))
        total = sum(adjusted.values())
        if total > 0:
            adjusted = {k: v / total for k, v in adjusted.items()}
        if all(WEIGHT_LOWER_BOUND <= v <= WEIGHT_UPPER_BOUND for v in adjusted.values()):
            break

    return adjusted


def compute_threshold_from_feedback(
    feedbacks: list[dict],
    default: float = 5.0,
    bounds: tuple[float, float] = THRESHOLD_BOUNDS,
) -> float:
    """Compute retry threshold from feedback patterns.

    Negative feedback on high-scoring prompts → lower threshold (user is easier to please).
    Positive feedback on low-scoring prompts → raise threshold (user is harder to please).
    """
    if not feedbacks:
        return default

    adjustments: list[float] = []
    for fb in feedbacks:
        rating = fb.get("rating", 0)
        score = fb.get("overall_score")
        if score is None or rating == 0:
            continue
        if rating > 0 and score < default:
            adjustments.append(-0.2)  # user happy with low score → lower bar
        elif rating < 0 and score >= default:
            adjustments.append(0.3)  # user unhappy with high score → raise bar

    if not adjustments:
        return default

    avg_adj = sum(adjustments) / len(adjustments)
    result = default + avg_adj * len(adjustments) * 0.1  # cumulative but damped
    return max(bounds[0], min(bounds[1], round(result, 1)))


def compute_strategy_affinities(
    feedbacks: list[dict],
    min_samples: int = MIN_SAMPLES_PER_STRATEGY,
    decay_days: int = STRATEGY_DECAY_DAYS,
) -> dict:
    """Compute per-task_type strategy preferences from feedback.

    Returns {task_type: {preferred: [frameworks], avoid: [frameworks]}}.
    """
    now = datetime.now(timezone.utc)
    by_task: dict[str, dict[str, list[int]]] = {}

    for fb in feedbacks:
        task_type = fb.get("task_type")
        framework = fb.get("primary_framework")
        rating = fb.get("rating", 0)
        created = fb.get("created_at")

        if not task_type or not framework or rating == 0:
            continue

        # Apply decay
        if created and isinstance(created, datetime):
            age = (now - created).days
            if age > decay_days:
                continue

        by_task.setdefault(task_type, {}).setdefault(framework, []).append(rating)

    affinities: dict[str, dict[str, list[str]]] = {}
    for task_type, frameworks in by_task.items():
        preferred = []
        avoid = []
        for fw, ratings in frameworks.items():
            if len(ratings) < min_samples:
                continue
            avg = sum(ratings) / len(ratings)
            if avg > 0.3:
                preferred.append(fw)
            elif avg < -0.3:
                avoid.append(fw)
        if preferred or avoid:
            affinities[task_type] = {"preferred": preferred, "avoid": avoid}

    return affinities


async def recompute_adaptation(
    user_id: str,
    db: AsyncSession,
    feedbacks: list | None = None,
    preferences: list | None = None,
) -> None:
    """Recompute user adaptation from accumulated feedback.

    Protected by per-user busy flag — concurrent calls for same user skip.
    Uses an atomic flag check (single-threaded asyncio) instead of a TOCTOU
    ``if lock.locked()`` pattern.
    """
    if _user_busy.get(user_id, False):
        logger.info("Adaptation recompute already in progress for user %s, skipping", user_id)
        return

    lock = _get_user_lock(user_id)
    async with lock:
        _user_busy[user_id] = True
        try:
            from sqlalchemy import select as sa_select

            from app.services.feedback_service import get_all_feedbacks_for_user

            if feedbacks is None:
                feedbacks_orm = await get_all_feedbacks_for_user(user_id, db)
                # Join with optimization to get validator scores for delta computation
                from app.models.optimization import Optimization
                feedbacks = []
                for fb in feedbacks_orm:
                    overrides = parse_json_column(fb.dimension_overrides) if fb.dimension_overrides else None
                    # Fetch optimization scores for this feedback
                    opt_stmt = sa_select(Optimization).where(Optimization.id == fb.optimization_id)
                    opt_result = await db.execute(opt_stmt)
                    opt = opt_result.scalar_one_or_none()
                    scores: dict = {}
                    if opt:
                        for dim in SCORE_DIMENSIONS:
                            val = getattr(opt, dim, None)
                            if val is not None:
                                scores[dim] = val
                        scores["overall_score"] = opt.overall_score
                    feedbacks.append({
                        "rating": fb.rating,
                        "dimension_overrides": overrides,
                        "scores": scores,
                        "overall_score": scores.get("overall_score"),
                        "task_type": getattr(opt, "task_type", None) if opt else None,
                        "primary_framework": getattr(opt, "primary_framework", None) if opt else None,
                        "created_at": fb.created_at,
                    })

            if len(feedbacks) < MIN_FEEDBACKS_FOR_ADAPTATION:
                return

            override_deltas = compute_override_deltas(feedbacks)
            adjusted_weights = adjust_weights_from_deltas(
                base_weights=DEFAULT_WEIGHTS,
                deltas=override_deltas,
                damping=MAX_DAMPING,
                min_samples=MIN_SAMPLES_PER_DIMENSION,
            )
            threshold = compute_threshold_from_feedback(feedbacks)
            affinities = compute_strategy_affinities(feedbacks)

            # Upsert user_adaptation (sa_select already imported above)
            stmt = sa_select(UserAdaptation).where(UserAdaptation.user_id == user_id)
            result = await db.execute(stmt)
            existing = result.scalar_one_or_none()

            now = datetime.now(timezone.utc)
            if existing:
                existing.dimension_weights = json.dumps(adjusted_weights)
                existing.strategy_affinities = json.dumps(affinities)
                existing.retry_threshold = threshold
                existing.feedback_count = len(feedbacks)
                existing.last_computed_at = now
            else:
                adaptation = UserAdaptation(
                    user_id=user_id,
                    dimension_weights=json.dumps(adjusted_weights),
                    strategy_affinities=json.dumps(affinities),
                    retry_threshold=threshold,
                    feedback_count=len(feedbacks),
                    last_computed_at=now,
                )
                db.add(adaptation)

            await db.flush()
            logger.info("Recomputed adaptation for user %s (%d feedbacks)", user_id, len(feedbacks))
        finally:
            _user_busy[user_id] = False


async def recompute_adaptation_safe(user_id: str) -> None:
    """Background-safe wrapper: manages its own DB session.

    Used by ``BackgroundTasks`` in routers — routers should import this
    instead of calling ``recompute_adaptation`` directly.
    """
    from app.database import get_session_context

    try:
        async with get_session_context() as db:
            await recompute_adaptation(user_id, db)
            await db.commit()
    except Exception:
        logger.exception("Background adaptation recompute failed for user %s", user_id)


async def load_adaptation(user_id: str, db: AsyncSession) -> dict | None:
    """Load user adaptation for pipeline injection. Returns None if not computed."""
    from sqlalchemy import select as sa_select
    stmt = sa_select(UserAdaptation).where(UserAdaptation.user_id == user_id)
    result = await db.execute(stmt)
    adaptation = result.scalar_one_or_none()
    if not adaptation:
        return None

    weights = parse_json_column(adaptation.dimension_weights) if adaptation.dimension_weights else None
    affinities = parse_json_column(adaptation.strategy_affinities) if adaptation.strategy_affinities else None

    return {
        "dimension_weights": weights,
        "strategy_affinities": affinities,
        "retry_threshold": adaptation.retry_threshold,
        "feedback_count": adaptation.feedback_count,
    }
