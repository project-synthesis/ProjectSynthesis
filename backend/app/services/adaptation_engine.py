"""Adaptation engine: feedback → pipeline parameter tuning.

Computes user-specific dimension weights, retry thresholds, and
strategy affinities from accumulated feedback and pairwise preferences.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from math import log

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.feedback import UserAdaptation
from app.services.framework_profiles import ISSUE_DIMENSION_MAP
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
MIN_SAMPLES_PER_DIMENSION = 3
MIN_SAMPLES_PER_STRATEGY = 2
STRATEGY_DECAY_DAYS = 90
PAIRWISE_WEIGHT_MULTIPLIER = 2.0

# Concurrency guard: per-user locks with skip-if-busy semantics
_user_locks: dict[str, asyncio.Lock] = {}
_user_busy: dict[str, bool] = {}

# Debounced recompute state
_debounce_handles: dict[str, asyncio.TimerHandle] = {}
_adaptation_versions: dict[str, int] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


# ── Progressive Damping ──────────────────────────────────────────────

def compute_effective_damping(feedbacks: list) -> float:
    """Progressive damping based on feedback count and consistency.

    Returns a damping value that increases with more feedback and higher
    consistency, capped at ``MAX_DAMPING * CONSISTENCY_CEILING_FACTOR``.
    """
    n = len(feedbacks)
    if n == 0:
        return 0.0

    base_damping = settings.BASE_DAMPING
    max_damping = settings.MAX_DAMPING
    ceiling_factor = settings.CONSISTENCY_CEILING_FACTOR

    # Base: logarithmic ramp with feedback count
    base = min(max_damping, base_damping * log(1 + n))

    # Consistency: rating variance → [0, 1] where 1 = all same
    ratings = [getattr(fb, "rating", 0) for fb in feedbacks]

    overall_consistency = _compute_consistency(ratings)

    # Recent consistency: last half of feedbacks weighted more
    half = max(1, n // 2)
    recent_ratings = ratings[-half:]
    recent_consistency = _compute_consistency(recent_ratings)

    # Blend: 60% recent, 40% overall
    blend = 0.6 * recent_consistency + 0.4 * overall_consistency

    # Multiplier: range [0.5, 1.2]
    multiplier = 0.5 + 0.7 * blend

    # Effective damping with ceiling
    consistency_ceiling = max_damping * ceiling_factor
    effective = min(consistency_ceiling, base * multiplier)

    return effective


def _compute_consistency(ratings: list[int | float]) -> float:
    """Compute consistency score from ratings: 0 = max disagreement, 1 = all same."""
    if len(ratings) <= 1:
        return 1.0

    mean = sum(ratings) / len(ratings)
    variance = sum((r - mean) ** 2 for r in ratings) / len(ratings)
    # Ratings are in [-1, 1], so max variance is 1.0 (half -1, half 1)
    # Normalize: 0 variance → 1.0 consistency, 1.0 variance → 0.0
    return max(0.0, 1.0 - variance)


# ── Issue Signals ────────────────────────────────────────────────────

def apply_issue_signals(
    base_deltas: dict[str, float],
    issue_frequency: dict[str, int],
    total_feedbacks: int,
) -> dict[str, float]:
    """Layer corrected issue frequency onto override deltas.

    Each issue maps to dimensions with directional weights via
    ``ISSUE_DIMENSION_MAP``. Frequencies are normalized by total feedback
    count and scaled by ``ISSUE_WEIGHT_FACTOR``.
    """
    if not issue_frequency:
        return dict(base_deltas)

    if total_feedbacks <= 0:
        return dict(base_deltas)

    result = dict(base_deltas)
    weight_factor = settings.ISSUE_WEIGHT_FACTOR

    for issue_id, count in issue_frequency.items():
        dim_map = ISSUE_DIMENSION_MAP.get(issue_id)
        if dim_map is None:
            continue

        # Normalize by total feedback count
        normalized = count / total_feedbacks

        for dim, direction_weight in dim_map.items():
            shift = normalized * direction_weight * weight_factor
            result[dim] = result.get(dim, 0.0) + shift

    return result


# ── Debounced Recompute ──────────────────────────────────────────────

def schedule_adaptation_recompute(user_id: str) -> None:
    """Schedule a debounced adaptation recompute for a user.

    Each call cancels the previous pending timer and schedules a new one.
    The version counter prevents stale computations from running.
    """
    loop = asyncio.get_running_loop()

    # Cancel previous timer if any
    existing = _debounce_handles.get(user_id)
    if existing is not None:
        existing.cancel()

    # Increment version
    version = _adaptation_versions.get(user_id, 0) + 1
    _adaptation_versions[user_id] = version

    delay_s = settings.ADAPTATION_DEBOUNCE_MS / 1000.0
    handle = loop.call_later(
        delay_s,
        lambda: asyncio.ensure_future(_debounced_recompute(user_id, version)),
    )
    _debounce_handles[user_id] = handle

    logger.debug(
        "Scheduled adaptation recompute for user %s (v%d, delay %.1fs)",
        user_id, version, delay_s,
    )


async def _debounced_recompute(user_id: str, scheduled_version: int) -> None:
    """Execute debounced recompute if version hasn't changed."""
    current_version = _adaptation_versions.get(user_id, 0)
    if scheduled_version != current_version:
        logger.debug(
            "Skipping stale recompute for user %s (v%d != v%d)",
            user_id, scheduled_version, current_version,
        )
        return

    await recompute_adaptation_safe(user_id)

    # Check if version changed during computation; re-queue once
    new_version = _adaptation_versions.get(user_id, 0)
    if new_version != scheduled_version:
        requeue_limit = settings.ADAPTATION_MAX_REQUEUE
        if requeue_limit > 0:
            logger.info(
                "Version changed during recompute for user %s, re-queuing",
                user_id,
            )
            await _debounced_recompute(user_id, new_version)


