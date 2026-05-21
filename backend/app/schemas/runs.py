"""Pydantic schemas for the unified run substrate (Foundation P3, v0.4.18)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """Mode-agnostic input to RunOrchestrator.run()."""

    mode: Literal["topic_probe", "seed_agent", "replay_run"]
    payload: dict


class RunSummary(BaseModel):
    """Compact view for list endpoints."""

    id: str
    mode: Literal["topic_probe", "seed_agent", "replay_run"]
    status: Literal["running", "completed", "failed", "partial"]
    started_at: datetime
    completed_at: datetime | None
    project_id: str | None
    repo_full_name: str | None
    topic: str | None
    display_name: str | None = None
    intent_hint: str | None
    prompts_generated: int


class RunResult(BaseModel):
    """Full RunRow detail view returned by /api/runs/{run_id} and equivalents."""

    id: str
    mode: Literal["topic_probe", "seed_agent", "replay_run"]
    status: Literal["running", "completed", "failed", "partial"]
    started_at: datetime
    completed_at: datetime | None
    error: str | None
    project_id: str | None
    repo_full_name: str | None
    topic: str | None
    display_name: str | None = None
    intent_hint: str | None
    prompts_generated: int
    prompt_results: list[dict]
    aggregate: dict
    taxonomy_delta: dict
    final_report: str
    suite_id: str | None
    topic_probe_meta: dict | None
    seed_agent_meta: dict | None


class RunListResponse(BaseModel):
    """Paginated list envelope matching the codebase convention."""

    total: int
    count: int
    offset: int
    items: list[RunSummary]
    has_more: bool
    next_offset: int | None


class RunPatch(BaseModel):
    """PATCH /api/runs/{id} request body.

    Empty string and `None` both clear the rename (set display_name to NULL).
    Length capped at 200 chars per spec §4.2.
    """
    display_name: str | None = Field(default=None, max_length=200)


class BulkIdsRequest(BaseModel):
    """POST /api/runs/bulk-delete + bulk-export request body.

    Server-side cap of 200 ids per spec §3.4 / §4.4.
    """
    ids: list[str] = Field(..., min_length=1, max_length=200)


class BulkDeleteResponse(BaseModel):
    deleted: list[str]
    not_found: list[str]
