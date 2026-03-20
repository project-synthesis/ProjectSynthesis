"""Verify TaxonomyNode and TaxonomySnapshot DB models."""

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    Optimization,
    PatternFamily,
    TaxonomyNode,
    TaxonomySnapshot,
)


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_taxonomy_node_roundtrip(db: AsyncSession):
    """Create, persist, and read back a TaxonomyNode."""
    embedding = np.random.randn(384).astype(np.float32).tobytes()
    node = TaxonomyNode(
        label="API Architecture",
        centroid_embedding=embedding,
        member_count=5,
        coherence=0.85,
        separation=0.72,
        stability=0.90,
        persistence=0.65,
        state="confirmed",
        color_hex="#a855f7",
    )
    db.add(node)
    await db.commit()

    result = await db.execute(select(TaxonomyNode))
    loaded = result.scalar_one()
    assert loaded.label == "API Architecture"
    assert loaded.state == "confirmed"
    assert loaded.member_count == 5
    assert loaded.id is not None
    assert loaded.created_at is not None
    assert loaded.parent_id is None


@pytest.mark.asyncio
async def test_taxonomy_node_parent_child(db: AsyncSession):
    """Verify parent-child relationship works."""
    parent = TaxonomyNode(
        label="Infrastructure",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        state="confirmed",
        color_hex="#00e5ff",
    )
    db.add(parent)
    await db.flush()

    child = TaxonomyNode(
        label="Backend APIs",
        parent_id=parent.id,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        state="candidate",
        color_hex="#a855f7",
    )
    db.add(child)
    await db.commit()

    result = await db.execute(
        select(TaxonomyNode).where(TaxonomyNode.parent_id == parent.id)
    )
    children = result.scalars().all()
    assert len(children) == 1
    assert children[0].label == "Backend APIs"


@pytest.mark.asyncio
async def test_taxonomy_snapshot_roundtrip(db: AsyncSession):
    """Create and read back a TaxonomySnapshot."""
    snap = TaxonomySnapshot(
        trigger="warm_path",
        q_system=0.847,
        q_coherence=0.812,
        q_separation=0.891,
        q_coverage=0.940,
        q_dbcv=0.0,
        operations="[]",
        nodes_created=2,
        nodes_retired=0,
        nodes_merged=1,
        nodes_split=0,
    )
    db.add(snap)
    await db.commit()

    result = await db.execute(select(TaxonomySnapshot))
    loaded = result.scalar_one()
    assert loaded.q_system == pytest.approx(0.847)
    assert loaded.trigger == "warm_path"


@pytest.mark.asyncio
async def test_pattern_family_has_taxonomy_fields(db: AsyncSession):
    """PatternFamily has taxonomy_node_id and domain_raw columns."""
    family = PatternFamily(
        intent_label="test",
        domain="general",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        taxonomy_node_id=None,
        domain_raw="REST API design",
    )
    db.add(family)
    await db.commit()

    result = await db.execute(select(PatternFamily))
    loaded = result.scalar_one()
    assert loaded.domain_raw == "REST API design"
    assert loaded.taxonomy_node_id is None


@pytest.mark.asyncio
async def test_optimization_has_taxonomy_fields(db: AsyncSession):
    """Optimization has taxonomy_node_id and domain_raw columns."""
    opt = Optimization(
        raw_prompt="test prompt",
        taxonomy_node_id=None,
        domain_raw="database schema",
    )
    db.add(opt)
    await db.commit()

    result = await db.execute(select(Optimization))
    loaded = result.scalar_one()
    assert loaded.domain_raw == "database schema"
