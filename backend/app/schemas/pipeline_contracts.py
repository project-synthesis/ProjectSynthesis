"""Pipeline Pydantic contracts (Section 12).

LLM output models use ``extra="forbid"`` so that JSON schema output enforcement
works correctly and unexpected fields are rejected immediately.

Orchestrator-assembled models (ResolvedContext, PipelineResult, PipelineEvent)
do NOT use ``extra="forbid"`` because they are constructed by Python code, not
parsed from LLM JSON output, and may grow new fields without a strict schema
bump.

Every field includes a ``Field(description=...)`` so that schemas derived via
``_pydantic_to_mcp_tool()`` for MCP sampling give the IDE model full context.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar, Literal

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

    clarity: float = Field(
        description="Clarity score (1.0-10.0): how clear and understandable is the prompt.",
    )
    specificity: float = Field(
        description="Specificity score (1.0-10.0): how precise and well-defined is the request.",
    )
    structure: float = Field(
        description="Structure score (1.0-10.0): how well-organized and logically laid out.",
    )
    faithfulness: float = Field(
        description="Faithfulness score (1.0-10.0): how true to the original intent.",
    )
    conciseness: float = Field(
        description="Conciseness score (1.0-10.0): how efficiently written without verbosity.",
    )

    @model_validator(mode="after")
    def _validate_ranges(self) -> "DimensionScores":
        for name in ("clarity", "specificity", "structure", "faithfulness", "conciseness"):
            value = getattr(self, name)
            if not (1.0 <= value <= 10.0):
                raise ValueError(f"{name} must be between 1.0 and 10.0, got {value}")
        return self

    _DIMENSIONS: ClassVar[tuple[str, ...]] = ("clarity", "specificity", "structure", "faithfulness", "conciseness")

    @classmethod
    def from_dict(cls, d: dict[str, float], default: float = 5.0) -> "DimensionScores":
        """Construct from a dict, using *default* for missing dimensions."""
        return cls(**{dim: d.get(dim, default) for dim in cls._DIMENSIONS})

    def to_dict(self) -> dict[str, float]:
        """Export the five dimensions as a plain dict."""
        return {dim: getattr(self, dim) for dim in self._DIMENSIONS}

    @property
    def overall(self) -> float:
        """Mean of the five dimension scores, rounded to 2 decimal places."""
        mean = (self.clarity + self.specificity + self.structure + self.faithfulness + self.conciseness) / 5
        return round(mean, 2)

    @classmethod
    def compute_deltas(cls, original: "DimensionScores", optimized: "DimensionScores") -> dict[str, float]:
        return {
            dim: round(getattr(optimized, dim) - getattr(original, dim), 2)
            for dim in cls._DIMENSIONS
        }


class AnalysisResult(BaseModel):
    """LLM output from the Analyze stage."""

    model_config = {"extra": "forbid"}

    task_type: TaskType = Field(
        description="Task classification: 'coding', 'writing', 'analysis', "
        "'creative', 'data', 'system', or 'general'.",
    )
    weaknesses: list[str] = Field(
        description="List of identified weaknesses in the prompt.",
    )
    strengths: list[str] = Field(
        description="List of identified strengths in the prompt.",
    )
    selected_strategy: str = Field(
        description="Recommended optimization strategy name.",
    )
    strategy_rationale: str = Field(
        description="Explanation for why this strategy is recommended.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score (0.0-1.0) in the analysis and recommendations.",
    )
    intent_label: str = Field(
        default="general",
        description="3-6 word intent label summarizing the prompt's purpose.",
    )
    domain: str = Field(
        default="general",
        description="Domain classification. Use a known domain if it matches "
        "(backend, frontend, database, data, devops, security, fullstack). "
        "If none match, use a descriptive domain name (e.g., 'marketing', "
        "'finance', 'saas'). Only use 'general' if truly domain-agnostic.",
    )


class OptimizationResult(BaseModel):
    """LLM output from the Optimize stage."""

    model_config = {"extra": "forbid"}

    optimized_prompt: str = Field(
        description="The rewritten prompt text only. Do NOT include ## Changes, "
        "## Applied Patterns, or any metadata sections in this field.",
    )
    changes_summary: str = Field(
        description="Summary of what changed and why. Include all rationale "
        "and applied pattern notes here — never in optimized_prompt.",
    )
    strategy_used: str = Field(
        description="Strategy name that was applied.",
    )


class ScoreResult(BaseModel):
    """LLM output from the Score/Validate stage."""

    model_config = {"extra": "forbid"}

    prompt_a_scores: DimensionScores = Field(
        description="Quality scores for the first prompt (presentation order A).",
    )
    prompt_b_scores: DimensionScores = Field(
        description="Quality scores for the second prompt (presentation order B).",
    )


class SuggestionsOutput(BaseModel):
    """Structured output for the suggestion generator (Haiku).

    Used by both the main pipeline (Phase 4) and refinement service (Stage 4).
    """

    model_config = {"extra": "forbid"}

    suggestions: list[dict[str, str]] = Field(
        description="List of suggestion dicts, each with 'text' (suggestion content) "
        "and 'source' (origin of the suggestion).",
    )


# ---------------------------------------------------------------------------
# Orchestrator-side input contracts — extra="forbid"
# ---------------------------------------------------------------------------


class AnalyzerInput(BaseModel):
    """Input assembled by the orchestrator for the Analyze stage."""

    model_config = {"extra": "forbid"}

    raw_prompt: str = Field(
        description="The original prompt to analyze.",
    )
    strategy_override: str | None = Field(
        default=None,
        description="Optional user override for strategy selection.",
    )
    available_strategies: list[str] = Field(
        description="List of available strategy names from prompts/strategies/.",
    )


class OptimizerInput(BaseModel):
    """Input assembled by the orchestrator for the Optimize stage."""

    model_config = {"extra": "forbid"}

    raw_prompt: str = Field(
        description="The original prompt text.",
    )
    analysis: AnalysisResult = Field(
        description="Analysis result from the Analyze stage.",
    )
    analysis_summary: str = Field(
        description="Formatted summary of the analysis for template injection.",
    )
    strategy_instructions: str = Field(
        description="Strategy-specific optimization instructions from the strategy file.",
    )
    codebase_guidance: str | None = Field(
        default=None,
        description="Workspace guidance context from agent guidance files.",
    )
    codebase_context: str | None = Field(
        default=None,
        description="Semantic codebase context from explore phase.",
    )
    adaptation_state: str | None = Field(
        default=None,
        description="Formatted feedback adaptation state for strategy tuning.",
    )
    applied_patterns: str | None = Field(
        default=None,
        description="Meta-patterns to inject into the optimization context.",
    )


class ScorerInput(BaseModel):
    """Input assembled by the orchestrator for the Score stage."""

    model_config = {"extra": "forbid"}

    prompt_a: str = Field(
        description="First prompt to score (original or optimized, per presentation order).",
    )
    prompt_b: str = Field(
        description="Second prompt to score (original or optimized, per presentation order).",
    )
    presentation_order: str = Field(
        description="Order in which prompts are presented: 'AB' or 'BA' (randomized for bias mitigation).",
    )


# ---------------------------------------------------------------------------
# Orchestrator-assembled models — no extra="forbid"
# ---------------------------------------------------------------------------


class ResolvedContext(BaseModel):
    """Fully resolved context object passed into the pipeline."""

    raw_prompt: str = Field(
        description="The original prompt text.",
    )
    strategy_override: str | None = Field(
        default=None,
        description="User override for strategy selection.",
    )
    codebase_guidance: str | None = Field(
        default=None,
        description="Resolved workspace guidance context.",
    )
    codebase_context: str | None = Field(
        default=None,
        description="Semantic codebase context from explore phase.",
    )
    adaptation_state: str | None = Field(
        default=None,
        description="Formatted adaptation state from feedback tracker.",
    )
    context_sources: dict[str, bool] = Field(
        default_factory=dict,
        description="Map of context source names to enabled/available flags.",
    )
    trace_id: str = Field(
        description="Trace ID for this pipeline run.",
    )

    model_config = {"extra": "allow"}


class PipelineEvent(BaseModel):
    """SSE event emitted during pipeline execution."""

    event: str = Field(
        description="Event type name (e.g. 'phase_start', 'phase_complete', 'error').",
    )
    data: dict[str, Any] = Field(
        description="Event payload data.",
    )

    model_config = {"extra": "allow"}


class PipelineResult(BaseModel):
    """Final pipeline output assembled by the orchestrator."""

    id: str = Field(description="Unique optimization record ID.")
    trace_id: str = Field(description="Trace ID for tracking.")
    raw_prompt: str = Field(description="Original prompt text.")
    optimized_prompt: str = Field(description="Optimized prompt text.")
    task_type: str = Field(description="Task classification.")
    strategy_used: str = Field(description="Strategy that was applied.")
    changes_summary: str = Field(description="Summary of changes made.")
    optimized_scores: DimensionScores | None = Field(
        default=None, description="Scores for the optimized prompt.",
    )
    original_scores: DimensionScores | None = Field(
        default=None, description="Scores for the original prompt.",
    )
    score_deltas: dict[str, float] | None = Field(
        default=None, description="Per-dimension score change (optimized minus original).",
    )
    overall_score: float | None = Field(
        default=None, description="Mean of all dimension scores (0.0-10.0).",
    )
    provider: str = Field(description="LLM provider name (e.g. 'claude_cli', 'anthropic_api').")
    routing_tier: str | None = Field(
        default=None,
        description="Execution tier that processed this optimization: 'internal', 'sampling', or 'passthrough'.",
    )
    model_used: str = Field(description="Model ID used for optimization.")
    models_by_phase: dict[str, str] = Field(
        default_factory=dict,
        description="Per-phase model IDs: {analyze: '...', optimize: '...', score: '...'}.",
    )
    scoring_mode: str = Field(
        description="Scoring method: 'independent', 'hybrid', 'heuristic', or 'skipped'.",
    )
    duration_ms: int = Field(description="Total pipeline execution time (milliseconds).")
    status: str = Field(description="Pipeline status: 'completed', 'failed', or 'interrupted'.")
    context_sources: dict[str, bool] = Field(
        description="Map of context sources and whether they were enabled.",
    )
    tokens_total: int = Field(default=0, description="Total tokens used across all phases.")
    tokens_by_phase: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage per phase (analyze, optimize, score, suggest).",
    )
    warnings: list[str] = Field(
        default_factory=list, description="Warnings from pipeline execution.",
    )
    suggestions: list[dict[str, str]] = Field(
        default_factory=list,
        description="Improvement suggestions, each with 'text' and 'source' keys.",
    )
    repo_full_name: str | None = Field(
        default=None, description="GitHub repo in 'owner/repo' format (if provided).",
    )
    codebase_context_snapshot: str | None = Field(
        default=None, description="Stored codebase context snapshot.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Timestamp of optimization creation (UTC).",
    )
    intent_label: str | None = Field(
        default=None, description="3-6 word intent label.",
    )
    domain: str | None = Field(
        default=None,
        description="Domain from known domain nodes (e.g., 'backend', 'database'). "
        "Use 'primary: qualifier' format for cross-cutting concerns.",
    )

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
