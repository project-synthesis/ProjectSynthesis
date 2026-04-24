# Phase 3B Design Spec: HNSW Embedding Index

**Date:** 2026-04-08
**ADR:** [ADR-005](../adr/ADR-005-taxonomy-scaling-architecture.md) (Phase 3, item 2 — Section 5)
**Depends on:** Phase 1 (EmbeddingIndex project_filter, _project_ids array), Phase 2A (project_ids populated on vectors)
**Status:** Shipped but dormant. `EmbeddingIndex` now ships with both `_NumpyBackend` (primary) and `_HnswBackend` (trigger-gated at `HNSW_CLUSTER_THRESHOLD=1000`) with automatic fallback to numpy on HNSW failure. Trigger condition — ≥1000 clusters sustained across warm cycles — has not been reached at current scale, so the numpy backend is always active in production. Large-corpus stress validation is Deferred on the ROADMAP.

## Problem

`EmbeddingIndex.search()` uses numpy brute-force cosine: O(N) where N = number of cluster centroids. At 500 clusters this is ~1ms (fine). At 3,000+ clusters, search exceeds 50ms per call. The hot path calls `search()` on every optimization, and pattern injection calls it per query. At scale, this becomes a latency bottleneck.

## Design Overview

```
EmbeddingIndex (embedding_index.py) — public API unchanged
    |
    +-- _backend: NumpyBackend | HnswBackend  (auto-selected on rebuild)
    |
    +-- _ids: list[str]              )
    +-- _project_ids: list[str|None] ) managed by EmbeddingIndex, not backend
    +-- _id_to_label: dict[str, int] ) stable label mapping for HNSW
    |
    +-- NumpyBackend
    |   +-- _matrix: np.ndarray
    |   +-- search(), build(), add(), remove_label()
    |
    +-- HnswBackend
        +-- _index: hnswlib.Index
        +-- search(), build(), add(), remove_label()
```

**Key design decision:** The EmbeddingIndex maintains a stable `_id_to_label: dict[str, int]` mapping between cluster_id strings and integer labels used by hnswlib. The `_ids` list is NOT compacted on remove — instead, removed entries are tombstoned (set to None). This prevents the index-shifting problem where `_ids.pop()` would desynchronize positional indices from HNSW labels.

## 1. Stable Label Mapping

### The problem with _ids.pop()

Current `remove()` does `_ids.pop(idx)` which shifts all subsequent indices. HNSW labels are permanent — `mark_deleted(label)` does not renumber other labels. After a pop, `_ids[i]` no longer corresponds to HNSW label `i` for all `i > idx`.

### Solution: tombstone + compaction

Instead of popping, mark the slot as dead:

```python
# In EmbeddingIndex
self._id_to_label: dict[str, int] = {}  # cluster_id -> stable int label
self._next_label: int = 0               # monotonically increasing label counter
self._tombstones: set[int] = set()      # labels that have been removed
```

**upsert():**
```python
if cluster_id in self._id_to_label:
    label = self._id_to_label[cluster_id]
    # Update in-place — same label
else:
    label = self._next_label
    self._next_label += 1
    self._id_to_label[cluster_id] = label
    self._ids.append(cluster_id)  # append, never insert
    self._project_ids.append(project_id)

self._backend.add(label, normalized_embedding)
```

**remove():**
```python
if cluster_id not in self._id_to_label:
    return
label = self._id_to_label.pop(cluster_id)
self._tombstones.add(label)
self._backend.remove_label(label)
# Do NOT pop from _ids — the label slot is tombstoned
```

**search() result translation:**
```python
# Backend returns list of (label, score)
results = self._backend.search(query, k, threshold, project_filter_fn)
# Translate labels to cluster_ids, skipping tombstones
return [(self._ids[label], score) for label, score in results
        if label not in self._tombstones]
```

**Compaction on rebuild():**
```python
# rebuild() resets all mappings — fresh start
self._id_to_label = {cid: i for i, cid in enumerate(new_ids)}
self._next_label = len(new_ids)
self._tombstones.clear()
self._ids = list(new_ids)
self._project_ids = list(new_project_ids)
self._backend.build(matrix, len(new_ids))
```

Compaction happens naturally on every cold path rebuild (typically every few hours). Between rebuilds, tombstones accumulate but are filtered in search results.

## 2. Backend Protocol

