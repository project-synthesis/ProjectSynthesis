"""Tests for score-weighted centroid in assign_cluster().

Verifies that higher-scoring optimizations shift the cluster centroid
more than lower-scoring ones, and that weighted_member_sum is correctly
initialized and accumulated.

All weight expectations use ``score_to_centroid_weight()`` — the shared
power-law formula: ``max(0.2, (score / 10.0) ** 1.5)``.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptCluster
from app.services.taxonomy.family_ops import assign_cluster, score_to_centroid_weight

EMBEDDING_DIM = 768


def _unit_vec(seed: int) -> np.ndarray:
    """Deterministic unit vector from seed."""
    rng = np.random.RandomState(seed)
    v = rng.randn(EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def _close_vec(base: np.ndarray, offset_seed: int, scale: float = 0.01) -> np.ndarray:
    """Create a unit vector close to *base* (high cosine similarity)."""
    rng = np.random.RandomState(offset_seed)
    noise = rng.randn(EMBEDDING_DIM).astype(np.float32) * scale
    v = base + noise
    return v / np.linalg.norm(v)


# ── New cluster creation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_cluster_weighted_member_sum_from_score(db: AsyncSession):
    """weighted_member_sum should be power-law score weight on a fresh cluster."""
    emb = _unit_vec(1)
    cluster = await assign_cluster(
        db, emb, label="test", domain="general",
        task_type="coding", overall_score=8.0,
    )
    assert cluster.weighted_member_sum == pytest.approx(
        score_to_centroid_weight(8.0), abs=1e-6,
    )
    assert cluster.member_count == 1


@pytest.mark.asyncio
async def test_new_cluster_weighted_member_sum_none_score(db: AsyncSession):
    """When overall_score is None, default weight uses score=5.0."""
    emb = _unit_vec(2)
    cluster = await assign_cluster(
        db, emb, label="test-none", domain="general",
        task_type="coding", overall_score=None,
    )
    assert cluster.weighted_member_sum == pytest.approx(
        score_to_centroid_weight(None), abs=1e-6,
    )


@pytest.mark.asyncio
async def test_new_cluster_weighted_member_sum_low_score(db: AsyncSession):
    """A very low score (0.5) should produce weight 0.2 (clamped floor)."""
    emb = _unit_vec(3)
    cluster = await assign_cluster(
        db, emb, label="test-low", domain="general",
        task_type="coding", overall_score=0.5,
    )
    # (0.5 / 10.0)^1.5 = 0.011, clamped to 0.2
    assert cluster.weighted_member_sum == pytest.approx(0.2, abs=1e-6)


@pytest.mark.asyncio
async def test_new_cluster_weighted_member_sum_zero_score(db: AsyncSession):
    """Score of 0.0 is falsy — treated as missing, defaults to score=5.0."""
    emb = _unit_vec(4)
    cluster = await assign_cluster(
        db, emb, label="test-zero", domain="general",
        task_type="coding", overall_score=0.0,
    )
    # 0.0 is falsy → (0.0 or 5.0) → power-law(5.0)
    assert cluster.weighted_member_sum == pytest.approx(
        score_to_centroid_weight(0.0), abs=1e-6,
    )


# ── Score-weighted merge ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_high_score_shifts_centroid_more(db: AsyncSession):
    """A high-score optimization (9) should shift the centroid MORE toward
    the new embedding than a low-score optimization (1), proving
    that score weighting works.
    """
    # Create a base cluster with a known centroid
    base = _unit_vec(10)
    seed_cluster = PromptCluster(
        label="seed",
        domain="general",
        task_type="coding",
        state="active",
        centroid_embedding=base.astype(np.float32).tobytes(),
        member_count=1,
        weighted_member_sum=0.5,  # initial weight from score=5
        avg_score=5.0,
        scored_count=1,
    )
    db.add(seed_cluster)
    await db.flush()

    # Direction we'll push toward — close enough to merge (cosine ~0.78,
    # above adaptive threshold ~0.59 for member_count=1) but distinct
    # enough to measure centroid shift
    push_direction = _close_vec(base, offset_seed=99, scale=0.04)

    # --- Merge with HIGH score (9.0) ---
    high_cluster = await assign_cluster(
        db, push_direction, label="high-merge", domain="general",
        task_type="coding", overall_score=9.0,
    )
    # Should merge into seed_cluster (same domain, close enough)
    assert high_cluster.id == seed_cluster.id
    high_centroid = np.frombuffer(high_cluster.centroid_embedding, dtype=np.float32)
    high_cos_to_push = float(np.dot(high_centroid, push_direction))

    # --- Reset cluster to original state for fair comparison ---
    seed_cluster.centroid_embedding = base.astype(np.float32).tobytes()
    seed_cluster.member_count = 1
    seed_cluster.weighted_member_sum = 0.5
    seed_cluster.avg_score = 5.0
    seed_cluster.scored_count = 1

    # --- Merge with LOW score (1.0) ---
    low_cluster = await assign_cluster(
        db, push_direction, label="low-merge", domain="general",
        task_type="coding", overall_score=1.0,
    )
    assert low_cluster.id == seed_cluster.id
    low_centroid = np.frombuffer(low_cluster.centroid_embedding, dtype=np.float32)
    low_cos_to_push = float(np.dot(low_centroid, push_direction))

    # The high-score merge should pull centroid closer to push_direction
    assert high_cos_to_push > low_cos_to_push, (
        f"High-score centroid should be closer to push direction: "
        f"high={high_cos_to_push:.4f} vs low={low_cos_to_push:.4f}"
    )


@pytest.mark.asyncio
async def test_weighted_member_sum_accumulates(db: AsyncSession):
    """weighted_member_sum should accumulate across merges."""
    base = _unit_vec(20)
    w_init = score_to_centroid_weight(5.0)
    cluster = PromptCluster(
        label="accumulate",
        domain="general",
        task_type="coding",
        state="active",
        centroid_embedding=base.astype(np.float32).tobytes(),
        member_count=1,
        weighted_member_sum=w_init,
        avg_score=5.0,
        scored_count=1,
    )
    db.add(cluster)
    await db.flush()

    # Merge optimization with score=10
    w_10 = score_to_centroid_weight(10.0)
    near = _close_vec(base, offset_seed=21, scale=0.05)
    result = await assign_cluster(
        db, near, label="merge1", domain="general",
        task_type="coding", overall_score=10.0,
    )
    assert result.id == cluster.id
    assert result.weighted_member_sum == pytest.approx(w_init + w_10, abs=1e-6)
    assert result.member_count == 2

    # Merge another with score=2 — use tighter scale so it stays above
    # adaptive threshold after the first merge shifted the centroid.
    w_2 = score_to_centroid_weight(2.0)
    near2 = _close_vec(base, offset_seed=22, scale=0.03)
    result2 = await assign_cluster(
        db, near2, label="merge2", domain="general",
        task_type="coding", overall_score=2.0,
    )
    assert result2.id == cluster.id
    assert result2.weighted_member_sum == pytest.approx(w_init + w_10 + w_2, abs=1e-6)
    assert result2.member_count == 3


@pytest.mark.asyncio
async def test_pre_migration_fallback(db: AsyncSession):
    """Clusters with weighted_member_sum=0.0 (pre-migration) should fall
    back to member_count as the weight denominator."""
    base = _unit_vec(30)
    cluster = PromptCluster(
        label="legacy",
        domain="general",
        task_type="coding",
        state="active",
        centroid_embedding=base.astype(np.float32).tobytes(),
        member_count=5,
        weighted_member_sum=0.0,  # pre-migration default
        avg_score=5.0,
        scored_count=5,
    )
    db.add(cluster)
    await db.flush()

    near = _close_vec(base, offset_seed=31, scale=0.05)
    result = await assign_cluster(
        db, near, label="legacy-merge", domain="general",
        task_type="coding", overall_score=8.0,
    )
    assert result.id == cluster.id
    # Should have used member_count (5) as old weighted_sum fallback
    # Power-law weight: max(0.2, (8.0/10.0)**1.5) ≈ 0.7155
    # New weighted_sum = 5.0 + 0.7155 ≈ 5.7155
    assert result.weighted_member_sum == pytest.approx(5.0 + max(0.2, (8.0 / 10.0) ** 1.5), abs=1e-4)
    assert result.member_count == 6


@pytest.mark.asyncio
async def test_centroid_stays_unit_norm_after_weighted_merge(db: AsyncSession):
    """Centroid should remain unit-normalized after score-weighted merge."""
    base = _unit_vec(40)
    cluster = PromptCluster(
        label="norm-check",
        domain="general",
        task_type="coding",
        state="active",
        centroid_embedding=base.astype(np.float32).tobytes(),
        member_count=1,
        weighted_member_sum=0.9,
        avg_score=9.0,
        scored_count=1,
    )
    db.add(cluster)
    await db.flush()

    near = _close_vec(base, offset_seed=41, scale=0.2)
    result = await assign_cluster(
        db, near, label="norm-merge", domain="general",
        task_type="coding", overall_score=7.0,
    )
    centroid = np.frombuffer(result.centroid_embedding, dtype=np.float32)
    norm = float(np.linalg.norm(centroid))
    assert norm == pytest.approx(1.0, abs=1e-5), f"Centroid norm={norm}, expected 1.0"
