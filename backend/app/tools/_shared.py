"""Shared state and helpers for MCP tool handlers.

Module-level state is initialised by ``mcp_server.py``'s lifespan via the
``init_*`` / ``set_*`` helpers below.  Tool handler modules import from here
rather than referencing ``mcp_server`` globals directly.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import DATA_DIR, PROMPTS_DIR
from app.database import async_session_factory

__all__ = [
    "DATA_DIR",
    "PROMPTS_DIR",
    "async_session_factory",
    "build_scores_dict",
    "get_context_service",
    "get_domain_resolver",
    "get_routing",
    "get_signal_loader",
    "get_taxonomy_engine",
    "set_context_service",
    "set_domain_resolver",
    "set_routing",
    "set_signal_loader",
    "set_taxonomy_engine",
]

if TYPE_CHECKING:
    from app.services.context_enrichment import ContextEnrichmentService
    from app.services.routing import RoutingManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level state — set once by mcp_server.py lifespan
# ---------------------------------------------------------------------------

_routing: RoutingManager | None = None
_taxonomy_engine = None  # TaxonomyEngine | None (avoid import for startup speed)
_context_service: ContextEnrichmentService | None = None


def set_routing(routing: RoutingManager | None) -> None:
    """Set the module-level routing manager (called by lifespan)."""
    global _routing
    _routing = routing


def set_taxonomy_engine(engine) -> None:
    """Set the module-level taxonomy engine (called by lifespan)."""
    global _taxonomy_engine
    _taxonomy_engine = engine


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


_domain_resolver = None  # DomainResolver | None
_signal_loader = None    # DomainSignalLoader | None


def set_domain_resolver(resolver) -> None:
    """Set the module-level domain resolver (called by lifespan)."""
    global _domain_resolver
    _domain_resolver = resolver


def get_domain_resolver():
    """Return domain resolver or raise if not initialized."""
    if _domain_resolver is None:
        raise ValueError("DomainResolver not initialized")
    return _domain_resolver


def set_signal_loader(loader) -> None:
    """Set the module-level signal loader (called by lifespan)."""
    global _signal_loader
    _signal_loader = loader


def get_signal_loader():
    """Return signal loader (may be None if init failed)."""
    return _signal_loader


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


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


