"""HDBSCAN clustering wrapper for the Evolutionary Taxonomy Engine.

Spec Section 2.3: batch clustering, nearest-centroid assignment,
coherence/separation metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import HDBSCAN

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ClusterResult:
    """Output from a single batch_cluster call.

    Attributes:
        labels: Integer cluster ID per input point (-1 = noise).
        n_clusters: Number of discovered clusters (noise excluded).
        noise_count: Number of points assigned label -1.
        persistences: Per-cluster persistence value (death - birth eps).
        centroids: Per-cluster mean of L2-normalised embeddings, re-normalised.
    """

    labels: np.ndarray
    n_clusters: int
    noise_count: int
    persistences: list[float] = field(default_factory=list)
    centroids: list[np.ndarray] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation.

    Args:
        vecs: 2-D float32 array of shape (N, D).

    Returns:
        Unit-norm version of *vecs*.
    """
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms == 0, 1.0, norms)
    return (vecs / norms).astype(np.float32)


def _extract_persistences(hdb: HDBSCAN, n_clusters: int) -> list[float]:
    """Pull per-cluster persistence from the HDBSCAN condensed tree.

    Falls back to 0.0 for every cluster if the attribute is unavailable.

    Args:
        hdb: A fitted HDBSCAN instance.
        n_clusters: Number of real clusters found.

    Returns:
        List of ``n_clusters`` float values.
    """
    try:
        tree = hdb.condensed_tree_
        # condensed_tree_ is a numpy structured array with fields:
        # parent, child, lambda_val, child_size
        # A leaf cluster's persistence = max lambda of its points.
        # We use the cluster_persistence_ attribute when available (scikit-learn
        # >= 1.3 exposes it directly).
        if hasattr(hdb, "cluster_persistence_"):
            return [float(p) for p in hdb.cluster_persistence_]

        # Manual extraction: for each cluster label 0..n_clusters-1, find the
        # lambda range spanned by its leaf branch in the condensed tree.
        if tree is None:
            return [0.0] * n_clusters

        arr = np.asarray(tree)
        persistences: list[float] = []
        for label in range(n_clusters):
            mask = arr["child_size"] == 1
            child_labels = hdb.labels_
            # Identify which leaf nodes belong to this cluster
            member_indices = np.where(child_labels == label)[0]
            if len(member_indices) == 0:
                persistences.append(0.0)
                continue
            # Find the lambda values for those leaf nodes in the condensed tree
            leaf_lambdas: list[float] = []
            for idx in member_indices:
                leaf_rows = arr[mask & (arr["child"] == idx)]
                if len(leaf_rows) > 0:
                    leaf_lambdas.append(float(leaf_rows["lambda_val"].max()))
            if leaf_lambdas:
                persistences.append(max(leaf_lambdas) - min(leaf_lambdas))
            else:
                persistences.append(0.0)
        return persistences
    except Exception:
        return [0.0] * n_clusters


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def nearest_centroid(
    query: np.ndarray,
    centroids: list[np.ndarray],
) -> tuple[int, float] | None:
    """Find the closest centroid to *query* by cosine similarity.

    Both *query* and each centroid are L2-normalised internally so the dot
    product equals cosine similarity.

    Args:
        query: 1-D float32 embedding vector.
        centroids: List of 1-D float32 embedding vectors.

    Returns:
        ``(index, cosine_score)`` of the best match, or ``None`` if
        *centroids* is empty.
    """
    if not centroids:
        return None

    q = query.astype(np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm > 0:
        q = q / q_norm

    best_idx = 0
    best_score = -2.0

    for i, c in enumerate(centroids):
        c = c.astype(np.float32)
        c_norm = np.linalg.norm(c)
        if c_norm > 0:
            c = c / c_norm
        score = float(np.dot(q, c))
        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx, best_score


def batch_cluster(
    embeddings: list[np.ndarray],
    min_cluster_size: int = 3,
) -> ClusterResult:
    """Cluster a list of embeddings with HDBSCAN.

    L2-normalises all embeddings first so that Euclidean distance in the
    normalised space approximates cosine distance.

    Args:
        embeddings: List of 1-D float32 arrays (all same dimension).
        min_cluster_size: Minimum number of points to form a cluster.

    Returns:
        :class:`ClusterResult` with labels, counts, persistences, and
        per-cluster centroids.
    """
    n = len(embeddings)

    # Guard: too few points to possibly form a cluster.
    if n < min_cluster_size:
        labels = np.full(n, -1, dtype=np.intp)
        return ClusterResult(
            labels=labels,
            n_clusters=0,
            noise_count=n,
            persistences=[],
            centroids=[],
        )

    # Stack into (N, D) matrix and L2-normalise.
    mat = np.stack(embeddings, axis=0).astype(np.float32)
    mat = _l2_normalize(mat)

    hdb = HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    hdb.fit(mat)

    labels: np.ndarray = hdb.labels_
    unique_labels = set(labels.tolist())
    cluster_ids = sorted(x for x in unique_labels if x >= 0)
    n_clusters = len(cluster_ids)
    noise_count = int(np.sum(labels == -1))

    # --- Persistences ---
    persistences = _extract_persistences(hdb, n_clusters)

    # --- Centroids: mean of normalised members, then re-normalise ---
    centroids: list[np.ndarray] = []
    for cid in cluster_ids:
        member_vecs = mat[labels == cid]
        mean_vec = member_vecs.mean(axis=0).astype(np.float32)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        centroids.append(mean_vec)

    return ClusterResult(
        labels=labels,
        n_clusters=n_clusters,
        noise_count=noise_count,
        persistences=persistences,
        centroids=centroids,
    )


def compute_pairwise_coherence(embeddings: list[np.ndarray]) -> float:
    """Mean pairwise cosine similarity within a group.

    Args:
        embeddings: List of 1-D float32 arrays.

    Returns:
        Mean cosine similarity in ``[−1, 1]``, or ``1.0`` for a single
        embedding and ``0.0`` for an empty list.
    """
    n = len(embeddings)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0

    mat = np.stack(embeddings, axis=0).astype(np.float32)
    mat = _l2_normalize(mat)
    sim_matrix = mat @ mat.T  # (N, N)

    # Sum upper triangle (excluding diagonal)
    triu = np.triu(sim_matrix, k=1)
    n_pairs = n * (n - 1) / 2
    return float(triu.sum() / n_pairs)


def compute_separation(centroids: list[np.ndarray]) -> float:
    """Minimum pairwise cosine *distance* between cluster centroids.

    Cosine distance = 1 − cosine similarity.

    Args:
        centroids: List of 1-D float32 centroid arrays.

    Returns:
        Minimum pairwise cosine distance, or ``0.0`` for fewer than two
        centroids.
    """
    if len(centroids) < 2:
        return 0.0

    mat = np.stack(centroids, axis=0).astype(np.float32)
    mat = _l2_normalize(mat)
    sim_matrix = mat @ mat.T  # (K, K), similarities in [−1, 1]
    dist_matrix = 1.0 - sim_matrix  # distances

    # Ignore the diagonal (self-distance = 0)
    np.fill_diagonal(dist_matrix, np.inf)
    return float(dist_matrix.min())
