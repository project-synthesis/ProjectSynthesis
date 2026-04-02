"""Tests for multi-embedding schema columns."""
from __future__ import annotations
import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Optimization, PromptCluster

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
