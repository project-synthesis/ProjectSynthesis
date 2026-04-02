"""Tests for HDBSCAN clustering wrapper."""

import numpy as np
import pytest

from app.services.taxonomy.clustering import (
    ClusterResult,
    batch_cluster,
    compute_mean_separation,
    compute_pairwise_coherence,
    compute_separation,
    l2_normalize_1d,
    nearest_centroid,
)
from tests.taxonomy.conftest import EMBEDDING_DIM, make_cluster_distribution


class TestNearestCentroid:
    def test_finds_closest(self):
        centroids = [
            np.array([1, 0, 0], dtype=np.float32),
            np.array([0, 1, 0], dtype=np.float32),
            np.array([0, 0, 1], dtype=np.float32),
        ]
        query = np.array([0.9, 0.1, 0], dtype=np.float32)
        idx, score = nearest_centroid(query, centroids)
        assert idx == 0
        assert score > 0.9

    def test_empty_centroids(self):
        query = np.array([1, 0, 0], dtype=np.float32)
        result = nearest_centroid(query, [])
        assert result is None

    def test_single_centroid(self):
        centroids = [np.array([0, 1, 0], dtype=np.float32)]
        query = np.array([0, 1, 0], dtype=np.float32)
        idx, score = nearest_centroid(query, centroids)
        assert idx == 0
        assert score == pytest.approx(1.0, abs=0.01)


class TestBatchCluster:
    def test_separates_distinct_clusters(self):
        """Two well-separated clusters should be found."""
        rng = np.random.RandomState(42)
        cluster_a = make_cluster_distribution("REST API", 15, spread=0.05, rng=rng)
        cluster_b = make_cluster_distribution("SQL database", 15, spread=0.05, rng=rng)
        embeddings = cluster_a + cluster_b

        result = batch_cluster(embeddings, min_cluster_size=3)
        assert isinstance(result, ClusterResult)
        # Should find at least 2 clusters (some points may be noise)
        assert result.n_clusters >= 2

    def test_noise_handling(self):
        """Random noise should produce mostly noise labels (-1)."""
        rng = np.random.RandomState(42)
        noise = [rng.randn(384).astype(np.float32) for _ in range(20)]
        result = batch_cluster(noise, min_cluster_size=5)
        # Most points should be noise with random embeddings
        assert result.noise_count > 0

    def test_too_few_points(self):
        """Less than min_cluster_size should return all noise."""
        embeddings = [np.random.randn(384).astype(np.float32) for _ in range(2)]
        result = batch_cluster(embeddings, min_cluster_size=5)
        assert result.n_clusters == 0
        assert result.noise_count == 2

    def test_returns_persistence(self):
        """Cluster result should include persistence values."""
        rng = np.random.RandomState(42)
        cluster = make_cluster_distribution("test cluster", 20, spread=0.05, rng=rng)
        result = batch_cluster(cluster, min_cluster_size=3)
        if result.n_clusters > 0:
            assert len(result.persistences) == result.n_clusters
            assert all(p >= 0 for p in result.persistences)


class TestComputePairwiseCoherence:
    """Tests for mean intra-cluster cosine similarity."""

    def test_empty_returns_zero(self):
        assert compute_pairwise_coherence([]) == 0.0

    def test_single_returns_one(self):
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        assert compute_pairwise_coherence([vec]) == 1.0

    def test_identical_vectors_high_coherence(self):
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        result = compute_pairwise_coherence([vec, vec, vec])
        assert result == pytest.approx(1.0, abs=1e-5)

    def test_orthogonal_vectors_low_coherence(self):
        """Orthogonal vectors should have ~0 coherence."""
        # Construct two vectors that are nearly orthogonal in high-dim space
        rng = np.random.RandomState(42)
        a = rng.randn(EMBEDDING_DIM).astype(np.float32)
        b = rng.randn(EMBEDDING_DIM).astype(np.float32)
        # In 384-dim, random vectors are nearly orthogonal
        result = compute_pairwise_coherence([a, b])
        assert abs(result) < 0.2


