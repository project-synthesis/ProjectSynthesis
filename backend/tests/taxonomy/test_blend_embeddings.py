"""Tests for blend_embeddings() — multi-signal HDBSCAN blending.

Copyright 2025-2026 Project Synthesis contributors.
"""

import numpy as np

from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_OPTIMIZED,
    CLUSTERING_BLEND_W_QUALIFIER,
    CLUSTERING_BLEND_W_RAW,
    CLUSTERING_BLEND_W_TRANSFORM,
)
from app.services.taxonomy.clustering import blend_embeddings

DIM = 384


def _random_unit(seed: int) -> np.ndarray:
    """Deterministic random unit vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(DIM).astype(np.float32)
    return v / np.linalg.norm(v)


class TestBlendEmbeddings:
    """Core blend_embeddings() behavior."""

    def test_all_signals_present(self):
        raw = _random_unit(1)
        opt = _random_unit(2)
        trans = _random_unit(3)
        blended = blend_embeddings(raw, opt, trans)

        # Correct shape and dtype
        assert blended.shape == (DIM,)
        assert blended.dtype == np.float32

        # L2-normalized
        assert abs(np.linalg.norm(blended) - 1.0) < 1e-5

        # Different from raw alone (opt and trans shift the result)
        assert not np.allclose(blended, raw / np.linalg.norm(raw), atol=1e-3)

    def test_optimized_missing(self):
        raw = _random_unit(10)
        trans = _random_unit(11)
        blended = blend_embeddings(raw, optimized=None, transformation=trans)

        # Still valid
        assert blended.shape == (DIM,)
        assert abs(np.linalg.norm(blended) - 1.0) < 1e-5

        # Should differ from raw-only (trans is present)
        raw_only = blend_embeddings(raw)
        assert not np.allclose(blended, raw_only, atol=1e-3)

    def test_transform_missing(self):
        raw = _random_unit(20)
        opt = _random_unit(21)
        blended = blend_embeddings(raw, optimized=opt, transformation=None)

        assert blended.shape == (DIM,)
        assert abs(np.linalg.norm(blended) - 1.0) < 1e-5

        raw_only = blend_embeddings(raw)
        assert not np.allclose(blended, raw_only, atol=1e-3)

    def test_both_missing_returns_raw(self):
        raw = _random_unit(30)
        blended = blend_embeddings(raw, optimized=None, transformation=None)

        # Should be exactly the L2-normalized raw
        expected = raw / np.linalg.norm(raw)
        assert np.allclose(blended, expected, atol=1e-6)

    def test_zero_vector_treated_as_missing(self):
        raw = _random_unit(40)
        zero = np.zeros(DIM, dtype=np.float32)

        blended_zero_opt = blend_embeddings(raw, optimized=zero, transformation=None)
        blended_none_opt = blend_embeddings(raw, optimized=None, transformation=None)

        # Zero-vector optimized should behave same as None
        assert np.allclose(blended_zero_opt, blended_none_opt, atol=1e-6)

    def test_custom_weights(self):
        raw = _random_unit(50)
        opt = _random_unit(51)
        trans = _random_unit(52)

        # Default weights
        b1 = blend_embeddings(raw, opt, trans)
        # Swap to output-heavy weights
        b2 = blend_embeddings(
            raw, opt, trans,
            w_raw=0.20, w_optimized=0.60, w_transform=0.20,
        )

        # Different weights should produce different results
        assert not np.allclose(b1, b2, atol=1e-3)

    def test_deterministic(self):
        raw = _random_unit(60)
        opt = _random_unit(61)
        trans = _random_unit(62)

        b1 = blend_embeddings(raw, opt, trans)
        b2 = blend_embeddings(raw, opt, trans)
        assert np.allclose(b1, b2, atol=1e-9)

    def test_weight_redistribution_proportional(self):
        """When one signal is missing, its weight is proportionally split."""
        raw = _random_unit(70)
        opt = _random_unit(71)

        # With trans missing: effective weights redistribute proportionally
        w_raw = CLUSTERING_BLEND_W_RAW
        w_opt = CLUSTERING_BLEND_W_OPTIMIZED
        blended = blend_embeddings(raw, optimized=opt, transformation=None)

        expected_w_raw = w_raw / (w_raw + w_opt)
        expected_w_opt = w_opt / (w_raw + w_opt)
        manual = expected_w_raw * raw + expected_w_opt * opt
        manual = manual / np.linalg.norm(manual)

        assert np.allclose(blended, manual, atol=1e-5)


class TestBlendConstants:
    """Verify default constants are sensible."""

    def test_weights_sum_to_one(self):
        total = (
            CLUSTERING_BLEND_W_RAW
            + CLUSTERING_BLEND_W_OPTIMIZED
            + CLUSTERING_BLEND_W_TRANSFORM
            + CLUSTERING_BLEND_W_QUALIFIER
        )
        assert abs(total - 1.0) < 1e-9

    def test_raw_dominates(self):
        assert CLUSTERING_BLEND_W_RAW > CLUSTERING_BLEND_W_OPTIMIZED
        assert CLUSTERING_BLEND_W_RAW > CLUSTERING_BLEND_W_TRANSFORM

    def test_all_positive(self):
        assert CLUSTERING_BLEND_W_RAW > 0
        assert CLUSTERING_BLEND_W_OPTIMIZED > 0
        assert CLUSTERING_BLEND_W_TRANSFORM > 0
        assert CLUSTERING_BLEND_W_QUALIFIER > 0

    def test_blend_with_qualifier_signal(self):
        """Qualifier signal blends into output when provided."""
        raw = np.array([1, 0, 0, 0], dtype=np.float32)
        qualifier = np.array([0, 1, 0, 0], dtype=np.float32)
        blended = blend_embeddings(raw, qualifier=qualifier)
        assert blended[1] > 0  # qualifier pulled toward [0,1,0,0]
        assert blended[0] > blended[1]  # raw still dominates (0.55 > 0.10)

    def test_blend_without_qualifier_matches_original(self):
        """Without qualifier, blend identical to 3-signal blend."""
        raw = np.array([1, 0, 0, 0], dtype=np.float32)
        opt = np.array([0, 1, 0, 0], dtype=np.float32)
        result_no_q = blend_embeddings(raw, optimized=opt)
        result_with_none = blend_embeddings(raw, optimized=opt, qualifier=None)
        np.testing.assert_array_almost_equal(result_no_q, result_with_none)
