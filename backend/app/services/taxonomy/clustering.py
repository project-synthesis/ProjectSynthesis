"""HDBSCAN clustering wrapper for the Evolutionary Taxonomy Engine.

Spec Section 2.3: batch clustering, nearest-centroid assignment,
coherence/separation metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from sklearn.cluster import HDBSCAN, SpectralClustering
from sklearn.metrics import silhouette_score

from app.services.taxonomy._constants import (
    CLUSTERING_BLEND_W_OPTIMIZED,
    CLUSTERING_BLEND_W_QUALIFIER,
    CLUSTERING_BLEND_W_RAW,
    CLUSTERING_BLEND_W_TRANSFORM,
    SPECTRAL_K_RANGE,
    SPECTRAL_MIN_GROUP_SIZE,
    SPECTRAL_SILHOUETTE_GATE,
)

logger = logging.getLogger(__name__)

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
    silhouette: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _l2_normalize(vecs: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalisation for 2-D arrays.

    Args:
        vecs: 2-D float32 array of shape (N, D).

    Returns:
        Unit-norm version of *vecs*.
    """
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.where(norms == 0, 1.0, norms)
    return (vecs / norms).astype(np.float32)


def l2_normalize_1d(vec: np.ndarray) -> np.ndarray:
    """L2-normalise a single 1-D vector.

    Public helper shared by lifecycle.py and engine.py to avoid duplication.

    Args:
        vec: 1-D float32 array.

    Returns:
        Unit-norm version of *vec* as float32.
    """
    norm = np.linalg.norm(vec)
    if norm < 1e-9:
        return vec.astype(np.float32)
    return (vec / norm).astype(np.float32)


def weighted_blend(
    signals: list[np.ndarray],
    weights: list[float],
) -> np.ndarray:
    """Core weighted blend of embedding signals with zero-vector redistribution.

    Shared by ``blend_embeddings()`` (HDBSCAN clustering) and
    ``CompositeQuery.fuse()`` (composite fusion queries).  Centralizes
    the zero-detection threshold (1e-9), weight redistribution, and
    L2-normalization to prevent algorithmic drift between the two paths.

    Args:
        signals: List of 1-D float32 embedding vectors.  Zero-norm vectors
            are filtered out and their weight redistributed proportionally.
        weights: Corresponding weight per signal (same length as *signals*).

    Returns:
        L2-normalized float32 blended vector. Returns a zero vector if
        all signals are zero-norm or *signals* is empty.
    """
    # Filter to non-zero signals
    active: list[tuple[np.ndarray, float]] = []
    for sig, w in zip(signals, weights):
        s = sig.astype(np.float32)
        if float(np.linalg.norm(s)) > 1e-9:
            active.append((s, w))

    if not active:
        dim = signals[0].shape[-1] if signals else 0
        return np.zeros(dim, dtype=np.float32)

    # Re-normalize weights to sum to 1
    total_w = sum(w for _, w in active)
    if total_w < 1e-9:
        normed = [1.0 / len(active)] * len(active)
    else:
        normed = [w / total_w for _, w in active]

    # Weighted sum + L2-normalize
    blended = np.zeros_like(active[0][0], dtype=np.float32)
    for (sig, _), nw in zip(active, normed):
        blended += nw * sig
    return l2_normalize_1d(blended)


