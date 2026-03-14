"""Pydantic models for MCP tool inputs and outputs.

These models provide:
- Input validation with Field() constraints for rich inputSchema generation
- Structured output via outputSchema + structuredContent in tool responses
- Type-safe contracts between MCP tools and consumers

Reuses existing models from feedback.py and pipeline_outputs.py where applicable.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Input Models ─────────────────────────────────────────────────────────────
#
# Used for tools with 3+ parameters.  Each model maps 1:1 to a tool function.
# ConfigDict(str_strip_whitespace=True, extra="forbid") prevents typo params.


class OptimizeInput(BaseModel):
    """Input for the synthesis_optimize tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=100_000,
        description="The raw prompt text to optimize (required).",
    )
    strategy: str | None = Field(
        default=None,
        description=(
            "Framework override — one of: chain-of-thought, constraint-injection, "
            "context-enrichment, CO-STAR, few-shot-scaffolding, persona-assignment, "
            "RISEN, role-task-format, step-by-step, structured-output. "
            "Omit to let the pipeline auto-select."
        ),
    )
    repo_full_name: str | None = Field(
        default=None,
        description="GitHub repo (owner/repo) for codebase-aware optimization.",
    )
    repo_branch: str | None = Field(
        default=None,
        description="Branch to explore (defaults to 'main' when repo_full_name is set).",
    )
    github_token: str | None = Field(
        default=None,
        description=(
            "GitHub token for repo exploration. "
            "Omit to use platform bot credentials (installation token)."
        ),
    )
    file_contexts: list[dict[str, str]] | None = Field(
        default=None,
        description='File content objects: [{"name": "file.py", "content": "..."}].',
    )
    instructions: list[str] | None = Field(
        default=None,
        description=(
            "Output constraint strings (e.g. 'always use bullet points'). "
            "These take absolute priority in the optimized prompt."
        ),
    )
    url_contexts: list[str] | None = Field(
        default=None,
        description="URLs to fetch and inject as reference material.",
    )
    project: str | None = Field(
        default=None,
        max_length=200,
        description="Project label for grouping optimizations in history.",
    )
    title: str | None = Field(
        default=None,
        max_length=500,
        description="Human-readable title for this optimization run.",
    )


class RetryInput(BaseModel):
    """Input for the synthesis_retry tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    optimization_id: str = Field(
        ...,
        min_length=1,
        description="UUID of the optimization to retry. Use synthesis_list_optimizations to find valid IDs.",
    )
    strategy: str | None = Field(
        default=None,
        description="Framework override for this retry run. Omit to auto-select.",
    )
    github_token: str | None = Field(
        default=None,
        description="GitHub token if the original had a linked repo. Omit to use platform bot credentials.",
    )
    file_contexts: list[dict[str, str]] | None = Field(
        default=None,
        description='File content objects: [{"name": "file.py", "content": "..."}].',
    )
    instructions: list[str] | None = Field(
        default=None,
        description="Output constraint strings for this retry run.",
    )
    url_contexts: list[str] | None = Field(
        default=None,
        description="URLs to fetch and inject as reference material.",
    )
    user_id: str | None = Field(
        default=None,
        description="Owner filter. Omit for unscoped access (single-user/localhost mode).",
    )


class ListOptimizationsInput(BaseModel):
    """Input for the synthesis_list_optimizations tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str | None = Field(default=None, description="Filter by project label (exact match).")
    task_type: str | None = Field(
        default=None,
        description="Filter by task classification (e.g. 'coding', 'writing', 'analysis').",
    )
    min_score: float | None = Field(
        default=None,
        ge=1.0,
        le=10.0,
        description="Only return optimizations with overall_score >= this value (1.0-10.0).",
    )
    search: str | None = Field(default=None, description="Text search across raw_prompt and title fields.")
    limit: int = Field(default=20, ge=1, le=100, description="Maximum results per page (default 20, max 100).")
    offset: int = Field(default=0, ge=0, description="Number of records to skip for pagination.")
    sort: str = Field(
        default="created_at",
        description=(
            "Sort column — one of: created_at, overall_score, task_type, updated_at, "
            "duration_ms, primary_framework, status, refinement_turns, branch_count."
        ),
    )
    order: str = Field(default="desc", description="Sort direction — 'asc' or 'desc'.")
    user_id: str | None = Field(
        default=None,
        description="Owner filter. Omit for unscoped access (single-user/localhost mode).",
    )

    @field_validator("order")
    @classmethod
    def validate_order(cls, v: str) -> str:
        if v not in ("asc", "desc"):
            raise ValueError("order must be 'asc' or 'desc'")
        return v


