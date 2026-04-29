"""synthesis_probe MCP tool (Topic Probe Tier 1, v0.4.12).

15th MCP tool. Thin wrapper around ``ProbeService.run()`` that collects
SSE events into a final ``ProbeRunResult``, streaming intermediate
progress to the MCP ``Context`` via ``ctx.report_progress()`` when
sampling-capable.

Construction unification (post-C6 REFACTOR): both this MCP-runtime path
and the FastAPI ``get_probe_service`` factory call the shared pure
constructor ``app.dependencies.probes.build_probe_service`` — no
duplicated wiring, single audit surface.

Schema validation (post-C6 REFACTOR): ``handle_probe`` constructs
``ProbeRunRequest(**kwargs)`` (validated, not ``model_construct``) so
the production contract is enforced even when the caller bypasses the
``@mcp.tool`` Field constraints (e.g. internal callers, integration
tests). The MCP-tool boundary still validates first; this is defence
in depth, not a duplicate gate.

The ``_service`` parameter is for test injection only (``_`` prefix flags
it as private); production callers leave it ``None`` and the MCP-runtime
singletons resolve via ``_resolve_service``.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from typing import Literal

from app.dependencies.probes import build_probe_service
from app.schemas.probes import (
    ProbeCompletedEvent,
    ProbeError,
    ProbeProgressEvent,
    ProbeRunRequest,
    ProbeRunResult,
)
from app.services.probe_service import ProbeService


async def _resolve_service() -> ProbeService:
    """Construct a ``ProbeService`` from MCP-process singletons.

    Resolves the routing provider + context service from the ``_shared``
    module-level state set by the MCP server's lifespan, then delegates
    actual construction to the canonical ``build_probe_service`` helper
    in ``app.dependencies.probes`` — the same builder the FastAPI
    factory uses.
    """
    from app.tools._shared import (
        async_session_factory,
        get_context_service,
        get_routing,
    )

    try:
        routing = get_routing()
        provider = routing.state.provider if routing is not None else None
    except ValueError:
        provider = None

    try:
        context_service = get_context_service()
    except ValueError:
        context_service = None

    db = async_session_factory()

    return build_probe_service(
        db=db,
        provider=provider,
        context_service=context_service,
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

    Builds a validated ``ProbeRunRequest`` (Pydantic enforces
    ``topic`` length 3-500 and ``n_prompts`` range 5-25), iterates
    ``ProbeService.run()``; on each ``ProbeProgressEvent`` reports
    progress to the MCP ``Context`` (best-effort, errors swallowed); on
    ``ProbeCompletedEvent`` resolves the final ``ProbeRunResult`` via
    ``ProbeService.fetch_result()``.

    ``ProbeError`` propagates unchanged so the FastMCP runtime can map
    canonical reason codes (``link_repo_first``, ``topic_not_found_in_repo``,
    ``generation_failed``) to client-facing remediation messages.
    """
    request = ProbeRunRequest(
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
