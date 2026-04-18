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
SPLIT_MIN_MEMBERS = 12         # minimum members before a node can be split
# Lowered from 25 to 12: the previous threshold created a dead zone where
# clusters with 6-24 members and coherence between 0.25 and 0.50 could
# not be dissolved (too big), force-split (coherence too high), or
# normal-split (too few members).  A 12-member cluster splitting into
# 2 children of 6 is viable for spectral clustering
# (SPECTRAL_MIN_GROUP_SIZE=3, min k=2).  Weight learning quality at
# 6 members is lower, but cluster health (removing incoherent groupings)
# takes priority over weight learning optimality.
MEGA_CLUSTER_MEMBER_FLOOR = 50  # cold path mega-cluster split threshold
# Only truly oversized clusters warrant cold-path splitting.
# Well above SPLIT_MIN_MEMBERS (12) — warm-path handles normal splits.

MAX_PATTERNS_PER_CLUSTER: int = 15  # prune cap after merge-based Phase 4 refresh


# ---------------------------------------------------------------------------
# Multi-embedding HDBSCAN blend weights
# ---------------------------------------------------------------------------
# Raw dominates (topic signal), optimized adds output-quality signal,
# transformation adds technique-direction signal.  When a signal is missing
# (None or zero vector), its weight is redistributed proportionally.
CLUSTERING_BLEND_W_RAW = 0.55
CLUSTERING_BLEND_W_OPTIMIZED = 0.20
CLUSTERING_BLEND_W_TRANSFORM = 0.15
CLUSTERING_BLEND_W_QUALIFIER = 0.10


# ---------------------------------------------------------------------------
# Structural state exclusion
# ---------------------------------------------------------------------------
# States that represent structural/organizational nodes, not active clusters.
# Used in all taxonomy queries that operate on "active" clusters:
#   PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES)
# Centralized here so adding a new structural state is a one-line change.
EXCLUDED_STRUCTURAL_STATES: frozenset[str] = frozenset({
    "domain",    # domain grouping nodes
    "archived",  # tombstoned clusters
    "project",   # project hierarchy nodes (ADR-005)
})


# ---------------------------------------------------------------------------
# Cross-project assignment (ADR-005 Section 2)
# ---------------------------------------------------------------------------
# Boost applied to the adaptive merge threshold when searching across projects.
# A prompt in Project B must be this much MORE similar to join a cluster in
# Project A than it would need to be within its own project.
CROSS_PROJECT_THRESHOLD_BOOST: float = 0.15


# ---------------------------------------------------------------------------
# Global Pattern Tier (ADR-005 Section 6)
# ---------------------------------------------------------------------------
GLOBAL_PATTERN_RELEVANCE_BOOST: float = 1.3
GLOBAL_PATTERN_CAP: int = 500
GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS: int = 5
GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS: int = 1
GLOBAL_PATTERN_PROMOTION_MIN_SCORE: float = 6.0
GLOBAL_PATTERN_DEMOTION_SCORE: float = 5.0
GLOBAL_PATTERN_DEDUP_COSINE: float = 0.90
GLOBAL_PATTERN_CYCLE_INTERVAL: int = 10
GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES: int = 30


# ---------------------------------------------------------------------------
# Split protection
# ---------------------------------------------------------------------------
# Merge protection window — must be longer than the warm path interval
# (default 300s = 5 min) to survive at least 2 warm cycles.  Previously
# 30 minutes, which was barely enough and caused Groundhog Day re-merges
# when timing was unlucky.
SPLIT_MERGE_PROTECTION_MINUTES = 60  # 1 hour

# Maximum times the same member set (by content hash) can fail split before
# permanent cooldown.  Prevents Groundhog Day loops where the same member
# pool is repeatedly split and rolled back/merged.
SPLIT_CONTENT_HASH_MAX_RETRIES = 2


# ---------------------------------------------------------------------------
# HNSW backend threshold (ADR-005 Phase 3B)
# ---------------------------------------------------------------------------
# When cluster count >= this on rebuild(), EmbeddingIndex switches from
# numpy matmul to HNSW (hnswlib) for O(log N) search.
HNSW_CLUSTER_THRESHOLD: int = 1000


# ---------------------------------------------------------------------------
# Sub-domain discovery (signal-driven)
# ---------------------------------------------------------------------------
# Sub-domains are discovered from domain_raw qualifiers ("backend: auth")
# and intent_label keyword fallback — not from embedding proximity.
# Adaptive threshold: max(LOW, HIGH - SCALE_RATE * total_members).
# MIN_KEYWORD_HITS = 1 because the domain is already confirmed by
# classification — a single keyword hit is strong enough to select
# the specific qualifier within that domain.
SUB_DOMAIN_QUALIFIER_MIN_MEMBERS = 5       # minimum optimizations with this qualifier
SUB_DOMAIN_QUALIFIER_CONSISTENCY_HIGH = 0.60  # threshold for small domains
SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW = 0.40   # threshold floor for large domains
SUB_DOMAIN_QUALIFIER_SCALE_RATE = 0.004       # per-member threshold reduction
SUB_DOMAIN_QUALIFIER_MIN_KEYWORD_HITS = 1       # minimum keyword hits to accept a qualifier

