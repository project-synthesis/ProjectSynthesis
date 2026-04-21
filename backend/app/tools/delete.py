"""Handler for synthesis_delete MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import DeleteOptimizationOutput
from app.services.optimization_service import OptimizationService

logger = logging.getLogger(__name__)


async def handle_delete(optimization_id: str) -> DeleteOptimizationOutput:
    """Delete one optimization and cascade dependents.

    Mirrors the REST ``DELETE /api/optimizations/{id}`` contract:
    translates the service's silent ``deleted=0`` on unknown id into a
    ``ValueError`` (surfaced to the MCP caller as a tool error) so that
    typos in id don't masquerade as successful no-ops.
    """
    async with async_session_factory() as db:
        probe = await db.execute(
            select(Optimization.id).where(Optimization.id == optimization_id)
        )
        if probe.scalar_one_or_none() is None:
            raise ValueError(f"Optimization not found: {optimization_id}")

        svc = OptimizationService(db)
        result = await svc.delete_optimizations(
            [optimization_id], reason="user_request",
        )

        return DeleteOptimizationOutput(
            deleted=result.deleted,
            affected_cluster_ids=sorted(result.affected_cluster_ids),
            affected_project_ids=sorted(result.affected_project_ids),
        )