```python
class EmbeddingBackend(Protocol):
    """Internal backend for vector operations."""

    def build(self, matrix: np.ndarray, count: int) -> None:
        """Full rebuild from matrix. Called by EmbeddingIndex.rebuild()."""
        ...

    def add(self, label: int, embedding: np.ndarray) -> None:
        """Add or replace a single vector at the given label."""
        ...

    def remove_label(self, label: int) -> None:
        """Mark a label as deleted."""
        ...

    def search(
        self, query: np.ndarray, k: int, threshold: float,
        filter_fn: Callable[[int], bool] | None,
    ) -> list[tuple[int, float]]:
        """Return (label, score) pairs above threshold."""
        ...
```

The backend operates on integer labels and raw numpy arrays. All cluster_id resolution, project_id tracking, locking, and tombstone management happen in the EmbeddingIndex layer.

## 3. NumpyBackend

Extracted from the current EmbeddingIndex logic — no behavior change:

```python
class _NumpyBackend:
    def __init__(self, dim: int):
        self._dim = dim
        self._matrix: np.ndarray = np.empty((0, dim), dtype=np.float32)

    def build(self, matrix: np.ndarray, count: int) -> None:
        self._matrix = matrix.copy()

    def add(self, label: int, embedding: np.ndarray) -> None:
        if label < self._matrix.shape[0]:
            self._matrix[label] = embedding
        else:
            # Extend matrix with zeros up to label, then set
            padding = label + 1 - self._matrix.shape[0]
            self._matrix = np.vstack([
                self._matrix,
                np.zeros((padding, self._dim), dtype=np.float32),
            ])
            self._matrix[label] = embedding

    def remove_label(self, label: int) -> None:
        if label < self._matrix.shape[0]:
            self._matrix[label] = 0.0  # zero out (tombstone)

    def search(
        self, query: np.ndarray, k: int, threshold: float,
        filter_fn: Callable[[int], bool] | None,
    ) -> list[tuple[int, float]]:
        if self._matrix.shape[0] == 0:
            return []

        scores = self._matrix @ query  # (n,)

        if filter_fn is not None:
            for i in range(len(scores)):
                if not filter_fn(i):
                    scores[i] = -1.0

        # Filter by threshold, top-k
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

        return [(int(valid_indices[i]), float(valid_scores[valid_indices[i]]))
                for i in order]
```

## 4. HnswBackend

```python
class _HnswBackend:
    def __init__(self, dim: int):
        self._dim = dim
        self._index: hnswlib.Index | None = None

    def build(self, matrix: np.ndarray, count: int) -> None:
        import hnswlib
        self._index = hnswlib.Index(space="cosine", dim=self._dim)
        max_elements = max(count * 2, 1000)
        self._index.init_index(
            max_elements=max_elements,
            ef_construction=200,
            M=16,
            allow_replace_deleted=True,  # required for add() with replace
        )
        self._index.set_ef(50)
        if count > 0:
            self._index.add_items(matrix, ids=np.arange(count))

    def add(self, label: int, embedding: np.ndarray) -> None:
        if self._index is None:
            return
        if label >= self._index.get_max_elements():
            self._index.resize_index(max(label * 2, self._index.get_max_elements() * 2))
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
            pass  # already deleted or never added

    def search(
        self, query: np.ndarray, k: int, threshold: float,
        filter_fn: Callable[[int], bool] | None,
    ) -> list[tuple[int, float]]:
        if self._index is None or self._index.get_current_count() == 0:
            return []

        effective_k = min(k * 3, self._index.get_current_count())  # over-fetch for threshold
        try:
            labels, distances = self._index.knn_query(
                query.reshape(1, -1),
                k=effective_k,
                filter=filter_fn,
            )
        except RuntimeError:
            return []  # empty index or all filtered

        results = []
        for label, dist in zip(labels[0], distances[0]):
            sim = 1.0 - dist  # hnswlib cosine returns distance
            if sim >= threshold:
                results.append((int(label), float(sim)))

        return sorted(results, key=lambda x: -x[1])[:k]
```

**Thread safety note:** hnswlib supports concurrent reads via `set_num_threads()`. The EmbeddingIndex's existing pattern (lock-free reads, locked writes) is compatible: `search()` reads from hnswlib without the lock; `upsert()`/`remove()`/`rebuild()` acquire the lock. hnswlib handles concurrent read+write safely as long as writes are serialized (which the asyncio lock guarantees under cooperative scheduling).

