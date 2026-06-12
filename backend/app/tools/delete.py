"""Handler for synthesis_delete MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import DeleteOptimizationOutput
from app.services.optimization_service import (
    OptimizationService,
    SuiteReferencedError,
)
from app.tools._shared import get_write_queue

logger = logging.getLogger(__name__)


async def handle_delete(
    optimization_id: str,
    force: bool = False,
) -> DeleteOptimizationOutput:
    """Delete one optimization and cascade dependents.

    Mirrors the REST ``DELETE /api/optimizations/{id}`` contract:
    translates the service's silent ``deleted=0`` on unknown id into a
    ``ValueError`` (surfaced to the MCP caller as a tool error) so that
    typos in id don't masquerade as successful no-ops.

    v0.4.37 §3.3: routes through the MCP-process WriteQueue (previously a
    legacy read-engine commit) and maps :class:`SuiteReferencedError` to a
    user-facing ValueError carrying the same structured payload as the
    REST 409 envelope. ``force=True`` overrides the guard.
    """
    try:
        write_queue = get_write_queue()
    except ValueError:
        # Test/standalone contexts without a lifespan-initialized queue —
        # OptimizationService falls back to its legacy direct-session path.
        write_queue = None

    async with async_session_factory() as db:
        probe = await db.execute(
            select(Optimization.id).where(Optimization.id == optimization_id)
        )
        if probe.scalar_one_or_none() is None:
            raise ValueError(f"Optimization not found: {optimization_id}")

        svc = OptimizationService(db, write_queue=write_queue)
        try:
            result = await svc.delete_optimizations(
                [optimization_id],
                reason="user_request",
                source="mcp",
                force=force,
            )
        except SuiteReferencedError as exc:
            raise ValueError(
                "suite_referenced: "
                + json.dumps({
                    "blocked": exc.blocked,
                    "hint": "retry with force=true or retire the suite",
                })
            ) from exc

        return DeleteOptimizationOutput(
            deleted=result.deleted,
            affected_cluster_ids=sorted(result.affected_cluster_ids),
            affected_project_ids=sorted(result.affected_project_ids),
        )
