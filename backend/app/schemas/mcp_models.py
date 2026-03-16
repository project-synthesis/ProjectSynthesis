"""MCP tool input/output Pydantic models."""

from pydantic import BaseModel, ConfigDict, Field


class OptimizeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(..., min_length=20, max_length=200000)
    strategy: str | None = None
    repo_full_name: str | None = None


class OptimizeOutput(BaseModel):
    optimization_id: str
    optimized_prompt: str
    task_type: str
    strategy_used: str
    changes_summary: str
    scores: dict[str, float]
    original_scores: dict[str, float]
    score_deltas: dict[str, float]
    scoring_mode: str


class PrepareInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(..., min_length=20, max_length=200000)
    strategy: str | None = None
    max_context_tokens: int = Field(128000, ge=4096)
    workspace_path: str | None = None
    repo_full_name: str | None = None


class PrepareOutput(BaseModel):
    trace_id: str
    assembled_prompt: str
    context_size_tokens: int
    strategy_requested: str


class SaveResultInput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    trace_id: str
    optimized_prompt: str
    changes_summary: str | None = None
    task_type: str | None = None
    strategy_used: str | None = None
    scores: dict[str, float] | None = None
    model: str | None = None


class SaveResultOutput(BaseModel):
    optimization_id: str
    scoring_mode: str
    bias_corrected_scores: dict[str, float]
    strategy_compliance: str
    heuristic_flags: list[str]
