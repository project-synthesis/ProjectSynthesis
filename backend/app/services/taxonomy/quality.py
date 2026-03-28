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

logger = logging.getLogger(__name__)

# Default weight targets (when DBCV is fully active)
_W_D_TARGET = 0.15
_W_C_BASE = 0.4
_W_S_BASE = 0.35
_W_V_BASE = 0.25

# Adaptive threshold scaling factor
_ALPHA = 0.15


@dataclass(frozen=True)
class NodeMetrics:
    """Lightweight metrics container for Q_system computation.

    Not a DB model — populated from PromptCluster fields for quality calculations.
    """

    coherence: float
    separation: float
    state: str  # 'candidate' | 'active' | 'archived'


@dataclass(frozen=True)
class QWeights:
    """Constant-sum weights for Q_system (always sum to 1.0).

    Spec Section 2.5 — DBCV ramps in linearly over 20 observations
    after >=5 active nodes exist. Other weights scale proportionally.
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
) -> float:
    """Compute composite system quality score.

    Reference: Spec Section 2.5

    Edge cases:
    - Empty or all-archived: returns 0.0
    - Single node: coherence=perfect, separation=perfect (no siblings)
    - NaN/Inf: replaced with 0.0
    """
    active = [n for n in nodes if n.state == "active"]
    if not active:
        return 0.0

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
    """Compute non-regression epsilon for Q_system comparison.

    Reference: Spec Section 2.5

    Young taxonomies get larger epsilon (~0.007 at age 20).
    Mature taxonomies get tiny epsilon (~0.001 at age 100).

    Args:
        warm_path_age: Number of warm-path cycles completed.
    """
    return max(0.001, 0.01 * math.exp(-warm_path_age / 50))


def is_non_regressive(
    q_before: float,
    q_after: float,
    warm_path_age: int,
) -> bool:
    """Check if a quality transition passes the non-regression gate.

    Reference: Spec Section 2.5
    Q_after >= Q_before - epsilon (tolerance)
    """
    eps = epsilon_tolerance(warm_path_age)
    return q_after >= q_before - eps


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
