"""Pydantic models for the compare and merge endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class ScoreComparison(BaseModel):
    dimensions: list[str]
    a_scores: dict[str, float | None]
    b_scores: dict[str, float | None]
    deltas: dict[str, float | None]
    overall_delta: float | None
    winner: str | None  # "a", "b", or None (tie)
    ceilings: list[str]  # dimensions where both >= 9
    floors: list[str]  # dimensions where both < 5


class StructuralComparison(BaseModel):
    a_input_words: int
    b_input_words: int
    a_output_words: int
    b_output_words: int
    a_expansion: float
    b_expansion: float
    a_complexity: str | None
    b_complexity: str | None


class EfficiencyComparison(BaseModel):
    a_duration_ms: int | None
    b_duration_ms: int | None
    a_tokens: int | None
    b_tokens: int | None
    a_cost: float | None
    b_cost: float | None
    a_score_per_token: float | None
    b_score_per_token: float | None
    a_stage_tokens: dict[str, int] | None = None  # per-stage token breakdown
    b_stage_tokens: dict[str, int] | None = None
    a_is_estimated: bool = False  # True when token count is estimated, not real
    b_is_estimated: bool = False


class StrategyComparison(BaseModel):
    a_framework: str | None
    a_source: str | None
    a_rationale: str | None
    a_guardrails: list[str]
    a_optimization_notes: str | None = None  # what the optimizer actually did
    b_framework: str | None
    b_source: str | None
    b_rationale: str | None
    b_guardrails: list[str]
    b_optimization_notes: str | None = None


class ContextComparison(BaseModel):
    a_repo: str | None
    b_repo: str | None
    a_has_codebase: bool
    b_has_codebase: bool
    a_instruction_count: int
    b_instruction_count: int
    a_task_type: str | None = None
    b_task_type: str | None = None


class ValidationComparison(BaseModel):
    a_verdict: str | None
    b_verdict: str | None
    a_issues: list[str]
    b_issues: list[str]
    a_changes_made: list[str]
    b_changes_made: list[str]
    a_is_improvement: bool | None
    b_is_improvement: bool | None


class AdaptationComparison(BaseModel):
    feedbacks_between: int
    weight_shifts: dict[str, float]
    guardrails_added: list[str]


class CompareGuidance(BaseModel):
    headline: str
    merge_suggestion: str
    strengths_a: list[str]
    strengths_b: list[str]
    persistent_weaknesses: list[str]
    actionable: list[str]
    merge_directives: list[str]


class CompareResponse(BaseModel):
    situation: str  # REFORGE, STRATEGY, EVOLVED, CROSS
    situation_label: str
    insight_headline: str
    modifiers: list[str]
    a: dict  # full optimization record
    b: dict  # full optimization record
    scores: ScoreComparison
    structural: StructuralComparison
    efficiency: EfficiencyComparison
    strategy: StrategyComparison
    context: ContextComparison
    validation: ValidationComparison
    adaptation: AdaptationComparison
    top_insights: list[str]
    cross_patterns: list[str]  # CROSS-only insights; empty for other situations
    a_is_trashed: bool
    b_is_trashed: bool
    guidance: CompareGuidance | None  # None if LLM guidance failed


class MergeAcceptRequest(BaseModel):
    optimization_id_a: str
    optimization_id_b: str
    merged_prompt: str


class MergeAcceptResponse(BaseModel):
    optimization_id: str
    status: str
