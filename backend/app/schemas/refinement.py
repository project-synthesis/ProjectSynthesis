"""Pydantic schemas for refinement and branching endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RefineRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    protect_dimensions: list[str] | None = None


class ForkRequest(BaseModel):
    parent_branch_id: str
    message: str = Field(..., min_length=1, max_length=2000)
    label: str | None = None


class SelectRequest(BaseModel):
    branch_id: str
    reason: str | None = None


class BranchResponse(BaseModel):
    id: str
    optimization_id: str
    parent_branch_id: str | None
    label: str
    optimized_prompt: str | None
    scores: dict | None
    turn_count: int
    status: str
    created_at: str
    updated_at: str | None


class BranchListResponse(BaseModel):
    branches: list[BranchResponse]
    total: int


class BranchCompareResponse(BaseModel):
    branch_a: BranchResponse
    branch_b: BranchResponse
    score_deltas: dict[str, float]
