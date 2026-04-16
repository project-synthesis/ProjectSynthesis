"""In-memory numpy cosine search index for per-cluster mean qualifier vectors.

Each vector represents the embedding of organic qualifier vocabulary keywords
associated with a cluster's sub-domain. L2-normalized, capturing the domain
specialization signal of a cluster. Used by Phase 2 composite query construction
to steer new optimizations toward clusters with matching qualifier vocabulary.

Thread-safe: mutations gated by asyncio.Lock. Reads operate on immutable
snapshots (copy-on-write). At 2000 clusters (384-dim), search is ~3ms.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class QualifierSnapshot:
    """Frozen copy of QualifierIndex state for rollback support.

    Used by Phase 2 composite query construction to restore the index if a
    quality gate rolls back its DB transaction.
    """

    matrix: np.ndarray  # deep copy of _matrix (N x dim, float32)
    ids: list[str]       # copy of _ids (cluster UUIDs)


class QualifierIndex:
    """In-memory cosine search index for per-cluster mean qualifier vectors.

    Vectors are L2-normalized mean qualifier keyword embeddings: the embedding
    of the organic sub-domain vocabulary keywords associated with each cluster.
    Searching with a query qualifier vector finds clusters whose domain
    specialization signal is most similar.
    """

    def __init__(self, dim: int = 384):
        self._dim = dim
        self._lock = asyncio.Lock()
        # Immutable snapshots — replaced atomically on mutation
        self._matrix: np.ndarray = np.empty((0, dim), dtype=np.float32)
        self._ids: list[str] = []

    @property
    def size(self) -> int:
        return len(self._ids)

    def search(
        self, embedding: np.ndarray, k: int = 5, threshold: float = 0.50
    ) -> list[tuple[str, float]]:
        """Top-k cosine search over qualifier vectors. Lock-free — reads current snapshot.

        Returns list of (cluster_id, cosine_similarity) sorted descending.
        Only results at or above threshold are returned.
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

        # Filter by threshold
        mask = scores >= threshold
        if not mask.any():
            top_idx = int(np.argmax(scores))
            logger.info(
                "QualifierIndex search miss: top_score=%.3f (threshold=%.2f, index_size=%d, best_id=%s)",
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

    def get_vector(self, cluster_id: str) -> np.ndarray | None:
        """Return the stored qualifier vector for a cluster, or None if absent.

        Lock-free — reads current immutable snapshot. Used by Phase 2
        build_composite_query() to retrieve the qualifier vector for a
        specific cluster by ID.
        """
        ids = self._ids  # snapshot reference
        matrix = self._matrix
        if cluster_id not in ids:
            return None
        idx = ids.index(cluster_id)
        return matrix[idx].copy()

    async def upsert(self, cluster_id: str, embedding: np.ndarray) -> None:
        """Insert or update a single qualifier vector. Creates new snapshot."""
        emb = embedding.astype(np.float32).ravel()
        norm = np.linalg.norm(emb)
        if norm < 1e-9:
            return
        emb = emb / norm

        async with self._lock:
            ids = list(self._ids)
            if cluster_id in ids:
                idx = ids.index(cluster_id)
                matrix = self._matrix.copy()
                matrix[idx] = emb
            else:
                ids.append(cluster_id)
                if self._matrix.shape[0] == 0:
                    matrix = emb.reshape(1, -1)
                else:
                    matrix = np.vstack([self._matrix, emb.reshape(1, -1)])

            # Atomic swap
            self._matrix = matrix
            self._ids = ids

    async def remove(self, cluster_id: str) -> None:
        """Remove a qualifier vector from the index. Creates new snapshot."""
        async with self._lock:
            if cluster_id not in self._ids:
                return
            ids = list(self._ids)
            idx = ids.index(cluster_id)
            ids.pop(idx)
            matrix = np.delete(self._matrix, idx, axis=0)

            self._matrix = matrix
            self._ids = ids

    async def rebuild(self, vectors: dict[str, np.ndarray]) -> None:
        """Full rebuild from scratch. Acquires lock."""
        if not vectors:
            async with self._lock:
                self._matrix = np.empty((0, self._dim), dtype=np.float32)
                self._ids = []
            return

        ids = list(vectors.keys())
        rows = []
        for cid in ids:
            emb = vectors[cid].astype(np.float32).ravel()
            norm = np.linalg.norm(emb)
            if norm > 1e-9:
                rows.append(emb / norm)
            else:
                rows.append(np.zeros(self._dim, dtype=np.float32))

        matrix = np.vstack(rows)

        async with self._lock:
            self._matrix = matrix
            self._ids = ids

        logger.info("QualifierIndex rebuilt: %d vectors", len(ids))

    async def snapshot(self) -> QualifierSnapshot:
        """Return a frozen copy of the current index state.

        Acquires the lock to prevent concurrent mutations during the copy.
        The returned snapshot is fully independent — subsequent mutations to
        the index do not affect it.
        """
        async with self._lock:
            return QualifierSnapshot(
                matrix=self._matrix.copy(),
                ids=list(self._ids),
            )

    async def restore(self, snapshot: QualifierSnapshot) -> None:
        """Atomically swap the index state back to a previously captured snapshot.

        Acquires the lock to prevent concurrent readers from observing a
        partially-restored state.
        """
        async with self._lock:
            self._matrix = snapshot.matrix.copy()
            self._ids = list(snapshot.ids)

    async def save_cache(self, cache_path: Path) -> None:
        """Serialize index to disk for fast startup recovery."""
        import pickle

        async with self._lock:
            data = {"matrix": self._matrix, "ids": list(self._ids)}
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(data, f)
            logger.info(
                "QualifierIndex cache saved: %d entries -> %s",
                len(data["ids"]),
                cache_path,
            )
        except Exception as exc:
            logger.warning("QualifierIndex cache save failed: %s", exc)

    async def load_cache(
        self, cache_path: Path, max_age_seconds: int = 3600
    ) -> bool:
        """Load index from disk cache if fresh. Returns True if loaded."""
        import pickle

        if not cache_path.exists():
            return False
        age = time.time() - cache_path.stat().st_mtime
        if age > max_age_seconds:
            logger.info(
                "QualifierIndex cache stale (%.0fs old, max %ds)",
                age,
                max_age_seconds,
            )
            return False
        try:
            with open(cache_path, "rb") as f:
                data = pickle.load(f)  # noqa: S301
            async with self._lock:
                self._matrix = data["matrix"]
                self._ids = data["ids"]
            logger.info(
                "QualifierIndex loaded from cache: %d entries (%.0fs old)",
                len(self._ids),
                age,
            )
            return True
        except Exception as exc:
            logger.warning("QualifierIndex cache load failed: %s", exc)
            return False
