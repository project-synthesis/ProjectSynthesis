"""Composite embedding fusion for multi-signal taxonomy queries.

Builds a weighted blend of five embedding signals — topic, transformation,
output, pattern, and qualifier — then L2-normalizes the result. PhaseWeights
control the blend ratio and adapt over time via EMA toward successful profiles
and decay back to per-phase defaults.

Used by pattern_injection and matching to produce richer queries than
raw-prompt embedding alone.

Copyright 2025 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WEIGHT_FLOOR = 0.05
ADAPTATION_ALPHA = 0.05
DECAY_RATE = 0.01
FUSION_CLUSTER_LOOKUP_THRESHOLD = 0.3
FUSION_PATTERN_TOP_K = 3
SCORE_ADAPTATION_MIN_SAMPLES = 2  # Bayesian shrinkage admits learning from m=2; prior dominates at low n
SCORE_ADAPTATION_PRIOR_KAPPA = 8.0  # T1.1 — pseudo-count for the prior;
                                    # at n=κ the empirical and prior contribute equally;
                                    # at n=2 prior carries 80% (2/(2+8)*emp + 8/10*prior).
SCORE_ADAPTATION_LOOKBACK = 200

# Default weight profiles: (w_topic, w_transform, w_output, w_pattern, w_qualifier)
_DEFAULT_PROFILES: dict[str, tuple[float, float, float, float, float]] = {
    "analysis":          (0.55, 0.15, 0.10, 0.15, 0.05),
    "optimization":      (0.18, 0.30, 0.22, 0.20, 0.10),
    "pattern_injection": (0.22, 0.22, 0.18, 0.28, 0.10),
    "scoring":           (0.13, 0.18, 0.42, 0.20, 0.07),
}

# Task-type weight biases — small directional offsets from phase defaults.
# These create organic diversity in phase_weights_json snapshots so that
# compute_score_correlated_target() has genuine variance to learn from.
# Each bias reflects a meaningful hypothesis about which signals matter
# for that task type. The learning loop validates or overrides these.
_TASK_TYPE_WEIGHT_BIAS: dict[str, dict[str, float]] = {
    "coding":   {"w_topic": -0.10, "w_transform": +0.15, "w_output": -0.05, "w_pattern": 0.00, "w_qualifier": +0.15},
    "writing":  {"w_topic": -0.05, "w_transform": -0.05, "w_output": +0.15, "w_pattern": -0.05, "w_qualifier": +0.05},
    "analysis": {"w_topic": +0.10, "w_transform": -0.05, "w_output": -0.10, "w_pattern": +0.05, "w_qualifier": +0.12},
    "creative": {"w_topic": -0.10, "w_transform": +0.05, "w_output": +0.10, "w_pattern": -0.05, "w_qualifier": +0.03},
    "data":     {"w_topic": +0.05, "w_transform": +0.10, "w_output": -0.10, "w_pattern": -0.05, "w_qualifier": +0.10},
    "system":   {"w_topic": +0.05, "w_transform": -0.05, "w_output": -0.05, "w_pattern": +0.05, "w_qualifier": +0.10},
    "general":  {"w_topic": 0.00,  "w_transform": 0.00,  "w_output": 0.00,  "w_pattern": 0.00,  "w_qualifier": +0.05},
}

# Blend ratio when cluster learned weights are available.
# 0.3 = 70% contextual prior + 30% cluster learned profile.
CLUSTER_LEARNED_BLEND_ALPHA = 0.3


# ---------------------------------------------------------------------------
# PhaseWeights
# ---------------------------------------------------------------------------


@dataclass
class PhaseWeights:
    """Five-signal weight profile for composite query fusion.

    Weights should sum to 1.0. Use ``enforce_floor()`` to guarantee
    a minimum of ``WEIGHT_FLOOR`` per dimension while maintaining
    normalization.
    """

    w_topic: float
    w_transform: float
    w_output: float
    w_pattern: float
    w_qualifier: float

    @property
    def total(self) -> float:
        """Sum of all weights."""
        return self.w_topic + self.w_transform + self.w_output + self.w_pattern + self.w_qualifier

    def enforce_floor(self) -> PhaseWeights:
        """Return a new PhaseWeights with each weight >= WEIGHT_FLOOR, re-normalized to sum=1.

        Weights below the floor are pinned at ``WEIGHT_FLOOR``. The
        remaining budget (1.0 minus total floor allocations) is
        distributed proportionally among the non-floored weights.
        Iterates until stable (at most 5 rounds for 5 weights).
        """
        n = 5
        raw = [max(self.w_topic, 0.0), max(self.w_transform, 0.0),
               max(self.w_output, 0.0), max(self.w_pattern, 0.0),
               max(self.w_qualifier, 0.0)]
        result = list(raw)
        pinned = [False] * n

        for _ in range(n):
            # Pin any below-floor weights
            changed = False
            for i in range(n):
                if not pinned[i] and result[i] < WEIGHT_FLOOR:
                    result[i] = WEIGHT_FLOOR
                    pinned[i] = True
                    changed = True

            # Compute budget for free weights
            pinned_sum = sum(result[i] for i in range(n) if pinned[i])
            remaining = 1.0 - pinned_sum
            free_indices = [i for i in range(n) if not pinned[i]]

            if not free_indices:
                # All pinned — equal split
                return PhaseWeights(0.20, 0.20, 0.20, 0.20, 0.20)

            free_raw_sum = sum(raw[i] for i in free_indices)
            if free_raw_sum < 1e-9:
                # Free weights are all zero — split remaining equally
                each = remaining / len(free_indices)
                for i in free_indices:
                    result[i] = each
            else:
                for i in free_indices:
                    result[i] = (raw[i] / free_raw_sum) * remaining

            # Break only when no weights were pinned AND redistribution
            # did not push any free weight below the floor
            if not changed:
                needs_another = any(result[i] < WEIGHT_FLOOR for i in free_indices)
                if not needs_another:
                    break

        # Final precision normalization
        total = sum(result)
        if total > 1e-9:
            result = [w / total for w in result]

        return PhaseWeights(
            w_topic=result[0],
            w_transform=result[1],
            w_output=result[2],
            w_pattern=result[3],
            w_qualifier=result[4],
        )

    @classmethod
    def for_phase(cls, phase: str) -> PhaseWeights:
        """Return the default weight profile for a pipeline phase.

        Known phases: ``analysis``, ``optimization``, ``pattern_injection``,
        ``scoring``. Unknown phases fall back to ``optimization``.
        """
        profile = _DEFAULT_PROFILES.get(phase, _DEFAULT_PROFILES["optimization"])
        return cls(
            w_topic=profile[0],
            w_transform=profile[1],
            w_output=profile[2],
            w_pattern=profile[3],
            w_qualifier=profile[4],
        )

    @classmethod
    def from_dict(cls, d: dict) -> PhaseWeights:
        """Construct from a plain dict. Missing keys default to 0.25 (0.0 for qualifier)."""
        return cls(
            w_topic=float(d.get("w_topic", 0.25)),
            w_transform=float(d.get("w_transform", 0.25)),
            w_output=float(d.get("w_output", 0.25)),
            w_pattern=float(d.get("w_pattern", 0.25)),
            w_qualifier=float(d.get("w_qualifier", 0.0)),
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict with rounded values."""
        return {
            "w_topic": round(self.w_topic, 4),
            "w_transform": round(self.w_transform, 4),
            "w_output": round(self.w_output, 4),
            "w_pattern": round(self.w_pattern, 4),
            "w_qualifier": round(self.w_qualifier, 4),
        }


