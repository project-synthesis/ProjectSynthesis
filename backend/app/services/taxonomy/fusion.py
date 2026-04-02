"""Composite embedding fusion for multi-signal taxonomy queries.

Builds a weighted blend of four embedding signals — topic, transformation,
output, and pattern — then L2-normalizes the result. PhaseWeights control
the blend ratio and adapt over time via EMA toward successful profiles and
decay back to per-phase defaults.

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
SCORE_ADAPTATION_MIN_SAMPLES = 10

# Default weight profiles: (w_topic, w_transform, w_output, w_pattern)
_DEFAULT_PROFILES: dict[str, tuple[float, float, float, float]] = {
    "analysis": (0.60, 0.15, 0.10, 0.15),
    "optimization": (0.20, 0.35, 0.25, 0.20),
    "pattern_injection": (0.25, 0.25, 0.20, 0.30),
    "scoring": (0.15, 0.20, 0.45, 0.20),
}


# ---------------------------------------------------------------------------
# PhaseWeights
# ---------------------------------------------------------------------------


@dataclass
class PhaseWeights:
    """Four-signal weight profile for composite query fusion.

    Weights should sum to 1.0. Use ``enforce_floor()`` to guarantee
    a minimum of ``WEIGHT_FLOOR`` per dimension while maintaining
    normalization.
    """

    w_topic: float
    w_transform: float
    w_output: float
    w_pattern: float

    @property
    def total(self) -> float:
        """Sum of all weights."""
        return self.w_topic + self.w_transform + self.w_output + self.w_pattern

    def enforce_floor(self) -> PhaseWeights:
        """Return a new PhaseWeights with each weight >= WEIGHT_FLOOR, re-normalized to sum=1.

        Weights below the floor are pinned at ``WEIGHT_FLOOR``. The
        remaining budget (1.0 minus total floor allocations) is
        distributed proportionally among the non-floored weights.
        Iterates until stable (at most 4 rounds for 4 weights).
        """
        n = 4
        raw = [max(self.w_topic, 0.0), max(self.w_transform, 0.0),
               max(self.w_output, 0.0), max(self.w_pattern, 0.0)]
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
                return PhaseWeights(0.25, 0.25, 0.25, 0.25)

            free_raw_sum = sum(raw[i] for i in free_indices)
            if free_raw_sum < 1e-9:
                # Free weights are all zero — split remaining equally
                each = remaining / len(free_indices)
                for i in free_indices:
                    result[i] = each
            else:
                for i in free_indices:
                    result[i] = (raw[i] / free_raw_sum) * remaining

            if not changed:
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
        )

    @classmethod
    def from_dict(cls, d: dict) -> PhaseWeights:
        """Construct from a plain dict. Missing keys default to 0.25."""
        return cls(
            w_topic=float(d.get("w_topic", 0.25)),
            w_transform=float(d.get("w_transform", 0.25)),
            w_output=float(d.get("w_output", 0.25)),
            w_pattern=float(d.get("w_pattern", 0.25)),
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict with rounded values."""
        return {
            "w_topic": round(self.w_topic, 4),
            "w_transform": round(self.w_transform, 4),
            "w_output": round(self.w_output, 4),
            "w_pattern": round(self.w_pattern, 4),
        }


# ---------------------------------------------------------------------------
# CompositeQuery
# ---------------------------------------------------------------------------


@dataclass
class CompositeQuery:
    """Four-signal composite embedding query.

    Attributes:
        topic: embed(raw_prompt) — what the user is asking about.
        transformation: mean transformation vector from matched cluster.
        output: embed(optimized_prompt) from best prior optimization.
        pattern: embed(top meta-patterns) — reusable technique signal.
    """

    topic: np.ndarray
    transformation: np.ndarray
    output: np.ndarray
    pattern: np.ndarray

    def fuse(self, weights: PhaseWeights) -> np.ndarray:
        """Weighted blend of available signals, L2-normalized.

        Zero vectors are detected and their weight is redistributed
        proportionally among non-zero signals so the fused result is
        dominated by available information rather than pulled toward
        the origin.

        Returns a unit-norm float32 vector, or a zero vector if all
        signals are zero.
        """
        signals = [self.topic, self.transformation, self.output, self.pattern]
        raw_weights = [weights.w_topic, weights.w_transform, weights.w_output, weights.w_pattern]

        # Detect which signals are non-zero
        active_weights = []
        active_signals = []
        for sig, w in zip(signals, raw_weights):
            norm = float(np.linalg.norm(sig))
            if norm > 1e-9:
                active_weights.append(w)
                active_signals.append(sig)

        if not active_signals:
            # All signals are zero — return zero vector
            return np.zeros_like(self.topic, dtype=np.float32)

        # Re-normalize active weights to sum to 1
        total_w = sum(active_weights)
        if total_w < 1e-9:
            # Degenerate weights — equal split
            normed_weights = [1.0 / len(active_weights)] * len(active_weights)
        else:
            normed_weights = [w / total_w for w in active_weights]

        # Weighted sum
        fused = np.zeros_like(self.topic, dtype=np.float32)
        for sig, w in zip(active_signals, normed_weights):
            fused += w * sig.astype(np.float32)

        # L2 normalize
        norm = float(np.linalg.norm(fused))
        if norm < 1e-9:
            return np.zeros_like(self.topic, dtype=np.float32)
        return (fused / norm).astype(np.float32)


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
    )
    return new.enforce_floor()


