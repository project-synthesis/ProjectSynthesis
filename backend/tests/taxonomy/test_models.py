"""Verify PromptCluster, TaxonomySnapshot, and related DB models."""

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import (
    Base,
    MetaPattern,
    Optimization,
    OptimizationPattern,
    PromptCluster,
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


@pytest.fixture
def tmp_engine():
    """Synchronous in-memory engine for schema introspection."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.mark.asyncio
async def test_prompt_cluster_roundtrip(db: AsyncSession):
    """Create, persist, and read back a PromptCluster."""
    embedding = np.random.randn(384).astype(np.float32).tobytes()
    cluster = PromptCluster(
        label="API Architecture",
        centroid_embedding=embedding,
        member_count=5,
        coherence=0.85,
        separation=0.72,
        stability=0.90,
        persistence=0.65,
        state="active",
        domain="backend",
        task_type="coding",
        color_hex="#a855f7",
    )
    db.add(cluster)
    await db.commit()

    result = await db.execute(select(PromptCluster))
    loaded = result.scalar_one()
    assert loaded.label == "API Architecture"
    assert loaded.state == "active"
    assert loaded.member_count == 5
    assert loaded.domain == "backend"
    assert loaded.task_type == "coding"
    assert loaded.id is not None
    assert loaded.created_at is not None
    assert loaded.parent_id is None


@pytest.mark.asyncio
async def test_prompt_cluster_parent_child(db: AsyncSession):
    """Verify parent-child relationship works."""
    parent = PromptCluster(
        label="Infrastructure",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        state="active",
        domain="general",
    )
    db.add(parent)
    await db.flush()

    child = PromptCluster(
        label="Backend APIs",
        parent_id=parent.id,
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
        state="candidate",
        domain="backend",
    )
    db.add(child)
    await db.commit()

    result = await db.execute(
        select(PromptCluster).where(PromptCluster.parent_id == parent.id)
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
        legacy=False,
    )
    db.add(snap)
    await db.commit()

    result = await db.execute(select(TaxonomySnapshot))
    loaded = result.scalar_one()
    assert loaded.q_system == pytest.approx(0.847)
    assert loaded.trigger == "warm_path"
    assert loaded.legacy is False


@pytest.mark.asyncio
async def test_optimization_has_cluster_id(db: AsyncSession):
    """Optimization has cluster_id and domain_raw columns."""
    opt = Optimization(
        raw_prompt="test prompt",
        cluster_id=None,
        domain_raw="database schema",
    )
    db.add(opt)
    await db.commit()

    result = await db.execute(select(Optimization))
    loaded = result.scalar_one()
    assert loaded.domain_raw == "database schema"
    assert loaded.cluster_id is None


@pytest.mark.asyncio
async def test_meta_pattern_links_to_cluster(db: AsyncSession):
    """MetaPattern has cluster_id FK to prompt_cluster."""
    cluster = PromptCluster(
        label="test cluster",
        state="active",
        domain="general",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()

    mp = MetaPattern(
        cluster_id=cluster.id,
        pattern_text="Use clear variable names",
    )
    db.add(mp)
    await db.commit()

    result = await db.execute(select(MetaPattern))
    loaded = result.scalar_one()
    assert loaded.cluster_id == cluster.id


@pytest.mark.asyncio
async def test_optimization_pattern_links_to_cluster(db: AsyncSession):
    """OptimizationPattern has cluster_id FK to prompt_cluster."""
    cluster = PromptCluster(
        label="test cluster",
        state="active",
        domain="general",
        centroid_embedding=np.zeros(384, dtype=np.float32).tobytes(),
    )
    db.add(cluster)
    await db.flush()

    opt = Optimization(raw_prompt="test prompt")
    db.add(opt)
    await db.flush()

    op = OptimizationPattern(
        optimization_id=opt.id,
        cluster_id=cluster.id,
        relationship="source",
    )
    db.add(op)
    await db.commit()

    result = await db.execute(select(OptimizationPattern))
    loaded = result.scalar_one()
    assert loaded.cluster_id == cluster.id


def test_prompt_cluster_schema(tmp_engine):
    """PromptCluster table has all required columns."""
    insp = inspect(tmp_engine)
    columns = {c["name"] for c in insp.get_columns("prompt_cluster")}
    required = {
        "id", "parent_id", "label", "state", "domain", "task_type",
        "centroid_embedding", "member_count", "usage_count", "avg_score",
        "coherence", "separation", "stability", "persistence",
        "umap_x", "umap_y", "umap_z", "color_hex",
        "preferred_strategy", "prune_flag_count", "last_used_at",
        "promoted_at", "archived_at", "created_at", "updated_at",
    }
    assert required.issubset(columns)


def test_prompt_cluster_state_values():
    """State column accepts all lifecycle states."""
    valid = {"candidate", "active", "mature", "template", "archived"}
    for state in valid:
        c = PromptCluster(label="test", state=state, domain="general", task_type="general")
        assert c.state == state


def test_taxonomy_snapshot_has_legacy_flag(tmp_engine):
    """TaxonomySnapshot table has the legacy column."""
    insp = inspect(tmp_engine)
    columns = {c["name"] for c in insp.get_columns("taxonomy_snapshots")}
    assert "legacy" in columns
