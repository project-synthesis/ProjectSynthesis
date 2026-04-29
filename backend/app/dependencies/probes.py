"""ProbeService dependency factory.

Centralized so both ``routers/probes.py`` (REST) and the C6 MCP tool
(``app/tools/probe.py``) construct a ``ProbeService`` from a single,
authoritative builder without cross-router imports or duplicated wiring.

Public surface:
    * ``build_probe_service(...)`` — pure constructor, no DI required.
      Reusable from any runtime (REST, MCP, tests, scripts).
    * ``get_probe_service(request, db)`` — FastAPI ``Depends(...)`` factory
      that resolves runtime singletons from ``request.app.state`` and
      delegates construction to ``build_probe_service``.

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


def _build_repo_query(db: AsyncSession) -> Any:
    """Construct a ``RepoIndexQuery`` or return ``None`` if unavailable.

    Lazy imports keep the dependency module light and degrade gracefully
    when the embedding stack is not initialised (e.g. minimal test setups).
    """
    try:
        from app.services.embedding_service import EmbeddingService
        from app.services.repo_index_query import RepoIndexQuery

        return RepoIndexQuery(db=db, embedding_service=EmbeddingService())
    except Exception:  # noqa: BLE001 — degrade gracefully when index not available
        logger.debug("build_probe_service: RepoIndexQuery init failed", exc_info=True)
        return None


def build_probe_service(
    *,
    db: AsyncSession,
    provider: Any,
    context_service: Any,
    repo_query: Any | None = None,
    embedding_service: Any | None = None,
    session_factory: Any | None = None,
) -> ProbeService:
    """Pure constructor for ``ProbeService``.

    No DI required — every dependency is passed explicitly. Both the
    FastAPI ``get_probe_service`` factory and the MCP-runtime resolver in
    ``app/tools/probe.py::_resolve_service`` call this helper so the
    wiring stays in one place.

    ``repo_query`` defaults to ``None`` and is constructed lazily via
    ``_build_repo_query`` when the caller doesn't supply one — keeping the
    caller free of embedding-service imports unless they care.
    ``embedding_service`` likewise defaults to ``None``; the probe will
    construct one lazily inside ``_persist_and_assign`` when not supplied.
    Threading the same singleton from app.state.embedding_service avoids
    a redundant model load per probe in long-running processes.
    ``session_factory`` defaults to ``app.database.async_session_factory``
    so concurrent per-prompt persistence uses fresh sessions instead of
    serializing on the orchestrator's request-scoped session. Tests that
    don't supply one fall back to a lock-serialized path on ``db``.
    ``event_bus`` is the in-process singleton from ``app.services.event_bus``;
    accepting it as a parameter would invite tests to substitute partial
    fakes, so we resolve it here.
    """
    if repo_query is None:
        repo_query = _build_repo_query(db)

    if session_factory is None:
        try:
            from app.database import async_session_factory as _default_factory
            session_factory = _default_factory
        except Exception:
            session_factory = None

    from app.services.event_bus import event_bus

    return ProbeService(
        db=db,
        provider=provider,
        repo_query=repo_query,
        context_service=context_service,
        event_bus=event_bus,
        embedding_service=embedding_service,
        session_factory=session_factory,
    )


async def get_probe_service(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ProbeService:
    """FastAPI DI factory — wraps ``build_probe_service`` for REST runtime.

    Tests override via ``app.dependency_overrides[get_probe_service]``.
    """
    routing = getattr(request.app.state, "routing", None)
    provider = routing.state.provider if routing is not None else None
    context_service = getattr(request.app.state, "context_service", None)

    return build_probe_service(
        db=db,
        provider=provider,
        context_service=context_service,
    )
