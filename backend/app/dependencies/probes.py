"""ProbeService dependency factory.

Centralized so both ``routers/probes.py`` (REST) and the C6 MCP tool can
construct a per-request ``ProbeService`` without cross-router imports.

Tests override via ``app.dependency_overrides[get_probe_service]`` (or the
re-exported alias on the router module — both point to the same callable).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.probe_service import ProbeService

logger = logging.getLogger(__name__)


async def get_probe_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ProbeService:
    """Construct a per-request ``ProbeService`` from app.state singletons.

    Tests override via ``app.dependency_overrides[get_probe_service]``.
    """
    routing = getattr(request.app.state, "routing", None)
    provider = routing.state.provider if routing is not None else None
    context_service = getattr(request.app.state, "context_service", None)

    repo_query: Any = None
    try:
        from app.services.embedding_service import EmbeddingService
        from app.services.repo_index_query import RepoIndexQuery

        repo_query = RepoIndexQuery(db=db, embedding_service=EmbeddingService())
    except Exception:  # noqa: BLE001 — degrade gracefully when index not available
        logger.debug("get_probe_service: RepoIndexQuery init failed", exc_info=True)

    from app.services.event_bus import event_bus

    return ProbeService(
        db=db,
        provider=provider,
        repo_query=repo_query,
        context_service=context_service,
        event_bus=event_bus,
    )
