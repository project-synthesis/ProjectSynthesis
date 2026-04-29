"""synthesis_probe MCP tool (Topic Probe Tier 1, v0.4.12).

15th MCP tool. Thin wrapper around ``ProbeService.run()`` that collects
SSE events into a final ``ProbeRunResult``, streaming intermediate
progress to the MCP ``Context`` via ``ctx.report_progress()`` when
sampling-capable.

The ``_service`` parameter is for test injection; production resolves
``ProbeService`` via the canonical factory ``app.dependencies.probes.
get_probe_service`` (post-C5 REFACTOR location). The MCP-process path
constructs the service from ``_shared`` singletons + ``async_session_factory``
because no FastAPI ``Request`` is available outside the REST router.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from typing import Literal

from app.schemas.probes import (
    ProbeCompletedEvent,
    ProbeError,
    ProbeProgressEvent,
    ProbeRunRequest,
    ProbeRunResult,
)
from app.services.probe_service import ProbeService

# Imported for canonical-factory awareness (post-C5 REFACTOR location).
# The MCP path cannot invoke it directly because it depends on a FastAPI
# Request; production construction happens inline in ``_resolve_service``
# below using the MCP-process singletons threaded through ``_shared``.
from app.dependencies.probes import get_probe_service  # noqa: F401


async def _resolve_service() -> ProbeService:
    """Construct a ``ProbeService`` from MCP-process singletons.

    Mirrors ``app.dependencies.probes.get_probe_service`` for the MCP
    runtime where no ``Request`` / ``Depends`` machinery is available.
    """
    from app.tools._shared import (
        async_session_factory,
        get_context_service,
        get_routing,
    )

    routing = get_routing()
    provider = routing.state.provider if routing is not None else None
    try:
        context_service = get_context_service()
    except ValueError:
        context_service = None

    db = async_session_factory()

    repo_query = None
    try:
        from app.services.embedding_service import EmbeddingService
        from app.services.repo_index_query import RepoIndexQuery

        repo_query = RepoIndexQuery(db=db, embedding_service=EmbeddingService())
    except Exception:  # noqa: BLE001 — degrade gracefully when index not available
        repo_query = None

    from app.services.event_bus import event_bus

    return ProbeService(
        db=db,
        provider=provider,
        repo_query=repo_query,
        context_service=context_service,
        event_bus=event_bus,
    )


async def handle_probe(
    topic: str,
    scope: str | None = None,
    intent_hint: Literal["audit", "refactor", "explore", "regression-test"] | None = None,
    n_prompts: int | None = None,
    ctx=None,  # FastMCP Context | None
    _service: ProbeService | None = None,
) -> ProbeRunResult:
    """MCP tool handler for ``synthesis_probe`` — see spec §4.7.

    Iterates ``ProbeService.run()``; on each ``ProbeProgressEvent`` reports
    progress to the MCP ``Context`` (best-effort, errors swallowed); on
    ``ProbeCompletedEvent`` resolves the final ``ProbeRunResult`` via
    ``ProbeService.fetch_result()``.
    """
    # Use ``model_construct`` to bypass validation: the MCP boundary
    # (``@mcp.tool`` Field constraints) already enforces ``topic`` length
    # and ``n_prompts`` range. The service's domain logic raises
    # canonical ``ProbeError`` codes for semantic errors that schema
    # validation can't express (e.g. ``link_repo_first``).
    request = ProbeRunRequest.model_construct(
        topic=topic,
        scope=scope,
        intent_hint=intent_hint,
        n_prompts=n_prompts,
        repo_full_name=None,
    )
    if _service is None:
        _service = await _resolve_service()

    final_result: ProbeRunResult | None = None
    async for event in _service.run(request):
        if isinstance(event, ProbeProgressEvent) and ctx is not None:
            try:
                await ctx.report_progress(
                    event.current,
                    event.total,
                    f"{event.intent_label or '?'}: {event.overall_score or 0:.2f}",
                )
            except Exception:
                pass
        elif isinstance(event, ProbeCompletedEvent):
            final_result = await _service.fetch_result(event.probe_id)

    if final_result is None:
        raise ProbeError("probe_completed_without_result")
    return final_result