**Double-normalization note:** The EmbeddingIndex normalizes vectors before passing to the backend. hnswlib's cosine space also normalizes internally. Double-normalization of an already-normalized vector is idempotent (no-op), so this is harmless.

## 5. Auto-Selection Logic

### Threshold constant

```python
HNSW_CLUSTER_THRESHOLD: int = 1000  # switch from numpy to hnsw at this count
```

**ADR note:** The ADR prescribes monitoring-driven activation (search > 50ms). This spec uses a proactive threshold (cluster count >= 1000) because monitoring instrumentation is not required as a prerequisite. At 1000 384-dim vectors, numpy search is ~5-10ms — we switch before it becomes a bottleneck rather than reacting after.

### Selection in rebuild()

Auto-selection happens ONLY in `rebuild()` (cold path). This is the only point where the full corpus size is known and a clean rebuild is happening anyway.

```python
if len(new_ids) >= HNSW_CLUSTER_THRESHOLD:
    if not isinstance(self._backend, _HnswBackend):
        self._backend = _HnswBackend(dim=self._dim)
        logger.info("EmbeddingIndex: switched to HNSW backend (%d centroids)", len(new_ids))
else:
    if not isinstance(self._backend, _NumpyBackend):
        self._backend = _NumpyBackend(dim=self._dim)
        logger.info("EmbeddingIndex: switched to numpy backend (%d centroids)", len(new_ids))

self._backend.build(matrix, len(new_ids))
```

If a deployment has 1,500 clusters and the cold path doesn't run for hours, the system stays on whatever backend was active at last rebuild. Hot-path upserts do NOT trigger backend switches (they work on either backend via the protocol).

## 6. EmbeddingIndex Adapter Layer

The `EmbeddingIndex` public methods delegate to the backend while managing the stable label mapping:

### search()

```python
def search(
    self, embedding: np.ndarray, k: int = 5, threshold: float = 0.72,
    project_filter: str | None = None,
) -> list[tuple[str, float]]:
    ids = self._ids
    project_ids = self._project_ids
    tombstones = self._tombstones

    if not self._id_to_label:
        return []

    query = embedding.astype(np.float32).ravel()
    norm = np.linalg.norm(query)
    if norm < 1e-9:
        return []
    query = query / norm

    # Build filter function combining project filter + tombstone exclusion
    filter_fn = None
    if project_filter is not None or tombstones:
        def filter_fn(label: int) -> bool:
            if label in tombstones:
                return False
            if project_filter is not None and label < len(project_ids):
                return project_ids[label] == project_filter
            return project_filter is None

    raw_results = self._backend.search(query, k, threshold, filter_fn)

    # Translate labels to cluster_ids
    return [(ids[label], score) for label, score in raw_results
            if label < len(ids) and ids[label] is not None]
```

### upsert()

```python
async def upsert(
    self, cluster_id: str, embedding: np.ndarray,
    project_id: str | None = None,
) -> None:
    emb = embedding.astype(np.float32).ravel()
    norm = np.linalg.norm(emb)
    if norm < 1e-9:
        return
    emb = emb / norm

    async with self._lock:
        if cluster_id in self._id_to_label:
            label = self._id_to_label[cluster_id]
            self._project_ids[label] = project_id
        else:
            label = self._next_label
            self._next_label += 1
            self._id_to_label[cluster_id] = label
            # Extend parallel arrays to accommodate new label
            while len(self._ids) <= label:
                self._ids.append(None)
                self._project_ids.append(None)
            self._ids[label] = cluster_id
            self._project_ids[label] = project_id

        # Discard tombstone if re-adding a previously removed label
        self._tombstones.discard(label)

        self._backend.add(label, emb)
```

### remove()

```python
async def remove(self, cluster_id: str) -> None:
    async with self._lock:
        if cluster_id not in self._id_to_label:
            return
        label = self._id_to_label.pop(cluster_id)
        self._tombstones.add(label)
        self._backend.remove_label(label)
        # Do NOT pop from _ids/_project_ids — tombstoned
```

### rebuild()

