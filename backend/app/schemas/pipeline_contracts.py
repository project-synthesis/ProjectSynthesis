"""Pipeline Pydantic contracts (Section 12).

LLM output models use ``extra="forbid"`` so that JSON schema output enforcement
works correctly and unexpected fields are rejected immediately.

Orchestrator-assembled models (ResolvedContext, PipelineResult, PipelineEvent)
do NOT use ``extra="forbid"`` because they are constructed by Python code, not
parsed from LLM JSON output, and may grow new fields without a strict schema
bump.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# Canonical task type values — must match the analyze.md template prompt
TaskType = Literal["coding", "writing", "analysis", "creative", "data", "system", "general"]

# Strategy names are now fully adaptive — discovered from prompts/strategies/*.md files.
# No hardcoded Literal. The analyzer outputs a string validated against available files.

# ---------------------------------------------------------------------------
# LLM output contracts — extra="forbid"
# ---------------------------------------------------------------------------


class DimensionScores(BaseModel):
    """Scores for the five quality dimensions, each in [1.0, 10.0]."""

    model_config = {"extra": "forbid"}

    clarity: float
    specificity: float
    structure: float
    faithfulness: float
    conciseness: float

    @model_validator(mode="after")
    def _validate_ranges(self) -> "DimensionScores":
        for name in ("clarity", "specificity", "structure", "faithfulness", "conciseness"):
            value = getattr(self, name)
            if not (1.0 <= value <= 10.0):
                raise ValueError(f"{name} must be between 1.0 and 10.0, got {value}")
        return self

    @property
    def overall(self) -> float:
        """Mean of the five dimension scores, rounded to 2 decimal places."""
        mean = (self.clarity + self.specificity + self.structure + self.faithfulness + self.conciseness) / 5
        return round(mean, 2)

    @classmethod
    def compute_deltas(cls, original: "DimensionScores", optimized: "DimensionScores") -> dict[str, float]:
        return {
            dim: round(getattr(optimized, dim) - getattr(original, dim), 2)
            for dim in ("clarity", "specificity", "structure", "faithfulness", "conciseness")
        }


class AnalysisResult(BaseModel):
    """LLM output from the Analyze stage."""

    model_config = {"extra": "forbid"}

    task_type: TaskType
    weaknesses: list[str]
    strengths: list[str]
    selected_strategy: str
    strategy_rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
    intent_label: str = "general"
    domain: str = "general"


class OptimizationResult(BaseModel):
    """LLM output from the Optimize stage."""

    model_config = {"extra": "forbid"}

    optimized_prompt: str
    changes_summary: str
    strategy_used: str


class ScoreResult(BaseModel):
    """LLM output from the Score/Validate stage."""

    model_config = {"extra": "forbid"}

    prompt_a_scores: DimensionScores
    prompt_b_scores: DimensionScores


class SuggestionsOutput(BaseModel):
    """Structured output for the suggestion generator (Haiku).

    Used by both the main pipeline (Phase 4) and refinement service (Stage 4).
    """

    model_config = {"extra": "forbid"}

    suggestions: list[dict[str, str]]  # [{text: str, source: str}]


# ---------------------------------------------------------------------------
# Orchestrator-side input contracts — extra="forbid"
# ---------------------------------------------------------------------------


class AnalyzerInput(BaseModel):
    """Input assembled by the orchestrator for the Analyze stage."""

    model_config = {"extra": "forbid"}

    raw_prompt: str
    strategy_override: str | None = None
    available_strategies: list[str]


class OptimizerInput(BaseModel):
    """Input assembled by the orchestrator for the Optimize stage."""

    model_config = {"extra": "forbid"}

    raw_prompt: str
    analysis: AnalysisResult
    analysis_summary: str
    strategy_instructions: str
    codebase_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None
    applied_patterns: str | None = None


class ScorerInput(BaseModel):
    """Input assembled by the orchestrator for the Score stage."""

    model_config = {"extra": "forbid"}

    prompt_a: str
    prompt_b: str
    presentation_order: str


# ---------------------------------------------------------------------------
# Orchestrator-assembled models — no extra="forbid"
# ---------------------------------------------------------------------------


class ResolvedContext(BaseModel):
    """Fully resolved context object passed into the pipeline."""

    raw_prompt: str
    strategy_override: str | None = None
    codebase_guidance: str | None = None
    codebase_context: str | None = None
    adaptation_state: str | None = None
    context_sources: dict[str, bool] = Field(default_factory=dict)
    trace_id: str

    model_config = {"extra": "allow"}


class PipelineEvent(BaseModel):
    """SSE event emitted during pipeline execution."""

    event: str
    data: dict[str, Any]

    model_config = {"extra": "allow"}


class PipelineResult(BaseModel):
    """Final pipeline output assembled by the orchestrator."""

    id: str
    trace_id: str
    raw_prompt: str
    optimized_prompt: str
    task_type: str
    strategy_used: str
    changes_summary: str
    optimized_scores: DimensionScores | None = None
    original_scores: DimensionScores | None = None
    score_deltas: dict[str, float] | None = None
    overall_score: float | None = None
    provider: str
    model_used: str
    scoring_mode: str
    duration_ms: int
    status: str
    context_sources: dict[str, bool]
    tokens_total: int = 0
    tokens_by_phase: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    suggestions: list[dict[str, str]] = Field(default_factory=list)
    repo_full_name: str | None = None
    codebase_context_snapshot: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"extra": "allow"}


__all__ = [
    "AnalysisResult",
    "AnalyzerInput",
    "DimensionScores",
    "OptimizerInput",
    "OptimizationResult",
    "PipelineEvent",
    "PipelineResult",
    "ResolvedContext",
    "ScoreResult",
    "ScorerInput",
    "SuggestionsOutput",
]
