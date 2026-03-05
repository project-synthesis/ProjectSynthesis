import warnings
warnings.filterwarnings("ignore", message="Field name.*shadows an attribute")

from pydantic import BaseModel, Field
from typing import Optional


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


class HistoryStatsResponse(BaseModel):
    total_optimizations: int = 0
    average_score: Optional[float] = None
    task_type_breakdown: dict[str, int] = {}
    framework_breakdown: dict[str, int] = {}
    provider_breakdown: dict[str, int] = {}
    model_usage: dict[str, int] = {}
    codebase_aware_count: int = 0
    improvement_rate: Optional[float] = None
