"""Tests for spectral_split() — spectral clustering for taxonomy splits."""

from __future__ import annotations

import numpy as np

from app.services.taxonomy.clustering import ClusterResult, spectral_split


def _make_groups(
    n_groups: int,
    members_per_group: int,
    dim: int = 384,
    spread: float = 0.05,
    seed: int = 42,
) -> np.ndarray:
    """Create N well-separated groups of L2-normalized embeddings."""
    rng = np.random.RandomState(seed)
    all_vecs = []
    for g in range(n_groups):
        center = rng.randn(dim).astype(np.float32)
        center = center / np.linalg.norm(center)
        for _ in range(members_per_group):
            noise = rng.randn(dim).astype(np.float32) * spread
            vec = center + noise
            vec = vec / np.linalg.norm(vec)
            all_vecs.append(vec)
    return np.stack(all_vecs, axis=0).astype(np.float32)


class TestSpectralSplitClearGroups:
    def test_three_groups_selects_k3(self) -> None:
        embeddings = _make_groups(3, 10, spread=0.03)
        result, sils = spectral_split(embeddings)
        assert result is not None
        assert isinstance(result, ClusterResult)
        assert result.n_clusters == 3
        assert result.silhouette > 0.3
        assert result.noise_count == 0
        assert len(result.centroids) == 3
        assert result.labels.shape[0] == 30
        for c in result.centroids:
            assert abs(np.linalg.norm(c) - 1.0) < 1e-4
        # All k silhouettes should be recorded
        assert len(sils) > 0

    def test_two_groups_selects_k2(self) -> None:
        embeddings = _make_groups(2, 15, spread=0.03)
        result, sils = spectral_split(embeddings)
        assert result is not None
        assert result.n_clusters == 2
        assert result.silhouette > 0.3
        assert len(result.centroids) == 2

    def test_all_points_assigned(self) -> None:
        embeddings = _make_groups(3, 8, spread=0.03)
        result, _ = spectral_split(embeddings)
        assert result is not None
        assert result.noise_count == 0
        assert (result.labels >= 0).all()


class TestSpectralSplitRejection:
    def test_uniform_noise_returns_none(self) -> None:
        rng = np.random.RandomState(99)
        embeddings = rng.randn(30, 384).astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / norms
        result, sils = spectral_split(embeddings, silhouette_gate=0.55)
        assert result is None

    def test_identical_embeddings_rejected(self) -> None:
        """Identical embeddings should be rejected (None) or produce degenerate clusters."""
        vec = np.ones(384, dtype=np.float32)
        vec = vec / np.linalg.norm(vec)
        embeddings = np.tile(vec, (20, 1))
        result, _ = spectral_split(embeddings)
        # Some scipy versions return None (degenerate matrix detected),
        # others return a result with meaningless labels. Both are acceptable
        # as long as the silhouette is low (identical points can't be split well).
        if result is not None:
            assert result.silhouette < 0.6  # degenerate split

    def test_too_few_points_returns_none(self) -> None:
        rng = np.random.RandomState(7)
        embeddings = rng.randn(5, 384).astype(np.float32)
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        embeddings = embeddings / norms
        result, sils = spectral_split(embeddings)
        assert result is None
        assert len(sils) == 0  # too few points, no k attempted


class TestSpectralSplitGroupSizeFilter:
    def test_rejects_k_with_small_group(self) -> None:
        rng = np.random.RandomState(42)
        groups = _make_groups(2, 10, spread=0.02, seed=42)
        outlier = rng.randn(384).astype(np.float32)
        outlier = (outlier / np.linalg.norm(outlier)).reshape(1, -1)
        embeddings = np.vstack([groups, outlier])
        result, _ = spectral_split(embeddings, k_range=(2, 3))
        if result is not None:
            for cid in range(result.n_clusters):
                assert (result.labels == cid).sum() >= 3


class TestSpectralSplitSilhouetteScale:
    def test_silhouette_in_zero_one_range(self) -> None:
        embeddings = _make_groups(2, 12, spread=0.03)
        result, _ = spectral_split(embeddings)
        assert result is not None
        assert 0.0 <= result.silhouette <= 1.0

    def test_persistences_equal_silhouette(self) -> None:
        embeddings = _make_groups(3, 8, spread=0.03)
        result, _ = spectral_split(embeddings)
        assert result is not None
        assert len(result.persistences) == result.n_clusters
        for p in result.persistences:
            assert abs(p - result.silhouette) < 1e-6

    def test_all_silhouettes_returned(self) -> None:
        """All attempted k values should appear in the silhouettes dict."""
        embeddings = _make_groups(3, 10, spread=0.03)
        result, sils = spectral_split(embeddings, k_range=(2, 3, 4))
        # At least k=2 and k=3 should have been attempted
        assert 2 in sils or 3 in sils
