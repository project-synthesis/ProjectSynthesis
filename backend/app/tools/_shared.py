"""Shared module-level singletons for tool handlers + FastAPI routers.

Originally scoped to MCP tool handlers only (set by ``mcp_server.py``'s
lifespan). Several FastAPI backend routers — ``github_auth``,
``providers``, ``strategies`` — also import the ``get_*`` accessors here
because they have no ``Request.app.state`` access at call time (lazy
imports inside ``submit_batch`` closures, async helper functions, etc.).
Both processes therefore initialise these singletons in their respective
lifespans:

* MCP server (``mcp_server.py``) — sets them all on its lifespan startup.
* FastAPI backend (``main.py``) — sets ``_write_queue`` alongside
  ``register_process_write_queue`` (and other singletons as needed).

Each Python process has its own module-level state, so the two lifespans
do not interfere. A backend router calling ``get_write_queue()`` retrieves
the FastAPI process's queue; an MCP tool handler calling the same
function retrieves the MCP process's queue. They are bound to the same
underlying SQLite writer engine (each process has one writer slot).

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import OperationalError, ProgrammingError

from app.config import DATA_DIR, PROMPTS_DIR
from app.database import async_session_factory

__all__ = [
    "DATA_DIR",
    "PROMPTS_DIR",
    "_fetch_historical_stats",
    "async_session_factory",
    "auto_resolve_repo",
    "build_scores_dict",
    "get_context_service",
    "get_domain_resolver",
    "get_routing",
    "get_run_orchestrator",
    "get_signal_loader",
    "get_taxonomy_engine",
    "get_write_queue",
    "set_context_service",
    "set_domain_resolver",
    "set_routing",
    "set_run_orchestrator",
    "set_signal_loader",
    "set_taxonomy_engine",
    "set_write_queue",
]

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.services.context_enrichment import ContextEnrichmentService
    from app.services.routing import RoutingManager
    from app.services.run_orchestrator import RunOrchestrator
    from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — set once by mcp_server.py lifespan
# ---------------------------------------------------------------------------

_routing: RoutingManager | None = None
_taxonomy_engine = None  # TaxonomyEngine | None (avoid import for startup speed)
_context_service: ContextEnrichmentService | None = None
_write_queue: "WriteQueue | None" = None
_run_orchestrator: "RunOrchestrator | None" = None


def set_routing(routing: RoutingManager | None) -> None:
    """Set the module-level routing manager (called by lifespan)."""
    global _routing
    _routing = routing


def set_write_queue(queue: "WriteQueue | None") -> None:
    """Set the module-level WriteQueue (called by lifespan).

    Mirrors ``set_routing`` / ``set_taxonomy_engine`` — the MCP process
    owns its own ``WriteQueue`` instance bound to the writer engine. MCP
    tool handlers retrieve it via :func:`get_write_queue`.
    """
    global _write_queue
    _write_queue = queue


def get_write_queue() -> "WriteQueue":
    """Return the WriteQueue or raise if not initialized.

    Used by MCP tool handlers (cycle 9+) to submit DB writes through the
    process-wide single-writer queue.
    """
    if _write_queue is None:
        raise ValueError("WriteQueue not initialized")
    return _write_queue


def set_taxonomy_engine(engine) -> None:
    """Set the module-level taxonomy engine (called by lifespan)."""
    global _taxonomy_engine
    _taxonomy_engine = engine


def set_run_orchestrator(orchestrator: "RunOrchestrator | None") -> None:
    """Set the module-level RunOrchestrator (called by lifespan).

    Foundation P3 (Cycle 13, v0.4.18): MCP tool handlers (synthesis_probe,
    synthesis_seed) dispatch through this orchestrator instead of the
    legacy ProbeService / inline orchestration path. The MCP process
    constructs its own orchestrator (separate from the FastAPI backend's
    ``app.state.run_orchestrator``) bound to the MCP-process WriteQueue.
    """
    global _run_orchestrator
    _run_orchestrator = orchestrator


def get_run_orchestrator() -> "RunOrchestrator":
    """Return the RunOrchestrator or raise if not initialized.

    Used by MCP tool handlers in Cycle 13+ to dispatch ``synthesis_probe``
    / ``synthesis_seed`` requests through the unified run substrate.
    """
    if _run_orchestrator is None:
        raise ValueError("RunOrchestrator not initialized")
    return _run_orchestrator


def get_routing() -> RoutingManager:
    """Return routing manager or raise if not initialized."""
    if _routing is None:
        raise ValueError("Routing service not initialized")
    return _routing


def get_taxonomy_engine():
    """Return the taxonomy engine (may be None if init failed)."""
    return _taxonomy_engine


def set_context_service(svc: ContextEnrichmentService | None) -> None:
    """Set the module-level context enrichment service (called by lifespan)."""
    global _context_service
    _context_service = svc


def get_context_service() -> ContextEnrichmentService:
    """Return the context enrichment service or raise if not initialized."""
    if _context_service is None:
        raise ValueError("Context enrichment service not initialized")
    return _context_service


def get_domain_resolver():
    """Return domain resolver or raise if not initialized.

    Delegates to the service module's process-level singleton.
    """
    from app.services.domain_resolver import get_domain_resolver as _get
    return _get()


def set_domain_resolver(resolver) -> None:
    """Set the domain resolver (delegates to service module singleton)."""
    from app.services.domain_resolver import set_domain_resolver as _set
    _set(resolver)


def get_signal_loader():
    """Return signal loader (delegates to service module singleton)."""
    from app.services.domain_signal_loader import get_signal_loader as _get
    return _get()


def set_signal_loader(loader) -> None:
    """Set the signal loader (delegates to service module singleton)."""
    from app.services.domain_signal_loader import set_signal_loader as _set
    _set(loader)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def auto_resolve_repo(repo_full_name: str | None) -> str | None:
    """Auto-resolve repo_full_name from the active linked repo if not provided.

    Delegates to :func:`app.services.project_service.resolve_effective_repo` so
    MCP tool callers and REST ``/api/optimize`` share one AA1 resolution path.
    MCP callers have no session cookie, so ``session_id`` is passed as ``None``;
    an explicit ``repo_full_name`` flows through unchanged.

    Returns the resolved repo name, or ``None`` if no linked repo exists.
    """
    try:
        from app.services.project_service import resolve_effective_repo

        async with async_session_factory() as db:
            resolved = await resolve_effective_repo(
                db,
                explicit_repo=repo_full_name,
                session_id=None,
            )
            if resolved and resolved != repo_full_name:
                logger.debug("MCP auto-resolved repo: %s", resolved)
            return resolved
    except Exception:
        logger.debug("auto_resolve_repo failed — preserving caller value", exc_info=True)
        return repo_full_name  # Non-fatal — preserve explicit caller value


def build_scores_dict(obj: object) -> dict[str, float] | None:
    """Build a {clarity, specificity, structure, faithfulness, conciseness} dict.

    Works with any object that has ``score_clarity`` through ``score_conciseness``
    attributes (e.g. ``Optimization``, ``RefinementTurn``).  Returns ``None`` if
    ``score_clarity`` is missing or ``None``.
    """
    clarity = getattr(obj, "score_clarity", None)
    if clarity is None:
        return None
    return {
        "clarity": clarity,
        "specificity": getattr(obj, "score_specificity", None) or 0.0,
        "structure": getattr(obj, "score_structure", None) or 0.0,
        "faithfulness": getattr(obj, "score_faithfulness", None) or 0.0,
        "conciseness": getattr(obj, "score_conciseness", None) or 0.0,
    }


async def _fetch_historical_stats(
    db: "AsyncSession",
    *,
    exclude_scoring_modes: list[str],
) -> dict[str, Any] | None:
    """Fetch score-distribution stats via OptimizationService for blended scoring.

    Foundation P4 — used by:
    - Cycle 1 (save_result, exclude=['heuristic', 'hybrid_passthrough'])
    - Cycle 2 (refine, exclude=['heuristic'])
    - Cycle 3 (orchestrator pre-fetch for run_hybrid_scoring, exclude=['heuristic'])

    Returns ``None`` if the stats query fails for a legitimate "data not
    available yet" reason (empty DB, locked DB during contention, missing
    table during a migration). Callers degrade to non-blended scoring.

    Return-shape contract: `OptimizationService.get_score_distribution`
    returns `dict[str, dict[str, float | int]]` (per-dimension count/mean/
    stddev). This is `dict[str, Any]`-compatible — `score_passthrough` and
    `blend_scores` already accept it under that wider annotation.

    Exception scope is intentionally narrow — `OperationalError` and
    `ProgrammingError` cover the legitimate cases. Other exceptions
    (greenlet errors, `AttributeError`, etc.) indicate real bugs and
    must propagate so tests catch them. If your test path needs this
    helper to fail loudly on a different exception class, monkeypatch
    it to raise — do NOT widen the catch.
    """
    try:
        from app.services.optimization_service import OptimizationService
        opt_svc = OptimizationService(db)
        return await opt_svc.get_score_distribution(
            exclude_scoring_modes=exclude_scoring_modes,
        )
    except (OperationalError, ProgrammingError):
        logger.debug(
            "_fetch_historical_stats unavailable (non-fatal, returning None)",
            exc_info=True,
        )
        return None


