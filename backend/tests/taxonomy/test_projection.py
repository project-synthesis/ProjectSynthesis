"""Tests for UMAP 3D projection, Procrustes alignment, and position interpolation."""

import numpy as np
import pytest

from app.services.taxonomy.projection import (
    UMAPProjector,
    interpolate_position,
    procrustes_align,
)


@pytest.fixture
def projector():
    return UMAPProjector(random_state=42)


class TestUMAPProjector:
    def test_fit_returns_3d(self, projector):
        """UMAP should produce 3-component output."""
        embeddings = [np.random.randn(384).astype(np.float32) for _ in range(20)]
        positions = projector.fit(embeddings)
        assert positions.shape == (20, 3)

    def test_transform_incremental(self, projector):
        """Incremental transform should be fast and consistent."""
        base = [np.random.randn(384).astype(np.float32) for _ in range(20)]
        projector.fit(base)

        new = [np.random.randn(384).astype(np.float32) for _ in range(3)]
        positions = projector.transform(new)
        assert positions.shape == (3, 3)

    def test_fit_too_few_points(self, projector):
        """Should handle < 5 points gracefully (UMAP needs minimum)."""
        embeddings = [np.random.randn(384).astype(np.float32) for _ in range(3)]
        positions = projector.fit(embeddings)
        # Fallback to PCA or random placement for small sets
        assert positions.shape == (3, 3)


class TestProcrustesAlign:
    def test_preserves_relative_positions(self):
        """Procrustes should find rotation that minimizes displacement."""
        old_pos = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float64)
        # Rotate 90 degrees around z-axis
        new_pos = np.array([[0, 0, 0], [0, 1, 0], [-1, 0, 0]], dtype=np.float64)
        aligned = procrustes_align(new_pos, old_pos)
        # After alignment, should be close to old_pos
        np.testing.assert_allclose(aligned, old_pos, atol=0.1)

    def test_identity_unchanged(self):
        """Same positions should stay the same."""
        pos = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float64)
        aligned = procrustes_align(pos, pos)
        np.testing.assert_allclose(aligned, pos, atol=1e-6)

    def test_handles_single_point(self):
        """Single point should return translated to match."""
        old = np.array([[1, 2, 3]], dtype=np.float64)
        new = np.array([[4, 5, 6]], dtype=np.float64)
        aligned = procrustes_align(new, old)
        np.testing.assert_allclose(aligned, old, atol=1e-6)


class TestInterpolatePosition:
    """Tests for interpolate_position() — pure function, no DB."""

    @staticmethod
    def _unit_vec(dim: int = 384, seed: int = 0) -> np.ndarray:
        """Create a deterministic unit-norm vector."""
        rng = np.random.RandomState(seed)
        v = rng.randn(dim).astype(np.float32)
        return v / np.linalg.norm(v)

    def test_empty_siblings_returns_none(self):
        """No siblings → None (caller should skip position assignment)."""
        centroid = self._unit_vec(seed=1)
        assert interpolate_position(centroid, []) is None

    def test_single_sibling(self):
        """Single sibling — result should be near the sibling's position (+ jitter)."""
        centroid = self._unit_vec(seed=1)
        sibling_centroid = self._unit_vec(seed=1)  # identical → cosine=1.0
        sibling_pos = (5.0, 10.0, -3.0)

        result = interpolate_position(
            centroid,
            [(sibling_centroid, *sibling_pos)],
            jitter=0.3,
        )
        assert result is not None
        x, y, z = result
        # Should be within jitter distance of the sibling position
        assert abs(x - 5.0) <= 0.3 + 1e-9
        assert abs(y - 10.0) <= 0.3 + 1e-9
        assert abs(z - -3.0) <= 0.3 + 1e-9

    def test_multiple_siblings_weighted_by_similarity(self):
        """Two siblings: one very similar, one orthogonal. Result biased toward similar."""
        centroid = self._unit_vec(seed=1)

        # Near-identical sibling at position (10, 0, 0)
        similar_sib = self._unit_vec(seed=1)
        # Orthogonal sibling at position (0, 10, 0)
        ortho_sib = np.zeros(384, dtype=np.float32)
        ortho_sib[0] = 1.0  # arbitrary unit vector, likely ~0 sim with centroid

        result = interpolate_position(
            centroid,
            [
                (similar_sib, 10.0, 0.0, 0.0),
                (ortho_sib, 0.0, 10.0, 0.0),
            ],
            jitter=0.0,  # no jitter for deterministic test
        )
        assert result is not None
        x, y, z = result
        # Similar sibling (cosine~1.0) should dominate — x closer to 10 than 0
        assert x > 5.0, f"Expected x > 5.0, got {x}"

    def test_all_negative_similarity_fallback(self):
        """When all cosine similarities are negative, equal-weight fallback."""
        # Create a centroid and siblings with negative cosine similarity
        centroid = np.zeros(384, dtype=np.float32)
        centroid[0] = 1.0

        neg_sib = np.zeros(384, dtype=np.float32)
        neg_sib[0] = -1.0  # anti-parallel → cosine = -1

        result = interpolate_position(
            centroid,
            [(neg_sib, 4.0, 6.0, 8.0)],
            jitter=0.0,
        )
        assert result is not None
        x, y, z = result
        # Equal-weight average of single point = the point itself
        assert abs(x - 4.0) < 1e-6
        assert abs(y - 6.0) < 1e-6
        assert abs(z - 8.0) < 1e-6

    def test_jitter_adds_randomness(self):
        """Running with jitter > 0 should produce different results each time."""
        centroid = self._unit_vec(seed=1)
        siblings = [(self._unit_vec(seed=2), 0.0, 0.0, 0.0)]

        results = set()
        for _ in range(20):
            pos = interpolate_position(centroid, siblings, jitter=1.0)
            assert pos is not None
            results.add(pos)

        # With jitter=1.0, 20 calls should produce multiple distinct values
        assert len(results) > 1, "Jitter should produce varying positions"

    def test_zero_jitter_deterministic(self):
        """With jitter=0, same inputs should always give same output."""
        centroid = self._unit_vec(seed=1)
        siblings = [
            (self._unit_vec(seed=2), 1.0, 2.0, 3.0),
            (self._unit_vec(seed=3), 4.0, 5.0, 6.0),
        ]

        results = [
            interpolate_position(centroid, siblings, jitter=0.0)
            for _ in range(5)
        ]
        # All results should be identical
        for r in results[1:]:
            assert r == results[0]

    def test_returns_tuple_of_three_floats(self):
        """Return type should be a 3-tuple of floats."""
        centroid = self._unit_vec(seed=1)
        siblings = [(self._unit_vec(seed=2), 1.0, 2.0, 3.0)]

        result = interpolate_position(centroid, siblings, jitter=0.0)
        assert result is not None
        assert len(result) == 3
        assert all(isinstance(v, float) for v in result)
