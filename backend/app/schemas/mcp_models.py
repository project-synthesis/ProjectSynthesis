"""MCP tool input/output Pydantic models.

These models are exposed to MCP clients via ``structured_output=True`` on tool
definitions.  Every field MUST include a ``Field(description=...)`` so that the
JSON Schema sent during ``tools/list`` gives IDE language models complete
context on how to format parameters and interpret results.
"""

from pydantic import BaseModel, ConfigDict, Field


class OptimizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(
        ..., min_length=20, max_length=200000,
        description="The raw prompt text to optimize (20–200k chars).",
    )
    strategy: str | None = Field(
        default=None,
        description="Optimization strategy name (e.g. 'auto', 'chain-of-thought', "
        "'few-shot', 'meta-prompting', 'role-playing', 'structured-output'). "
        "Defaults to user preference or 'auto'.",
    )
    repo_full_name: str | None = Field(
        default=None,
        description="GitHub repo in 'owner/repo' format for codebase-aware optimization.",
    )


class OptimizeOutput(BaseModel):
    """Union output for all 5 synthesis_optimize execution paths.

    Optional fields cover the superset across internal, sampling, and
    passthrough modes.
    """

    status: str = Field(
        default="completed",
        description="Pipeline status: 'completed' or 'pending_external'.",
    )
    pipeline_mode: str = Field(
        default="internal",
        description="Execution path used: 'internal', 'sampling', or 'passthrough'.",
    )
    strategy_used: str = Field(
        default="auto",
        description="Optimization strategy that was applied.",
    )
    optimization_id: str | None = Field(
        default=None,
        description="Unique optimization record ID for DB persistence.",
    )
    optimized_prompt: str | None = Field(
        default=None,
        description="The optimized prompt text (null for passthrough/pending).",
    )
    task_type: str | None = Field(
        default=None,
        description="Task classification: 'coding', 'writing', 'analysis', "
        "'creative', 'data', 'system', or 'general'.",
    )
    changes_summary: str | None = Field(
        default=None,
        description="Brief summary of changes made during optimization.",
    )
    scores: dict[str, float] | None = Field(
        default=None,
        description="Dimension scores for the optimized prompt: clarity, "
        "specificity, structure, faithfulness, conciseness (each 0-10).",
    )
    original_scores: dict[str, float] | None = Field(
        default=None,
        description="Baseline dimension scores for the original prompt.",
    )
    score_deltas: dict[str, float] | None = Field(
        default=None,
        description="Per-dimension score change (optimized minus original).",
    )
    scoring_mode: str | None = Field(
        default=None,
        description="Scoring method: 'independent', 'hybrid', 'heuristic', "
        "'hybrid_passthrough', or 'skipped'.",
    )
    suggestions: list[dict[str, str]] = Field(
        default_factory=list,
        description="Follow-up improvement suggestions, each with 'text' and 'source' keys.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings or issues encountered during optimization.",
    )
    model_used: str | None = Field(
        default=None,
        description="Model ID that performed the optimization (e.g. 'claude-sonnet-4-6').",
    )
    intent_label: str | None = Field(
        default=None,
        description="3-6 word intent label extracted by the analyzer.",
    )
    domain: str | None = Field(
        default=None,
        description="Domain category: 'backend', 'frontend', 'database', "
        "'devops', 'security', 'fullstack', or 'general'.",
    )
    trace_id: str | None = Field(
        default=None,
        description="Trace ID for passthrough correlation with synthesis_save_result.",
    )
    assembled_prompt: str | None = Field(
        default=None,
        description="Full assembled prompt template (passthrough mode only).",
    )
    instructions: str | None = Field(
        default=None,
        description="Optimization instructions included in assembled prompt (passthrough mode only).",
    )