class SearchInput(BaseModel):
    """Input for the synthesis_search_optimizations tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Search string to match against prompt and title text.",
    )
    limit: int = Field(default=10, ge=1, le=100, description="Maximum results per page (default 10, max 100).")
    offset: int = Field(default=0, ge=0, description="Number of records to skip for pagination.")
    user_id: str | None = Field(
        default=None,
        description="Owner filter. Omit for unscoped access.",
    )


class GetByProjectInput(BaseModel):
    """Input for the synthesis_get_by_project tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Project label to filter by (exact match, case-sensitive).",
    )
    include_prompts: bool = Field(
        default=True,
        description="Include raw_prompt and optimized_prompt text. Set False for compact summary.",
    )
    limit: int = Field(default=50, ge=1, le=200, description="Maximum results to return (default 50).")
    user_id: str | None = Field(
        default=None,
        description="Owner filter. Omit for unscoped access.",
    )


class TagInput(BaseModel):
    """Input for the synthesis_tag_optimization tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    optimization_id: str = Field(..., min_length=1, description="UUID of the optimization to update.")
    add_tags: list[str] | None = Field(default=None, description="Tags to add (duplicates are ignored).")
    remove_tags: list[str] | None = Field(default=None, description="Tags to remove (missing tags silently ignored).")
    project: str | None = Field(default=None, description="New project label. Pass empty string to clear.")
    title: str | None = Field(default=None, description="New title. Pass empty string to clear.")
    expected_version: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Expected row_version for optimistic locking. "
            "If provided and mismatched, the update is rejected."
        ),
    )
    user_id: str | None = Field(default=None, description="Owner filter. Omit for unscoped access.")


class BatchDeleteInput(BaseModel):
    """Input for the synthesis_batch_delete tool."""

    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "UUIDs of optimizations to delete (1-50 items). "
            "Use synthesis_list_optimizations to discover valid IDs."
        ),
    )
    user_id: str | None = Field(
        default=None,
        description="Owner filter. When set, all records must belong to this user.",
    )


class GitHubReadFileInput(BaseModel):
    """Input for the synthesis_github_read_file tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    full_name: str = Field(
        ...,
        min_length=3,
        description="Repository in 'owner/repo' format (e.g. 'anthropics/anthropic-sdk-python').",
    )
    path: str = Field(
        ...,
        min_length=1,
        description="File path within the repository (e.g. 'src/main.py', 'README.md').",
    )
    token: str = Field(
        default="",
        description="GitHub token. Leave empty to use platform bot credentials.",
    )
    branch: str | None = Field(
        default=None,
        description="Branch, tag, or commit SHA. Defaults to the repo's default branch.",
    )