# ---------------------------------------------------------------------------
# Contextual weight resolution
# ---------------------------------------------------------------------------

_KNOWN_PHASES = ("analysis", "optimization", "pattern_injection", "scoring")


def resolve_contextual_weights(
    task_type: str,
    cluster_learned_weights: dict[str, dict[str, float]] | None = None,
) -> dict[str, dict[str, float]]:
    """Compute a full per-phase weight profile from task-type context and cluster learning.

    This is the primary mechanism that breaks the weight bootstrap fixed point.
    Instead of every optimization snapshotting identical global defaults, each
    optimization gets a weight profile derived from its natural context:

    1. Start from the phase default profile (``PhaseWeights.for_phase()``)
    2. Apply the task-type bias vector (``_TASK_TYPE_WEIGHT_BIAS``)
    3. If the cluster has learned weights, blend toward them (alpha=0.3)

    Different task types produce genuinely different profiles, giving
    ``compute_score_correlated_target()`` the variance it needs to learn.

    Args:
        task_type: Optimization task type (coding, writing, analysis, etc.).
        cluster_learned_weights: Per-phase learned profiles from the cluster's
            ``cluster_metadata["learned_phase_weights"]``, or None if no
            cluster learning has occurred yet.

    Returns:
        Dict mapping phase name to weight dict suitable for
        ``Optimization.phase_weights_json``.
    """
    bias = _TASK_TYPE_WEIGHT_BIAS.get(task_type, _TASK_TYPE_WEIGHT_BIAS["general"])
    result: dict[str, dict[str, float]] = {}

    for phase in _KNOWN_PHASES:
        base = PhaseWeights.for_phase(phase)

        # Apply task-type bias
        biased = PhaseWeights(
            w_topic=base.w_topic + bias.get("w_topic", 0.0),
            w_transform=base.w_transform + bias.get("w_transform", 0.0),
            w_output=base.w_output + bias.get("w_output", 0.0),
            w_pattern=base.w_pattern + bias.get("w_pattern", 0.0),
            w_qualifier=base.w_qualifier + bias.get("w_qualifier", 0.0),
        ).enforce_floor()

        # Blend toward cluster learned weights if available
        if cluster_learned_weights and phase in cluster_learned_weights:
            learned = PhaseWeights.from_dict(cluster_learned_weights[phase])
            alpha = CLUSTER_LEARNED_BLEND_ALPHA
            biased = PhaseWeights(
                w_topic=biased.w_topic + alpha * (learned.w_topic - biased.w_topic),
                w_transform=biased.w_transform + alpha * (learned.w_transform - biased.w_transform),
                w_output=biased.w_output + alpha * (learned.w_output - biased.w_output),
                w_pattern=biased.w_pattern + alpha * (learned.w_pattern - biased.w_pattern),
                w_qualifier=biased.w_qualifier + alpha * (learned.w_qualifier - biased.w_qualifier),
            ).enforce_floor()

        result[phase] = biased.to_dict()

    return result


