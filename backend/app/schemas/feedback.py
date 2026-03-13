"""Pydantic schemas for feedback and adaptation endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
        return self


class DimensionDelta(BaseModel):
    """Per-dimension score change between retry attempts."""
    dimension: str
    before: int
    after: int
    delta: int


class RetryHistoryEntry(BaseModel):
    """One entry in the retry_history JSON array on Optimization."""
    attempt: int
    scores: dict[str, int | float]
    focus_areas: list[str]
    dimension_deltas: dict[str, int] = {}
    prompt_hash: str = ""


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
    avg_override_delta: dict[str, float] | None
    most_corrected_dimension: str | None
    adaptation_state: dict | None


class AdaptationStateResponse(BaseModel):
    dimension_weights: dict[str, float] | None = None
    strategy_affinities: dict | None = None
    retry_threshold: float = 5.0
    feedback_count: int = 0