```python
async def rebuild(
    self, centroids: dict[str, np.ndarray],
    project_ids: dict[str, str | None] | None = None,
) -> None:
    if not centroids:
        async with self._lock:
            self._ids = []
            self._project_ids = []
            self._id_to_label = {}
            self._next_label = 0
            self._tombstones.clear()
            self._backend.build(np.empty((0, self._dim), dtype=np.float32), 0)
        return

    new_ids = list(centroids.keys())
    p_ids = [project_ids.get(cid) if project_ids else None for cid in new_ids]
    rows = []
    for cid in new_ids:
        emb = centroids[cid].astype(np.float32).ravel()
        norm = np.linalg.norm(emb)
        rows.append(emb / norm if norm > 1e-9 else np.zeros(self._dim, dtype=np.float32))
    matrix = np.vstack(rows)

    async with self._lock:
        # Auto-select backend
        if len(new_ids) >= HNSW_CLUSTER_THRESHOLD:
            if not isinstance(self._backend, _HnswBackend):
                self._backend = _HnswBackend(dim=self._dim)
                logger.info("EmbeddingIndex: switched to HNSW backend (%d centroids)", len(new_ids))
        else:
            if not isinstance(self._backend, _NumpyBackend):
                self._backend = _NumpyBackend(dim=self._dim)

        # Reset stable mapping — compaction
        self._ids = new_ids
        self._project_ids = p_ids
        self._id_to_label = {cid: i for i, cid in enumerate(new_ids)}
        self._next_label = len(new_ids)
        self._tombstones.clear()

        self._backend.build(matrix, len(new_ids))

    logger.info("EmbeddingIndex rebuilt: %d centroids", len(new_ids))
```

## 7. Snapshot and Restore

### IndexSnapshot

```python
@dataclass
class IndexSnapshot:
    matrix: np.ndarray
    ids: list[str]
    project_ids: list[str | None] = field(default_factory=list)
    id_to_label: dict[str, int] = field(default_factory=dict)
    next_label: int = 0
    tombstones: set[int] = field(default_factory=set)
```

No `backend` field — backend is always rebuilt from the matrix on restore.

### snapshot()

```python
async def snapshot(self) -> IndexSnapshot:
    async with self._lock:
        # Compact matrix for snapshot (exclude tombstoned rows)
        live_labels = sorted(self._id_to_label.values())
        if live_labels and isinstance(self._backend, _NumpyBackend):
            matrix = self._backend._matrix.copy()
        else:
            # For HNSW, we don't have direct matrix access — store the rebuild data
            matrix = np.empty((0, self._dim), dtype=np.float32)
            # ... or reconstruct from the ids + centroids

        return IndexSnapshot(
            matrix=self._backend._matrix.copy() if isinstance(self._backend, _NumpyBackend) else self._last_rebuild_matrix.copy(),
            ids=list(self._ids),
            project_ids=list(self._project_ids),
            id_to_label=dict(self._id_to_label),
            next_label=self._next_label,
            tombstones=set(self._tombstones),
        )
```

**HNSW snapshot strategy:** Store `_last_rebuild_matrix` (the matrix from the most recent `rebuild()` call) on the EmbeddingIndex. On restore, rebuild the backend from this matrix. Cost: O(N * ef_construction) for HNSW rebuild on restore. At 3K clusters with ef_construction=200, this is ~50-100ms. The warm path calls restore on rejected phases (up to 3 per cycle). Worst case: 150-300ms rebuild overhead per cycle — acceptable since warm cycles already take seconds.

### restore()

```python
async def restore(self, snapshot: IndexSnapshot) -> None:
    async with self._lock:
        self._ids = list(snapshot.ids)
        self._project_ids = list(snapshot.project_ids)
        self._id_to_label = dict(snapshot.id_to_label)
        self._next_label = snapshot.next_label
        self._tombstones = set(snapshot.tombstones)

        # Rebuild backend from matrix
        live_count = len(self._id_to_label)
        if live_count >= HNSW_CLUSTER_THRESHOLD:
            if not isinstance(self._backend, _HnswBackend):
                self._backend = _HnswBackend(dim=self._dim)
        else:
            if not isinstance(self._backend, _NumpyBackend):
                self._backend = _NumpyBackend(dim=self._dim)

        self._backend.build(snapshot.matrix, snapshot.matrix.shape[0])
```

## 8. Cache Compatibility

### Save format

```python
async def save_cache(self, cache_path: Path) -> None:
    import pickle
    async with self._lock:
        data = {
            "matrix": self._last_rebuild_matrix if hasattr(self, '_last_rebuild_matrix') else (
                self._backend._matrix if isinstance(self._backend, _NumpyBackend) else np.empty((0, self._dim))
            ),
            "ids": [cid for cid in self._ids if cid is not None],
            "project_ids": [pid for cid, pid in zip(self._ids, self._project_ids) if cid is not None],
            "backend": "hnsw" if isinstance(self._backend, _HnswBackend) else "numpy",
        }
    # ... pickle dump (existing pattern)
```

