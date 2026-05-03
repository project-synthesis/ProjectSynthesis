"""Template library + lifecycle endpoints.

v0.4.13 cycle 8: write paths route through ``write_queue.submit()`` via
``Depends(get_write_queue)`` so the fork/retire/use commits serialize
against every other backend writer through the single-writer queue.
Operation labels: ``template_fork`` / ``template_retire`` / ``template_use``.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies.rate_limit import RateLimit
from app.dependencies.write_queue import get_write_queue
from app.schemas.templates import RetireRequest, TemplateListResponse, TemplateRead
from app.services.template_service import TemplateService
from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["templates"])

_svc = TemplateService()


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates(
    project_id: str | None = Query(None),
    include_retired: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> TemplateListResponse:
    """List templates scoped to a project, with optional retired inclusion."""
    page = await _svc.list_for_project(
        project_id,
        db,
        include_retired=include_retired,
        limit=limit,
        offset=offset,
    )
    return TemplateListResponse(
        total=page.total,
        count=page.count,
        offset=page.offset,
        items=[TemplateRead.model_validate(t) for t in page.items],
        has_more=page.has_more,
        next_offset=page.next_offset,
    )


@router.get("/templates/{template_id}", response_model=TemplateRead)
async def get_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
) -> TemplateRead:
    """Fetch a single template by ID (includes retired templates)."""
    tpl = await _svc.get(template_id, db)
    if tpl is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return TemplateRead.model_validate(tpl)


@router.post(
    "/clusters/{cluster_id}/fork-template",
    response_model=TemplateRead,
    dependencies=[Depends(RateLimit(lambda: "10/minute"))],
)
async def fork_template(
    cluster_id: str,
    db: AsyncSession = Depends(get_db),
    write_queue: WriteQueue = Depends(get_write_queue),
) -> TemplateRead:
    """Fork an immutable template snapshot from a cluster's top optimization.

    Idempotent — returns the existing live template when one already exists
    for the (cluster, top_optimization) pair.

    v0.4.13 cycle 8: the fork INSERT routes through
    ``write_queue.submit()`` under ``operation_label='template_fork'``.
    """

    async def _do_fork(write_db: AsyncSession):
        tpl = await _svc.fork_from_cluster(cluster_id, write_db, auto=False)
        if tpl is None:
            return None
        await write_db.commit()
        # Return the id so we can re-read on the request session for
        # serialization (fresh attribute load).
        return tpl.id

    tpl_id = await write_queue.submit(_do_fork, operation_label="template_fork")
    if tpl_id is None:
        logger.warning("fork_template: cluster %s has no optimizations to fork", cluster_id)
        raise HTTPException(
            status_code=400,
            detail="Cluster has no optimizations to fork from",
        )
    tpl = await _svc.get(tpl_id, db)
    if tpl is None:
        # Defensive: the row should exist since we just committed it.
        raise HTTPException(
            status_code=500,
            detail="Template fork committed but not visible on read engine",
        )
    return TemplateRead.model_validate(tpl)


@router.post("/templates/{template_id}/retire", response_model=TemplateRead)
async def retire_template(
    template_id: str,
    body: RetireRequest | None = None,
    db: AsyncSession = Depends(get_db),
    write_queue: WriteQueue = Depends(get_write_queue),
) -> TemplateRead:
    """Soft-retire a live template. Returns the updated record.

    v0.4.13 cycle 8: the retire UPDATE routes through
    ``write_queue.submit()`` under ``operation_label='template_retire'``.
    """
    reason = body.reason if body else "manual"

    async def _do_retire(write_db: AsyncSession) -> bool:
        ok = await _svc.retire(template_id, write_db, reason=reason)
        if ok:
            await write_db.commit()
        return ok

    ok = await write_queue.submit(_do_retire, operation_label="template_retire")
    if not ok:
        raise HTTPException(
            status_code=404, detail="Template not found or already retired"
        )
    tpl = await _svc.get(template_id, db)
    return TemplateRead.model_validate(tpl)


@router.post(
    "/templates/{template_id}/use",
    response_model=TemplateRead,
    dependencies=[Depends(RateLimit(lambda: "30/minute"))],
)
async def use_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    write_queue: WriteQueue = Depends(get_write_queue),
) -> TemplateRead:
    """Record a template usage event and return the updated record.

    v0.4.13 cycle 8: the usage UPDATE routes through
    ``write_queue.submit()`` under ``operation_label='template_use'``.
    """
    tpl = await _svc.get(template_id, db)
    if tpl is None or tpl.retired_at is not None:
        raise HTTPException(status_code=404, detail="Template not found or retired")

    async def _do_use(write_db: AsyncSession) -> None:
        await _svc.increment_usage(template_id, write_db)
        await write_db.commit()

    await write_queue.submit(_do_use, operation_label="template_use")
    tpl = await _svc.get(template_id, db)
    return TemplateRead.model_validate(tpl)
