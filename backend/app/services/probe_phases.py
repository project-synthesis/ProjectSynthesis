"""v0.4.17 P2 — Curated-context unpackers for Phase 1 (grounding).

These helpers pull structured data out of the ``CuratedCodebaseContext``
returned by ``RepoIndexQuery.query_curated_context`` so the orchestrator
can build the ``ProbeContext`` schema. They are pure functions -- no DB
access, no network, no side effects.

This module is a leaf: it has no inter-module dependencies on the other
v0.4.17 P2 split modules (``probe_common``, ``probe_phase_5``).
"""
from __future__ import annotations

from typing import Any


def _resolve_curated_files(curated: Any) -> list[str]:
    """Return file paths from a ``CuratedCodebaseContext``-shaped object.

    Production shape: ``selected_files: list[dict]`` with ``path`` keys
    (see ``services/repo_index_query.py``). Returns ``[]`` on absent or
    falsy input.
    """
    if curated is None:
        return []
    selected = getattr(curated, "selected_files", None) or []
    out: list[str] = []
    for d in selected:
        if isinstance(d, dict):
            path = d.get("path") or d.get("file_path")
            if path:
                out.append(str(path))
    return out


def _resolve_curated_synthesis(curated: Any) -> str | None:
    """Return the cached explore-synthesis excerpt for the probe.

    The probe-specific ``explore_synthesis_excerpt`` attribute is preferred
    (set by Tier 2 grounding when the cached synthesis is layered on top of
    curated retrieval). Falls back to ``context_text`` from the production
    ``CuratedCodebaseContext`` shape.
    """
    if curated is None:
        return None
    for attr in ("explore_synthesis_excerpt", "context_text"):
        v = getattr(curated, attr, None)
        if v:
            return str(v)
    return None


def _resolve_dominant_stack(curated: Any) -> list[str]:
    """Return dominant tech stack as a list of stable string tokens.

    Tier 2 grounding will source this from ``WorkspaceIntelligence`` and
    layer it onto the curated-context object before passing it here.
    """
    if curated is None:
        return []
    stack = getattr(curated, "dominant_stack", None)
    if isinstance(stack, list):
        return [str(s) for s in stack]
    return []


__all__ = [
    "_resolve_curated_files",
    "_resolve_curated_synthesis",
    "_resolve_dominant_stack",
]
