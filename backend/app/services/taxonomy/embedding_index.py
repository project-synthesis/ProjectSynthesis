"""In-memory numpy cosine search index for PromptCluster centroids.

Thread-safe: mutations gated by asyncio.Lock. Reads operate on immutable
snapshots (copy-on-write). At 2000 clusters (384-dim), search is ~3ms.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class IndexSnapshot:
    """Frozen copy of EmbeddingIndex state for rollback support.

    Used by the warm-path speculative lifecycle to restore the index if the
    quality gate rolls back its DB transaction.
    """

    matrix: np.ndarray  # deep copy of _matrix (N x dim, float32)
    ids: list[str]       # copy of _ids (cluster UUIDs)
    project_ids: list[str | None] = field(default_factory=list)  # ADR-005


class EmbeddingIndex:
    """In-memory embedding search index for PromptCluster centroids."""

    def __init__(self, dim: int = 384):
        self._dim = dim
        self._lock = asyncio.Lock()
        # Immutable snapshots — replaced atomically on mutation
        self._matrix: np.ndarray = np.empty((0, dim), dtype=np.float32)
        self._ids: list[str] = []
        self._project_ids: list[str | None] = []  # ADR-005: parallel array

    @property
    def size(self) -> int:
        return len(self._ids)

    def pairwise_similarities(
        self, threshold: float = 0.50, k: int = 100
    ) -> list[tuple[str, str, float]]:
        """All pairwise cosine similarities above threshold. Lock-free — reads current snapshot.

        Returns list of (id_a, id_b, score) sorted descending, truncated to k.
        Each pair appears once (upper triangle only).
        """
        matrix = self._matrix  # snapshot reference
        ids = self._ids
        n = len(ids)
        if n < 2:
            return []
        if n > 2000:
            logger.warning("pairwise_similarities skipped: index too large (%d)", n)
            return []

        # (n, n) cosine similarity — rows are L2-normalized
        scores = matrix @ matrix.T

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
            (ids[int(valid_rows[i])], ids[int(valid_cols[i])], float(valid_scores[i]))
            for i in order
        ]

    def search(
        self, embedding: np.ndarray, k: int = 5, threshold: float = 0.72,
        project_filter: str | None = None,
    ) -> list[tuple[str, float]]:
        """Top-k cosine search. Lock-free — reads current snapshot.

        Args:
            project_filter: If set, only include vectors tagged with this project_id.

        Returns list of (cluster_id, cosine_similarity) sorted descending.
        """
        matrix = self._matrix  # snapshot reference
        ids = self._ids
        if len(ids) == 0:
            return []

        # Normalize query
        query = embedding.astype(np.float32).ravel()
        norm = np.linalg.norm(query)
        if norm < 1e-9:
            return []
        query = query / norm

        # Cosine similarity via matmul (matrix rows are L2-normalized)
        scores = matrix @ query  # (n,)

        # ADR-005: project filter mask
        if project_filter is not None:
            project_ids = self._project_ids
            if project_ids:
                project_mask = np.array(
                    [pid == project_filter for pid in project_ids],
                    dtype=bool,
                )
                scores = np.where(project_mask, scores, -1.0)

        # Filter by threshold
        mask = scores >= threshold
        if not mask.any():
            top_idx = int(np.argmax(scores))
            logger.info(
                "Embedding search miss: top_score=%.3f (threshold=%.2f, index_size=%d, best_id=%s)",
                float(scores[top_idx]), threshold, len(ids), ids[top_idx][:8],
            )
            return []

        # Top-k via argpartition
        valid_indices = np.where(mask)[0]
        valid_scores = scores[valid_indices]

        if len(valid_indices) <= k:
            top_indices = valid_indices[np.argsort(-valid_scores)]
        else:
            partition_idx = np.argpartition(-valid_scores, k)[:k]
            top_indices = valid_indices[partition_idx]
            top_scores = scores[top_indices]
            top_indices = top_indices[np.argsort(-top_scores)]

        return [(ids[i], float(scores[i])) for i in top_indices]

    async def upsert(
        self, cluster_id: str, embedding: np.ndarray,
        project_id: str | None = None,
    ) -> None:
        """Insert or update a single centroid. Creates new snapshot."""
        emb = embedding.astype(np.float32).ravel()
        norm = np.linalg.norm(emb)
        if norm < 1e-9:
            return
        emb = emb / norm

        async with self._lock:
            ids = list(self._ids)
            project_ids = list(self._project_ids)
            if cluster_id in ids:
                idx = ids.index(cluster_id)
                matrix = self._matrix.copy()
                matrix[idx] = emb
                project_ids[idx] = project_id
            else:
                ids.append(cluster_id)
                project_ids.append(project_id)
                if self._matrix.shape[0] == 0:
                    matrix = emb.reshape(1, -1)
                else:
                    matrix = np.vstack([self._matrix, emb.reshape(1, -1)])

            # Atomic swap
            self._matrix = matrix
            self._ids = ids
            self._project_ids = project_ids

    async def remove(self, cluster_id: str) -> None:
        """Remove a centroid from the index. Creates new snapshot."""
        async with self._lock:
            if cluster_id not in self._ids:
                return
            ids = list(self._ids)
            project_ids = list(self._project_ids)
            idx = ids.index(cluster_id)
            ids.pop(idx)
            project_ids.pop(idx)
            matrix = np.delete(self._matrix, idx, axis=0)

            self._matrix = matrix
            self._ids = ids
            self._project_ids = project_ids

    async def rebuild(
        self, centroids: dict[str, np.ndarray],
        project_ids: dict[str, str | None] | None = None,
    ) -> None:
        """Full rebuild from scratch (cold path). Acquires lock."""
        if not centroids:
            async with self._lock:
                self._matrix = np.empty((0, self._dim), dtype=np.float32)
                self._ids = []
                self._project_ids = []
            return

        ids = list(centroids.keys())
        rows = []
        for cid in ids:
            emb = centroids[cid].astype(np.float32).ravel()
            norm = np.linalg.norm(emb)
            if norm > 1e-9:
                rows.append(emb / norm)
            else:
                rows.append(np.zeros(self._dim, dtype=np.float32))

        matrix = np.vstack(rows)
        p_ids = [project_ids.get(cid) if project_ids else None for cid in ids]

        async with self._lock:
            self._matrix = matrix
            self._ids = ids
            self._project_ids = p_ids

        logger.info("EmbeddingIndex rebuilt: %d centroids", len(ids))

    async def snapshot(self) -> IndexSnapshot:
        """Return a frozen copy of the current index state.

        Acquires the lock to prevent concurrent mutations during the copy.
        The returned snapshot is fully independent — subsequent mutations to
        the index do not affect it.
        """
        async with self._lock:
            return IndexSnapshot(
                matrix=self._matrix.copy(),
                ids=list(self._ids),
                project_ids=list(self._project_ids),
            )

    async def restore(self, snapshot: IndexSnapshot) -> None:
        """Atomically swap the index state back to a previously captured snapshot.

        Acquires the lock to prevent concurrent readers from observing a
        partially-restored state. Safe to call from within a rolled-back
        DB transaction handler.
        """
        async with self._lock:
            self._matrix = snapshot.matrix.copy()
            self._ids = list(snapshot.ids)
            self._project_ids = list(snapshot.project_ids) if snapshot.project_ids else [None] * len(snapshot.ids)

    async def save_cache(self, cache_path: Path) -> None:
        """Serialize index to disk for fast startup recovery."""
        import pickle

        async with self._lock:
            data = {
                "matrix": self._matrix,
                "ids": list(self._ids),
                "project_ids": list(self._project_ids),
            }
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(data, f)
            logger.info("EmbeddingIndex cache saved: %d entries → %s", len(data["ids"]), cache_path)
        except Exception as exc:
            logger.warning("EmbeddingIndex cache save failed: %s", exc)

    async def load_cache(self, cache_path: Path, max_age_seconds: int = 3600) -> bool:
        """Load index from disk cache if fresh. Returns True if loaded."""
        import pickle

        if not cache_path.exists():
            return False
        age = time.time() - cache_path.stat().st_mtime
        if age > max_age_seconds:
            logger.info("EmbeddingIndex cache stale (%.0fs old, max %ds)", age, max_age_seconds)
            return False
        try:
            with open(cache_path, "rb") as f:
                data = pickle.load(f)  # noqa: S301
            async with self._lock:
                self._matrix = data["matrix"]
                self._ids = data["ids"]
                self._project_ids = data.get("project_ids", [None] * len(self._ids))
            logger.info("EmbeddingIndex loaded from cache: %d entries (%.0fs old)", len(self._ids), age)
            return True
        except Exception as exc:
            logger.warning("EmbeddingIndex cache load failed: %s", exc)
            return False
