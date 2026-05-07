"""Unified runs surface (Foundation P3, v0.4.18).

GET /api/runs — paginated list, filterable by mode/status/project_id, ordered started_at desc
GET /api/runs/{run_id} — full RunRow detail; 404 on miss
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RunRow
from app.schemas.runs import RunListResponse, RunResult, RunSummary

router = APIRouter(prefix="/api", tags=["runs"])


def _serialize_summary(row: RunRow) -> RunSummary:
    return RunSummary(
        id=row.id,
        mode=row.mode,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        started_at=row.started_at,
        completed_at=row.completed_at,
        project_id=row.project_id,
        repo_full_name=row.repo_full_name,
        topic=row.topic,
        intent_hint=row.intent_hint,
        prompts_generated=row.prompts_generated or 0,
    )


def _serialize_full(row: RunRow) -> RunResult:
    return RunResult(
        id=row.id,
        mode=row.mode,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        started_at=row.started_at,
        completed_at=row.completed_at,
        error=row.error,
        project_id=row.project_id,
        repo_full_name=row.repo_full_name,
        topic=row.topic,
        intent_hint=row.intent_hint,
        prompts_generated=row.prompts_generated or 0,
        prompt_results=row.prompt_results or [],
        aggregate=row.aggregate or {},
        taxonomy_delta=row.taxonomy_delta or {},
        final_report=row.final_report or "",
        suite_id=row.suite_id,
        topic_probe_meta=row.topic_probe_meta,
        seed_agent_meta=row.seed_agent_meta,
    )


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    mode: Literal["topic_probe", "seed_agent"] | None = Query(None),
    status: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> RunListResponse:
    base = select(RunRow)
    if mode is not None:
        base = base.where(RunRow.mode == mode)
    if status is not None:
        base = base.where(RunRow.status == status)
    if project_id is not None:
        base = base.where(RunRow.project_id == project_id)

    total_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(total_q)).scalar_one()

    page_q = base.order_by(RunRow.started_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(page_q)).scalars().all()
    items = [_serialize_summary(r) for r in rows]

    has_more = offset + len(items) < total
    next_offset = offset + len(items) if has_more else None

    return RunListResponse(
        total=int(total),
        count=len(items),
        offset=offset,
        items=items,
        has_more=has_more,
        next_offset=next_offset,
    )


@router.get("/runs/{run_id}", response_model=RunResult)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> RunResult:
    row = await db.get(RunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return _serialize_full(row)
