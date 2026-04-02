"""Tests for multi-embedding schema columns and hot-path embedding."""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization, PromptCluster
from app.services.taxonomy.engine import TaxonomyEngine


@pytest.mark.asyncio
async def test_optimization_has_optimized_embedding_column(db: AsyncSession):
    opt = Optimization(raw_prompt="test", optimized_prompt="optimized", status="completed")
    db.add(opt)
    await db.flush()
    assert hasattr(opt, "optimized_embedding")
    assert opt.optimized_embedding is None

@pytest.mark.asyncio
async def test_optimization_has_transformation_embedding_column(db: AsyncSession):
    opt = Optimization(raw_prompt="test", optimized_prompt="optimized", status="completed")
    db.add(opt)
    await db.flush()
    assert hasattr(opt, "transformation_embedding")
    assert opt.transformation_embedding is None

@pytest.mark.asyncio
async def test_prompt_cluster_has_weighted_member_sum(db: AsyncSession):
    cluster = PromptCluster(label="test", state="active", domain="general")
    db.add(cluster)
    await db.flush()
    assert cluster.weighted_member_sum == 0.0

@pytest.mark.asyncio
async def test_embeddings_store_and_load(db: AsyncSession):
    emb = np.random.randn(384).astype(np.float32)
    opt = Optimization(
        raw_prompt="test", optimized_prompt="optimized", status="completed",
        optimized_embedding=emb.tobytes(),
        transformation_embedding=emb.tobytes(),
    )
    db.add(opt)
    await db.flush()
    loaded = (await db.execute(select(Optimization).where(Optimization.id == opt.id))).scalar_one()
    assert loaded.optimized_embedding is not None
    vec = np.frombuffer(loaded.optimized_embedding, dtype=np.float32)
    assert vec.shape == (384,)


@pytest.mark.asyncio
async def test_process_optimization_stores_optimized_embedding(db: AsyncSession):
    """Hot path should embed optimized_prompt and store on Optimization."""
    mock_emb_svc = MagicMock()
    call_count = 0
    async def _embed(text):
        nonlocal call_count
        call_count += 1
        rng = np.random.RandomState(call_count)
        vec = rng.randn(384).astype(np.float32)
        return vec / np.linalg.norm(vec)
    mock_emb_svc.aembed_single = _embed
    mock_emb_svc.cosine_search = lambda *a, **kw: []

    engine = TaxonomyEngine(embedding_service=mock_emb_svc, provider=None)

    opt = Optimization(
        raw_prompt="Design a REST API",
        optimized_prompt="Design a REST API with explicit constraints...",
        status="completed",
        task_type="coding",
        domain="backend",
    )
    db.add(opt)
    await db.flush()

    await engine.process_optimization(opt.id, db)

    loaded = (await db.execute(select(Optimization).where(Optimization.id == opt.id))).scalar_one()
    assert loaded.embedding is not None
    assert loaded.optimized_embedding is not None
    assert loaded.transformation_embedding is not None

    # Transformation should be L2-normalized
    t_vec = np.frombuffer(loaded.transformation_embedding, dtype=np.float32)
    assert abs(np.linalg.norm(t_vec) - 1.0) < 0.01
