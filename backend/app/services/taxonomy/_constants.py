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
SPLIT_MIN_MEMBERS = 25         # minimum members before a node can be split
# Raised from 6 to 25: clusters under 25 members don't have enough data
# to sustain independent weight learning (min 10 samples) after splitting
# into 2-3 children. Pattern injection quality also degrades when clusters
# are fragmented — a broad 25-member cluster with diverse meta-patterns
# serves the optimizer better than 3 narrow 8-member clusters.
# See session analysis: 26% split success rate, 29% children re-merged.
MEGA_CLUSTER_MEMBER_FLOOR = 50  # cold path mega-cluster split threshold
# Raised from 12 to 50: only truly oversized clusters warrant cold-path
# splitting. Aligns with SPLIT_MIN_MEMBERS * 2 logic.


# ---------------------------------------------------------------------------
# Multi-embedding HDBSCAN blend weights
# ---------------------------------------------------------------------------
# Raw dominates (topic signal), optimized adds output-quality signal,
# transformation adds technique-direction signal.  When a signal is missing
# (None or zero vector), its weight is redistributed proportionally.
CLUSTERING_BLEND_W_RAW = 0.65
CLUSTERING_BLEND_W_OPTIMIZED = 0.20
CLUSTERING_BLEND_W_TRANSFORM = 0.15


# ---------------------------------------------------------------------------
# Sub-domain discovery
# ---------------------------------------------------------------------------
# When a domain's total member count exceeds this AND its mean child coherence
# is below the ceiling, HDBSCAN is used to discover semantic sub-groups that
# can be promoted to sub-domain nodes.
# Split child merge protection window — must be longer than the warm path
# interval (default 300s = 5 min) to survive at least 2 warm cycles.
# Previously 30 minutes, which was barely enough and caused Groundhog Day
# re-merges when timing was unlucky.
SPLIT_MERGE_PROTECTION_MINUTES = 60  # 1 hour

# Maximum times the same member set (by content hash) can fail split before
# permanent cooldown.  Prevents Groundhog Day loops where the same ~25-39
# member pool is repeatedly split and rolled back/merged.
SPLIT_CONTENT_HASH_MAX_RETRIES = 2

SUB_DOMAIN_MIN_MEMBERS = 20         # domain must have ≥20 total members
SUB_DOMAIN_COHERENCE_CEILING = 0.50 # mean child coherence must be below this
SUB_DOMAIN_MIN_GROUP_MEMBERS = 5    # each HDBSCAN group needs ≥5 members
SUB_DOMAIN_HDBSCAN_MIN_CLUSTER = 5  # HDBSCAN min_cluster_size parameter


# ---------------------------------------------------------------------------
# Spectral split algorithm
# ---------------------------------------------------------------------------
SPECTRAL_K_RANGE = (2, 3, 4)            # k values to try
SPECTRAL_SILHOUETTE_GATE = 0.15         # minimum rescaled silhouette to accept
SPECTRAL_MIN_GROUP_SIZE = 3             # minimum members per sub-cluster

# ---------------------------------------------------------------------------
# Candidate lifecycle
# ---------------------------------------------------------------------------
CANDIDATE_COHERENCE_FLOOR = 0.30        # minimum coherence for promotion

# ---------------------------------------------------------------------------
# Cluster dissolution
# ---------------------------------------------------------------------------
# Small incoherent clusters that can't be split (below SPLIT_MIN_MEMBERS)
# are dissolved: members reassigned to nearest active cluster, cluster archived.
DISSOLVE_COHERENCE_CEILING = 0.30       # dissolve if coherence below this
DISSOLVE_MAX_MEMBERS = 5                # only dissolve clusters with <= N members
DISSOLVE_MIN_AGE_HOURS = 2              # cluster must be at least N hours old


def _utcnow() -> datetime:
    """Naive UTC timestamp — matches SQLAlchemy DateTime() round-trip on SQLite.

    SQLAlchemy's ``DateTime()`` (without ``timezone=True``) strips tzinfo on
    storage and returns naive datetimes on read.  Using naive UTC ensures
    in-memory comparisons never hit ``TypeError: can't compare offset-naive
    and offset-aware datetimes``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
