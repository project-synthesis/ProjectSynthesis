"""Pydantic schemas for the unified run substrate (Foundation P3, v0.4.18)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RunRequest(BaseModel):
    """Mode-agnostic input to RunOrchestrator.run()."""

    mode: Literal["topic_probe", "seed_agent"]
    payload: dict


class RunSummary(BaseModel):
    """Compact view for list endpoints."""

    id: str
    mode: Literal["topic_probe", "seed_agent"]
    status: Literal["running", "completed", "failed", "partial"]
    started_at: datetime
    completed_at: datetime | None
    project_id: str | None
    repo_full_name: str | None
    topic: str | None
    intent_hint: str | None
    prompts_generated: int


class RunResult(BaseModel):
    """Full RunRow detail view returned by /api/runs/{run_id} and equivalents."""

    id: str
    mode: Literal["topic_probe", "seed_agent"]
    status: Literal["running", "completed", "failed", "partial"]
    started_at: datetime
    completed_at: datetime | None
    error: str | None
    project_id: str | None
    repo_full_name: str | None
    topic: str | None
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
