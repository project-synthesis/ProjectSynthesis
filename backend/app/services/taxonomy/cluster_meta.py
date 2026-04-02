"""Typed accessors for PromptCluster.cluster_metadata JSON column.

The ``cluster_metadata`` column is a schemaless JSON dict used by hot, warm,
and cold paths.  This module provides:

  - ``ClusterMeta`` — a TypedDict declaring every known key with its expected
    type, so readers get autocomplete and type checkers flag mismatches.
  - ``read_meta()`` / ``write_meta()`` — safe accessors that always default
    missing keys and coerce to correct types, eliminating the scattered
    ``node.cluster_metadata or {}`` pattern across the codebase.

New keys should be added here first, then used via the helpers.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ClusterMeta(TypedDict, total=False):
    """All known keys in PromptCluster.cluster_metadata.

    ``total=False`` makes every key optional — mirrors the JSON column
    where any key may be absent on older rows.
    """

    # --- Domain metadata (written by domain creation / signal refresh) ---
    source: str                          # "seed" | "manual" | "discovered"
    signal_keywords: list[str]           # TF-IDF keywords for domain matching
    discovered_at: str | None            # ISO8601 timestamp or None
    proposed_by_snapshot: str | None      # Snapshot ID that proposed creation
    signal_member_count_at_generation: int  # member_count when signals were last generated
    signal_generated_at: str             # ISO8601 timestamp of last signal refresh

    # --- Warm-path lifecycle tracking ---
    split_failures: int                  # Consecutive HDBSCAN split failures (cooldown after 3)
    coherence_member_count: int          # member_count at last coherence recomputation
    pattern_member_count: int            # member_count at last meta-pattern extraction
    label_refreshed_at: str              # ISO8601 timestamp of last label refresh

    # --- Positional metadata ---
    position_source: str                 # "interpolated" when UMAP position was interpolated


# -------------------------------------------------------------------------
# Safe read / write helpers
# -------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "source": "seed",
    "signal_keywords": [],
    "discovered_at": None,
    "proposed_by_snapshot": None,
    "signal_member_count_at_generation": 0,
    "signal_generated_at": "",
    "split_failures": 0,
    "coherence_member_count": 0,
    "pattern_member_count": 0,
    "label_refreshed_at": "",
    "position_source": "",
}

# Type coercion map — ensures malformed JSON values don't cause TypeErrors
# downstream (e.g. comparing ``split_failures >= 3`` when it's a string).
_COERCE: dict[str, type] = {
    "split_failures": int,
    "coherence_member_count": int,
    "pattern_member_count": int,
    "signal_member_count_at_generation": int,
}


def read_meta(raw: dict[str, Any] | None) -> ClusterMeta:
    """Return a typed copy of cluster_metadata with defaults filled.

    Coerces integer fields to ``int`` so downstream comparisons
    (e.g. ``split_failures >= 3``) never raise ``TypeError``.

    Mutable defaults (lists) are shallow-copied to prevent aliasing.
    """
    meta: dict[str, Any] = dict(raw) if raw else {}
    for key, default in _DEFAULTS.items():
        if key not in meta:
            # Copy mutable defaults to avoid shared-reference aliasing
            meta[key] = list(default) if isinstance(default, list) else default
    # Coerce types
    for key, target_type in _COERCE.items():
        try:
            meta[key] = target_type(meta[key])
        except (ValueError, TypeError):
            meta[key] = _DEFAULTS[key]
    return meta  # type: ignore[return-value]


def write_meta(
    existing: dict[str, Any] | None,
    **updates: Any,
) -> ClusterMeta:
    """Merge updates into existing cluster_metadata, returning a new dict.

    Replaces the scattered ``{**(node.cluster_metadata or {}), key: val}``
    pattern with a single call.  The input dict is never mutated — a
    fresh copy is always returned.
    """
    meta = dict(existing) if existing else {}
    meta.update(updates)
    return meta  # type: ignore[return-value]
