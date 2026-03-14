"""Result intelligence service — verdict, insights, trade-offs, and actions.

Runs after pipeline validation completes.  Computes a ResultAssessment
from pipeline output, user history, and adaptation state.
"""

from __future__ import annotations

import logging

from app.schemas.result_assessment import (
    ActionSuggestion,
    Confidence,
    DimensionInsight,
    FrameworkFitReport,
    ImprovementSignal,
    ResultAssessment,
    RetryJourney,
    TradeOff,
    Verdict,
)
from app.services.adaptation_engine import DEFAULT_WEIGHTS
from app.services.framework_profiles import (
    FRAMEWORK_PROFILES,
    is_typical_trade_off,
)
from app.services.prompt_diff import SCORE_DIMENSIONS

logger = logging.getLogger(__name__)

# ── Verdict thresholds ────────────────────────────────────────────────
_STRONG_THRESHOLD = 7.5
_SOLID_THRESHOLD = 6.0
_MIXED_THRESHOLD = 4.5
_WEAK_DIM_THRESHOLD = 5
_STRONG_DIM_THRESHOLD = 8


def compute_verdict(
    overall_score: float,
    threshold: float,
    framework_avg: float | None,
    user_weights: dict[str, float] | None,
    scores: dict[str, float],
    gate_triggered: str | None,
) -> tuple[Verdict, Confidence, str]:
    """Compute the overall quality verdict with confidence and headline.

    Args:
        overall_score: The pipeline's overall score (1-10).
        threshold: The user's retry threshold.
        framework_avg: Average score for this framework (from history).
        user_weights: User's adapted dimension weights (or None).
        scores: Per-dimension scores.
        gate_triggered: Which retry gate triggered termination.

    Returns:
        (Verdict, Confidence, headline) tuple.
    """
    # Determine verdict
    if overall_score >= _STRONG_THRESHOLD:
        verdict = Verdict.STRONG
    elif overall_score >= _SOLID_THRESHOLD:
        verdict = Verdict.SOLID
    elif overall_score >= _MIXED_THRESHOLD:
        verdict = Verdict.MIXED
    else:
        verdict = Verdict.WEAK

    # Determine confidence
    confidence = _compute_confidence(
        overall_score, threshold, framework_avg, user_weights, scores, gate_triggered,
    )

    # Build headline
    headline = _build_headline(verdict, overall_score, gate_triggered)

    return verdict, confidence, headline


def _compute_confidence(
    overall_score: float,
    threshold: float,
    framework_avg: float | None,
    user_weights: dict[str, float] | None,
    scores: dict[str, float],
    gate_triggered: str | None,
) -> Confidence:
    """Determine confidence based on signal strength."""
    signals = 0
    total = 0

    # Signal 1: Score relative to threshold
    total += 1
    if abs(overall_score - threshold) > 1.5:
        signals += 1

    # Signal 2: Framework average comparison
    if framework_avg is not None:
        total += 1
        if abs(overall_score - framework_avg) < 1.0:
            signals += 1  # consistent with historical performance

    # Signal 3: User weights available (adapted)
    if user_weights:
        total += 1
        signals += 1  # having adapted weights means confident assessment

    # Signal 4: Dimension score consistency
    if scores:
        dim_values = [v for v in scores.values() if isinstance(v, (int, float))]
        if dim_values:
            total += 1
            spread = max(dim_values) - min(dim_values)
            if spread < 3:
                signals += 1  # low spread = consistent quality

    # Signal 5: Gate type
    if gate_triggered:
        total += 1
        if gate_triggered in ("threshold_met", "perfect_score"):
            signals += 1

    if total == 0:
        return Confidence.LOW

    ratio = signals / total
    if ratio >= 0.7:
        return Confidence.HIGH
    elif ratio >= 0.4:
        return Confidence.MEDIUM
    return Confidence.LOW


def _build_headline(
    verdict: Verdict,
    overall_score: float,
    gate_triggered: str | None,
) -> str:
    """Build a human-readable headline for the verdict."""
    score_str = f"{overall_score:.1f}/10"
    if verdict == Verdict.STRONG:
        return f"Strong result ({score_str}) — optimization achieved high quality across dimensions."
    elif verdict == Verdict.SOLID:
        return f"Solid result ({score_str}) — good quality with room for targeted improvement."
    elif verdict == Verdict.MIXED:
        gate_note = ""
        if gate_triggered == "budget_exhausted":
            gate_note = " Retry budget exhausted."
        return f"Mixed result ({score_str}) — some dimensions need attention.{gate_note}"
    else:
        return f"Below expectations ({score_str}) — consider refining or trying a different framework."


