"""Dual-backend embedding index for PromptCluster centroids.

Thread-safe: mutations gated by asyncio.Lock. Reads operate on immutable
snapshots (copy-on-write). Auto-selects HNSW when cluster count >= 1000.

Backend abstraction: _NumpyBackend (default, < 1000 clusters) and
_HnswBackend (>= 1000 clusters) share a common interface. Stable label
mapping via _id_to_label dict prevents index corruption on remove() —
tombstones replace the old pop-and-shift approach.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Backend implementations
# ---------------------------------------------------------------------------


class _NumpyBackend:
    """Dense numpy matrix backend — O(N) search via matmul."""

    def __init__(self, dim: int):
        self._dim = dim
        self._matrix: np.ndarray = np.empty((0, dim), dtype=np.float32)

    def build(self, matrix: np.ndarray, count: int) -> None:
        self._matrix = matrix.copy()

    def add(self, label: int, embedding: np.ndarray) -> None:
        if label < self._matrix.shape[0]:
            self._matrix[label] = embedding
        else:
            padding = label + 1 - self._matrix.shape[0]
            self._matrix = np.vstack([
                self._matrix,
                np.zeros((padding, self._dim), dtype=np.float32),
            ])
            self._matrix[label] = embedding

    def remove_label(self, label: int) -> None:
        if label < self._matrix.shape[0]:
            self._matrix[label] = 0.0  # zero out row (tombstone)

    def search(
        self,
        query: np.ndarray,
        k: int,
        threshold: float,
        filter_fn,
    ) -> list[tuple[int, float]]:
        """Return up to k (label, score) pairs above threshold."""
        if self._matrix.shape[0] == 0:
            return []

        scores = self._matrix @ query  # (n,)

        if filter_fn is not None:
            for i in range(len(scores)):
                if not filter_fn(i):
                    scores[i] = -1.0

        mask = scores >= threshold
        if not mask.any():
            return []

        valid_indices = np.where(mask)[0]
        valid_scores = scores[valid_indices]

        if len(valid_indices) <= k:
            order = np.argsort(-valid_scores)
        else:
            partition_idx = np.argpartition(-valid_scores, k)[:k]
            order = partition_idx[np.argsort(-valid_scores[partition_idx])]

        return [(int(valid_indices[i]), float(scores[int(valid_indices[i])])) for i in order]


class _HnswBackend:
    """HNSW backend via hnswlib — O(log N) search."""

    def __init__(self, dim: int):
        self._dim = dim
        self._index = None

    def build(self, matrix: np.ndarray, count: int) -> None:
        import hnswlib

        self._index = hnswlib.Index(space="cosine", dim=self._dim)
        max_elements = max(count * 2, 1000)
        self._index.init_index(
            max_elements=max_elements,
            ef_construction=200,
            M=16,
            allow_replace_deleted=True,
        )
        self._index.set_ef(50)
        if count > 0:
            self._index.add_items(matrix[:count], ids=np.arange(count))

    def add(self, label: int, embedding: np.ndarray) -> None:
        if self._index is None:
            return
        if label >= self._index.get_max_elements():
            self._index.resize_index(
                max(label * 2, self._index.get_max_elements() * 2)
            )
        self._index.add_items(
            embedding.reshape(1, -1),
            ids=np.array([label]),
            replace_deleted=True,
        )

    def remove_label(self, label: int) -> None:
        if self._index is None:
            return
        try:
            self._index.mark_deleted(label)
        except RuntimeError:
            pass

    def search(
        self,
        query: np.ndarray,
        k: int,
        threshold: float,
        filter_fn,
    ) -> list[tuple[int, float]]:
        """Return up to k (label, score) pairs above threshold."""
        if self._index is None or self._index.get_current_count() == 0:
            return []
        effective_k = min(k * 3, self._index.get_current_count())
        try:
            labels, distances = self._index.knn_query(
                query.reshape(1, -1), k=effective_k, filter=filter_fn,
            )
        except RuntimeError:
            return []
        results = []
        for label, dist in zip(labels[0], distances[0]):
            sim = 1.0 - dist
            if sim >= threshold:
                results.append((int(label), float(sim)))
        return sorted(results, key=lambda x: -x[1])[:k]


# ---------------------------------------------------------------------------
# IndexSnapshot dataclass
# ---------------------------------------------------------------------------


@dataclass
class IndexSnapshot:
    """Frozen copy of EmbeddingIndex state for rollback support.

    Used by the warm-path speculative lifecycle to restore the index if the
    quality gate rolls back its DB transaction.
    """

    matrix: np.ndarray  # deep copy of _matrix (N x dim, float32)
    ids: list[str | None]  # copy of _ids (cluster UUIDs, None = tombstoned)
    project_ids: list[str | None] = field(default_factory=list)  # ADR-005
    id_to_label: dict[str, int] = field(default_factory=dict)
    next_label: int = 0
    tombstones: set[int] = field(default_factory=set)


# ---------------------------------------------------------------------------
# EmbeddingIndex — public API unchanged
# ---------------------------------------------------------------------------


class EmbeddingIndex:
    """In-memory embedding search index for PromptCluster centroids.

    Dual-backend: numpy (default) or HNSW (>= 1000 clusters on rebuild).
    Stable label mapping prevents index corruption from remove().
    """

    def __init__(self, dim: int = 384):
        self._dim = dim
        self._lock = asyncio.Lock()
        self._backend: _NumpyBackend | _HnswBackend = _NumpyBackend(dim=dim)
        self._ids: list[str | None] = []
        self._project_ids: list[str | None] = []
        self._id_to_label: dict[str, int] = {}
        self._next_label: int = 0
        self._tombstones: set[int] = set()
        self._last_rebuild_matrix: np.ndarray = np.empty((0, dim), dtype=np.float32)

    # -- Backward-compatible _matrix property for engine.py reset + tests --

    @property
    def _matrix(self) -> np.ndarray:
        """Backward-compatible access to the underlying numpy matrix.

        Only meaningful for _NumpyBackend. Returns the last rebuild matrix
        for _HnswBackend.
        """
        if isinstance(self._backend, _NumpyBackend):
            return self._backend._matrix
        return self._last_rebuild_matrix

    @_matrix.setter
    def _matrix(self, value: np.ndarray) -> None:
        """Backward-compatible setter — used by engine.py reset_taxonomy.

        Resets internal state to match a fresh empty matrix.
        """
        if isinstance(self._backend, _NumpyBackend):
            self._backend._matrix = value
        self._last_rebuild_matrix = value
        # When setting to empty, also clear label mapping and project_ids
        if value.shape[0] == 0:
            self._id_to_label.clear()
            self._next_label = 0
            self._tombstones.clear()
            self._project_ids = []

    @property
    def size(self) -> int:
        return len(self._id_to_label)

    # -- pairwise_similarities --

    def pairwise_similarities(
        self, threshold: float = 0.50, k: int = 100,
    ) -> list[tuple[str, str, float]]:
        """All pairwise cosine similarities above threshold. Lock-free.

        Returns list of (id_a, id_b, score) sorted descending, truncated to k.
        Each pair appears once (upper triangle only).
        """
        if not isinstance(self._backend, _NumpyBackend):
            logger.warning("pairwise_similarities not available with HNSW backend")
            return []

        matrix = self._backend._matrix  # snapshot reference
        ids = self._ids
        tombstones = self._tombstones

        # Build compact list of active (non-tombstoned) labels
        active_labels = []
        active_ids = []
        for label in range(len(ids)):
            if label not in tombstones and ids[label] is not None:
                active_labels.append(label)
                active_ids.append(ids[label])

        n = len(active_labels)
        if n < 2:
            return []
        if n > 2000:
            logger.warning("pairwise_similarities skipped: index too large (%d)", n)
            return []

        # Extract compact matrix of active rows
        if matrix.shape[0] == 0:
            return []
        compact_matrix = matrix[active_labels]

        # (n, n) cosine similarity — rows are L2-normalized
        scores = compact_matrix @ compact_matrix.T

        # Zero diagonal
        np.fill_diagonal(scores, 0.0)

        # Upper triangle only (avoid duplicates)
        rows, cols = np.triu_indices(n, k=1)
        upper_scores = scores[rows, cols]

        # Filter by threshold
        mask = upper_scores >= threshold
        if not mask.any():
            return []

        valid_rows = rows[mask]
        valid_cols = cols[mask]
        valid_scores = upper_scores[mask]

        # Sort descending, truncate to k
        if len(valid_scores) <= k:
            order = np.argsort(-valid_scores)
        else:
            partition_idx = np.argpartition(-valid_scores, k)[:k]
            order = partition_idx[np.argsort(-valid_scores[partition_idx])]

        return [
            (active_ids[int(valid_rows[i])], active_ids[int(valid_cols[i])], float(valid_scores[i]))
            for i in order
        ]

    # -- search --

    def search(
        self, embedding: np.ndarray, k: int = 5, threshold: float = 0.72,
        project_filter: str | None = None,
    ) -> list[tuple[str, float]]:
        """Top-k cosine search. Lock-free — reads current snapshot.

        Args:
            project_filter: If set, only include vectors tagged with this project_id.

        Returns list of (cluster_id, cosine_similarity) sorted descending.
        """
        ids = self._ids
        project_ids = self._project_ids
        tombstones = self._tombstones

        if not self._id_to_label:
            return []

        # Normalize query
        query = embedding.astype(np.float32).ravel()
        norm = np.linalg.norm(query)
        if norm < 1e-9:
            return []
        query = query / norm

        # Build filter function for tombstones + project filter
        filter_fn = None
        if project_filter is not None or tombstones:
            def filter_fn(label: int) -> bool:
                if label in tombstones:
                    return False
                if project_filter is not None:
                    if label < len(project_ids):
                        return project_ids[label] == project_filter
                    return False
                return True

        raw_results = self._backend.search(query, k, threshold, filter_fn)

        if not raw_results:
            # Diagnostic: log the best score even when below threshold
            _diag = self._backend.search(query, 1, -1.0, filter_fn)
            if _diag:
                _best_label, _best_score = _diag[0]
                _best_id = ids[_best_label] if _best_label < len(ids) else "?"
                logger.info(
                    "Embedding search miss: top_score=%.3f (threshold=%.2f, "
                    "index_size=%d, best_id=%s)",
                    _best_score, threshold, self.size,
                    _best_id[:8] if _best_id and _best_id != "?" else "?",
                )

        return [
            (ids[label], score)
            for label, score in raw_results
            if label < len(ids) and ids[label] is not None
        ]

    # -- upsert --

    async def upsert(
        self, cluster_id: str, embedding: np.ndarray,
        project_id: str | None = None,
    ) -> None:
        """Insert or update a single centroid."""
        emb = embedding.astype(np.float32).ravel()
        norm = np.linalg.norm(emb)
        if norm < 1e-9:
            return
        emb = emb / norm

        async with self._lock:
            if cluster_id in self._id_to_label:
                # Update existing
                label = self._id_to_label[cluster_id]
                self._project_ids[label] = project_id
            else:
                # Assign new label
                label = self._next_label
                self._next_label += 1
                self._id_to_label[cluster_id] = label
                # Extend sparse arrays to fit new label
                while len(self._ids) <= label:
                    self._ids.append(None)
                    self._project_ids.append(None)
                self._ids[label] = cluster_id
                self._project_ids[label] = project_id
            self._tombstones.discard(label)
            self._backend.add(label, emb)

    # -- remove --

    async def remove(self, cluster_id: str) -> None:
        """Remove a centroid from the index via tombstoning."""
        async with self._lock:
            if cluster_id not in self._id_to_label:
                return
            label = self._id_to_label.pop(cluster_id)
            self._tombstones.add(label)
            if label < len(self._ids):
                self._ids[label] = None
            if label < len(self._project_ids):
                self._project_ids[label] = None
            self._backend.remove_label(label)

    # -- rebuild --

    async def rebuild(
        self, centroids: dict[str, np.ndarray],
        project_ids: dict[str, str | None] | None = None,
    ) -> None:
        """Full rebuild from scratch (cold path). Acquires lock."""
        if not centroids:
            async with self._lock:
                self._ids = []
                self._project_ids = []
                self._id_to_label = {}
                self._next_label = 0
                self._tombstones.clear()
                self._last_rebuild_matrix = np.empty((0, self._dim), dtype=np.float32)
                self._backend = _NumpyBackend(dim=self._dim)
                self._backend.build(np.empty((0, self._dim), dtype=np.float32), 0)
            return

        new_ids = list(centroids.keys())
        p_ids = [project_ids.get(cid) if project_ids else None for cid in new_ids]
        rows = []
        for cid in new_ids:
            emb = centroids[cid].astype(np.float32).ravel()
            norm = np.linalg.norm(emb)
            if norm > 1e-9:
                rows.append(emb / norm)
            else:
                rows.append(np.zeros(self._dim, dtype=np.float32))
        matrix = np.vstack(rows)

        async with self._lock:
            from app.services.taxonomy._constants import HNSW_CLUSTER_THRESHOLD

            if len(new_ids) >= HNSW_CLUSTER_THRESHOLD:
                if not isinstance(self._backend, _HnswBackend):
                    self._backend = _HnswBackend(dim=self._dim)
                    logger.info(
                        "EmbeddingIndex: switched to HNSW backend (%d centroids)",
                        len(new_ids),
                    )
            else:
                if not isinstance(self._backend, _NumpyBackend):
                    self._backend = _NumpyBackend(dim=self._dim)

            self._ids = list(new_ids)
            self._project_ids = p_ids
            self._id_to_label = {cid: i for i, cid in enumerate(new_ids)}
            self._next_label = len(new_ids)
            self._tombstones.clear()
            self._last_rebuild_matrix = matrix.copy()
            self._backend.build(matrix, len(new_ids))

        logger.info("EmbeddingIndex rebuilt: %d centroids", len(new_ids))

    # -- snapshot / restore --

    async def snapshot(self) -> IndexSnapshot:
        """Return a frozen copy of the current index state.

        Acquires the lock to prevent concurrent mutations during the copy.
        The returned snapshot is fully independent.
        """
        async with self._lock:
            # Get the matrix to snapshot
            if isinstance(self._backend, _NumpyBackend):
                matrix = self._backend._matrix.copy()
            else:
                matrix = self._last_rebuild_matrix.copy()

            return IndexSnapshot(
                matrix=matrix,
                ids=list(self._ids),
                project_ids=list(self._project_ids),
                id_to_label=dict(self._id_to_label),
                next_label=self._next_label,
                tombstones=set(self._tombstones),
            )

    async def restore(self, snapshot: IndexSnapshot) -> None:
        """Atomically swap the index state back to a previously captured snapshot.

        Rebuilds the backend from the snapshot matrix.
        """
        async with self._lock:
            self._ids = list(snapshot.ids)
            self._project_ids = (
                list(snapshot.project_ids)
                if snapshot.project_ids
                else [None] * len(snapshot.ids)
            )

            # Restore label mapping if present, otherwise rebuild from ids
            if snapshot.id_to_label:
                self._id_to_label = dict(snapshot.id_to_label)
                self._next_label = snapshot.next_label
                self._tombstones = set(snapshot.tombstones)
            else:
                # Legacy snapshot without label mapping — rebuild from ids
                self._id_to_label = {
                    cid: i for i, cid in enumerate(self._ids)
                    if cid is not None
                }
                self._next_label = len(self._ids)
                self._tombstones = {
                    i for i, cid in enumerate(self._ids) if cid is None
                }

            matrix = snapshot.matrix.copy()
            self._last_rebuild_matrix = matrix.copy()

            # Rebuild backend from matrix. Always restores to numpy for simplicity.
            # At scale (3K+ clusters using HNSW), this means warm-path rollbacks
            # temporarily degrade to O(N) search until the next cold-path rebuild
            # restores HNSW. Acceptable: warm cycles take seconds already, and
            # cold rebuilds run every few hours. See spec Section 7 for discussion.
            self._backend = _NumpyBackend(dim=self._dim)
            self._backend.build(matrix, matrix.shape[0])

    # -- reset --

    async def reset(self) -> None:
        """Clear all state. Replaces direct _matrix/_ids access from engine.py."""
        async with self._lock:
            self._ids = []
            self._project_ids = []
            self._id_to_label = {}
            self._next_label = 0
            self._tombstones.clear()
            self._last_rebuild_matrix = np.empty((0, self._dim), dtype=np.float32)
            self._backend = _NumpyBackend(dim=self._dim)
            self._backend.build(np.empty((0, self._dim), dtype=np.float32), 0)

    # -- cache persistence --

    async def save_cache(self, cache_path: Path) -> None:
        """Serialize index to disk for fast startup recovery.

        Saves compacted data (tombstoned entries excluded).
        """
        import pickle

        async with self._lock:
            # Compact: only active entries
            active_ids = []
            active_pids = []
            active_rows = []
            matrix = (
                self._backend._matrix
                if isinstance(self._backend, _NumpyBackend)
                else self._last_rebuild_matrix
            )

            for cid, label in self._id_to_label.items():
                active_ids.append(cid)
                active_pids.append(
                    self._project_ids[label] if label < len(self._project_ids) else None
                )
                if label < matrix.shape[0]:
                    active_rows.append(matrix[label])
                else:
                    active_rows.append(np.zeros(self._dim, dtype=np.float32))

            if active_rows:
                compact_matrix = np.vstack(active_rows)
            else:
                compact_matrix = np.empty((0, self._dim), dtype=np.float32)

            data = {
                "matrix": compact_matrix,
                "ids": active_ids,
                "project_ids": active_pids,
            }

        try:
            with open(cache_path, "wb") as f:
                pickle.dump(data, f)
            logger.info(
                "EmbeddingIndex cache saved: %d entries → %s",
                len(data["ids"]),
                cache_path,
            )
        except Exception as exc:
            logger.warning("EmbeddingIndex cache save failed: %s", exc)

    async def load_cache(self, cache_path: Path, max_age_seconds: int = 3600) -> bool:
        """Load index from disk cache if fresh. Returns True if loaded.

        Legacy caches (without label mapping) are loaded via rebuild().
        """
        import pickle

        if not cache_path.exists():
            return False
        age = time.time() - cache_path.stat().st_mtime
        if age > max_age_seconds:
            logger.info(
                "EmbeddingIndex cache stale (%.0fs old, max %ds)",
                age, max_age_seconds,
            )
            return False

        try:
            with open(cache_path, "rb") as f:
                data = pickle.load(f)  # noqa: S301

            matrix = data["matrix"]
            ids = data["ids"]
            p_ids = data.get("project_ids", [None] * len(ids))

            # Rebuild via centroids dict for clean label mapping
            centroids = {cid: matrix[i] for i, cid in enumerate(ids)}
            p_map = {cid: p_ids[i] for i, cid in enumerate(ids)}
            await self.rebuild(centroids, project_ids=p_map)

            logger.info(
                "EmbeddingIndex loaded from cache: %d entries (%.0fs old)",
                len(ids), age,
            )
            return True
        except Exception as exc:
            logger.warning("EmbeddingIndex cache load failed: %s", exc)
            return False
