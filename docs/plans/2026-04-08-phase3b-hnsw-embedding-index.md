# Phase 3B: HNSW Embedding Index — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace numpy brute-force embedding search with a dual-backend system (numpy + hnswlib). Auto-selects HNSW when cluster count >= 1000 on rebuild. Stable label mapping prevents index corruption on remove. All callers unchanged.

**Architecture:** `EmbeddingIndex` maintains `_id_to_label: dict[str, int]` mapping and `_tombstones: set[int]` for removed entries. Backend protocol with `_NumpyBackend` (extracted from current code) and `_HnswBackend` (wraps hnswlib). Auto-selection in `rebuild()` only. Compaction on rebuild clears tombstones. Cache backward-compatible with Phase 1 format.

**Tech Stack:** Python 3.12, numpy, hnswlib, pytest

**Spec:** `docs/specs/2026-04-08-phase3b-hnsw-embedding-index.md`

---

### Task 1: Add hnswlib dependency

**Files:**
- Modify: `backend/requirements.txt`

- [ ] **Step 1: Add hnswlib**

```bash
echo "hnswlib>=0.8.0" >> backend/requirements.txt
cd backend && source .venv/bin/activate && pip install hnswlib>=0.8.0
```

- [ ] **Step 2: Verify import works**

```bash
python3 -c "import hnswlib; print(f'hnswlib {hnswlib.__version__}')"
```

- [ ] **Step 3: Commit**

```bash
git add backend/requirements.txt
git commit -m "chore: add hnswlib dependency for Phase 3B"
```

---

### Task 2: Extract NumpyBackend from current EmbeddingIndex

**Files:**
- Modify: `backend/app/services/taxonomy/embedding_index.py`
- Test: `backend/tests/taxonomy/test_numpy_backend.py` (create)

- [ ] **Step 1: Write NumpyBackend test**

```python
# backend/tests/taxonomy/test_numpy_backend.py
"""Tests for extracted NumpyBackend (Phase 3B)."""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import _NumpyBackend


@pytest.fixture
def backend():
    return _NumpyBackend(dim=4)


class TestNumpyBackend:
    def test_build(self, backend):
        matrix = np.eye(4, dtype=np.float32)
        backend.build(matrix, 4)
        assert backend._matrix.shape == (4, 4)

    def test_add_new(self, backend):
        backend.build(np.zeros((2, 4), dtype=np.float32), 2)
        emb = np.array([1, 0, 0, 0], dtype=np.float32)
        backend.add(3, emb)  # label beyond current size
        assert backend._matrix.shape[0] == 4

    def test_add_existing(self, backend):
        matrix = np.zeros((2, 4), dtype=np.float32)
        backend.build(matrix, 2)
        emb = np.array([1, 0, 0, 0], dtype=np.float32)
        backend.add(0, emb)
        np.testing.assert_array_equal(backend._matrix[0], emb)

    def test_remove_label(self, backend):
        matrix = np.eye(4, dtype=np.float32)
        backend.build(matrix, 4)
        backend.remove_label(1)
        np.testing.assert_array_equal(backend._matrix[1], np.zeros(4))

    def test_search(self, backend):
        matrix = np.array([[1,0,0,0],[0,1,0,0],[0,0,1,0]], dtype=np.float32)
        backend.build(matrix, 3)
        query = np.array([0.9, 0.1, 0, 0], dtype=np.float32)
        query = query / np.linalg.norm(query)
        results = backend.search(query, k=2, threshold=0.0, filter_fn=None)
        assert len(results) >= 1
        assert results[0][0] == 0  # label 0 is [1,0,0,0], closest

    def test_search_with_filter(self, backend):
        matrix = np.array([[1,0,0,0],[0.9,0.1,0,0]], dtype=np.float32)
        for i in range(2):
            matrix[i] = matrix[i] / np.linalg.norm(matrix[i])
        backend.build(matrix, 2)
        query = np.array([1, 0, 0, 0], dtype=np.float32)
        # Filter out label 0
        results = backend.search(query, k=2, threshold=0.0, filter_fn=lambda l: l != 0)
        assert len(results) == 1
        assert results[0][0] == 1
```