# ---------------------------------------------------------------------------
# Readiness history — persisted snapshots for trajectory visualization.
# Stored as JSONL under data/readiness_history/snapshots-YYYY-MM-DD.jsonl.
# Sampled every warm-cycle Phase 5 (~5min) per top-level domain.
# Volume: 30 domains × 288/day = ~8.6k rows/day → ~260k rows in a 30d window.
READINESS_HISTORY_DIR_NAME: str = "readiness_history"
READINESS_HISTORY_RETENTION_DAYS: int = 30
# Windows ≥ this threshold get hourly bucket means to keep payload < 2KB.
READINESS_HISTORY_BUCKET_THRESHOLD_DAYS: int = 7
# Minimum distinct clusters a qualifier must span before it can promote to a
# sub-domain.  A single-cluster qualifier adds hierarchy depth without
# navigational value — the sub-domain would be a 1:1 wrapper.  Consumed by
# both engine._propose_sub_domains and the readiness emergence primitive.
SUB_DOMAIN_MIN_CLUSTER_BREADTH = 2

# Sub-domain archival — much shorter than the 90-day top-level domain threshold.
# Sub-domains are created and reparented within a single Phase 5 call, so one
# that is empty by the next warm cycle is genuinely orphaned.  1 hour is enough
# to survive a few warm cycles while preventing accumulation.
SUB_DOMAIN_ARCHIVAL_IDLE_HOURS: int = 1

# Sub-domain dissolution — graceful re-grouping when qualifier consistency degrades.
# Hysteresis: creation threshold is 0.40-0.60, dissolution at 0.25 prevents flip-flop.
SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR: float = 0.25
SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS: int = 6  # don't dissolve freshly created sub-domains

# ---------------------------------------------------------------------------
# Domain dissolution — graceful re-grouping when domains lose relevance.
# Domains have stricter guards than sub-domains: higher age gate, member
# ceiling, and sub-domain anchor rule.  Aligns with ADR-006 vision that
# seed domains are bootstrapping data, not permanent fixtures.
DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR: float = 0.15   # well below 60% creation threshold (45pt hysteresis)
DOMAIN_DISSOLUTION_MIN_AGE_HOURS: int = 48            # domains earn permanence through time
DOMAIN_DISSOLUTION_MEMBER_CEILING: int = 5             # large domains don't dissolve on consistency alone


# ---------------------------------------------------------------------------
# Maintenance phase cadence
# ---------------------------------------------------------------------------
# Maintenance phases (discover, archive, audit) run independently of the
# dirty-cluster gate on this cadence.  Every Nth warm cycle, maintenance
# runs even when no clusters were modified.  Retries after transient
# failure are immediate (next cycle), bypassing this cadence.
MAINTENANCE_CYCLE_INTERVAL: int = 6  # ~30 min at default 5-min warm interval


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

# Forced split for large incoherent clusters that exceed dissolution member cap
# but have very low coherence. These clusters are too big to dissolve and too
# small for the normal split path (SPLIT_MIN_MEMBERS=12) — the forced split
# catches the gap (6-11 members with coherence < 0.35).
# Raised from 0.25 to 0.35 to close the dead zone between dissolution
# (coherence < 0.30, members <= 5) and normal split (members >= 12).
FORCED_SPLIT_COHERENCE_FLOOR = 0.35     # force split if coherence below this
FORCED_SPLIT_MIN_MEMBERS = 6            # minimum members for forced spectral split
# Set to 6 (not 8) to close the gap with DISSOLVE_MAX_MEMBERS=5.
# Spectral clustering works at 6 members (SPECTRAL_MIN_GROUP_SIZE=3, min k=2).

# ---------------------------------------------------------------------------
# Intent label coherence — supplementary split signal
# ---------------------------------------------------------------------------
# When a cluster's pairwise intent-label Jaccard overlap is below this
# threshold, it strengthens the case for splitting (alongside embedding
# coherence). Never used as a sole split trigger — only a secondary signal.
LABEL_COHERENCE_SPLIT_SIGNAL = 0.15

# ---------------------------------------------------------------------------
# Groundhog Day loop prevention
# ---------------------------------------------------------------------------
# Embedding coherence above which clusters are exempt from splitting.
# Addresses the dead zone (0.38-0.50) where clusters are too incoherent
# to be stable but too coherent to split into viable children.
SPLIT_COHERENCE_EXEMPT = 0.38

# Max pairwise centroid cosine between split children before the split
# is aborted. Children above this threshold will merge back within 1-2
# warm cycles, making the split futile.
SPLIT_SIBLING_SIMILARITY_CEILING = 0.75

# Domain-level ring buffer of content hashes from failed/futile splits.
# Survives across cluster ID changes (the identity anchor).
DOMAIN_SPLIT_HASH_MAX_ENTRIES = 20
DOMAIN_SPLIT_HASH_TTL_HOURS = 6

# Grace period after merge_protected_until expires. If a cluster merges
# back within this window of its protection expiry, it's a futile split.
MERGE_BACK_GRACE_MINUTES = 30


# ---------------------------------------------------------------------------
# Readiness crossing notifications — proactive tier transitions.
# A crossing fires only after the new tier has been observed at least
# READINESS_CROSSING_HYSTERESIS_CYCLES consecutive times (suppresses 1-cycle
# spikes around the warming/ready boundary).  Same-axis re-fires are
# suppressed for READINESS_CROSSING_COOLDOWN_SECONDS per domain.
READINESS_CROSSING_HYSTERESIS_CYCLES: int = 2
READINESS_CROSSING_COOLDOWN_SECONDS: float = 600.0  # 10 minutes


def _utcnow() -> datetime:
    """Naive UTC timestamp — matches SQLAlchemy DateTime() round-trip on SQLite.

    SQLAlchemy's ``DateTime()`` (without ``timezone=True``) strips tzinfo on
    storage and returns naive datetimes on read.  Using naive UTC ensures
    in-memory comparisons never hit ``TypeError: can't compare offset-naive
    and offset-aware datetimes``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
