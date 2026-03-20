"""Tests for HDBSCAN clustering wrapper."""

import numpy as np
import pytest

from app.services.taxonomy.clustering import (
    ClusterResult,
    batch_cluster,
    nearest_centroid,
)
from tests.taxonomy.conftest import make_cluster_distribution


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
