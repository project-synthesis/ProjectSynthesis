"""Feedback API endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session
from app.dependencies.auth import get_current_user
from app.dependencies.rate_limit import RateLimit
from app.models.feedback import Feedback
from app.schemas.auth import AuthenticatedUser
from app.schemas.feedback import (
    AdaptationPulse,
    AdaptationSummary,
    FeedbackConfirmation,
    FeedbackCreate,
    FeedbackStatsResponse,
    FeedbackWithAggregate,
)
from app.services.adaptation_engine import (
    DEFAULT_WEIGHTS,
    compute_adaptation_pulse,
    load_adaptation,
    schedule_adaptation_recompute,
)
from app.services.feedback_service import (
    get_feedback_aggregate,
    get_feedback_for_optimization,
    get_user_feedback_history,
    upsert_feedback,
)
from app.services.framework_profiles import ISSUE_EFFECT_LABELS

logger = logging.getLogger(__name__)
router = APIRouter(tags=["feedback"])


def _build_confirmation(
    rating: int,
    created: bool,
    corrected_issues: list[str] | None,
    adaptation_count: int,
) -> FeedbackConfirmation:
    """Build a FeedbackConfirmation from submission context."""
    action = "recorded" if created else "updated"
    rating_label = {1: "positive", 0: "neutral", -1: "negative"}.get(rating, "neutral")
    summary = f"{rating_label.capitalize()} feedback {action}."

    effects: list[str] = []
    if corrected_issues:
        for issue_id in corrected_issues:
            label = ISSUE_EFFECT_LABELS.get(issue_id)
            if label:
                effects.append(label)

    min_needed = settings.MIN_FEEDBACKS_FOR_ADAPTATION
    if adaptation_count + 1 < min_needed:
        remaining = min_needed - (adaptation_count + 1)
        stage_note = f"{remaining} more feedback(s) needed before adaptation activates."
    else:
        stage_note = "Adaptation recompute scheduled."

    return FeedbackConfirmation(
        summary=summary,
        effects=effects,
        stage_note=stage_note,
    )


@router.post(
    "/api/optimize/{optimization_id}/feedback",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_FEEDBACK))],
    response_model=FeedbackConfirmation,
)
async def submit_feedback(
    optimization_id: str,
    body: FeedbackCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    result = await upsert_feedback(
        optimization_id=optimization_id,
        user_id=current_user.id,
        rating=body.rating,
        dimension_overrides=body.dimension_overrides,
        corrected_issues=body.corrected_issues,
        comment=body.comment,
        db=db,
    )
    await db.commit()

    # Trigger debounced adaptation recomputation
    schedule_adaptation_recompute(current_user.id)

    # Load adaptation to determine stage note
    adaptation = await load_adaptation(current_user.id, db)
    adaptation_count = adaptation.get("feedback_count", 0) if adaptation else 0

    return _build_confirmation(
        rating=body.rating,
        created=result["created"],
        corrected_issues=body.corrected_issues,
        adaptation_count=adaptation_count,
    )


@router.get(
    "/api/optimize/{optimization_id}/feedback",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def get_feedback(
    optimization_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    feedback = await get_feedback_for_optimization(optimization_id, current_user.id, db)
    aggregate = await get_feedback_aggregate(optimization_id, db)
    return FeedbackWithAggregate(
        feedback=feedback,
        aggregate=aggregate,
    )


@router.get(
    "/api/feedback/history",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
)
async def feedback_history(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
    offset: int = 0,
    limit: int = 50,
    rating: int | None = None,
):
    return await get_user_feedback_history(current_user.id, db, limit, offset, rating)


@router.get(
    "/api/feedback/stats",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
    response_model=FeedbackStatsResponse,
)
async def feedback_stats(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    # Use SQL COUNT/GROUP BY instead of loading all rows
    rating_stmt = (
        select(Feedback.rating, func.count(Feedback.id))
        .where(Feedback.user_id == current_user.id)
        .group_by(Feedback.rating)
    )
    rating_result = await db.execute(rating_stmt)
    rating_counts = {row[0]: row[1] for row in rating_result}

    total_stmt = (
        select(func.count(Feedback.id))
        .where(Feedback.user_id == current_user.id)
    )
    total_result = await db.execute(total_stmt)
    total = total_result.scalar() or 0

    rating_dist = {
        "positive": sum(v for k, v in rating_counts.items() if k > 0),
        "negative": sum(v for k, v in rating_counts.items() if k < 0),
        "neutral": rating_counts.get(0, 0),
    }

    adaptation = await load_adaptation(current_user.id, db)

    return FeedbackStatsResponse(
        total_feedbacks=total,
        rating_distribution=rating_dist,
        adaptation_state=adaptation,
    )


@router.get(
    "/api/feedback/pulse",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
    response_model=AdaptationPulse,
)
async def feedback_pulse(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Compact adaptation status pulse for UI display."""
    adaptation = await load_adaptation(current_user.id, db)
    pulse = compute_adaptation_pulse(adaptation)
    return AdaptationPulse(**pulse)


@router.get(
    "/api/feedback/summary",
    dependencies=[Depends(RateLimit(lambda: settings.RATE_LIMIT_HISTORY))],
    response_model=AdaptationSummary,
)
async def feedback_summary(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """High-level adaptation summary for dashboard display."""
    adaptation = await load_adaptation(current_user.id, db)

    if not adaptation:
        return AdaptationSummary()

    # Build priorities from dimension weight shifts
    weights = adaptation.get("dimension_weights") or {}
    priorities = []
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

    return AdaptationSummary(
        feedback_count=adaptation.get("feedback_count", 0),
        priorities=priorities,
        active_guardrails=active_guardrails,
        framework_preferences=framework_prefs,
        top_frameworks=top_frameworks,
        issue_resolution=issue_freq,
        retry_threshold=adaptation.get("retry_threshold", 5.0),
        last_updated=None,
    )
