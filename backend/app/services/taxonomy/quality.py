"""Quality metrics and gates for the taxonomy engine.

Implements Q_system computation, constant-sum weight normalization,
adaptive thresholds, and speculative operation evaluation.

Reference: Spec Section 2.4, 2.5
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass

from app.services.pipeline_constants import DOMAIN_COHERENCE_FLOOR
from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES
from app.utils.text_cleanup import LABEL_STOP_WORDS

logger = logging.getLogger(__name__)

# Default weight targets (when DBCV is fully active)
_W_D_TARGET = 0.15
_W_C_BASE = 0.4
_W_S_BASE = 0.35
_W_V_BASE = 0.25

# Adaptive threshold scaling factor
_ALPHA = 0.15

# Cold-path non-regression tolerance.
# HDBSCAN refits are destructive and non-deterministic, so a wider
# flat epsilon (5%) is used instead of the warm-path adaptive decay.
# Tightened from 8% to 5% after observing cold-path refits accepted
# with 5-6% Q drops that degraded taxonomy quality meaningfully.
# Operations with negative q_delta within the epsilon are ACCEPTED BY DESIGN
# because stochastic algorithms need headroom.
COLD_PATH_EPSILON = 0.05


@dataclass(frozen=True)
class NodeMetrics:
    """Lightweight metrics container for Q_system computation.

    Not a DB model — populated from PromptCluster fields for quality calculations.
    """

    coherence: float
    separation: float
    state: str  # 'candidate' | 'active' | 'mature' | 'archived' | 'domain'
    # Q-contributing states: candidate, active, mature
    # Excluded from Q computation: domain, archived
    member_count: int = 1


@dataclass(frozen=True)
class QWeights:
    """Constant-sum weights for Q_system (always sum to 1.0).

    Spec Section 2.5 — DBCV weight ramps in linearly over 20
    observations after >=5 active nodes exist. The DBCV slot is
    fed by silhouette_score (rescaled to [0,1]) computed during
    batch_cluster() and stored on ClusterResult.silhouette. Cold
    path passes it directly; warm path reuses the last cold-path
    value via engine._last_silhouette.
    """

    w_c: float  # coherence
    w_s: float  # separation
    w_v: float  # coverage
    w_d: float  # DBCV

    @classmethod
    def from_ramp(cls, ramp_progress: float) -> QWeights:
        """Create weights for a given DBCV ramp progress (0.0–1.0).

        Args:
            ramp_progress: 0.0 = DBCV inactive, 1.0 = fully active.
                Clamped to [0.0, 1.0].
        """
        ramp = max(0.0, min(1.0, ramp_progress))
        w_d = _W_D_TARGET * ramp
        remaining = 1.0 - w_d
        return cls(
            w_c=_W_C_BASE * remaining,
            w_s=_W_S_BASE * remaining,
            w_v=_W_V_BASE * remaining,
            w_d=w_d,
        )


def compute_q_system(
    nodes: list[NodeMetrics],
    weights: QWeights,
    coverage: float = 1.0,
    dbcv: float = 0.0,
) -> float | None:
    """Compute composite system quality score.

    Reference: Spec Section 2.5

    Edge cases:
    - Empty or all-excluded (domain/archived only): returns None — undefined
    - Fewer than 2 active nodes: returns None (separation undefined with <2 siblings)
    - NaN/Inf: replaced with 0.0

    A5 N-guard: Q is meaningless when fewer than 2 active clusters exist.
    A single cluster has no siblings to compare against, so both coherence
    and separation degenerate to trivial perfect values (Q ≈ 1.0). That's
    a lie, not a measurement. Return None so the UI can render "—".

    Included states: candidate, active, mature, template.
    Excluded states: domain (structural containers), archived (retired).
    """
    active = [n for n in nodes if n.state not in EXCLUDED_STRUCTURAL_STATES]
    if len(active) < 2:
        return None

    # Gather finite coherence values
    coherences = [
        n.coherence for n in active if math.isfinite(n.coherence)
    ]
    separations = [
        n.separation for n in active if math.isfinite(n.separation)
    ]

    mean_c = statistics.mean(coherences) if coherences else 0.0
    mean_s = statistics.mean(separations) if separations else 1.0

    # Clamp all components to [0.0, 1.0]
    mean_c = max(0.0, min(1.0, mean_c))
    mean_s = max(0.0, min(1.0, mean_s))
    coverage = max(0.0, min(1.0, coverage))
    dbcv = max(0.0, min(1.0, dbcv))

    raw = (
        weights.w_c * mean_c
        + weights.w_s * mean_s
        + weights.w_v * coverage
        + weights.w_d * dbcv
    )

    # Defensive: self-heal if weights drift
    total_weight = weights.w_c + weights.w_s + weights.w_v + weights.w_d
    if total_weight < 1e-9:
        return 0.0
    if abs(total_weight - 1.0) > 1e-6:
        logger.warning("Q_system weight drift (sum=%.6f, expected=1.0) — normalizing", total_weight)
        raw /= total_weight

    return max(0.0, min(1.0, raw))


@dataclass(frozen=True)
class QHealthResult:
    """Member-weighted taxonomy health metric breakdown.

    Unlike Q_system (arithmetic mean across clusters), q_health weights
    each cluster's coherence and separation by its member_count, so a
    30-member cluster matters 30x more than a singleton.

    A5 N-guard: ``q_health`` is ``None`` when fewer than 2 active clusters
    exist — the metric is undefined without siblings to compare against.
    Other breakdown fields remain populated for observability.
    """

    q_health: float | None
    coherence_weighted: float
    separation_weighted: float
    coverage: float
    dbcv: float
    weights: dict[str, float]
    total_members: int
    cluster_count: int


def _degenerate_q_health(
    active: list[NodeMetrics],
    weights: QWeights,
    coverage: float,
    dbcv: float,
) -> QHealthResult:
    """Build a QHealthResult with q_health=None for degenerate N<2 cases.

    Preserves observability fields (cluster_count, total_members) so callers
    can still reason about taxonomy size even when the Q metric itself is
    undefined.
    """
    total_members = sum(max(n.member_count, 0) for n in active)
    return QHealthResult(
        q_health=None,
        coherence_weighted=0.0,
        separation_weighted=0.0,
        coverage=max(0.0, min(1.0, coverage)),
        dbcv=max(0.0, min(1.0, dbcv)),
        weights={
            "w_c": weights.w_c, "w_s": weights.w_s,
            "w_v": weights.w_v, "w_d": weights.w_d,
        },
        total_members=total_members,
        cluster_count=len(active),
    )


def compute_q_health(
    nodes: list[NodeMetrics],
    weights: QWeights,
    coverage: float = 1.0,
    dbcv: float = 0.0,
) -> QHealthResult:
    """Compute member-weighted system health score.

    Each cluster's coherence and separation are weighted by its member_count.
    A 30-member cluster contributes 30x more than a singleton. This produces
    a metric that reflects what users actually experience — the quality of
    the clusters where their prompts live.

    Falls back to equal weighting (identical to compute_q_system) when all
    clusters have member_count <= 1 or total_members == 0.

    A5 N-guard: Returns ``q_health=None`` when fewer than 2 active clusters
    exist (separation undefined). Breakdown fields still populated so callers
    retain observability into cluster_count / total_members / coverage.
    """
    active = [n for n in nodes if n.state not in EXCLUDED_STRUCTURAL_STATES]
    if len(active) < 2:
        return _degenerate_q_health(active, weights, coverage, dbcv)

    total_members = sum(max(n.member_count, 0) for n in active)

    # Fallback: if all clusters have 0 members, use equal weighting
    # but report actual total_members=0 in the result (not the fallback).
    if total_members == 0:
        effective_weights = [1 for _ in active]
    else:
        effective_weights = [max(n.member_count, 0) for n in active]

    # Member-weighted means — per-dimension weight sums so that nodes with
    # non-finite coherence/separation don't dilute the other dimension.
    # Mirrors compute_q_system's behavior of skipping non-finite values.
    coh_sum = 0.0
    sep_sum = 0.0
    coh_weight_sum = 0
    sep_weight_sum = 0
    for n, w in zip(active, effective_weights):
        if math.isfinite(n.coherence):
            coh_sum += n.coherence * w
            coh_weight_sum += w
        if math.isfinite(n.separation):
            sep_sum += n.separation * w
            sep_weight_sum += w

    mean_c = max(0.0, min(1.0, coh_sum / max(coh_weight_sum, 1)))
    mean_s = max(0.0, min(1.0, sep_sum / max(sep_weight_sum, 1)))
    cov = max(0.0, min(1.0, coverage))
    dbcv_clamped = max(0.0, min(1.0, dbcv))

    raw = (
        weights.w_c * mean_c
        + weights.w_s * mean_s
        + weights.w_v * cov
        + weights.w_d * dbcv_clamped
    )

    # Defensive weight normalization (same as compute_q_system)
    total_weight = weights.w_c + weights.w_s + weights.w_v + weights.w_d
    if total_weight < 1e-9:
        raw = 0.0
    elif abs(total_weight - 1.0) > 1e-6:
        raw /= total_weight

    q = max(0.0, min(1.0, raw))

    return QHealthResult(
        q_health=round(q, 4),
        coherence_weighted=round(mean_c, 4),
        separation_weighted=round(mean_s, 4),
        coverage=round(cov, 4),
        dbcv=round(dbcv_clamped, 4),
        weights={
            "w_c": round(weights.w_c, 4),
            "w_s": round(weights.w_s, 4),
            "w_v": round(weights.w_v, 4),
            "w_d": round(weights.w_d, 4),
        },
        total_members=total_members,
        cluster_count=len(active),
    )


def adaptive_threshold(
    base: float,
    population: int,
    alpha: float = _ALPHA,
) -> float:
    """Scale threshold with population size.

    Reference: Spec Section 2.4

    Formula: base * (1 + alpha * log(1 + population))

    Small populations get lenient thresholds (let clusters form).
    Large populations get strict thresholds (well-defined by now).

    Clamped to 1.0 — cosine similarity cannot exceed 1.0, so the
    threshold should never exceed that.
    """
    return min(base * (1 + alpha * math.log(1 + population)), 1.0)


def epsilon_tolerance(warm_path_age: int) -> float:
    """Compute warm-path non-regression epsilon for Q_system comparison.

    Reference: Spec Section 2.5

    Used by the warm path (HDBSCAN-lite speculative mutations).
    Young taxonomies get larger epsilon (~0.007 at age 20).
    Mature taxonomies get tiny epsilon (~0.001 at age 100).

    For the cold path (full HDBSCAN refit), use COLD_PATH_EPSILON
    instead — it is a flat 0.05 tolerance because cold-path refits are
    destructive and non-deterministic.

    Args:
        warm_path_age: Number of warm-path cycles completed.
    """
    # Tightened from 0.01 to 0.006 base: at age 10, epsilon was 0.0082
    # which accepted merges with delta=-0.004. New base yields 0.0049
    # at age 10 — rejects merges that degrade Q by more than ~0.5%.
    return max(0.001, 0.006 * math.exp(-warm_path_age / 50))


def is_non_regressive(
    q_before: float | None,
    q_after: float | None,
    warm_path_age: int,
) -> bool:
    """Check if a warm-path quality transition passes the non-regression gate.

    Reference: Spec Section 2.5

    Uses an adaptive epsilon that decays with warm_path_age (see
    epsilon_tolerance). For cold-path gating use is_cold_path_non_regressive.

    Q_after >= Q_before - epsilon (tolerance)

    A5: Q is ``None`` when fewer than 2 active clusters exist.
    Transition semantics when a side is ``None``:
      * ``None → defined``: crossing the N=2 threshold is growth — accept.
      * ``defined → None``: a prior valid taxonomy became degenerate
        (e.g. a phase archived the last active cluster) — reject.
      * ``None → None``: no measurable progress, and any ops that ran
        operated on a degenerate taxonomy — reject conservatively.
    """
    if q_after is None:
        return False
    if q_before is None:
        return True
    eps = epsilon_tolerance(warm_path_age)
    return q_after >= q_before - eps


def is_cold_path_non_regressive(
    q_before: float | None,
    q_after: float | None,
    *,
    phase: int | None = None,
) -> bool:
    """Check if a cold-path quality transition passes the non-regression gate.

    Reference: Spec Section 2.5

    Used after a full HDBSCAN refit (cold path). The tolerance is a flat
    COLD_PATH_EPSILON (0.05 = 5%) rather than the warm-path adaptive decay
    because HDBSCAN refits are destructive and non-deterministic.

    Q_after >= Q_before - COLD_PATH_EPSILON

    A5: Transition semantics when a side is ``None`` (<2 active clusters):
      * ``None → defined``: refit crossed the N=2 threshold — accept.
      * ``defined → None``: refit destroyed the active set — reject.
      * ``None → None``: refit made no measurable progress — reject.

    Args:
        q_before: Q_system score before the cold-path refit (or None).
        q_after: Q_system score after the cold-path refit (or None).
        phase: v0.4.16 P1a — optional phase index (1 or 2) used by the
            chunked cold path's per-phase Q-gate to distinguish the post-
            re-embed gate from the post-reassign gate in observability +
            test injection.  ``None`` for callers that don't track phases
            (e.g. legacy single-shot end-of-refit gate).

    Returns:
        True if the transition is within the allowed regression window.
    """
    # ``phase`` kwarg is for caller observability + per-phase test injection
    # (see test_cold_path_q_check_fires_only_after_phases_1_and_2).  The
    # tolerance itself is identical for every phase — both gates use
    # COLD_PATH_EPSILON.
    del phase  # unused at the math level; visible to spy patches
    if q_after is None:
        return False
    if q_before is None:
        return True
    return q_after >= q_before - COLD_PATH_EPSILON


def suggestion_threshold(
    base: float = 0.72,
    coherence: float = 0.0,
    alpha: float = 0.15,
) -> float:
    """Adaptive suggestion threshold based on cluster coherence.

    Reference: Spec Section 7.9

    High coherence → threshold near base (centroid is representative).
    Low coherence → threshold rises (centroid is blurred).
    """
    return base + alpha * (1.0 - max(0.0, min(1.0, coherence)))


CLUSTER_COHERENCE_FLOOR = 0.6


def coherence_threshold(node) -> float:
    """Return the coherence floor for a node based on its state.

    Domain nodes use a lower threshold because they span multiple
    sub-topics — lower coherence is expected and correct.
    """
    return DOMAIN_COHERENCE_FLOOR if node.state == "domain" else CLUSTER_COHERENCE_FLOOR


# ---------------------------------------------------------------------------
# Intent label coherence — supplementary split signal (Tier 5b)
# ---------------------------------------------------------------------------


def compute_intent_label_coherence(intent_labels: list[str]) -> float:
    """Mean pairwise Jaccard token overlap across member intent labels.

    Returns a value in [0.0, 1.0]. Low values (< 0.15) suggest the cluster
    mixes unrelated intents — useful as a supplementary split signal alongside
    embedding coherence.

    Args:
        intent_labels: List of intent_label strings from cluster members.

    Returns:
        Mean pairwise Jaccard similarity. Returns 1.0 for 0-1 labels.
    """
    # Tokenize each label
    token_sets: list[set[str]] = []
    for label in intent_labels:
        if not label:
            continue
        tokens = {
            w for w in label.lower().split()
            if w not in LABEL_STOP_WORDS and len(w) > 1
        }
        if tokens:
            token_sets.append(tokens)

    if len(token_sets) <= 1:
        return 1.0  # trivially coherent

    # Compute mean pairwise Jaccard
    total = 0.0
    n_pairs = 0
    for i in range(len(token_sets)):
        for j in range(i + 1, len(token_sets)):
            intersection = len(token_sets[i] & token_sets[j])
            union = len(token_sets[i] | token_sets[j])
            if union > 0:
                total += intersection / union
            n_pairs += 1

    return total / n_pairs if n_pairs > 0 else 1.0