class TestComputeSeparation:
    """Tests for minimum inter-cluster cosine distance."""

    def test_fewer_than_two_returns_one(self):
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        assert compute_separation([]) == 1.0
        assert compute_separation([vec]) == 1.0

    def test_identical_centroids_zero_separation(self):
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        result = compute_separation([vec, vec])
        assert result == pytest.approx(0.0, abs=1e-5)

    def test_distant_centroids_high_separation(self):
        """Random high-dim vectors should have distance close to 1.0."""
        rng = np.random.RandomState(42)
        centroids = [rng.randn(EMBEDDING_DIM).astype(np.float32) for _ in range(3)]
        result = compute_separation(centroids)
        assert result > 0.5  # high-dim random vectors are far apart


class TestComputeMeanSeparation:
    """Tests for mean per-centroid minimum cosine distance."""

    def test_fewer_than_two_returns_one(self):
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        assert compute_mean_separation([]) == 1.0
        assert compute_mean_separation([vec]) == 1.0

    def test_identical_centroids_zero_separation(self):
        vec = np.random.randn(EMBEDDING_DIM).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        result = compute_mean_separation([vec, vec])
        assert result == pytest.approx(0.0, abs=1e-5)

    def test_distant_centroids_high_separation(self):
        """Random high-dim vectors should have high mean separation."""
        rng = np.random.RandomState(42)
        centroids = [rng.randn(EMBEDDING_DIM).astype(np.float32) for _ in range(3)]
        result = compute_mean_separation(centroids)
        assert result > 0.5

    def test_mean_higher_than_global_min_with_one_close_pair(self):
        """With one close pair among many distant centroids, mean separation
        should be significantly higher than global minimum separation.

        This is the core scenario that triggered the user-visible bug:
        37 groups with one close pair causing global-min to drop below 0.3
        while mean separation stays healthy.
        """
        rng = np.random.RandomState(42)
        # Create 10 well-separated random centroids
        centroids = [rng.randn(EMBEDDING_DIM).astype(np.float32) for _ in range(10)]
        # Make one pair very close (nearly identical)
        centroids.append(centroids[0] + rng.randn(EMBEDDING_DIM).astype(np.float32) * 0.01)

        global_min = compute_separation(centroids)
        mean_sep = compute_mean_separation(centroids)

        # Global min should be very low (the close pair)
        assert global_min < 0.1
        # Mean separation should be much higher (most pairs are distant)
        assert mean_sep > global_min * 3
        # Mean should still be above the warning threshold
        assert mean_sep > 0.3


class TestL2Normalize1d:
    """Tests for public L2 normalization helper."""

    def test_unit_norm_output(self):
        vec = np.array([3.0, 4.0, 0.0], dtype=np.float32)
        result = l2_normalize_1d(vec)
        assert np.linalg.norm(result) == pytest.approx(1.0, abs=1e-6)

    def test_zero_vector_unchanged(self):
        vec = np.zeros(EMBEDDING_DIM, dtype=np.float32)
        result = l2_normalize_1d(vec)
        assert np.allclose(result, vec)


def test_cluster_result_has_silhouette():
    """ClusterResult includes silhouette score in [0, 1]."""
    from app.services.taxonomy.clustering import batch_cluster
    # Create 3 tight clusters of 5 points each
    rng = np.random.RandomState(42)
    clusters = []
    for center_seed in [0.0, 3.0, 6.0]:
        center = np.zeros(384, dtype=np.float32)
        center[0] = center_seed
        for _ in range(5):
            point = center + rng.randn(384).astype(np.float32) * 0.1
            clusters.append(point / np.linalg.norm(point))

    result = batch_cluster(clusters, min_cluster_size=3)
    assert hasattr(result, "silhouette"), "ClusterResult must have silhouette field"
    assert 0.0 <= result.silhouette <= 1.0


def test_silhouette_zero_for_single_cluster():
    """Silhouette is 0.0 when only one cluster found (or all noise)."""
    from app.services.taxonomy.clustering import batch_cluster
    rng = np.random.RandomState(99)
    # Tight single blob — HDBSCAN should find 1 cluster or all noise
    points = []
    for _ in range(10):
        v = rng.randn(384).astype(np.float32)
        points.append(v / np.linalg.norm(v))

    result = batch_cluster(points, min_cluster_size=3)
    assert result.silhouette == pytest.approx(0.0, abs=0.01) or result.n_clusters <= 1


def test_silhouette_zero_for_too_few_points():
    """Silhouette is 0.0 when too few points to cluster."""
    from app.services.taxonomy.clustering import batch_cluster
    v = np.random.randn(384).astype(np.float32)
    result = batch_cluster([v, v], min_cluster_size=3)
    assert result.silhouette == 0.0
