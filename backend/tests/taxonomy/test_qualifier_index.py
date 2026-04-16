"""Unit tests for QualifierIndex.

Covers:
1. upsert and get_vector — insert a vector, verify L2-normalized copy returned
2. search — upsert two vectors, search for nearest, verify correct ID returned
3. remove — upsert then remove, verify get_vector returns None
4. rebuild — rebuild from dict, verify size
5. snapshot/restore — snapshot, remove, restore, verify size restored
"""

import numpy as np
import pytest

from app.services.taxonomy.qualifier_index import QualifierIndex

DIM = 4  # small for tests


@pytest.mark.asyncio
async def test_upsert_and_get():
    idx = QualifierIndex(dim=DIM)
    vec = np.array([1, 0, 0, 0], dtype=np.float32)
    await idx.upsert("c-1", vec)
    result = idx.get_vector("c-1")
    assert result is not None
    assert np.allclose(result, vec / np.linalg.norm(vec))


@pytest.mark.asyncio
async def test_search():
    idx = QualifierIndex(dim=DIM)
    await idx.upsert("c-1", np.array([1, 0, 0, 0], dtype=np.float32))
    await idx.upsert("c-2", np.array([0, 1, 0, 0], dtype=np.float32))
    results = idx.search(np.array([1, 0, 0, 0], dtype=np.float32), k=1)
    assert results[0][0] == "c-1"


@pytest.mark.asyncio
async def test_remove():
    idx = QualifierIndex(dim=DIM)
    await idx.upsert("c-1", np.array([1, 0, 0, 0], dtype=np.float32))
    await idx.remove("c-1")
    assert idx.get_vector("c-1") is None


@pytest.mark.asyncio
async def test_rebuild():
    idx = QualifierIndex(dim=DIM)
    vecs = {
        "c-1": np.array([1, 0, 0, 0], dtype=np.float32),
        "c-2": np.array([0, 1, 0, 0], dtype=np.float32),
    }
    await idx.rebuild(vecs)
    assert idx.size == 2


@pytest.mark.asyncio
async def test_snapshot_restore():
    idx = QualifierIndex(dim=DIM)
    await idx.upsert("c-1", np.array([1, 0, 0, 0], dtype=np.float32))
    snap = await idx.snapshot()
    await idx.remove("c-1")
    assert idx.size == 0
    await idx.restore(snap)
    assert idx.size == 1