# ── Adaptation Pulse ─────────────────────────────────────────────────

def compute_adaptation_pulse(adaptation: dict | None) -> dict:
    """L0 observability status pulse for adaptation state.

    Returns a dict with status, label, and detail for UI display.
    """
    if adaptation is None:
        return {
            "status": "inactive",
            "label": "No adaptation",
            "detail": "Submit feedback to begin personalizing the pipeline.",
        }

    count = adaptation.get("feedback_count", 0)
    min_needed = settings.MIN_FEEDBACKS_FOR_ADAPTATION

    if count < min_needed:
        remaining = min_needed - count
        return {
            "status": "learning",
            "label": "Learning",
            "detail": (
                f"{remaining} more feedback(s) needed before "
                f"adaptation activates."
            ),
        }

    # Active: find top priority dimension (most shifted from default)
    weights = adaptation.get("dimension_weights")
    if weights:
        max_shift = 0.0
        top_dim = None
        for dim, weight in weights.items():
            default = DEFAULT_WEIGHTS.get(dim, 0.2)
            shift = abs(weight - default)
            if shift > max_shift:
                max_shift = shift
                top_dim = dim

        dim_label = (
            top_dim.replace("_score", "").replace("_", " ").title()
            if top_dim
            else "balanced"
        )
        return {
            "status": "active",
            "label": "Adapted",
            "detail": (
                f"Pipeline tuned from {count} feedbacks. "
                f"Priority: {dim_label}."
            ),
        }

    return {
        "status": "active",
        "label": "Adapted",
        "detail": f"Pipeline tuned from {count} feedbacks.",
    }


# ── Audit Trail ──────────────────────────────────────────────────────