- [ ] **Step 2: Extract _NumpyBackend class**

Add `_NumpyBackend` class to `embedding_index.py` per spec Section 3. This is a pure refactor — extract the matrix operations from the existing methods into the backend class.

- [ ] **Step 3: Run ALL existing embedding index tests**

```bash
pytest tests/taxonomy/test_embedding_index_project.py tests/taxonomy/test_numpy_backend.py -v
pytest --tb=short -q
```

All existing tests must pass (pure refactor).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/taxonomy/embedding_index.py backend/tests/taxonomy/test_numpy_backend.py
git commit -m "refactor(taxonomy): Phase 3B extract NumpyBackend from EmbeddingIndex"
```

---

### Task 3: Stable label mapping + tombstones

**Files:**
- Modify: `backend/app/services/taxonomy/embedding_index.py`
- Test: `backend/tests/taxonomy/test_stable_label_mapping.py` (create)

- [ ] **Step 1: Write label mapping tests**

```python
# backend/tests/taxonomy/test_stable_label_mapping.py
"""Tests for stable label mapping (Phase 3B)."""

import numpy as np
import pytest

from app.services.taxonomy.embedding_index import EmbeddingIndex


@pytest.fixture
def index():
    return EmbeddingIndex(dim=4)


def _emb(vals):
    v = np.array(vals, dtype=np.float32)
    return v / np.linalg.norm(v)


class TestStableLabelMapping:
    @pytest.mark.asyncio
    async def test_upsert_assigns_sequential_labels(self, index):
        await index.upsert("c1", _emb([1,0,0,0]))
        await index.upsert("c2", _emb([0,1,0,0]))
        assert index._id_to_label == {"c1": 0, "c2": 1}
        assert index._next_label == 2

    @pytest.mark.asyncio
    async def test_remove_tombstones_label(self, index):
        await index.upsert("c1", _emb([1,0,0,0]))
        await index.upsert("c2", _emb([0,1,0,0]))
        await index.remove("c1")
        assert 0 in index._tombstones
        assert "c1" not in index._id_to_label
        assert index.size == 1  # only c2 is live

    @pytest.mark.asyncio
    async def test_remove_does_not_shift_labels(self, index):
        await index.upsert("c1", _emb([1,0,0,0]))
        await index.upsert("c2", _emb([0,1,0,0]))
        await index.upsert("c3", _emb([0,0,1,0]))
        await index.remove("c2")  # remove middle
        # c3's label should still be 2 (no shifting)
        assert index._id_to_label["c3"] == 2

    @pytest.mark.asyncio
    async def test_search_excludes_tombstoned(self, index):
        await index.upsert("c1", _emb([1,0,0,0]))
        await index.upsert("c2", _emb([0.9,0.1,0,0]))
        await index.remove("c1")
        results = index.search(_emb([1,0,0,0]), k=5, threshold=0.0)
        ids = [r[0] for r in results]
        assert "c1" not in ids
        assert "c2" in ids

    @pytest.mark.asyncio
    async def test_rebuild_compacts_tombstones(self, index):
        await index.upsert("c1", _emb([1,0,0,0]))
        await index.upsert("c2", _emb([0,1,0,0]))
        await index.remove("c1")
        assert len(index._tombstones) == 1

        # Rebuild compacts
        await index.rebuild({"c2": _emb([0,1,0,0]), "c3": _emb([0,0,1,0])})
        assert len(index._tombstones) == 0
        assert index._id_to_label == {"c2": 0, "c3": 1}
        assert index._next_label == 2
