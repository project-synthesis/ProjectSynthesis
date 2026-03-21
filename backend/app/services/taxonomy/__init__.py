"""Evolutionary Taxonomy Engine — self-organizing hierarchical clustering.

Public API:
    TaxonomyEngine — unified orchestrator
    TaxonomyMapping — domain mapping result
    PatternMatch — pattern matching result
    QWeights — quality metric weights
    SparklineData — sparkline-ready Q_system history data
    compute_sparkline_data — transform raw Q values into sparkline data
    get_engine — process-wide singleton accessor
    set_engine — register the canonical instance (called from main.py lifespan)
"""

from __future__ import annotations

import threading
from typing import Any

from app.services.taxonomy.engine import (
    PatternMatch,
    TaxonomyEngine,
    TaxonomyMapping,
)
from app.services.taxonomy.quality import QWeights
from app.services.taxonomy.sparkline import SparklineData, compute_sparkline_data

__all__ = [
    "PatternMatch",
    "QWeights",
    "SparklineData",
    "TaxonomyEngine",
    "TaxonomyMapping",
    "compute_sparkline_data",
    "get_engine",
    "reset_engine",
    "set_engine",
]

# ---------------------------------------------------------------------------
# Process-wide singleton — ensures all callers share the same asyncio.Lock
# ---------------------------------------------------------------------------

_process_engine: TaxonomyEngine | None = None
_engine_lock = threading.Lock()


def get_engine(app: Any | None = None) -> TaxonomyEngine:
    """Return the canonical TaxonomyEngine instance.

    Resolution order:
      1. ``app.state.taxonomy_engine`` (FastAPI request path)
      2. Module-level ``_process_engine`` (set by lifespan or lazy-init)
      3. Lazy-create a fallback instance (read-only paths, tests)

    Thread-safe: lazy creation is guarded by a lock to prevent
    duplicate instances from concurrent callers.
    """
    global _process_engine
    if app is not None:
        engine = getattr(app.state, "taxonomy_engine", None)
        if engine is not None:
            return engine
    if _process_engine is not None:
        return _process_engine
    # Double-checked locking for thread-safe lazy creation
    with _engine_lock:
        if _process_engine is not None:
            return _process_engine
        from app.services.embedding_service import EmbeddingService

        _process_engine = TaxonomyEngine(embedding_service=EmbeddingService())
        return _process_engine


def set_engine(engine: TaxonomyEngine) -> None:
    """Register the canonical TaxonomyEngine (called from main.py lifespan)."""
    global _process_engine
    _process_engine = engine


def reset_engine() -> None:
    """Clear the process singleton (test teardown only)."""
    global _process_engine
    _process_engine = None