# ---------------------------------------------------------------------------
# CompositeQuery
# ---------------------------------------------------------------------------


@dataclass
class CompositeQuery:
    """Five-signal composite embedding query.

    Attributes:
        topic: embed(raw_prompt) — what the user is asking about.
        transformation: mean transformation vector from matched cluster.
        output: embed(optimized_prompt) from best prior optimization.
        pattern: embed(top meta-patterns) — reusable technique signal.
        qualifier: embed(qualifier keywords) — organic vocabulary signal.
    """

    topic: np.ndarray
    transformation: np.ndarray
    output: np.ndarray
    pattern: np.ndarray
    qualifier: np.ndarray

    def fuse(self, weights: PhaseWeights) -> np.ndarray:
        """Weighted blend of available signals, L2-normalized.

        Zero vectors are detected and their weight is redistributed
        proportionally among non-zero signals so the fused result is
        dominated by available information rather than pulled toward
        the origin.

        Delegates to :func:`~app.services.taxonomy.clustering.weighted_blend`
        which centralizes the zero-detection threshold, weight redistribution,
        and L2-normalization shared with ``blend_embeddings()``.

        Returns a unit-norm float32 vector, or a zero vector if all
        signals are zero.
        """
        from app.services.taxonomy.clustering import weighted_blend

        return weighted_blend(
            signals=[self.topic, self.transformation, self.output, self.pattern, self.qualifier],
            weights=[weights.w_topic, weights.w_transform, weights.w_output, weights.w_pattern, weights.w_qualifier],
        )