async def _record_adaptation_event(
    user_id: str,
    db: AsyncSession,
    event_type: str,
    payload: dict | None = None,
) -> None:
    """Record an adaptation event for the audit trail."""
    from app.models.adaptation_event import AdaptationEvent

    event = AdaptationEvent(
        user_id=user_id,
        event_type=event_type,
        payload=json.dumps(payload) if payload else None,
    )
    db.add(event)
    await db.flush()


async def _purge_old_events(user_id: str, db: AsyncSession) -> None:
    """Delete adaptation events older than retention period.

    Retention boundary: events with ``created_at`` strictly before
    ``now - ADAPTATION_EVENT_RETENTION_DAYS`` (default 90 days) are
    deleted.  Events exactly at the boundary are preserved.  Only
    events belonging to ``user_id`` are affected.
    """
    from sqlalchemy import delete as sa_delete

    from app.models.adaptation_event import AdaptationEvent

    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.ADAPTATION_EVENT_RETENTION_DAYS,
    )
    stmt = (
        sa_delete(AdaptationEvent)
        .where(AdaptationEvent.user_id == user_id)
        .where(AdaptationEvent.created_at < cutoff)
    )
    await db.execute(stmt)


# ── Core Functions ───────────────────────────────────────────────────

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
    damping: float,
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
            # Ensure tz-aware comparison (DB may store naive UTC)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
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


def _aggregate_issue_frequency(feedbacks: list[dict]) -> dict[str, int]:
    """Aggregate corrected_issues counts across all feedbacks."""
    freq: dict[str, int] = {}
    for fb in feedbacks:
        issues = fb.get("corrected_issues")
        if not issues:
            continue
        if isinstance(issues, str):
            try:
                issues = json.loads(issues)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(issues, list):
            for issue_id in issues:
                if isinstance(issue_id, str):
                    freq[issue_id] = freq.get(issue_id, 0) + 1
    return freq


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
                    overrides = (
                        parse_json_column(fb.dimension_overrides)
                        if fb.dimension_overrides
                        else None
                    )
                    corrected = (
                        parse_json_column(fb.corrected_issues)
                        if fb.corrected_issues
                        else None
                    )
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
                        "corrected_issues": corrected,
                        "scores": scores,
                        "overall_score": scores.get("overall_score"),
                        "task_type": getattr(opt, "task_type", None) if opt else None,
                        "primary_framework": (
                            getattr(opt, "primary_framework", None) if opt else None
                        ),
                        "created_at": fb.created_at,
                    })

            min_required = settings.MIN_FEEDBACKS_FOR_ADAPTATION
            if len(feedbacks) < min_required:
                return

            # Compute effective damping from feedback consistency
            damping = compute_effective_damping(
                [type("F", (), {"rating": fb.get("rating", 0)})() for fb in feedbacks],
            )

            override_deltas = compute_override_deltas(feedbacks)

            # Aggregate issue frequency and apply to deltas
            issue_freq = _aggregate_issue_frequency(feedbacks)
            adjusted_deltas = apply_issue_signals(
                override_deltas, issue_freq, total_feedbacks=len(feedbacks),
            )

            adjusted_weights = adjust_weights_from_deltas(
                base_weights=DEFAULT_WEIGHTS,
                deltas=adjusted_deltas,
                damping=damping,
                min_samples=MIN_SAMPLES_PER_DIMENSION,
            )
            threshold = compute_threshold_from_feedback(feedbacks)
            affinities = compute_strategy_affinities(feedbacks)

            # Compute consistency for storage
            ratings = [fb.get("rating", 0) for fb in feedbacks]
            consistency = _compute_consistency(ratings)

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
                existing.issue_frequency = json.dumps(issue_freq) if issue_freq else None
                existing.damping_level = damping
                existing.consistency_score = consistency
                existing.adaptation_version = _adaptation_versions.get(user_id, 0)
            else:
                adaptation = UserAdaptation(
                    user_id=user_id,
                    dimension_weights=json.dumps(adjusted_weights),
                    strategy_affinities=json.dumps(affinities),
                    retry_threshold=threshold,
                    feedback_count=len(feedbacks),
                    last_computed_at=now,
                    issue_frequency=json.dumps(issue_freq) if issue_freq else None,
                    damping_level=damping,
                    consistency_score=consistency,
                )
                db.add(adaptation)

            await db.flush()

            # Record audit event
            await _record_adaptation_event(
                user_id, db, "recompute",
                {
                    "feedback_count": len(feedbacks),
                    "damping": round(damping, 4),
                    "consistency": round(consistency, 4),
                    "issue_frequency": issue_freq,
                },
            )

            # Purge old audit events
            await _purge_old_events(user_id, db)

            logger.info(
                "Recomputed adaptation for user %s (%d feedbacks, damping=%.3f)",
                user_id, len(feedbacks), damping,
            )
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
    issue_frequency = parse_json_column(adaptation.issue_frequency) if adaptation.issue_frequency else None

    return {
        "dimension_weights": weights,
        "strategy_affinities": affinities,
        "retry_threshold": adaptation.retry_threshold,
        "feedback_count": adaptation.feedback_count,
        "issue_frequency": issue_frequency,
        "damping_level": adaptation.damping_level,
        "consistency_score": adaptation.consistency_score,
        "adaptation_version": adaptation.adaptation_version or 0,
    }