For HNSW, also save the hnswlib index as a sidecar file:
```python
if isinstance(self._backend, _HnswBackend) and self._backend._index:
    hnsw_path = cache_path.with_suffix(".hnsw")
    self._backend._index.save_index(str(hnsw_path))
```

### Load format

```python
async def load_cache(self, cache_path: Path, max_age_seconds: int = 3600) -> bool:
    # ... existing freshness check ...
    data = pickle.load(f)

    ids = data["ids"]
    project_ids = data.get("project_ids", [None] * len(ids))
    backend_type = data.get("backend", "numpy")

    # Rebuild via rebuild() which handles backend selection and stable mapping
    centroids = {}
    matrix = data["matrix"]
    for i, cid in enumerate(ids):
        if i < matrix.shape[0]:
            centroids[cid] = matrix[i]

    pid_dict = {cid: pid for cid, pid in zip(ids, project_ids)}
    await self.rebuild(centroids, project_ids=pid_dict)

    return True
```

### Backward compatibility

- Legacy caches (no `"backend"` key, no `"project_ids"`) load via rebuild() with defaults.
- Phase 1 caches (have `"project_ids"` but no `"backend"`) load as numpy (rebuild handles selection).
- HNSW sidecar file (`.hnsw`) is optional — if missing, rebuild from matrix.

## 9. pairwise_similarities

Stays numpy-only regardless of backend. It requires the full N x N distance matrix which HNSW doesn't produce. Already capped at 2000 clusters with a warning. No change needed.

## 10. Direct Internal Access

`engine.py` line ~696-702 directly accesses `_embedding_index._matrix` and `_ids` for reset operations. After the refactor, add a public method `reset()` on EmbeddingIndex that clears all state, instead of reaching into internals. The `_matrix` property should delegate to `_backend._matrix` for the numpy backend or raise for HNSW.

## 11. Dependency

Add to `requirements.txt`:
```
hnswlib>=0.8.0
```

Pure C++ extension compiled via pip. Wheels available for Linux/macOS/Windows. No system-level dependencies.

## 12. Validation

### Seed targets
- 5K+ optimizations to grow taxonomy past 1K clusters.
- Trigger cold path refit to rebuild index (switch from numpy to HNSW).

### Assertions
- Below 1000 clusters: numpy backend active, search < 5ms.
- At/above 1000 clusters: HNSW backend auto-selected on rebuild.
- HNSW search < 5ms at 1K clusters.
- Project filter produces identical results on both backends (cross-validate by running both and comparing).
- Cache round-trip: save HNSW cache, restart, load, verify search results match.
- Legacy cache (numpy, no project_ids) loads correctly even when cluster count > threshold.
- Snapshot/restore cycles correctly (including HNSW rebuild on restore).
- Warm path speculative rollback works with HNSW (restore is functional, < 200ms at 3K clusters).
- Tombstone handling: remove + search correctly excludes removed clusters.
- Compaction on rebuild: tombstones cleared, labels reassigned.

### Benchmark protocol
```python
import time
timings = []
for _ in range(100):
    query = random_embedding(384)
    start = time.monotonic()
    results = index.search(query, k=5, threshold=0.50)
    timings.append((time.monotonic() - start) * 1000)
p50 = sorted(timings)[50]
p95 = sorted(timings)[95]
p99 = sorted(timings)[99]
print(f"p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")
```

### Test files
- `tests/taxonomy/test_hnsw_backend.py` — HnswBackend search, add, remove_label, build
- `tests/taxonomy/test_numpy_backend.py` — NumpyBackend (extracted, verify no regression)
- `tests/taxonomy/test_stable_label_mapping.py` — tombstones, compaction on rebuild, label reuse
- `tests/taxonomy/test_backend_auto_selection.py` — threshold crossing, numpy<->hnsw switch
- `tests/taxonomy/test_backend_cache_compat.py` — cache round-trip, legacy load, sidecar file
- `tests/taxonomy/test_backend_project_filter.py` — cross-validate numpy vs HNSW results
- `tests/taxonomy/test_backend_snapshot_restore.py` — warm path rollback with both backends
- `tests/taxonomy/test_backend_benchmark.py` — latency assertions at 1K/3K/5K clusters
