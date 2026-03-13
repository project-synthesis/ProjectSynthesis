"""Pydantic schemas for refinement and branching endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.services.prompt_diff import SCORE_DIMENSIONS

_VALID_DIMENSIONS = set(SCORE_DIMENSIONS)


class RefineRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    protect_dimensions: list[str] | None = None

    @model_validator(mode="after")
    def validate_protect_dimensions(self) -> "RefineRequest":
        if self.protect_dimensions:
            for dim in self.protect_dimensions:
                if dim not in _VALID_DIMENSIONS:
                    raise ValueError(f"Invalid dimension: {dim}. Valid: {sorted(_VALID_DIMENSIONS)}")
        return self


class ForkRequest(BaseModel):
    parent_branch_id: str = Field(..., min_length=1, max_length=36)
    message: str = Field(..., min_length=1, max_length=2000)
    label: str | None = Field(None, max_length=100)


class SelectRequest(BaseModel):
    branch_id: str = Field(..., min_length=1, max_length=36)
    reason: str | None = Field(None, max_length=500)


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