# ---------------------------------------------------------------------------
# Weight adaptation functions
# ---------------------------------------------------------------------------


def adapt_weights(
    current: PhaseWeights,
    successful: PhaseWeights,
    alpha: float = ADAPTATION_ALPHA,
) -> PhaseWeights:
    """EMA step toward a successful weight profile.

    Each weight is moved ``alpha`` fraction of the way toward the
    corresponding value in ``successful``. The result is then
    floor-enforced and re-normalized.

    Args:
        current: The current weight profile.
        successful: The profile that produced a successful outcome.
        alpha: Learning rate (0..1). Default ``ADAPTATION_ALPHA``.

    Returns:
        New PhaseWeights after the EMA step.
    """
    new = PhaseWeights(
        w_topic=current.w_topic + alpha * (successful.w_topic - current.w_topic),
        w_transform=current.w_transform + alpha * (successful.w_transform - current.w_transform),
        w_output=current.w_output + alpha * (successful.w_output - current.w_output),
        w_pattern=current.w_pattern + alpha * (successful.w_pattern - current.w_pattern),
        w_qualifier=current.w_qualifier + alpha * (successful.w_qualifier - current.w_qualifier),
    )
    return new.enforce_floor()


def decay_toward_target(
    current: PhaseWeights,
    phase: str,
    target: PhaseWeights | None = None,
    rate: float = DECAY_RATE,
) -> PhaseWeights:
    """Drift weights toward a target profile (or phase defaults if no target).

    Each weight is moved ``rate`` fraction of the way toward the target.
    When ``target`` is provided (e.g. cluster learned weights), the system
    decays toward what works rather than toward an arbitrary starting point.

    Args:
        current: The current weight profile.
        phase: Pipeline phase name (determines fallback target).
        target: Explicit decay target. If None, falls back to
            ``PhaseWeights.for_phase(phase)`` (the hardcoded default).
        rate: Decay rate (0..1). Default ``DECAY_RATE``.

    Returns:
        New PhaseWeights after decay.
    """
    anchor = target if target is not None else PhaseWeights.for_phase(phase)
    new = PhaseWeights(
        w_topic=current.w_topic + rate * (anchor.w_topic - current.w_topic),
        w_transform=current.w_transform + rate * (anchor.w_transform - current.w_transform),
        w_output=current.w_output + rate * (anchor.w_output - current.w_output),
        w_pattern=current.w_pattern + rate * (anchor.w_pattern - current.w_pattern),
        w_qualifier=current.w_qualifier + rate * (anchor.w_qualifier - current.w_qualifier),
    )
    return new.enforce_floor()


# Backward-compatible alias for existing call sites
decay_toward_defaults = decay_toward_target


# ---------------------------------------------------------------------------
# Score-correlated adaptation
# ---------------------------------------------------------------------------


