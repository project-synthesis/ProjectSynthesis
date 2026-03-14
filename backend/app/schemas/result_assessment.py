"""Pydantic schemas for result intelligence — verdict, insights, and actions.

Used by the result_intelligence service to return structured assessments
after pipeline validation completes.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    """Overall quality verdict for a pipeline result."""

    STRONG = "strong"
    SOLID = "solid"
    MIXED = "mixed"
    WEAK = "weak"


class Confidence(str, Enum):
    """Confidence level in the verdict assessment."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DimensionInsight(BaseModel):
    """Per-dimension analysis with context and recommendations."""

    dimension: str
    score: float
    weight: float = 0.2
    label: str = ""
    assessment: str = ""
    is_weak: bool = False
    is_strong: bool = False
    delta_from_previous: float | None = None
    framework_avg: float | None = None
    user_priority: str = "normal"  # "high" | "normal" | "low"


class TradeOff(BaseModel):
    """Detected trade-off between dimensions across retry attempts."""

    gained_dimension: str
    lost_dimension: str
    gained_delta: float
    lost_delta: float
    is_typical_for_framework: bool = False
    description: str = ""


class RetryJourney(BaseModel):
    """Summary of the retry journey for this optimization."""

    total_attempts: int = 1
    best_attempt: int = 1
    score_trajectory: list[float] = []
    gate_sequence: list[str] = []
    momentum_trend: str = "stable"  # "improving" | "stable" | "declining"
    summary: str = ""


class FrameworkFitReport(BaseModel):
    """How well the selected framework fits this task type."""

    framework: str
    task_type: str
    fit_score: float = 0.5  # 0-1
    fit_label: str = "neutral"  # "strong" | "good" | "neutral" | "poor"
    user_rating_avg: float | None = None
    sample_count: int = 0
    alternatives: list[str] = []
    recommendation: str = ""


class ImprovementSignal(BaseModel):
    """Actionable improvement signal for a dimension."""

    dimension: str
    current_score: float
    potential_gain: float
    elasticity: float = 0.0
    effort_label: str = "medium"  # "low" | "medium" | "high"
    suggestion: str = ""


class ActionSuggestion(BaseModel):
    """Recommended next action based on the assessment."""

    action: str
    rationale: str
    priority: str = "medium"  # "high" | "medium" | "low"
    category: str = "general"  # "retry" | "refine" | "framework" | "feedback" | "general"


class PercentileContext(BaseModel):
    """Score percentile relative to user history or global distribution."""

    overall_percentile: float | None = None
    per_dimension: dict[str, float] = {}
    sample_size: int = 0


class TrendAnalysis(BaseModel):
    """Score trends over recent optimizations."""

    direction: str = "stable"  # "improving" | "stable" | "declining"
    recent_scores: list[float] = []
    avg_recent: float | None = None
    avg_previous: float | None = None


class AdaptationImpactReport(BaseModel):
    """How adaptation settings affected this result."""

    improvements: list[dict] = []      # [{dim, prev, curr}]
    regressions: list[dict] = []       # [{dim, prev, curr}]
    resolved_issues: list[str] = []
    active_guardrails: list[str] = []
    has_meaningful_change: bool = False
    # Existing fields kept for backward compat
    weights_applied: dict[str, float] = {}
    guardrails_active: list[str] = []
    threshold_used: float = 5.0
    estimated_impact: str = "none"  # "positive" | "neutral" | "negative" | "none"


class ResultAssessment(BaseModel):
    """Complete result assessment from the intelligence service."""

    verdict: Verdict = Verdict.MIXED
    confidence: Confidence = Confidence.MEDIUM
    headline: str = ""
    dimension_insights: list[DimensionInsight] = []
    trade_offs: list[TradeOff] = []
    retry_journey: RetryJourney = Field(default_factory=RetryJourney)
    framework_fit: FrameworkFitReport | None = None
    improvement_signals: list[ImprovementSignal] = []
    next_actions: list[ActionSuggestion] = []
    percentile_context: PercentileContext | None = None
    trend_analysis: TrendAnalysis | None = None
    adaptation_impact: AdaptationImpactReport | None = None

    @classmethod
    def first_time_fallback(cls) -> "ResultAssessment":
        """Default assessment for first-time users with no history."""
        return cls(
            verdict=Verdict.MIXED,
            confidence=Confidence.LOW,
            headline="First optimization — submit feedback to unlock personalized insights.",
            next_actions=[
                ActionSuggestion(
                    action="Submit feedback on this result",
                    rationale=(
                        "Your feedback trains the adaptation engine to "
                        "prioritize the dimensions you care about."
                    ),
                    priority="high",
                    category="feedback",
                ),
            ],
        )