class GitHubSearchCodeInput(BaseModel):
    """Input for the synthesis_github_search_code tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    full_name: str = Field(
        ...,
        min_length=3,
        description="Repository in 'owner/repo' format.",
    )
    pattern: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Text pattern or keyword to search for.",
    )
    token: str = Field(
        default="",
        description="GitHub token. Leave empty to use platform bot credentials.",
    )
    extension: str | None = Field(
        default=None,
        description="Restrict search to files with this extension (e.g. 'py', 'ts', 'md').",
    )


class SubmitFeedbackInput(BaseModel):
    """Input for the synthesis_submit_feedback tool."""

    model_config = ConfigDict(extra="forbid")

    optimization_id: str = Field(..., min_length=1, description="UUID of the optimization.")
    rating: Literal[-1, 0, 1] = Field(
        ...,
        description="Feedback rating: -1 (negative), 0 (neutral), 1 (positive).",
    )
    dimension_overrides: dict[str, int] | None = Field(
        default=None,
        description=(
            "Per-dimension score overrides (1-10), e.g. "
            '{"clarity_score": 8, "specificity_score": 7}.'
        ),
    )
    corrected_issues: list[str] | None = Field(
        default=None,
        max_length=50,
        description=(
            "Issue IDs the user observed (e.g. 'lost_key_terms', 'too_verbose'). "
            "See CORRECTABLE_ISSUES for valid IDs."
        ),
    )
    comment: str | None = Field(
        default=None,
        max_length=2000,
        description="Free-text feedback comment.",
    )


# ── Output Models ────────────────────────────────────────────────────────────
#
# Returned by tools for structured output (outputSchema + structuredContent).
# Fields are Optional where the ORM column is nullable.


class StageDuration(BaseModel):
    """Timing info for a single pipeline stage."""

    duration_ms: int = 0
    token_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_creation: int = 0
    is_estimated: bool = False


class OptimizationRecord(BaseModel):
    """Full optimization record as returned by to_dict().

    Covers all columns on the Optimization ORM model.
    JSON-stored columns are deserialized to their native types.
    """

    # Allow "model_*" fields (ORM column names) and extra fields (future-proof).
    model_config = ConfigDict(extra="allow", protected_namespaces=())

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_lists(cls, data: Any) -> Any:
        """Convert explicit None to [] for list fields.

        The ORM to_dict() returns None for NULL JSON columns, but Pydantic
        does not apply the field default when an explicit None is passed.
        """
        if isinstance(data, dict):
            _LIST_FIELDS = {
                "weaknesses", "strengths", "changes_made",
                "secondary_frameworks", "issues", "tags",
                "retry_history", "per_instruction_compliance",
            }
            for field_name in _LIST_FIELDS:
                if field_name in data and data[field_name] is None:
                    data[field_name] = []
        return data

    id: str
    created_at: str | None = None
    updated_at: str | None = None
    raw_prompt: str
    optimized_prompt: str | None = None
    task_type: str | None = None
    complexity: str | None = None
    weaknesses: list[str] = []
    strengths: list[str] = []
    changes_made: list[str] = []
    analysis_quality: str | None = None
    primary_framework: str | None = None
    secondary_frameworks: list[str] = []
    approach_notes: str | None = None
    framework_applied: str | None = None
    optimization_notes: str | None = None
    strategy_rationale: str | None = None
    strategy_source: str | None = None
    clarity_score: int | None = None
    specificity_score: int | None = None
    structure_score: int | None = None
    faithfulness_score: int | None = None
    conciseness_score: int | None = None
    overall_score: float | None = None
    is_improvement: bool | None = None
    verdict: str | None = None
    issues: list[str] = []
    validation_quality: str | None = None
    duration_ms: int | None = None
    stage_durations: dict[str, Any] | None = None
    provider_used: str | None = None
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_cache_read_tokens: int | None = None
    total_cache_creation_tokens: int | None = None
    estimated_cost_usd: float | None = None
    usage_is_estimated: bool | None = None
    model_explore: str | None = None
    model_analyze: str | None = None
    model_strategy: str | None = None
    model_optimize: str | None = None
    model_validate: str | None = None
    status: str = "completed"
    error_message: str | None = None
    user_id: str | None = None
    deleted_at: str | None = None
    project: str | None = None
    tags: list[str] = []
    title: str | None = None
    version: str | None = None
    retry_of: str | None = None
    row_version: int = 0
    linked_repo_full_name: str | None = None
    linked_repo_branch: str | None = None
    codebase_context_snapshot: dict[str, Any] | None = None
    retry_history: list[dict[str, Any]] = []
    per_instruction_compliance: list[dict[str, Any]] = []
    session_id: str | None = None
    refinement_turns: int | None = 0
    active_branch_id: str | None = None
    branch_count: int | None = 0
    adaptation_snapshot: dict[str, Any] | None = None


class PipelineResult(BaseModel):
    """Result of an optimize or retry pipeline run."""

    optimization_id: str
    retry_of: str | None = None
    analysis: dict[str, Any] | None = None
    strategy: dict[str, Any] | None = None
    optimization: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    retry_diagnostics: dict[str, Any] | None = None
    retry_best_selected: dict[str, Any] | None = None
    branch_created: dict[str, Any] | None = None


T = TypeVar("T")


class PaginationEnvelope(BaseModel, Generic[T]):
    """Standard pagination envelope for list endpoints."""

    total: int
    count: int
    offset: int
    items: list[Any]  # Generic T not serializable in outputSchema; use Any
    has_more: bool
    next_offset: int | None = None


class StatsResult(BaseModel):
    """Aggregate statistics across optimization history."""

    total_optimizations: int = 0
    average_score: float | None = None
    task_type_breakdown: dict[str, int] = {}
    framework_breakdown: dict[str, int] = {}
    provider_breakdown: dict[str, int] = {}
    model_usage: dict[str, int] = {}
    codebase_aware_count: int = 0
    improvement_rate: float | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float | None = None


class DeleteResult(BaseModel):
    """Result of a single delete operation."""

    deleted: bool = True
    id: str


class BatchDeleteResult(BaseModel):
    """Result of a batch delete operation."""

    deleted_count: int
    ids: list[str]


class RestoreResult(BaseModel):
    """Result of a restore operation."""

    restored: bool = True
    id: str


class GitHubRepoItem(BaseModel):
    """A single GitHub repository in listing results."""

    full_name: str
    default_branch: str = "main"
    language: str | None = None
    private: bool = False


class GitHubFileContent(BaseModel):
    """Result of reading a file from GitHub."""

    content: str
    path: str
    repo: str


class GitHubCodeMatch(BaseModel):
    """A single code search match from GitHub."""

    path: str
    name: str


class GitHubSearchResult(BaseModel):
    """Result of a GitHub code search."""

    matches: list[GitHubCodeMatch]
    total: int


class FeedbackSubmitResult(BaseModel):
    """Result of submitting feedback."""

    id: str
    created: bool


class BranchItem(BaseModel):
    """A single refinement branch."""

    id: str
    optimization_id: str
    parent_branch_id: str | None = None
    label: str
    optimized_prompt: str | None = None
    scores: dict[str, Any] | None = None
    turn_count: int = 0
    status: str = "active"
    created_at: str | None = None
    updated_at: str | None = None


class BranchesResult(BaseModel):
    """Result of listing refinement branches."""

    branches: list[BranchItem]
    total: int


class MCPError(BaseModel):
    """Structured error response from MCP tools."""

    error: str
    hint: str | None = None
    id: str | None = None
    status: int | None = None
    current_version: int | None = None
