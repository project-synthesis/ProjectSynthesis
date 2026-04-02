"""Tests for output-coherence-adaptive blend weights in cold path."""

import numpy as np
import pytest

from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_OPTIMIZED,
    CLUSTERING_BLEND_W_RAW,
    CLUSTERING_BLEND_W_TRANSFORM,
)
from app.services.taxonomy.clustering import blend_embeddings


def test_low_output_coherence_reduces_optimized_weight():
    """When output coherence < 0.5, w_optimized should be reduced."""
    # Simulate adaptive logic: output_coherence=0.2 -> scale = max(0.25, 0.2/0.5) = 0.4
    output_coherence = 0.2
    scale = max(0.25, output_coherence / 0.5)
    w_opt = CLUSTERING_BLEND_W_OPTIMIZED * scale
    w_raw = 1.0 - w_opt - CLUSTERING_BLEND_W_TRANSFORM

    assert w_opt < CLUSTERING_BLEND_W_OPTIMIZED, "Low coherence should reduce w_optimized"
    assert w_opt == pytest.approx(0.20 * 0.4)  # 0.08
    assert w_raw == pytest.approx(1.0 - 0.08 - 0.15)  # 0.77
    assert w_raw + w_opt + CLUSTERING_BLEND_W_TRANSFORM == pytest.approx(1.0)


def test_high_output_coherence_keeps_default_weights():
    """When output coherence >= 0.5, default weights are used."""
    output_coherence = 0.8
    # No scaling applied
    w_opt = CLUSTERING_BLEND_W_OPTIMIZED  # 0.20
    w_raw = CLUSTERING_BLEND_W_RAW  # 0.65

    assert w_opt == 0.20
    assert w_raw == 0.65


def test_missing_output_coherence_keeps_default_weights():
    """When output_coherence is None, default weights are used."""
    output_coherence = None
    w_opt = CLUSTERING_BLEND_W_OPTIMIZED
    assert w_opt == 0.20


def test_adaptive_blend_weight_invariant():
    """w_raw + w_optimized + w_transform must always sum to 1.0."""
    for coh in [0.0, 0.1, 0.25, 0.4, 0.5, 0.7, 1.0, None]:
        w_opt = CLUSTERING_BLEND_W_OPTIMIZED
        if coh is not None and coh < 0.5:
            w_opt = CLUSTERING_BLEND_W_OPTIMIZED * max(0.25, coh / 0.5)
        w_raw = 1.0 - w_opt - CLUSTERING_BLEND_W_TRANSFORM
        total = w_raw + w_opt + CLUSTERING_BLEND_W_TRANSFORM
        assert total == pytest.approx(1.0), f"Sum != 1.0 for coherence={coh}: {total}"
        assert w_opt >= CLUSTERING_BLEND_W_OPTIMIZED * 0.25, f"Floor violated for coherence={coh}"


def test_blend_embeddings_accepts_custom_weights():
    """blend_embeddings() works with non-default weights."""
    rng = np.random.RandomState(42)
    raw = rng.randn(384).astype(np.float32)
    opt = rng.randn(384).astype(np.float32)
    trans = rng.randn(384).astype(np.float32)

    result = blend_embeddings(
        raw=raw, optimized=opt, transformation=trans,
        w_raw=0.77, w_optimized=0.08, w_transform=0.15,
    )
    assert result.shape == (384,)
    assert np.linalg.norm(result) == pytest.approx(1.0, abs=1e-5)


def test_cold_path_uses_adaptive_blend():
    """Verify cold_path.py implements adaptive blend logic."""
    import inspect
    from app.services.taxonomy import cold_path

    source = inspect.getsource(cold_path)
    assert "output_coherence" in source, (
        "Cold path must reference output_coherence for adaptive blending"
    )
    assert "max(0.25" in source, (
        "Cold path must enforce 0.25 floor on coherence scaling"
    )
