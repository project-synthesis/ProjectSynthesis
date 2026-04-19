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
        description="Pipeline status: 'completed', 'pending_external', or 'error'.",
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
        "specificity, structure, faithfulness, conciseness (each 1.0-10.0).",
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
    models_by_phase: dict[str, str] | None = Field(
        default=None,
        description="Per-phase model IDs: {analyze: '...', optimize: '...', score: '...'}.",
    )
    intent_label: str | None = Field(
        default=None,
        description="3-6 word intent label extracted by the analyzer.",
    )
    domain: str | None = Field(
        default=None,
        description="Domain from known domain nodes (e.g., 'backend', 'frontend', "
        "'database', 'devops', 'security'). Use 'primary: qualifier' for cross-cutting.",
    )
    trace_id: str | None = Field(
        default=None,
        description="Trace ID for passthrough correlation with synthesis_save_result.",
    )
    assembled_prompt: str | None = Field(
        default=None,
        description="Full assembled prompt for your LLM to process (passthrough mode only). "
        "When present, process this with your LLM, then call synthesis_save_result "
        "with the trace_id and the optimized output.",
    )
    instructions: str | None = Field(
        default=None,
        description="Step-by-step instructions for completing the passthrough workflow "
        "(passthrough mode only). Follow these to process assembled_prompt and "
        "call synthesis_save_result.",
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
        description="Pre-computed parameters for synthesis_optimize "
        "(keys: strategy). The prompt is already in your context.",
    )
    intent_label: str = Field(
        default="general",
        description="3-6 word intent label extracted by the analyzer.",
    )
    domain: str = Field(
        default="general",
        description="Domain from known domain nodes (e.g., 'backend', 'frontend', "
        "'database', 'devops', 'security'). Use 'primary: qualifier' for cross-cutting.",
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
    was_truncated: bool = Field(
        default=False,
        description="True if the assembled prompt was truncated to fit max_context_tokens.",
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
        "structure, faithfulness, conciseness (each 1.0-10.0 float, clamped to this range).",
    )
    model: str | None = Field(
        default=None,
        description="Model ID that produced the optimization (e.g. 'claude-sonnet-4-6').",
    )
    codebase_context: str | None = Field(
        default=None,
        description="IDE-provided codebase context snapshot to store alongside the result.",
    )
    domain: str | None = Field(
        default=None,
        description="Domain from known domain nodes (e.g., 'backend', 'frontend', "
        "'database', 'devops', 'security'). Use 'primary: qualifier' for cross-cutting.",
    )
    intent_label: str | None = Field(
        default=None,
        description="Short 3-6 word intent classification label.",
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
        description="Compliance assessment: 'matched' (requested == used), "
        "'partial' (different strategy used), or 'unknown' (no baseline).",
    )
    heuristic_flags: list[str] = Field(
        description="Quality issues flagged by heuristic analysis.",
    )
    suggestions: list[dict[str, str]] = Field(
        default_factory=list,
        description="Follow-up improvement suggestions, each with 'text' and 'source' keys.",
    )


# ---------------------------------------------------------------------------
# New tool output models (MCP tool chain expansion)
# ---------------------------------------------------------------------------


class FeedbackOutput(BaseModel):
    """Output for synthesis_feedback."""

    feedback_id: str = Field(
        description="ID of the created feedback record.",
    )
    optimization_id: str = Field(
        description="ID of the rated optimization.",
    )
    rating: str = Field(
        description="Rating that was recorded: 'thumbs_up' or 'thumbs_down'.",
    )
    strategy_affinity_updated: bool = Field(
        description="Whether strategy adaptation was triggered by this feedback.",
    )


class RefineOutput(BaseModel):
    """Output for synthesis_refine."""

    optimization_id: str = Field(
        description="Parent optimization ID.",
    )
    version: int = Field(
        description="New version number in the refinement chain.",
    )
    branch_id: str = Field(
        description="Branch this refinement turn belongs to.",
    )
    refined_prompt: str = Field(
        description="The refined prompt text after improvement.",
    )
    scores: dict[str, float] | None = Field(
        default=None,
        description="Quality scores for the refined prompt "
        "(clarity, specificity, structure, faithfulness, conciseness).",
    )
    score_deltas: dict[str, float] | None = Field(
        default=None,
        description="Score changes from the previous version.",
    )
    overall_score: float | None = Field(
        default=None,
        description="Mean of all dimension scores (0.0-10.0).",
    )
    suggestions: list[dict[str, str]] = Field(
        default_factory=list,
        description="Follow-up improvement suggestions, each with 'text' and 'source' keys.",
    )
    strategy_used: str | None = Field(
        default=None,
        description="Strategy used for this refinement turn.",
    )


class HistoryItem(BaseModel):
    """Single optimization summary within HistoryOutput."""

    id: str = Field(
        description="Optimization ID.",
    )
    created_at: str | None = Field(
        default=None,
        description="ISO 8601 creation timestamp.",
    )
    task_type: str | None = Field(
        default=None,
        description="Classified task type.",
    )
    strategy_used: str | None = Field(
        default=None,
        description="Strategy used for optimization.",
    )
    overall_score: float | None = Field(
        default=None,
        description="Quality score (0.0-10.0).",
    )
    status: str = Field(
        description="Optimization status: 'completed', 'failed', 'analyzed', 'pending'.",
    )
    intent_label: str | None = Field(
        default=None,
        description="Short intent label (3-6 words).",
    )
    domain: str | None = Field(
        default=None,
        description="Domain category.",
    )
    raw_prompt_preview: str | None = Field(
        default=None,
        description="First 200 characters of the original prompt.",
    )
    optimized_prompt_preview: str | None = Field(
        default=None,
        description="First 200 characters of the optimized prompt.",
    )
    feedback_rating: str | None = Field(
        default=None,
        description="Latest feedback rating ('thumbs_up', 'thumbs_down', or null).",
    )


class HistoryOutput(BaseModel):
    """Output for synthesis_history."""

    total: int = Field(
        description="Total number of matching optimizations.",
    )
    count: int = Field(
        description="Number of items returned in this page.",
    )
    has_more: bool = Field(
        description="Whether more pages exist beyond this one.",
    )
    items: list[HistoryItem] = Field(
        description="Optimization summaries for this page.",
    )


class MetaPatternSummary(BaseModel):
    """Reusable pattern from the knowledge graph."""

    id: str = Field(
        description="Meta-pattern ID. Pass to synthesis_optimize.applied_pattern_ids "
        "to inject this pattern into optimization context.",
    )
    pattern_text: str = Field(
        description="The reusable technique or pattern text.",
    )
    source_count: int = Field(
        description="Number of optimizations this pattern was extracted from.",
    )


class MatchOutput(BaseModel):
    """Output for synthesis_match."""

    match_level: str = Field(
        description="Match quality: 'family' (strong, cosine >= 0.72), "
        "'cluster' (moderate, cosine >= 0.60), or 'none'.",
    )
    similarity: float = Field(
        description="Cosine similarity score (0.0-1.0).",
    )
    cluster_id: str | None = Field(
        default=None,
        description="Matched cluster ID.",
    )
    cluster_label: str | None = Field(
        default=None,
        description="Human-readable cluster label.",
    )
    taxonomy_breadcrumb: list[str] = Field(
        default_factory=list,
        description="Path from root to matched node in the taxonomy tree.",
    )
    meta_patterns: list[MetaPatternSummary] = Field(
        default_factory=list,
        description="Reusable patterns from this cluster. Pass their IDs to "
        "synthesis_optimize.applied_pattern_ids.",
    )
    cross_cluster_patterns: list[MetaPatternSummary] = Field(
        default_factory=list,
        description="Universal patterns from other clusters with high cross-cluster "
        "presence (global_source_count >= 3). These techniques are effective "
        "across domains.",
    )
    recommended_strategy: str | None = Field(
        default=None,
        description="Preferred strategy for this cluster based on feedback history.",
    )


class StrategyInfo(BaseModel):
    """Single strategy entry within StrategiesOutput."""

    name: str = Field(
        description="Strategy identifier to pass to synthesis_optimize.",
    )
    tagline: str = Field(
        description="One-line category or description (from YAML frontmatter).",
    )
    description: str = Field(
        description="Full description of what this strategy does.",
    )


class StrategiesOutput(BaseModel):
    """Output for synthesis_strategies."""

    strategies: list[StrategyInfo] = Field(
        description="Available optimization strategies.",
    )


class OptimizationDetailOutput(BaseModel):
    """Output for synthesis_get_optimization."""

    id: str = Field(
        description="Optimization ID.",
    )
    created_at: str | None = Field(
        default=None,
        description="ISO 8601 creation timestamp.",
    )
    raw_prompt: str = Field(
        description="Original prompt text.",
    )
    optimized_prompt: str | None = Field(
        default=None,
        description="Optimized prompt text.",
    )
    task_type: str | None = Field(
        default=None,
        description="Classified task type.",
    )
    strategy_used: str | None = Field(
        default=None,
        description="Strategy used for optimization.",
    )
    changes_summary: str | None = Field(
        default=None,
        description="Summary of changes made during optimization.",
    )
    scores: dict[str, float] | None = Field(
        default=None,
        description="Quality dimension scores (clarity, specificity, structure, "
        "faithfulness, conciseness).",
    )
    original_scores: dict[str, float] | None = Field(
        default=None,
        description="Baseline scores of the original prompt.",
    )
    score_deltas: dict[str, float] | None = Field(
        default=None,
        description="Per-dimension score changes (optimized minus original).",
    )
    overall_score: float | None = Field(
        default=None,
        description="Mean of all dimension scores (0.0-10.0).",
    )
    status: str = Field(
        description="Optimization status.",
    )
    intent_label: str | None = Field(
        default=None,
        description="3-6 word intent label.",
    )
    domain: str | None = Field(
        default=None,
        description="Domain category.",
    )
    scoring_mode: str | None = Field(
        default=None,
        description="How scores were computed: 'independent', 'hybrid', "
        "'heuristic', 'hybrid_passthrough', or 'skipped'.",
    )
    has_feedback: bool = Field(
        default=False,
        description="Whether feedback has been submitted for this optimization.",
    )
    refinement_versions: int = Field(
        default=0,
        description="Number of refinement turns completed.",
    )


class LinkedRepoHealth(BaseModel):
    """Linked-repo summary for synthesis_health.

    Surfaces the repo that ``synthesis_optimize`` / ``synthesis_match`` /
    ``synthesis_prepare_optimization`` will auto-resolve when ``repo_full_name``
    is not supplied.  Matches the repo exposed on the web UI's GitHub panel.
    """

    full_name: str = Field(
        description="GitHub repo in 'owner/repo' format.",
    )
    branch: str | None = Field(
        default=None,
        description="Active branch (or default_branch when unset).",
    )
    language: str | None = Field(
        default=None,
        description="Primary language reported by GitHub.",
    )
    index_status: str | None = Field(
        default=None,
        description="Index lifecycle: 'pending', 'fetching_tree', 'embedding', "
        "'synthesizing', 'ready', 'error', or null when no index row exists.",
    )
    index_phase: str | None = Field(
        default=None,
        description="Granular phase label for UI progress.",
    )
    files_indexed: int = Field(
        default=0,
        description="Files with embeddings written to the index.",
    )
    synthesis_ready: bool = Field(
        default=False,
        description="True when explore_synthesis is present — codebase-aware "
        "optimization will inject architectural context.",
    )


class HealthOutput(BaseModel):
    """Output for synthesis_health."""

    status: str = Field(
        description="System status: 'healthy' if a provider is available, "
        "'degraded' if passthrough-only.",
    )
    provider: str | None = Field(
        default=None,
        description="Active LLM provider name (e.g. 'claude_cli', 'anthropic_api').",
    )
    available_tiers: list[str] = Field(
        description="Reachable routing tiers: 'internal', 'sampling', 'passthrough'.",
    )
    sampling_capable: bool | None = Field(
        default=None,
        description="Whether the MCP client supports sampling (null if unknown).",
    )
    total_optimizations: int = Field(
        default=0,
        description="Total optimizations in history.",
    )
    avg_score: float | None = Field(
        default=None,
        description="Average quality score across completed optimizations.",
    )
    recent_error_rate: float | None = Field(
        default=None,
        description="Error rate in the last 24 hours (0.0-1.0).",
    )
    available_strategies: list[str] = Field(
        default_factory=list,
        description="Names of loaded optimization strategies.",
    )
    domain_count: int = Field(
        default=0,
        description="Number of active domain nodes in the taxonomy.",
    )
    domain_ceiling: int = Field(
        default=30,
        description="Maximum allowed domain nodes (DOMAIN_COUNT_CEILING=30). "
        "When domain_count >= domain_ceiling, new domain creation is suppressed.",
    )
    linked_repo: LinkedRepoHealth | None = Field(
        default=None,
        description="Currently linked GitHub repo that optimize/match/prepare "
        "will auto-resolve when called without explicit repo_full_name. Null "
        "when no repo is linked.",
    )


class ExplainResult(BaseModel):
    """Output for synthesis_explain — plain-English optimization summary."""

    summary: str = Field(
        description="1-2 sentence plain-English overview of what improved and why.",
    )
    changes: list[str] = Field(
        description="3-5 bullet points describing specific changes in non-technical language.",
    )
    strategy_used: str = Field(
        description="Strategy name with a lay-audience description of the approach.",
    )
    score_delta: float = Field(
        description="Overall score improvement (positive = better).",
    )
