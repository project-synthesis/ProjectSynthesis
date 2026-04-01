"""UMAP 3D projection and Procrustes alignment for the Evolutionary Taxonomy Engine.

Spec Section 8.5.
"""

# ruff: noqa: N803, N806 — mathematical notation (X, U, S, R, X_centered)

from __future__ import annotations

import logging
import random

import numpy as np
from scipy.linalg import orthogonal_procrustes

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Position interpolation for hot/warm paths
# ---------------------------------------------------------------------------


def interpolate_position(
    centroid: np.ndarray,
    siblings: list[tuple[np.ndarray, float, float, float]],
    jitter: float = 0.3,
) -> tuple[float, float, float] | None:
    """Interpolate UMAP position for a new cluster from positioned siblings.

    Computes cosine similarity between *centroid* and each sibling's
    centroid embedding, then takes a weighted average of sibling UMAP
    coordinates (weights = clamped cosine similarities).  Random jitter
    is added per axis to prevent overlap.

    This is a pure function — no I/O, no DB access.

    Parameters
    ----------
    centroid:
        L2-normalized embedding of the new cluster (1-D float32).
    siblings:
        List of ``(centroid_embedding, umap_x, umap_y, umap_z)`` tuples
        from sibling clusters that already have UMAP positions.
    jitter:
        Maximum random offset per axis (uniform ±jitter).

    Returns
    -------
    ``(x, y, z)`` tuple, or ``None`` if *siblings* is empty.
    """
    if not siblings:
        return None

    weights: list[float] = []
    positions: list[tuple[float, float, float]] = []

    for sib_centroid, ux, uy, uz in siblings:
        # Cosine similarity between unit vectors = dot product
        sim = float(np.dot(centroid, sib_centroid))
        # Clamp negative similarities to 0
        w = max(sim, 0.0)
        weights.append(w)
        positions.append((ux, uy, uz))

    total_weight = sum(weights)

    if total_weight == 0.0:
        # All similarities were negative or zero — equal-weight fallback
        n = len(positions)
        x = sum(p[0] for p in positions) / n
        y = sum(p[1] for p in positions) / n
        z = sum(p[2] for p in positions) / n
    else:
        x = sum(w * p[0] for w, p in zip(weights, positions)) / total_weight
        y = sum(w * p[1] for w, p in zip(weights, positions)) / total_weight
        z = sum(w * p[2] for w, p in zip(weights, positions)) / total_weight

    # Add random jitter to prevent overlap
    x += random.uniform(-jitter, jitter)
    y += random.uniform(-jitter, jitter)
    z += random.uniform(-jitter, jitter)

    return (x, y, z)


class UMAPProjector:
    """Wraps umap.UMAP for 3D embedding projection.

    Supports a full batch fit and an incremental transform for new points.
    Falls back to PCA (via SVD) when the input has fewer than 5 points,
    because UMAP requires a minimum number of neighbors.
    """

    _MIN_POINTS_FOR_UMAP = 5

    def __init__(self, random_state: int = 42) -> None:
        self._random_state = random_state
        self._model: object | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fit(self, embeddings: list[np.ndarray]) -> np.ndarray:
        """Fit the projector and return 3D positions.

        Parameters
        ----------
        embeddings:
            List of 1-D float32 vectors of equal length.

        Returns
        -------
        ndarray of shape (N, 3).
        """
        X = np.stack(embeddings, axis=0).astype(np.float32)
        n_points = X.shape[0]

        if n_points < self._MIN_POINTS_FOR_UMAP:
            return self._pca_fallback(X)

        # Clamp n_neighbors so UMAP never requests more neighbors than
        # there are data points available.
        n_neighbors = min(n_points - 1, 15)

        import umap  # local import — heavy dependency

        self._model = umap.UMAP(
            n_components=3,
            metric="cosine",
            low_memory=True,
            random_state=self._random_state,
            n_neighbors=n_neighbors,
        )
        positions: np.ndarray = self._model.fit_transform(X)
        return positions.astype(np.float64)

    def transform(self, new_embeddings: list[np.ndarray]) -> np.ndarray:
        """Incrementally project new embeddings using the fitted model.

        Parameters
        ----------
        new_embeddings:
            List of 1-D float32 vectors.

        Returns
        -------
        ndarray of shape (M, 3).

        Raises
        ------
        ValueError
            If called before :meth:`fit`.
        """
        if self._model is None:
            raise ValueError("UMAPProjector must be fitted before calling transform().")

        X = np.stack(new_embeddings, axis=0).astype(np.float32)
        positions: np.ndarray = self._model.transform(X)
        return positions.astype(np.float64)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pca_fallback(X: np.ndarray) -> np.ndarray:
        """Return 3D positions via truncated SVD (PCA) for small point sets."""
        n_points, n_features = X.shape

        # Centre the data.
        X_centered = X - X.mean(axis=0)

        # SVD: X_centered = U S Vt.  Coordinates in PC space are U * S.
        U, S, _Vt = np.linalg.svd(X_centered, full_matrices=False)
        coords = U * S  # (n_points, min(n_points, n_features))

        n_out = min(3, coords.shape[1])
        result = np.zeros((n_points, 3), dtype=np.float64)
        result[:, :n_out] = coords[:, :n_out]
        return result


# ---------------------------------------------------------------------------
# Procrustes alignment
# ---------------------------------------------------------------------------


def procrustes_align(
    new_positions: np.ndarray,
    old_positions: np.ndarray,
) -> np.ndarray:
    """Align *new_positions* to *old_positions* via Procrustes rotation.

    Finds the orthogonal rotation matrix R that minimises
    ``|| new_centred @ R − old_centred ||_F``, then applies the rotation and
    re-centres the result around the old mean.

    For a single input point the function falls back to a pure translation
    (rotation is undefined for a single point).

    Parameters
    ----------
    new_positions:
        ndarray of shape (N, 3).
    old_positions:
        ndarray of shape (N, 3).

    Returns
    -------
    ndarray of shape (N, 3) — *new_positions* rotated and translated to best
    match *old_positions*.
    """
    new_positions = np.asarray(new_positions, dtype=np.float64)
    old_positions = np.asarray(old_positions, dtype=np.float64)

    n = new_positions.shape[0]

    if n == 1:
        # Only translation is possible with a single point.
        return old_positions.copy()

    new_mean = new_positions.mean(axis=0)
    old_mean = old_positions.mean(axis=0)

    new_centred = new_positions - new_mean
    old_centred = old_positions - old_mean

    # orthogonal_procrustes finds R such that new_centred @ R ≈ old_centred.
    R, _ = orthogonal_procrustes(new_centred, old_centred)

    aligned: np.ndarray = new_centred @ R + old_mean
    return aligned
