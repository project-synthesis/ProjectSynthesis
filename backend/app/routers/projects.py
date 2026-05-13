"""Project management endpoints (ADR-005).

- ``GET /api/projects`` — list all project nodes (for the Navigator dropdown).
- ``POST /api/projects/migrate`` — bulk-move ``Optimization`` rows between
  projects.  Thin wrapper over ``project_service.migrate_optimizations()``.
  Emits ``taxonomy_changed`` so the frontend re-fetches scoped views.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.dependencies.write_queue import get_write_queue
from app.models import Optimization, PromptCluster
from app.services.event_bus import event_bus
from app.services.project_service import migrate_optimizations
from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectInfo(BaseModel):
    """Summary for the project-selector dropdown."""

    id: str
    label: str
    member_count: int = 0


@router.get("", response_model=list[ProjectInfo])
async def list_projects(
    db: AsyncSession = Depends(get_db),
) -> list[ProjectInfo]:
    """List all project nodes for the Navigator dropdown (F2).

    Sorts Legacy first (it's always present), then by label alphabetically.
    ``member_count`` aggregates Optimization rows attributed to each project.
    """
    projects = (await db.execute(
        select(PromptCluster)
        .where(PromptCluster.state == "project")
        .order_by(PromptCluster.label.asc())
    )).scalars().all()

    counts_rows = (await db.execute(
        select(Optimization.project_id, func.count(Optimization.id))
        .where(Optimization.project_id.isnot(None))
        .group_by(Optimization.project_id)
    )).all()
    counts: dict[str, int] = {pid: int(n) for pid, n in counts_rows if pid}

    def _is_legacy(p: PromptCluster) -> int:
        return 0 if (p.label or "").strip().lower() == "legacy" else 1

    out = [
        ProjectInfo(
            id=p.id,
            label=p.label or "unnamed",
            member_count=counts.get(p.id, 0),
        )
        for p in sorted(projects, key=lambda p: (_is_legacy(p), (p.label or "").lower()))
    ]
    return out


class MigrateRequest(BaseModel):
    """Body of ``POST /api/projects/migrate``."""

    from_project_id: str = Field(..., min_length=1)
    to_project_id: str = Field(..., min_length=1)
    since: datetime | None = None
    repo_full_name_is_null: bool = False
    dry_run: bool = False


class MigrateResponse(BaseModel):
    """Response body from ``POST /api/projects/migrate``."""

    migrated: int
    dry_run: bool


@router.post(
    "/migrate",
    response_model=MigrateResponse,
    dependencies=[Depends(RateLimit(lambda: "10/minute"))],
)
async def post_migrate_projects(
    body: MigrateRequest,
    db: AsyncSession = Depends(get_db),
    write_queue: WriteQueue = Depends(get_write_queue),
) -> MigrateResponse:
    """Bulk-migrate optimizations between projects (ADR-005 B3).

    Always explicit — no automatic routing, the user is the decider.

    v0.4.13 cycle 8: the migration UPDATE + commit routes through
    ``write_queue.submit()`` under ``operation_label='projects_migrate'``.
    Dry-run still uses the read session — no commit.
    """
    # v0.4.22 soak-gate Day 1 fix: previously this endpoint called
    # migrate_optimizations(db, ...) with the request-bound read session
    # which issued ``UPDATE optimizations SET project_id=...`` on the read
    # engine. Under the v0.4.22 RAISE flip this would trigger
    # ``WriteOnReadEngineError`` autoflush on the next read. The
    # empty-commit closure submitted afterward committed an empty write
    # transaction and did NOT actually move the rows.
    #
    # Dry-run path stays on the read session (no writes). Non-dry path
    # routes the full UPDATE through ``write_queue.submit()`` on the
    # write-engine session.
    try:
        if body.dry_run:
            count = await migrate_optimizations(
                db,
                from_project_id=body.from_project_id,
                to_project_id=body.to_project_id,
                since=body.since,
                repo_full_name_is_null=body.repo_full_name_is_null,
                dry_run=True,
            )
        else:
            async def _persist_migrate(write_db: AsyncSession) -> int:
                migrated = await migrate_optimizations(
                    write_db,
                    from_project_id=body.from_project_id,
                    to_project_id=body.to_project_id,
                    since=body.since,
                    repo_full_name_is_null=body.repo_full_name_is_null,
                    dry_run=False,
                )
                await write_db.commit()
                return int(migrated)

            count = await write_queue.submit(
                _persist_migrate,
                operation_label="projects_migrate",
            )
    except ValueError as exc:
        # Invalid destination project id — 400, not 500.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not body.dry_run and count > 0:
        # Notify the tree to re-fetch scoped views for both projects.
        try:
            event_bus.publish("taxonomy_changed", {
                "trigger": "project_migration",
                "from_project_id": body.from_project_id,
                "to_project_id": body.to_project_id,
                "count": int(count),
            })
        except Exception as exc:
            logger.debug("taxonomy_changed publish failed: %s", exc)

    return MigrateResponse(migrated=int(count), dry_run=body.dry_run)