class AnalyzeOutput(BaseModel):
    """Output for synthesis_analyze."""

    optimization_id: str = Field(
        description="Unique optimization record ID.",
    )
    task_type: str = Field(
        description="Task classification: 'coding', 'writing', 'analysis', "
        "'creative', 'data', 'system', or 'general'.",
    )
    weaknesses: list[str] = Field(
        description="Identified weaknesses in the prompt.",
    )
    strengths: list[str] = Field(
        description="Identified strengths in the prompt.",
    )
    selected_strategy: str = Field(
        description="Recommended optimization strategy.",
    )
    strategy_rationale: str = Field(
        description="Explanation for why this strategy is recommended.",
    )
    confidence: float = Field(
        description="Confidence score (0.0-1.0) in the analysis.",
    )
    baseline_scores: dict[str, float] = Field(
        description="Baseline quality scores for the original prompt "
        "(clarity, specificity, structure, faithfulness, conciseness).",
    )
    overall_score: float = Field(
        description="Mean of all dimension scores (0.0-10.0).",
    )
    duration_ms: int = Field(
        description="Time taken to analyze (milliseconds).",
    )
    next_steps: list[str] = Field(
        description="Suggested next actions based on analysis results.",
    )
    optimization_ready: dict[str, str] = Field(
        description="Pre-computed parameters to pass to synthesis_optimize "
        "(keys: prompt, strategy).",
    )
    intent_label: str = Field(
        default="general",
        description="3-6 word intent label extracted by the analyzer.",
    )
    domain: str = Field(
        default="general",
        description="Domain category: 'backend', 'frontend', 'database', "
        "'devops', 'security', 'fullstack', or 'general'.",
    )


class PrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(
        ..., min_length=20, max_length=200000,
        description="The raw prompt text to prepare for external optimization.",
    )
    strategy: str | None = Field(
        default=None,
        description="Optimization strategy name. Defaults to user preference or 'auto'.",
    )
    max_context_tokens: int = Field(
        default=128000, ge=4096,
        description="Maximum context window budget in tokens. "
        "Assembled prompt is truncated to fit.",
    )
    workspace_path: str | None = Field(
        default=None,
        description="Absolute path to the workspace root for context injection.",
    )
    repo_full_name: str | None = Field(
        default=None,
        description="GitHub repo in 'owner/repo' format for codebase-aware optimization.",
    )


class PrepareOutput(BaseModel):
    trace_id: str = Field(
        description="Trace ID for correlating with synthesis_save_result.",
    )
    assembled_prompt: str = Field(
        description="The full assembled prompt for external LLM consumption.",
    )
    context_size_tokens: int = Field(
        description="Estimated token count of the assembled prompt.",
    )
    strategy_requested: str = Field(
        description="The strategy name that was requested.",
    )


class SaveResultInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trace_id: str = Field(
        description="Trace ID from synthesis_prepare_optimization to link this result.",
    )
    optimized_prompt: str = Field(
        description="The optimized prompt text produced by the external LLM.",
    )
    changes_summary: str | None = Field(
        default=None,
        description="Brief summary of changes made during optimization.",
    )
    task_type: str | None = Field(
        default=None,
        description="Task classification: 'coding', 'writing', 'analysis', "
        "'creative', 'data', 'system', or 'general'.",
    )
    strategy_used: str | None = Field(
        default=None,
        description="Strategy name used. Normalized to known strategies if verbose.",
    )
    scores: dict[str, float] | None = Field(
        default=None,
        description="Self-rated scores dict with keys: clarity, specificity, "
        "structure, faithfulness, conciseness (each 0-10 float).",
    )
    model: str | None = Field(
        default=None,
        description="Model ID that produced the optimization (e.g. 'claude-sonnet-4-6').",
    )


class SaveResultOutput(BaseModel):
    optimization_id: str = Field(
        description="ID of the persisted Optimization record.",
    )
    scoring_mode: str = Field(
        description="Scoring method applied: 'hybrid_passthrough', 'heuristic', or 'skipped'.",
    )
    scores: dict[str, float] = Field(
        default_factory=dict,
        description="Final computed scores for the optimized prompt.",
    )
    original_scores: dict[str, float] | None = Field(
        default=None,
        description="Scores of the original prompt (if available).",
    )
    score_deltas: dict[str, float] | None = Field(
        default=None,
        description="Per-dimension score change (optimized minus original).",
    )
    overall_score: float | None = Field(
        default=None,
        description="Mean of all dimension scores (0.0-10.0).",
    )
    strategy_compliance: str = Field(
        description="Compliance assessment: 'full', 'partial', 'minimal', or 'none'.",
    )
    heuristic_flags: list[str] = Field(
        description="Quality issues flagged by heuristic analysis.",
    )