def compute_dimension_insights(
    scores: dict[str, float],
    user_weights: dict[str, float] | None,
    threshold: float,
    user_history: list[dict] | None,
    framework_perf: dict | None,
    elasticity: dict[str, float] | None,
    previous_scores: dict[str, float] | None,
) -> list[DimensionInsight]:
    """Compute per-dimension insights with context.

    Args:
        scores: Current per-dimension scores.
        user_weights: User's adapted dimension weights.
        threshold: User's retry threshold.
        user_history: Historical optimization data.
        framework_perf: Framework performance data for this task type.
        elasticity: Per-dimension elasticity values.
        previous_scores: Scores from the previous attempt (for delta).

    Returns:
        List of DimensionInsight objects, sorted by priority.
    """
    weights = user_weights or DEFAULT_WEIGHTS
    insights = []

    # Compute framework averages if available
    fw_avgs: dict[str, float] = {}
    if framework_perf and framework_perf.get("avg_scores"):
        fw_avgs = framework_perf["avg_scores"]

    for dim in SCORE_DIMENSIONS:
        score = scores.get(dim)
        if score is None:
            continue

        weight = weights.get(dim, 0.2)
        dim_label = dim.replace("_score", "").replace("_", " ").title()

        is_weak = score < _WEAK_DIM_THRESHOLD
        is_strong = score >= _STRONG_DIM_THRESHOLD

        # Delta from previous attempt
        delta = None
        if previous_scores and dim in previous_scores:
            delta = score - previous_scores[dim]

        # Framework average
        fw_avg = fw_avgs.get(dim)

        # User priority based on weight
        default_w = DEFAULT_WEIGHTS.get(dim, 0.2)
        if weight > default_w + 0.03:
            priority = "high"
        elif weight < default_w - 0.03:
            priority = "low"
        else:
            priority = "normal"

        # Assessment text
        if is_weak:
            assessment = f"{dim_label} scored {score}/10 — below the quality bar."
        elif is_strong:
            assessment = f"{dim_label} scored {score}/10 — strong performance."
        else:
            assessment = f"{dim_label} scored {score}/10 — adequate."

        if delta is not None and abs(delta) >= 1:
            direction = "improved" if delta > 0 else "declined"
            assessment += f" {direction.capitalize()} by {abs(delta):.0f} point(s)."

        insights.append(DimensionInsight(
            dimension=dim,
            score=score,
            weight=weight,
            label=dim_label,
            assessment=assessment,
            is_weak=is_weak,
            is_strong=is_strong,
            delta_from_previous=delta,
            framework_avg=fw_avg,
            user_priority=priority,
        ))

    # Sort: weak dimensions first, then by weight (descending)
    insights.sort(key=lambda i: (not i.is_weak, -i.weight))
    return insights


def detect_trade_offs(
    attempts: list[dict],
    user_weights: dict[str, float] | None,
    framework: str | None,
) -> list[TradeOff]:
    """Detect trade-offs between dimensions across retry attempts.

    A trade-off occurs when one dimension improves while another declines
    between consecutive attempts.

    Args:
        attempts: List of attempt dicts with per-dimension scores.
        user_weights: User's dimension weights.
        framework: The framework used.

    Returns:
        List of detected TradeOff objects.
    """
    if len(attempts) < 2:
        return []

    trade_offs: list[TradeOff] = []
    for i in range(1, len(attempts)):
        prev = attempts[i - 1]
        curr = attempts[i]

        gains: list[tuple[str, float]] = []
        losses: list[tuple[str, float]] = []

        for dim in SCORE_DIMENSIONS:
            prev_score = prev.get(dim)
            curr_score = curr.get(dim)
            if prev_score is None or curr_score is None:
                continue
            delta = curr_score - prev_score
            if delta >= 1:
                gains.append((dim, delta))
            elif delta <= -1:
                losses.append((dim, delta))

        for gained_dim, gained_delta in gains:
            for lost_dim, lost_delta in losses:
                is_typical = False
                if framework:
                    is_typical = is_typical_trade_off(framework, gained_dim, lost_dim)

                gained_label = gained_dim.replace("_score", "")
                lost_label = lost_dim.replace("_score", "")
                desc = (
                    f"{gained_label} improved by {gained_delta:.0f} "
                    f"while {lost_label} declined by {abs(lost_delta):.0f}"
                )
                if is_typical:
                    desc += f" (typical for {framework})"

                trade_offs.append(TradeOff(
                    gained_dimension=gained_dim,
                    lost_dimension=lost_dim,
                    gained_delta=gained_delta,
                    lost_delta=lost_delta,
                    is_typical_for_framework=is_typical,
                    description=desc,
                ))

    return trade_offs