def compute_score_correlated_target(
    scored_profiles: list[tuple[float, dict[str, dict[str, float]]]],
    min_samples: int = SCORE_ADAPTATION_MIN_SAMPLES,
    prior: dict[str, PhaseWeights] | None = None,
    prior_kappa: float = SCORE_ADAPTATION_PRIOR_KAPPA,
) -> dict[str, PhaseWeights] | None:
    """Compute score-weighted target weight profiles from historical data.

    Identifies which weight profiles correlate with the highest
    ``overall_score`` values and produces a per-phase target that
    the warm-path adaptation can move toward via EMA.

    Args:
        scored_profiles: List of ``(score, phase_weights_json)`` tuples.
            ``score`` should be ``improvement_score`` when available (wider
            variance: std≈0.53 vs 0.27 for ``overall_score``) so the
            z-score weighting has more signal to work with; fall back to
            ``overall_score`` for optimizations that lack improvement data.
            Each ``phase_weights_json`` maps phase name to weight dict
            (e.g. ``{"analysis": {"w_topic": 0.6, ...}}``).
        min_samples: Minimum profiles required for meaningful signal.
            Returns ``None`` below this threshold.

    Returns:
        Dict mapping phase name to target ``PhaseWeights``, or ``None``
        if insufficient data.

    Weighting formula:
        - Compute median and stdev of scores
        - ``contribution = max(0, (score - median) / stdev)``
        - Below-median optimizations contribute 0 (no anti-reinforcement)
        - If stdev < 0.01 (all scores identical), equal contribution
        - Target = score-weighted mean of phase weights, floor-enforced

    T1.1 — Bayesian shrinkage:
        When ``prior`` is supplied, the returned target is a posterior:
        ``posterior = (n / (n + κ)) * empirical + (κ / (n + κ)) * prior``
        With ``κ=8`` (default) and ``n=2`` (the minimum sample count), the
        prior carries 80% weight; at ``n=8`` they contribute equally; at
        ``n=24`` empirical dominates 75/25.  This eliminates the prior
        ``≥10`` hard threshold that prevented all but the largest cluster
        from learning anything beyond bootstrap.
    """
    if len(scored_profiles) < min_samples:
        return None

    scores = [s for s, _ in scored_profiles]
    sorted_scores = sorted(scores)
    n = len(sorted_scores)

    # Median
    if n % 2 == 1:
        median = sorted_scores[n // 2]
    else:
        median = (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2.0

    # Standard deviation
    mean = sum(scores) / n
    variance = sum((s - mean) ** 2 for s in scores) / n
    stdev = variance ** 0.5

    # Compute per-profile contribution weights
    contributions: list[float] = []
    for score, _ in scored_profiles:
        if stdev < 0.01:
            # All scores essentially identical — equal contribution
            contributions.append(1.0)
        else:
            # Z-score above median: only above-median optimizations contribute
            contributions.append(max(0.0, (score - median) / stdev))

    total_contribution = sum(contributions)
    if total_contribution < 1e-9:
        # All below or at median (degenerate) — fall back to equal weighting
        contributions = [1.0] * len(scored_profiles)
        total_contribution = float(len(scored_profiles))

    # Collect all phase names across all profiles
    all_phases: set[str] = set()
    for _, pw_json in scored_profiles:
        if isinstance(pw_json, dict):
            all_phases.update(pw_json.keys())

    if not all_phases:
        return None

    # Compute score-weighted mean per phase
    result: dict[str, PhaseWeights] = {}
    for phase in all_phases:
        w_topic = 0.0
        w_transform = 0.0
        w_output = 0.0
        w_pattern = 0.0
        w_qualifier = 0.0
        phase_contribution = 0.0

        for (_, pw_json), contribution in zip(scored_profiles, contributions):
            if not isinstance(pw_json, dict):
                continue
            phase_dict = pw_json.get(phase)
            if not isinstance(phase_dict, dict):
                continue

            pw = PhaseWeights.from_dict(phase_dict)
            w_topic += pw.w_topic * contribution
            w_transform += pw.w_transform * contribution
            w_output += pw.w_output * contribution
            w_pattern += pw.w_pattern * contribution
            # Skip qualifier dimension for old profiles (w_qualifier=0.0)
            # to avoid treating "no data" as "zero weight is optimal"
            if pw.w_qualifier > 0.0:
                w_qualifier += pw.w_qualifier * contribution
            phase_contribution += contribution

        if phase_contribution < 1e-9:
            continue

        # Qualifier dimension: if no profiles had qualifier data,
        # use the phase default rather than learned zero
        q_weight = w_qualifier / phase_contribution if w_qualifier > 0 else PhaseWeights.for_phase(phase).w_qualifier

        target = PhaseWeights(
            w_topic=w_topic / phase_contribution,
            w_transform=w_transform / phase_contribution,
            w_output=w_output / phase_contribution,
            w_pattern=w_pattern / phase_contribution,
            w_qualifier=q_weight,
        )
        result[phase] = target.enforce_floor()

    # T1.1 Bayesian shrinkage: blend the empirical posterior with the prior
    # so small-sample clusters (n=2..8) start contributing signal without
    # over-fitting to a handful of optimizations.  ``n`` is the count of
    # profiles that actually contributed (i.e. that had non-zero weight).
    if prior and result:
        n = float(len(scored_profiles))
        kappa = max(0.0, prior_kappa)
        if n + kappa > 1e-9:
            w_emp = n / (n + kappa)
            w_pri = kappa / (n + kappa)
            shrunk: dict[str, PhaseWeights] = {}
            for phase, emp_target in result.items():
                pri_target = prior.get(phase) or PhaseWeights.for_phase(phase)
                shrunk[phase] = PhaseWeights(
                    w_topic=w_emp * emp_target.w_topic + w_pri * pri_target.w_topic,
                    w_transform=w_emp * emp_target.w_transform + w_pri * pri_target.w_transform,
                    w_output=w_emp * emp_target.w_output + w_pri * pri_target.w_output,
                    w_pattern=w_emp * emp_target.w_pattern + w_pri * pri_target.w_pattern,
                    w_qualifier=w_emp * emp_target.w_qualifier + w_pri * pri_target.w_qualifier,
                ).enforce_floor()
            return shrunk

    return result if result else None


# ---------------------------------------------------------------------------
# Composite query builder
# ---------------------------------------------------------------------------


async def build_composite_query(
    raw_prompt: str,
    embedding_service,
    taxonomy_engine,
    db: AsyncSession,
    topic_embedding: np.ndarray | None = None,
) -> CompositeQuery:
    """Construct a CompositeQuery from all available signals.

    Gracefully degrades: each signal defaults to a zero vector when
    the corresponding data source is unavailable or errors out.

    Args:
        raw_prompt: User's raw prompt text.
        embedding_service: EmbeddingService instance (sync or async).
        taxonomy_engine: TaxonomyEngine with embedding_index and
            _transformation_index attributes.
        db: Active async DB session for querying optimizations and
            meta-patterns.
        topic_embedding: Pre-computed topic embedding to avoid
            double-embedding the raw prompt (Errata E2-2).

    Returns:
        CompositeQuery with five signal vectors (some may be zero).
    """
    dim = getattr(embedding_service, "dimension", 384) or 384

    # Signal 1: Topic (embed raw_prompt)
    if topic_embedding is not None:
        topic = topic_embedding.astype(np.float32)
    else:
        try:
            topic = await embedding_service.aembed_single(raw_prompt)
            topic = topic.astype(np.float32)
        except Exception:
            logger.warning("build_composite_query: failed to embed raw_prompt")
            topic = np.zeros(dim, dtype=np.float32)

    # Signal 2 + 3 share a cluster lookup — deduplicate the search
    transformation = np.zeros(dim, dtype=np.float32)
    output = np.zeros(dim, dtype=np.float32)
    matched_cluster_id: str | None = None
    try:
        emb_idx = getattr(taxonomy_engine, "embedding_index", None)
        if emb_idx and emb_idx.size > 0:
            topic_matches = emb_idx.search(topic, k=1, threshold=FUSION_CLUSTER_LOOKUP_THRESHOLD)
            if topic_matches:
                matched_cluster_id = topic_matches[0][0]
    except Exception:
        logger.debug("build_composite_query: cluster lookup failed")

    # Signal 2: Transformation (from nearest cluster's mean transformation vector)
    try:
        if matched_cluster_id is not None:
            t_idx = getattr(taxonomy_engine, "_transformation_index", None)
            if t_idx:
                vec = t_idx.get_vector(matched_cluster_id)
                if vec is not None:
                    transformation = vec.astype(np.float32)
    except Exception:
        logger.debug("build_composite_query: transformation signal unavailable")

    # Signal 3: Output (cluster mean optimized embedding from OptimizedEmbeddingIndex)
    # Uses the in-memory index (mean of all member optimized_embeddings) rather than
    # a DB query for the single best-scoring member.  More representative and faster.
    try:
        if matched_cluster_id is not None:
            opt_idx = getattr(taxonomy_engine, "_optimized_index", None)
            if opt_idx:
                vec = opt_idx.get_vector(matched_cluster_id)
                if vec is not None:
                    output = vec.astype(np.float32)
    except Exception:
        logger.debug("build_composite_query: output signal unavailable")

    # Signal 4: Pattern (average pre-computed embeddings of top global patterns)
    # Uses global_source_count (cross-cluster presence) not source_count (per-cluster).
    pattern = np.zeros(dim, dtype=np.float32)
    try:
        from app.models import MetaPattern, PromptCluster
        from app.services.pipeline_constants import CROSS_CLUSTER_MIN_SOURCE_COUNT
        from app.services.taxonomy._constants import EXCLUDED_STRUCTURAL_STATES

        result = await db.execute(
            select(MetaPattern.embedding)
            .join(PromptCluster, MetaPattern.cluster_id == PromptCluster.id)
            .where(
                MetaPattern.global_source_count >= CROSS_CLUSTER_MIN_SOURCE_COUNT,
                MetaPattern.embedding.isnot(None),
                PromptCluster.state.notin_(EXCLUDED_STRUCTURAL_STATES),
            )
            .order_by(MetaPattern.global_source_count.desc())
            .limit(FUSION_PATTERN_TOP_K)
        )
        embeddings = []
        for row in result.scalars().all():
            try:
                embeddings.append(np.frombuffer(row, dtype=np.float32).copy())  # type: ignore[arg-type]
            except (ValueError, TypeError):
                continue
        if embeddings:
            pattern = np.mean(np.stack(embeddings), axis=0).astype(np.float32)
            p_norm = np.linalg.norm(pattern)
            if p_norm > 1e-9:
                pattern = pattern / p_norm
    except Exception:
        logger.debug("build_composite_query: pattern signal unavailable")

    # Signal 5: Qualifier (from qualifier_index if available)
    qualifier = np.zeros(dim, dtype=np.float32)
    try:
        if matched_cluster_id is not None:
            q_idx = getattr(taxonomy_engine, "_qualifier_index", None)
            if q_idx:
                vec = q_idx.get_vector(matched_cluster_id)
                if vec is not None:
                    qualifier = vec.astype(np.float32)
    except Exception:
        logger.debug("build_composite_query: qualifier signal unavailable")

    return CompositeQuery(
        topic=topic,
        transformation=transformation,
        output=output,
        pattern=pattern,
        qualifier=qualifier,
    )


async def resolve_fused_embedding(
    raw_prompt: str,
    topic_embedding: np.ndarray,
    embedding_service,
    taxonomy_engine,
    db: AsyncSession,
    phase: str = "pattern_injection",
) -> np.ndarray:
    """Build CompositeQuery, load adapted weights, fuse into single search vector.

    Shared helper that consolidates the identical pattern used by both
    ``pattern_injection.auto_inject_patterns()`` and ``matching.match_prompt()``.
    Falls back to ``topic_embedding`` on any failure.

    Args:
        raw_prompt: Raw prompt text.
        topic_embedding: Pre-computed topic embedding (avoids double-embed).
        embedding_service: EmbeddingService instance.
        taxonomy_engine: TaxonomyEngine (may be None for graceful fallback).
        db: Active async DB session.
        phase: Pipeline phase name for weight lookup.

    Returns:
        Fused embedding vector (unit-norm float32).
    """
    try:
        composite = await build_composite_query(
            raw_prompt, embedding_service, taxonomy_engine, db,
            topic_embedding=topic_embedding,
        )
        # Load adapted weights from preferences if available, else defaults
        from app.services.preferences import PreferencesService

        prefs = PreferencesService().load()
        pw_dict = prefs.get("phase_weights", {}).get(phase, {})
        weights = PhaseWeights.from_dict(pw_dict) if pw_dict else PhaseWeights.for_phase(phase)
        return composite.fuse(weights)
    except Exception:
        logger.debug("resolve_fused_embedding: falling back to topic-only for phase=%s", phase)
        return topic_embedding
