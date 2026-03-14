"""Pydantic schemas for feedback and adaptation endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services.framework_profiles import CORRECTABLE_ISSUES
from app.services.prompt_diff import SCORE_DIMENSIONS

VALID_DIMENSIONS = set(SCORE_DIMENSIONS)


class FeedbackCreate(BaseModel):
    rating: Literal[-1, 0, 1]
    dimension_overrides: dict[str, int] | None = None
    corrected_issues: list[str] | None = Field(None, max_length=50)
    comment: str | None = Field(None, max_length=2000)

    @model_validator(mode="after")
    def validate_dimension_overrides(self) -> "FeedbackCreate":
        if self.dimension_overrides:
            for key, value in self.dimension_overrides.items():
                if key not in VALID_DIMENSIONS:
                    raise ValueError(f"Invalid dimension: {key}")
                if not (1 <= value <= 10):
                    raise ValueError(f"Score must be 1-10, got {value} for {key}")
        if self.corrected_issues is not None:
            for issue_id in self.corrected_issues:
                if issue_id not in CORRECTABLE_ISSUES:
                    raise ValueError(
                        f"Invalid issue ID: {issue_id}. "
                        f"Valid: {sorted(CORRECTABLE_ISSUES)}"
                    )
            self.corrected_issues = list(dict.fromkeys(self.corrected_issues))
        return self


class DimensionDelta(BaseModel):
    """Per-dimension score change between retry attempts."""
    dimension: str
    before: int
    after: int
    delta: int


class RetryHistoryEntry(BaseModel):
    """One entry in the retry_history JSON array on Optimization.

    Shape matches ``RetryOracle.get_diagnostics()`` output exactly.
    """
    attempt: int
    overall_score: float
    threshold: float
    action: str
    reason: str = ""
    focus_areas: list[str] = []
    gate: str = "pending"
    momentum: float = 0.0
    best_attempt_index: int = 0
    best_score: float | None = None


class InstructionCompliance(BaseModel):
    """Per-instruction compliance from validation."""
    instruction: str
    satisfied: bool
    note: str | None = None


class FeedbackResponse(BaseModel):
    id: str
    optimization_id: str
    user_id: str
    rating: int
    dimension_overrides: dict[str, int] | None = None
    corrected_issues: list[str] | None = None
    comment: str | None = None
    created_at: str


class FeedbackAggregate(BaseModel):
    total_ratings: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    avg_dimension_overrides: dict[str, float] | None = None


class FeedbackWithAggregate(BaseModel):
    feedback: FeedbackResponse | None = None
    aggregate: FeedbackAggregate


class FeedbackStatsResponse(BaseModel):
    total_feedbacks: int
    rating_distribution: dict[str, int]
    avg_override_delta: dict[str, float] | None = None  # deprecated
    most_corrected_dimension: str | None = None  # deprecated
    issue_frequency: dict[str, int] = {}
    adaptation_state: dict | None = None


class AdaptationPulse(BaseModel):
    """Compact adaptation status indicator for UI."""

    status: Literal["inactive", "learning", "active"]
    label: str
    detail: str


class AdaptationSummary(BaseModel):
    """High-level adaptation summary for dashboard display."""

    feedback_count: int = 0
    priorities: list[dict] = []
    active_guardrails: list[str] = []
    framework_preferences: dict[str, float] = {}
    top_frameworks: list[str] = []
    issue_resolution: dict[str, int] = {}
    retry_threshold: float = 5.0
    last_updated: str | None = None


class FeedbackConfirmation(BaseModel):
    """Immediate confirmation after submitting feedback."""

    summary: str
    effects: list[str] = []
    stage_note: str | None = None


class AdaptationStateResponse(BaseModel):
    dimension_weights: dict[str, float] | None = None
    strategy_affinities: dict | None = None
    retry_threshold: float = 5.0
    feedback_count: int = 0
    issue_frequency: dict[str, int] = {}
    damping_level: float = 0.15
    consistency_score: float = 0.0
    adaptation_version: int = 0