def compute_retry_journey(
    attempts: list[dict],
    oracle_diagnostics: list[dict] | None,
) -> RetryJourney:
    """Compute a summary of the retry journey.

    Args:
        attempts: List of attempt dicts with overall_score.
        oracle_diagnostics: Retry oracle diagnostic records.

    Returns:
        RetryJourney summary.
    """
    if not attempts:
        return RetryJourney()

    scores = [a.get("overall_score", 0) for a in attempts]
    best_score = max(scores)
    best_idx = scores.index(best_score) + 1

    gates = []
    if oracle_diagnostics:
        gates = [d.get("gate", "unknown") for d in oracle_diagnostics]

    # Momentum trend
    if len(scores) >= 3:
        first_half = sum(scores[: len(scores) // 2]) / max(len(scores) // 2, 1)
        second_half = sum(scores[len(scores) // 2 :]) / max(len(scores) - len(scores) // 2, 1)
        if second_half > first_half + 0.3:
            trend = "improving"
        elif second_half < first_half - 0.3:
            trend = "declining"
        else:
            trend = "stable"
    elif len(scores) == 2:
        trend = "improving" if scores[1] > scores[0] else "declining" if scores[1] < scores[0] else "stable"
    else:
        trend = "stable"

    # Summary
    if len(attempts) == 1:
        summary = "Single attempt — no retry needed."
    else:
        summary = (
            f"{len(attempts)} attempts. "
            f"Best score {best_score:.1f} on attempt {best_idx}. "
            f"Trend: {trend}."
        )

    return RetryJourney(
        total_attempts=len(attempts),
        best_attempt=best_idx,
        score_trajectory=scores,
        gate_sequence=gates,
        momentum_trend=trend,
        summary=summary,
    )


def compute_framework_fit(
    framework: str | None,
    task_type: str | None,
    overall_score: float,
    framework_perf: dict | None,
    all_perfs: list[dict] | None,
) -> FrameworkFitReport | None:
    """Compute how well the framework fits the task type.

    Args:
        framework: The framework used.
        task_type: The task classification.
        overall_score: Current overall score.
        framework_perf: Performance data for this specific framework+task.
        all_perfs: Performance data for all frameworks for this task type.

    Returns:
        FrameworkFitReport or None if insufficient data.
    """
    if not framework or not task_type:
        return None

    sample_count = 0
    user_rating_avg = None
    if framework_perf:
        sample_count = framework_perf.get("sample_count", 0)
        user_rating_avg = framework_perf.get("user_rating_avg")

    # Compute fit score based on available signals
    fit_signals: list[float] = []

    # Signal 1: Current score quality
    fit_signals.append(min(1.0, overall_score / 10.0))

    # Signal 2: User rating history
    if user_rating_avg is not None:
        # Rating is -1 to 1, normalize to 0-1
        fit_signals.append((user_rating_avg + 1) / 2)

    # Signal 3: Framework profile match
    profile = FRAMEWORK_PROFILES.get(framework)
    if profile:
        emphasis = profile.get("emphasis", {})
        if emphasis:
            # Check if emphasized dimensions scored well
            emphasis_scores = []
            for dim, mult in emphasis.items():
                if mult > 1.0:
                    emphasis_scores.append(1.0)  # basic fit indicator
            if emphasis_scores:
                fit_signals.append(sum(emphasis_scores) / len(emphasis_scores))

    fit_score = sum(fit_signals) / len(fit_signals) if fit_signals else 0.5

    if fit_score >= 0.75:
        fit_label = "strong"
    elif fit_score >= 0.55:
        fit_label = "good"
    elif fit_score >= 0.35:
        fit_label = "neutral"
    else:
        fit_label = "poor"

    # Find alternatives from all performance data
    alternatives: list[str] = []
    if all_perfs:
        for perf in all_perfs:
            fw = perf.get("framework")
            if fw and fw != framework:
                fw_rating = perf.get("user_rating_avg", 0)
                if fw_rating and fw_rating > 0.3:
                    alternatives.append(fw)
        alternatives = alternatives[:3]

    recommendation = ""
    if fit_label == "poor":
        if alternatives:
            recommendation = f"Consider switching to {alternatives[0]} for {task_type} tasks."
        else:
            recommendation = f"This framework may not be ideal for {task_type} tasks."
    elif fit_label == "strong":
        recommendation = f"{framework} is a strong fit for {task_type} tasks."

    return FrameworkFitReport(
        framework=framework,
        task_type=task_type,
        fit_score=round(fit_score, 2),
        fit_label=fit_label,
        user_rating_avg=user_rating_avg,
        sample_count=sample_count,
        alternatives=alternatives,
        recommendation=recommendation,
    )


def compute_improvement_potential(
    scores: dict[str, float],
    elasticity: dict[str, float] | None,
    framework: str | None,
    user_weights: dict[str, float] | None,
) -> list[ImprovementSignal]:
    """Compute improvement potential per dimension.

    Dimensions with low scores and high elasticity have the most
    improvement potential.

    Args:
        scores: Per-dimension scores.
        elasticity: Per-dimension elasticity values.
        framework: The framework used.
        user_weights: User's dimension weights.

    Returns:
        List of ImprovementSignal objects sorted by potential gain.
    """
    weights = user_weights or DEFAULT_WEIGHTS
    elastic = elasticity or {}
    signals: list[ImprovementSignal] = []

    for dim in SCORE_DIMENSIONS:
        score = scores.get(dim)
        if score is None:
            continue

        weight = weights.get(dim, 0.2)
        elast = elastic.get(dim, 0.5)

        # Potential gain: how much room for improvement * elasticity * weight
        room = 10.0 - score
        potential = room * elast * weight * 10  # scale to interpretable range
        potential = round(min(potential, room), 1)

        if potential < 0.5:
            continue

        # Effort label based on elasticity
        if elast >= 0.7:
            effort = "low"
        elif elast >= 0.4:
            effort = "medium"
        else:
            effort = "high"

        dim_label = dim.replace("_score", "").replace("_", " ")
        suggestion = f"Focus refinement on {dim_label} (current: {score:.0f}/10, elasticity: {elast:.1f})."

        signals.append(ImprovementSignal(
            dimension=dim,
            current_score=score,
            potential_gain=potential,
            elasticity=elast,
            effort_label=effort,
            suggestion=suggestion,
        ))

    signals.sort(key=lambda s: s.potential_gain, reverse=True)
    return signals


def compute_next_actions(
    verdict: Verdict,
    confidence: Confidence,
    weak_dims: list[str],
    framework_fit: FrameworkFitReport | None,
    improvement_signals: list[ImprovementSignal],
    trade_offs: list[TradeOff],
    active_guardrails: list[str] | None,
) -> list[ActionSuggestion]:
    """Compute recommended next actions based on the full assessment.

    Returns a prioritized list of 1-5 action suggestions.
    """
    actions: list[ActionSuggestion] = []

    # Action 1: Verdict-based primary action
    if verdict in (Verdict.WEAK, Verdict.MIXED):
        if weak_dims:
            dim_names = [d.replace("_score", "") for d in weak_dims[:3]]
            actions.append(ActionSuggestion(
                action=f"Refine to improve: {', '.join(dim_names)}",
                rationale="These dimensions scored below the quality bar.",
                priority="high",
                category="refine",
            ))
    elif verdict == Verdict.STRONG:
        actions.append(ActionSuggestion(
            action="Submit positive feedback to reinforce this pattern",
            rationale="Strong results help the adaptation engine learn your preferences.",
            priority="medium",
            category="feedback",
        ))

    # Action 2: Framework fit
    if framework_fit and framework_fit.fit_label == "poor" and framework_fit.alternatives:
        actions.append(ActionSuggestion(
            action=f"Try {framework_fit.alternatives[0]} for better results",
            rationale=framework_fit.recommendation,
            priority="high",
            category="framework",
        ))

    # Action 3: Top improvement signal
    if improvement_signals:
        top = improvement_signals[0]
        if top.effort_label == "low":
            actions.append(ActionSuggestion(
                action=f"Quick win: improve {top.dimension.replace('_score', '')}",
                rationale=top.suggestion,
                priority="medium",
                category="refine",
            ))

    # Action 4: Trade-off awareness
    if trade_offs:
        non_typical = [t for t in trade_offs if not t.is_typical_for_framework]
        if non_typical:
            t = non_typical[0]
            actions.append(ActionSuggestion(
                action="Address dimension trade-off",
                rationale=t.description,
                priority="medium",
                category="refine",
            ))

    # Action 5: Feedback prompt (if not already added)
    if not any(a.category == "feedback" for a in actions):
        actions.append(ActionSuggestion(
            action="Submit feedback to improve future results",
            rationale="Your feedback tunes dimension weights and framework selection.",
            priority="low",
            category="feedback",
        ))

    return actions[:5]


def compute_result_assessment(
    overall_score: float,
    scores: dict[str, float],
    threshold: float = 5.0,
    framework: str | None = None,
    task_type: str | None = None,
    user_weights: dict[str, float] | None = None,
    framework_perf: dict | None = None,
    all_framework_perfs: list[dict] | None = None,
    elasticity: dict[str, float] | None = None,
    previous_scores: dict[str, float] | None = None,
    attempts: list[dict] | None = None,
    oracle_diagnostics: list[dict] | None = None,
    gate_triggered: str | None = None,
    active_guardrails: list[str] | None = None,
    user_history: list[dict] | None = None,
) -> ResultAssessment:
    """Orchestrate the full result assessment.

    This is the main entry point called after pipeline validation.
    """
    # Compute framework average from performance data
    framework_avg = None
    if framework_perf and framework_perf.get("avg_scores"):
        avg_scores = framework_perf["avg_scores"]
        if avg_scores:
            vals = [v for v in avg_scores.values() if isinstance(v, (int, float))]
            if vals:
                framework_avg = sum(vals) / len(vals)

    # 1. Verdict
    verdict, confidence, headline = compute_verdict(
        overall_score=overall_score,
        threshold=threshold,
        framework_avg=framework_avg,
        user_weights=user_weights,
        scores=scores,
        gate_triggered=gate_triggered,
    )

    # 2. Dimension insights
    dimension_insights = compute_dimension_insights(
        scores=scores,
        user_weights=user_weights,
        threshold=threshold,
        user_history=user_history,
        framework_perf=framework_perf,
        elasticity=elasticity,
        previous_scores=previous_scores,
    )

    # 3. Trade-offs
    trade_offs = detect_trade_offs(
        attempts=attempts or [],
        user_weights=user_weights,
        framework=framework,
    )

    # 4. Retry journey
    retry_journey = compute_retry_journey(
        attempts=attempts or [{"overall_score": overall_score}],
        oracle_diagnostics=oracle_diagnostics,
    )

    # 5. Framework fit
    framework_fit = compute_framework_fit(
        framework=framework,
        task_type=task_type,
        overall_score=overall_score,
        framework_perf=framework_perf,
        all_perfs=all_framework_perfs,
    )

    # 6. Improvement potential
    improvement_signals = compute_improvement_potential(
        scores=scores,
        elasticity=elasticity,
        framework=framework,
        user_weights=user_weights,
    )

    # 7. Weak dimensions for action computation
    weak_dims = [i.dimension for i in dimension_insights if i.is_weak]

    # 8. Next actions
    next_actions = compute_next_actions(
        verdict=verdict,
        confidence=confidence,
        weak_dims=weak_dims,
        framework_fit=framework_fit,
        improvement_signals=improvement_signals,
        trade_offs=trade_offs,
        active_guardrails=active_guardrails,
    )

    # TODO: Compute percentile_context from user_history when sufficient data
    # exists (>= 5 past optimizations). Requires aggregating per-dimension
    # score distributions from user_history to place the current result.
    percentile_context = None

    # TODO: Compute trend_analysis from user_history when sufficient data
    # exists (>= 3 recent optimizations). Compare recent vs previous average
    # overall scores to detect improving/declining/stable trajectory.
    trend_analysis = None

    return ResultAssessment(
        verdict=verdict,
        confidence=confidence,
        headline=headline,
        dimension_insights=dimension_insights,
        trade_offs=trade_offs,
        retry_journey=retry_journey,
        framework_fit=framework_fit,
        improvement_signals=improvement_signals,
        next_actions=next_actions,
        percentile_context=percentile_context,
        trend_analysis=trend_analysis,
    )
