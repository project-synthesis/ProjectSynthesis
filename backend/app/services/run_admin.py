"""Run admin operations (delete, rename, bulk-delete) routed through WriteQueue.

Canonical pattern per backend/app/services/write_queue.py:223 +
backend/app/services/gc.py:92 + backend/app/services/seed_agent_promoter.py:302:
- closures are `async def _do_X(write_db: AsyncSession) -> T`
- closure must `await write_db.commit()` before returning
- submit form: `await write_queue.submit(_do_X, operation_label="...")` (kwarg-only)

The process-wide ``WriteQueue`` singleton is retrieved via
``app.tools._shared.get_write_queue()`` (the same accessor used by
``sampling_pipeline.py:941`` + ``mcp_server.py:1597``).

v0.4.32 — spec §3.4.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RunRow
from app.tools._shared import get_write_queue


async def delete_run(run_id: str) -> bool:
    """Hard-delete a RunRow by id. Returns True if found+deleted, False if 404.

    Cascade safety: ValidationSuite.source_run_id has ondelete=SET NULL
    (models.py:705-712); the suite is preserved with source_run_id=NULL.
    """
    async def _do_delete(write_db: AsyncSession) -> bool:
        row = await write_db.get(RunRow, run_id)
        if row is None:
            return False
        await write_db.delete(row)
        await write_db.commit()
        return True

    write_queue = get_write_queue()
    return await write_queue.submit(_do_delete, operation_label="runs.delete")


async def rename_run(run_id: str, display_name: str | None) -> RunRow | None:
    """Set RunRow.display_name. Empty string and None both clear it.

    Returns the refreshed RunRow on success, None if 404.
    """
    async def _do_rename(write_db: AsyncSession) -> RunRow | None:
        row = await write_db.get(RunRow, run_id)
        if row is None:
            return None
        row.display_name = display_name if display_name else None
        await write_db.commit()
        await write_db.refresh(row)
        return row

    write_queue = get_write_queue()
    return await write_queue.submit(_do_rename, operation_label="runs.rename")


async def bulk_delete_runs(ids: list[str]) -> dict[str, list[str]]:
    """Delete multiple RunRows in one transaction.

    Returns {"deleted": [...found ids], "not_found": [...missing ids]}.
    All-or-none semantics: a single COMMIT after deleting all found rows.
    """
    async def _do_bulk_delete(write_db: AsyncSession) -> dict[str, list[str]]:
        result = await write_db.execute(
            select(RunRow).where(RunRow.id.in_(ids))
        )
        rows = list(result.scalars().all())
        found = {r.id for r in rows}
        for r in rows:
            await write_db.delete(r)
        await write_db.commit()
        return {
            "deleted": list(found),
            "not_found": [i for i in ids if i not in found],
        }

    write_queue = get_write_queue()
    return await write_queue.submit(_do_bulk_delete, operation_label="runs.bulk_delete")