```

- [ ] **Step 2: Add _id_to_label, _next_label, _tombstones to __init__**

- [ ] **Step 3: Rewrite upsert(), remove(), search(), rebuild() to use label mapping**

Per spec Section 6 (adapter layer). This is the core refactor.

- [ ] **Step 4: Update snapshot() and restore() to include label mapping**

Per spec Section 7.

- [ ] **Step 5: Run ALL tests**

```bash
pytest tests/taxonomy/ -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/embedding_index.py backend/tests/taxonomy/test_stable_label_mapping.py
git commit -m "feat(taxonomy): Phase 3B stable label mapping + tombstones"
```

---

### Task 4: HnswBackend implementation

**Files:**
- Modify: `backend/app/services/taxonomy/embedding_index.py`
- Test: `backend/tests/taxonomy/test_hnsw_backend.py` (create)

- [ ] **Step 1: Write HnswBackend tests**

Tests covering: build, add, remove_label, search, search with filter_fn. Same structure as test_numpy_backend.py but verifying HNSW-specific behavior (cosine distances, allow_replace_deleted).

- [ ] **Step 2: Implement _HnswBackend**

Per spec Section 4. Key: `allow_replace_deleted=True` in `init_index()`, `replace_deleted=True` in `add_items()`.

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_hnsw_backend.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/embedding_index.py backend/tests/taxonomy/test_hnsw_backend.py
git commit -m "feat(taxonomy): Phase 3B HnswBackend implementation"
```

---

### Task 5: Auto-selection logic in rebuild()

**Files:**
- Modify: `backend/app/services/taxonomy/embedding_index.py`
- Test: `backend/tests/taxonomy/test_backend_auto_selection.py` (create)

- [ ] **Step 1: Write auto-selection tests**

Tests: below threshold = numpy, at/above = HNSW, switching in both directions on rebuild.

- [ ] **Step 2: Add HNSW_CLUSTER_THRESHOLD constant and auto-selection in rebuild()**

Per spec Section 5.

- [ ] **Step 3: Run tests, commit**

```bash
pytest tests/taxonomy/test_backend_auto_selection.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/embedding_index.py backend/tests/taxonomy/test_backend_auto_selection.py
git commit -m "feat(taxonomy): Phase 3B auto-selection numpy<->HNSW on rebuild"
```

---

### Task 6: Cache compatibility + snapshot/restore

**Files:**
- Modify: `backend/app/services/taxonomy/embedding_index.py` (save_cache, load_cache)
- Test: `backend/tests/taxonomy/test_backend_cache_compat.py` (create)
- Test: `backend/tests/taxonomy/test_backend_snapshot_restore.py` (create)

- [ ] **Step 1: Write cache compat tests**

Tests: save+load round-trip, legacy cache load (no project_ids, no backend key), HNSW sidecar file.

- [ ] **Step 2: Write snapshot/restore tests**

Tests: snapshot preserves label mapping, restore rebuilds backend, restore with HNSW triggers rebuild.

- [ ] **Step 3: Update save_cache/load_cache per spec Section 8**

- [ ] **Step 4: Store `_last_rebuild_matrix` on rebuild for HNSW snapshot**

- [ ] **Step 5: Run ALL tests**

```bash
pytest tests/taxonomy/ -v
pytest --tb=short -q
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/taxonomy/embedding_index.py backend/tests/taxonomy/
git commit -m "feat(taxonomy): Phase 3B cache compat + snapshot/restore with label mapping"
```

---

### Task 7: Replace engine.py direct internal access + cross-validate backends

**Files:**
- Modify: `backend/app/services/taxonomy/engine.py` (replace _matrix/_ids access)
- Test: `backend/tests/taxonomy/test_backend_project_filter.py` (create)

- [ ] **Step 1: Add reset() method to EmbeddingIndex**

```python
async def reset(self) -> None:
    """Clear all state. Replaces direct _matrix/_ids access from engine.py."""
    async with self._lock:
        self._ids = []
        self._project_ids = []
        self._id_to_label = {}
        self._next_label = 0
        self._tombstones.clear()
        self._backend = _NumpyBackend(dim=self._dim)
        self._backend.build(np.empty((0, self._dim), dtype=np.float32), 0)
```