def build_adaptation_summary_data(adaptation: dict | None) -> dict:
    """Build adaptation summary from loaded adaptation state.

    Shared by the REST ``/api/feedback/summary`` endpoint and the
    ``synthesis_get_adaptation_summary`` MCP tool.

    Returns a dict with keys matching ``AdaptationSummary`` fields:
    feedback_count, priorities, active_guardrails, framework_preferences,
    top_frameworks, issue_resolution, retry_threshold.
    """
    if not adaptation:
        return {
            "feedback_count": 0,
            "priorities": [],
            "active_guardrails": [],
            "framework_preferences": {},
            "top_frameworks": [],
            "issue_resolution": {},
            "retry_threshold": 5.0,
        }

    # Build priorities from dimension weight shifts
    weights = adaptation.get("dimension_weights") or {}
    priorities: list[dict] = []
    for dim, weight in sorted(
        weights.items(),
        key=lambda x: abs(x[1] - DEFAULT_WEIGHTS.get(x[0], 0.2)),
        reverse=True,
    ):
        default = DEFAULT_WEIGHTS.get(dim, 0.2)
        shift = weight - default
        if abs(shift) > 0.01:
            priorities.append({
                "dimension": dim,
                "weight": round(weight, 3),
                "shift": round(shift, 3),
                "direction": "up" if shift > 0 else "down",
            })

    # Extract guardrails from issue frequency
    issue_freq = adaptation.get("issue_frequency") or {}
    active_guardrails = [
        issue_id for issue_id, count in issue_freq.items() if count >= 2
    ]

    # Framework preferences from strategy affinities
    affinities = adaptation.get("strategy_affinities") or {}
    framework_prefs: dict[str, float] = {}
    top_frameworks: list[str] = []
    for _task_type, prefs in affinities.items():
        for fw in prefs.get("preferred", []):
            framework_prefs[fw] = framework_prefs.get(fw, 0) + 1.0
        for fw in prefs.get("avoid", []):
            framework_prefs[fw] = framework_prefs.get(fw, 0) - 1.0

    if framework_prefs:
        top_frameworks = sorted(
            [fw for fw, score in framework_prefs.items() if score > 0],
            key=lambda fw: framework_prefs[fw],
            reverse=True,
        )[:3]

    return {
        "feedback_count": adaptation.get("feedback_count", 0),
        "priorities": priorities,
        "active_guardrails": active_guardrails,
        "framework_preferences": framework_prefs,
        "top_frameworks": top_frameworks,
        "issue_resolution": issue_freq,
        "retry_threshold": adaptation.get("retry_threshold", 5.0),
    }
