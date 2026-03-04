import warnings
warnings.filterwarnings("ignore", message="Field name.*shadows an attribute")

from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class OptimizeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="The raw prompt to optimize")
    project: Optional[str] = None
    tags: Optional[list[str]] = None
    title: Optional[str] = None
    strategy: Optional[str] = None
    repo_full_name: Optional[str] = None
    repo_branch: Optional[str] = None


class PatchOptimizationRequest(BaseModel):
    title: Optional[str] = None
    tags: Optional[list[str]] = None
    version: Optional[str] = None
    project: Optional[str] = None


class RetryRequest(BaseModel):
    strategy: Optional[str] = None


class ValidationScores(BaseModel):
    clarity_score: int = 0
    specificity_score: int = 0
    structure_score: int = 0
    faithfulness_score: int = 0
    conciseness_score: int = 0
    overall_score: int = 0


class OptimizationResponse(BaseModel):
    id: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    raw_prompt: str
    optimized_prompt: Optional[str] = None
    task_type: Optional[str] = None
    complexity: Optional[str] = None
    weaknesses: Optional[list[str]] = None
    strengths: Optional[list[str]] = None
    changes_made: Optional[list[str]] = None
    primary_framework: Optional[str] = None
    framework_applied: Optional[str] = None
    optimization_notes: Optional[str] = None
    strategy_rationale: Optional[str] = None
    clarity_score: Optional[int] = None
    specificity_score: Optional[int] = None
    structure_score: Optional[int] = None
    faithfulness_score: Optional[int] = None
    conciseness_score: Optional[int] = None
    overall_score: Optional[int] = None
    is_improvement: Optional[bool] = None
    verdict: Optional[str] = None
    issues: Optional[list[str]] = None
    duration_ms: Optional[int] = None
    provider_used: Optional[str] = None
    model_explore: Optional[str] = None
    model_analyze: Optional[str] = None
    model_strategy: Optional[str] = None
    model_optimize: Optional[str] = None
    model_validate: Optional[str] = None
    status: str = "completed"
    error_message: Optional[str] = None
    project: Optional[str] = None
    tags: Optional[list[str]] = None
    title: Optional[str] = None
    version: Optional[str] = None
    retry_of: Optional[str] = None
    linked_repo_full_name: Optional[str] = None
    linked_repo_branch: Optional[str] = None
    codebase_context_snapshot: Optional[str] = None

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class HistoryStatsResponse(BaseModel):
    total_optimizations: int = 0
    average_score: Optional[float] = None
    task_type_breakdown: dict[str, int] = {}
    framework_breakdown: dict[str, int] = {}
    provider_breakdown: dict[str, int] = {}
    model_usage: dict[str, int] = {}
    codebase_aware_count: int = 0
    improvement_rate: Optional[float] = None
