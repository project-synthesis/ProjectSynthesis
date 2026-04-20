"""Domain classification and sub-qualifier enrichment.

Delegates to the ``DomainSignalLoader`` singleton for keyword-based domain
scoring, then layers an organic-vocabulary sub-qualifier on top (produced
by Haiku during warm-path Phase 5 discovery and cached on the domain
node's metadata).

Extracted from ``heuristic_analyzer.py`` (Phase 3F).  Public API is
preserved via re-exports in ``heuristic_analyzer.py``.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _get_signal_loader():
    """Resolve the signal loader from the service-level singleton.

    Returns ``None`` if not yet initialized (startup race, tests that
    don't seed domain nodes).
    """
    from app.services.domain_signal_loader import get_signal_loader
    return get_signal_loader()


def set_signal_loader(loader) -> None:
    """Set the DomainSignalLoader singleton (called from lifespan).

    Kept as a re-export shim for backward compat: ``main.py`` and
    ``mcp_server.py`` import this symbol from ``heuristic_analyzer``.
    """
    from app.services.domain_signal_loader import set_signal_loader as _set
    _set(loader)


def get_signal_loader():
    """Return the DomainSignalLoader singleton (or None if unconfigured)."""
    return _get_signal_loader()


def classify_domain(scored: dict[str, float]) -> str:
    """Classify domain by delegating to the DomainSignalLoader.

    Returns ``"general"`` when no signal loader is configured (e.g. during
    early startup or in tests that don't seed domain nodes).
    """
    loader = _get_signal_loader()
    if loader is None:
        return "general"
    return loader.classify(scored)


def enrich_domain_qualifier(domain: str, prompt_lower: str) -> str:
    """Enrich a plain domain label with a sub-qualifier from organic vocabulary.

    Reads qualifier vocabulary from ``DomainSignalLoader.get_qualifiers()``,
    which is populated organically by Haiku from cluster labels during the
    warm path's Phase 5 discovery.

    If *domain* already contains a qualifier (has ``:``) or the loader has
    no vocabulary for this domain, returns the original string unchanged.

    Returns:
        Enriched domain string (e.g. ``"saas: growth"``) or original.
    """
    if ":" in domain:
        return domain

    primary = domain.strip().lower()

    try:
        loader = get_signal_loader()
        if not loader:
            return domain
        qualifiers = loader.get_qualifiers(primary)
    except Exception:
        return domain

    if not qualifiers:
        return domain

    from app.services.domain_signal_loader import DomainSignalLoader

    best_qualifier, best_hits = DomainSignalLoader.find_best_qualifier(
        prompt_lower, qualifiers,
    )

    from app.services.taxonomy._constants import SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS

    if best_qualifier and best_hits >= SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS:
        logger.debug(
            "qualifier_enrichment: domain=%s qualifier=%s hits=%d",
            primary, best_qualifier, best_hits,
        )
        return f"{primary}: {best_qualifier}"
    return domain


__all__ = [
    "classify_domain",
    "enrich_domain_qualifier",
    "get_signal_loader",
    "set_signal_loader",
]
