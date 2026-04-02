"""Shared constants and utilities for the taxonomy engine package.

Centralized here to avoid duplication across engine.py, warm_phases.py,
cold_path.py, and lifecycle.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Warm path operational limits
# ---------------------------------------------------------------------------

DEADLOCK_BREAKER_THRESHOLD = 5  # consecutive rejected cycles before forcing
SPLIT_COHERENCE_FLOOR = 0.5    # below this coherence, node is a split candidate
SPLIT_MIN_MEMBERS = 6          # minimum members before a node can be split


def _utcnow() -> datetime:
    """Naive UTC timestamp — matches SQLAlchemy DateTime() round-trip on SQLite.

    SQLAlchemy's ``DateTime()`` (without ``timezone=True``) strips tzinfo on
    storage and returns naive datetimes on read.  Using naive UTC ensures
    in-memory comparisons never hit ``TypeError: can't compare offset-naive
    and offset-aware datetimes``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