- [ ] **Step 2: Replace engine.py direct access with reset()**

Find `engine.py` line ~696-702 where `_embedding_index._matrix` and `_ids` are accessed. Replace with `await self._embedding_index.reset()`.

- [ ] **Step 3: Write cross-validation test**

```python
# backend/tests/taxonomy/test_backend_project_filter.py
"""Cross-validate numpy vs HNSW search results (Phase 3B)."""

import numpy as np
import pytest
from app.services.taxonomy.embedding_index import EmbeddingIndex, _NumpyBackend, _HnswBackend


@pytest.mark.asyncio
async def test_numpy_and_hnsw_return_same_results():
    """Search results must be identical regardless of backend."""
    dim = 384
    n_clusters = 50
    np.random.seed(42)

    centroids = {}
    project_ids = {}
    for i in range(n_clusters):
        v = np.random.randn(dim).astype(np.float32)
        v = v / np.linalg.norm(v)
        centroids[f"c{i}"] = v
        project_ids[f"c{i}"] = f"proj-{'A' if i % 2 == 0 else 'B'}"

    # Build numpy index
    numpy_idx = EmbeddingIndex(dim=dim)
    numpy_idx._backend = _NumpyBackend(dim=dim)
    await numpy_idx.rebuild(centroids, project_ids=project_ids)

    # Build hnsw index
    hnsw_idx = EmbeddingIndex(dim=dim)
    hnsw_idx._backend = _HnswBackend(dim=dim)
    await hnsw_idx.rebuild(centroids, project_ids=project_ids)

    # Run 10 random queries
    for _ in range(10):
        query = np.random.randn(dim).astype(np.float32)
        query = query / np.linalg.norm(query)

        numpy_results = numpy_idx.search(query, k=5, threshold=0.0)
        hnsw_results = hnsw_idx.search(query, k=5, threshold=0.0)

        # Same cluster IDs in top results (order may differ slightly for ties)
        numpy_ids = {r[0] for r in numpy_results}
        hnsw_ids = {r[0] for r in hnsw_results}
        # Allow 1 difference due to floating point in cosine
        assert len(numpy_ids & hnsw_ids) >= len(numpy_ids) - 1

    # Test with project_filter
    for _ in range(5):
        query = np.random.randn(dim).astype(np.float32)
        query = query / np.linalg.norm(query)

        numpy_results = numpy_idx.search(query, k=5, threshold=0.0, project_filter="proj-A")
        hnsw_results = hnsw_idx.search(query, k=5, threshold=0.0, project_filter="proj-A")

        # All results should be from proj-A
        for cid, _ in numpy_results:
            assert project_ids[cid] == "proj-A"
        for cid, _ in hnsw_results:
            assert project_ids[cid] == "proj-A"
```

- [ ] **Step 4: Run tests, commit**

```bash
pytest tests/taxonomy/test_backend_project_filter.py -v
pytest --tb=short -q
git add backend/app/services/taxonomy/embedding_index.py backend/app/services/taxonomy/engine.py backend/tests/taxonomy/
git commit -m "feat(taxonomy): Phase 3B engine reset() + numpy/HNSW cross-validation"
```

---

### Task 8: E2E validation + benchmark

- [ ] **Step 1: Restart, run full tests**

```bash
./init.sh restart
cd backend && source .venv/bin/activate && pytest --tb=short -q
```

- [ ] **Step 2: Verify auto-selection behavior**

With < 1000 clusters, numpy should be active:

```bash
grep "EmbeddingIndex" data/backend.log | head -5
```

Expected: "numpy backend" or no backend switch message.

- [ ] **Step 3: Run full test suite including benchmarks**

```bash
pytest tests/taxonomy/ -v
```

- [ ] **Step 4: Commit if fixes needed**