def decay_toward_defaults(
    current: PhaseWeights,
    phase: str,
    rate: float = DECAY_RATE,
) -> PhaseWeights:
    """Drift weights back toward their phase defaults.

    Each weight is moved ``rate`` fraction of the way toward the
    default value for ``phase``. Prevents runaway drift when
    adaptation is not regularly reinforced by successful outcomes.

    Args:
        current: The current weight profile.
        phase: Pipeline phase name (determines default target).
        rate: Decay rate (0..1). Default ``DECAY_RATE``.

    Returns:
        New PhaseWeights after decay.
    """
    defaults = PhaseWeights.for_phase(phase)
    new = PhaseWeights(
        w_topic=current.w_topic + rate * (defaults.w_topic - current.w_topic),
        w_transform=current.w_transform + rate * (defaults.w_transform - current.w_transform),
        w_output=current.w_output + rate * (defaults.w_output - current.w_output),
        w_pattern=current.w_pattern + rate * (defaults.w_pattern - current.w_pattern),
    )
    return new.enforce_floor()


# ---------------------------------------------------------------------------
# Score-correlated adaptation
# ---------------------------------------------------------------------------


def compute_score_correlated_target(
    scored_profiles: list[tuple[float, dict[str, dict[str, float]]]],
    min_samples: int = SCORE_ADAPTATION_MIN_SAMPLES,
) -> dict[str, PhaseWeights] | None:
    """Compute score-weighted target weight profiles from historical data.

    Identifies which weight profiles correlate with the highest
    ``overall_score`` values and produces a per-phase target that
    the warm-path adaptation can move toward via EMA.

    Args:
        scored_profiles: List of ``(overall_score, phase_weights_json)``
            tuples. Each ``phase_weights_json`` maps phase name to
            weight dict (e.g. ``{"analysis": {"w_topic": 0.6, ...}}``).
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
            phase_contribution += contribution

        if phase_contribution < 1e-9:
            continue

        target = PhaseWeights(
            w_topic=w_topic / phase_contribution,
            w_transform=w_transform / phase_contribution,
            w_output=w_output / phase_contribution,
            w_pattern=w_pattern / phase_contribution,
        )
        result[phase] = target.enforce_floor()

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
        CompositeQuery with four signal vectors (some may be zero).
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

    # Signal 3: Output (best-scoring optimized_prompt embedding from matched cluster)
    try:
        if matched_cluster_id is not None:
            from app.models import Optimization

            result = await db.execute(
                select(Optimization.optimized_embedding)
                .where(
                    Optimization.cluster_id == matched_cluster_id,
                    Optimization.optimized_embedding.isnot(None),
                    Optimization.status == "completed",
                )
                .order_by(
                    Optimization.overall_score.desc().nullslast(),
                    Optimization.created_at.desc(),
                )
                .limit(1)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                output = np.frombuffer(row, dtype=np.float32).copy()
    except Exception:
        logger.debug("build_composite_query: output signal unavailable")

    # Signal 4: Pattern (average pre-computed embeddings of top global patterns)
    # Uses global_source_count (cross-cluster presence) not source_count (per-cluster).
    pattern = np.zeros(dim, dtype=np.float32)
    try:
        from app.models import MetaPattern
        from app.services.pipeline_constants import CROSS_CLUSTER_MIN_SOURCE_COUNT

        result = await db.execute(
            select(MetaPattern.embedding)
            .where(
                MetaPattern.global_source_count >= CROSS_CLUSTER_MIN_SOURCE_COUNT,
                MetaPattern.embedding.isnot(None),
            )
            .order_by(MetaPattern.global_source_count.desc())
            .limit(FUSION_PATTERN_TOP_K)
        )
        embeddings = []
        for row in result.scalars().all():
            try:
                embeddings.append(np.frombuffer(row, dtype=np.float32).copy())
            except (ValueError, TypeError):
                continue
        if embeddings:
            pattern = np.mean(np.stack(embeddings), axis=0).astype(np.float32)
            p_norm = np.linalg.norm(pattern)
            if p_norm > 1e-9:
                pattern = pattern / p_norm
    except Exception:
        logger.debug("build_composite_query: pattern signal unavailable")

    return CompositeQuery(
        topic=topic,
        transformation=transformation,
        output=output,
        pattern=pattern,
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