def blend_embeddings(
    raw: np.ndarray,
    optimized: np.ndarray | None = None,
    transformation: np.ndarray | None = None,
    w_raw: float = CLUSTERING_BLEND_W_RAW,
    w_optimized: float = CLUSTERING_BLEND_W_OPTIMIZED,
    w_transform: float = CLUSTERING_BLEND_W_TRANSFORM,
    *,
    qualifier: np.ndarray | None = None,
    w_qualifier: float = CLUSTERING_BLEND_W_QUALIFIER,
) -> np.ndarray:
    """Blend raw + optimized + transformation + qualifier embeddings for HDBSCAN clustering.

    Produces a single 384-dim L2-normalized vector that captures topic (raw),
    output quality (optimized), technique direction (transformation), and
    domain qualifier signal (qualifier).
    When a signal is missing (None or near-zero norm), its weight is
    redistributed proportionally to the remaining non-zero signals.

    Delegates to :func:`weighted_blend` for the core algorithm shared with
    ``CompositeQuery.fuse()``.

    Args:
        raw: 1-D float32 raw prompt embedding (required, always present).
        optimized: 1-D float32 optimized prompt embedding, or None.
        transformation: 1-D float32 transformation vector, or None.
        w_raw: Weight for the raw signal.
        w_optimized: Weight for the optimized signal.
        w_transform: Weight for the transformation signal.
        qualifier: 1-D float32 domain qualifier embedding, or None.
            Must be passed as a keyword argument.
        w_qualifier: Weight for the qualifier signal.

    Returns:
        L2-normalized 384-dim float32 blended embedding.
        Falls back to L2-normalized raw if all other signals are absent.
    """
    raw_vec = raw.astype(np.float32).ravel()
    signals = [raw_vec]
    weights = [w_raw]

    if optimized is not None:
        signals.append(optimized.astype(np.float32).ravel())
        weights.append(w_optimized)

    if transformation is not None:
        signals.append(transformation.astype(np.float32).ravel())
        weights.append(w_transform)

    if qualifier is not None:
        signals.append(qualifier.astype(np.float32).ravel())
        weights.append(w_qualifier)

    return weighted_blend(signals, weights)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors.

    Returns the dot product of L2-normalised inputs.  Returns 0.0 if
    either vector has zero norm.

    Args:
        a: 1-D float array.
        b: 1-D float array.

    Returns:
        Scalar cosine similarity in [-1, 1].
    """
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def _extract_persistences(hdb: HDBSCAN, n_clusters: int) -> list[float]:
    """Pull per-cluster persistence from the HDBSCAN condensed tree.

    Falls back to 0.0 for every cluster if the attribute is unavailable.

    Args:
        hdb: A fitted HDBSCAN instance.
        n_clusters: Number of real clusters found.

    Returns:
        List of ``n_clusters`` float values.
    """
    if n_clusters == 0:
        return []
    try:
        # Guard: condensed_tree_ is not available with all HDBSCAN parameter
        # combinations (e.g., small datasets, certain metric/selection combos).
        if not hasattr(hdb, "condensed_tree_"):
            return [0.0] * n_clusters
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
    except Exception as exc:
        logger.warning("Persistence extraction failed (n_clusters=%d): %s", n_clusters, exc)
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


def spectral_split(
    embeddings: np.ndarray,
    k_range: tuple[int, ...] = SPECTRAL_K_RANGE,
    silhouette_gate: float = SPECTRAL_SILHOUETTE_GATE,
) -> tuple[ClusterResult | None, dict[int, float]]:
    """Split embeddings into sub-clusters using spectral clustering.

    Unlike HDBSCAN, spectral clustering finds sub-communities by analyzing
    the similarity graph structure — not density — which works for
    uniform-density embedding spaces that HDBSCAN cannot separate.

    Tries each k in *k_range*, selects the partition with the best
    silhouette score (rescaled to [0, 1]).  Rejects partitions where
    any group has fewer than ``SPECTRAL_MIN_GROUP_SIZE`` members.

    Returns:
        Tuple of (ClusterResult or None, dict mapping k → rescaled
        silhouette for ALL k values attempted). The dict is always populated
        even when the result is None — needed for observability.
    """
    n = embeddings.shape[0]
    min_required = max(k_range) * SPECTRAL_MIN_GROUP_SIZE
    all_silhouettes: dict[int, float] = {}
    if n < min_required:
        logger.debug(
            "spectral_split: N=%d < min_required=%d, skipping", n, min_required,
        )
        return None, all_silhouettes

    # Cosine similarity matrix (L2-normalized → dot product = cosine)
    sim_matrix = embeddings @ embeddings.T
    sim_matrix = np.clip(sim_matrix, 0, None)  # spectral requires non-negative

    best_k: int | None = None
    best_sil: float = -1.0
    best_labels: np.ndarray | None = None

    for k in k_range:
        if n < k * SPECTRAL_MIN_GROUP_SIZE:
            continue
        try:
            sc = SpectralClustering(
                n_clusters=k,
                affinity="precomputed",
                random_state=42,
                assign_labels="kmeans",
            )
            labels = sc.fit_predict(sim_matrix)
        except Exception as exc:
            logger.warning("SpectralClustering failed for k=%d: %s", k, exc)
            continue

        # Reject if any group is too small
        group_sizes = [int((labels == cid).sum()) for cid in range(k)]
        if any(s < SPECTRAL_MIN_GROUP_SIZE for s in group_sizes):
            all_silhouettes[k] = -1.0  # rejected for group size
            continue

        # Silhouette score (cosine metric, raw range [-1, 1])
        try:
            raw_sil = silhouette_score(embeddings, labels, metric="cosine")
            rescaled = (raw_sil + 1.0) / 2.0
        except Exception:
            all_silhouettes[k] = -1.0  # silhouette computation failed
            continue

        all_silhouettes[k] = round(rescaled, 4)
        if rescaled > best_sil:
            best_sil = rescaled
            best_k = k
            best_labels = labels

    if best_labels is None or best_sil < silhouette_gate:
        logger.debug(
            "spectral_split: no valid partition (best_sil=%.4f, gate=%.4f)",
            best_sil, silhouette_gate,
        )
        return None, all_silhouettes

    # Compute L2-normalized centroids
    centroids: list[np.ndarray] = []
    for cid in range(best_k):
        member_vecs = embeddings[best_labels == cid]
        mean_vec = member_vecs.mean(axis=0).astype(np.float32)
        norm = np.linalg.norm(mean_vec)
        if norm > 0:
            mean_vec = mean_vec / norm
        centroids.append(mean_vec)

    return ClusterResult(
        labels=best_labels,
        n_clusters=best_k,
        noise_count=0,
        persistences=[best_sil] * best_k,
        centroids=centroids,
        silhouette=best_sil,
    ), all_silhouettes


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
            silhouette=0.0,
        )

    # Stack into (N, D) matrix and L2-normalise.
    mat = np.stack(embeddings, axis=0).astype(np.float32)
    mat = _l2_normalize(mat)

    hdb = HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=max(1, min_cluster_size - 1),  # Lowered: reduces noise rate on small datasets
        metric="euclidean",
        cluster_selection_method="eom",
        copy=True,  # Explicit to silence sklearn >=1.10 FutureWarning
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

    # --- Silhouette score: cluster validity metric ---
    # Requires >= 2 clusters and >= 2 non-noise points.
    # Rescale from [-1, 1] to [0, 1] for Q_system compatibility.
    sil = 0.0
    non_noise_mask = labels >= 0
    if n_clusters >= 2 and non_noise_mask.sum() >= 2:
        try:
            raw_sil = silhouette_score(mat[non_noise_mask], labels[non_noise_mask])
            sil = (raw_sil + 1.0) / 2.0
        except Exception:
            sil = 0.0

    return ClusterResult(
        labels=labels,
        n_clusters=n_clusters,
        noise_count=noise_count,
        persistences=persistences,
        centroids=centroids,
        silhouette=sil,
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
        Minimum pairwise cosine distance, or ``1.0`` for fewer than two
        centroids (perfect separation — no siblings to conflict with).
    """
    if len(centroids) < 2:
        return 1.0

    mat = np.stack(centroids, axis=0).astype(np.float32)
    mat = _l2_normalize(mat)
    sim_matrix = mat @ mat.T  # (K, K), similarities in [−1, 1]
    dist_matrix = 1.0 - sim_matrix  # distances

    # Ignore the diagonal (self-distance = 0)
    np.fill_diagonal(dist_matrix, np.inf)
    return float(dist_matrix.min())


def compute_mean_separation(centroids: list[np.ndarray]) -> float:
    """Mean of per-centroid minimum cosine distances.

    For each centroid, finds the minimum distance to any other centroid,
    then returns the mean of those values.  This reflects the typical
    separation quality rather than the single worst-case pair.

    Mirrors the per-node separation logic in
    ``TaxonomyEngine._update_per_node_separation`` which Q_system
    averages via ``statistics.mean(separations)``.

    Args:
        centroids: List of 1-D float32 centroid arrays.

    Returns:
        Mean of per-centroid min distances, or ``1.0`` for fewer than
        two centroids (perfect separation — no siblings to conflict with).
    """
    if len(centroids) < 2:
        return 1.0

    mat = np.stack(centroids, axis=0).astype(np.float32)
    mat = _l2_normalize(mat)
    sim_matrix = mat @ mat.T  # (K, K), similarities in [−1, 1]
    dist_matrix = 1.0 - sim_matrix  # distances

    # Ignore the diagonal (self-distance = 0)
    np.fill_diagonal(dist_matrix, np.inf)

    # Per-centroid minimum distance, then take the mean
    per_centroid_min = dist_matrix.min(axis=1)
    return float(per_centroid_min.mean())
