"""Tests for GlobalPattern injection provenance (Phase 2B)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern, Optimization, OptimizationPattern, PromptCluster


@pytest.mark.asyncio
async def test_global_injection_creates_provenance_record(db: AsyncSession):
    """Global pattern injection creates OptimizationPattern with relationship='global_injected'."""
    cluster = PromptCluster(
        label="test", state="active", domain="general",
        task_type="coding", member_count=1,
    )
    db.add(cluster)
    await db.flush()

    gp = GlobalPattern(
        pattern_text="Use chain-of-thought",
        source_cluster_ids=[cluster.id],
        source_project_ids=["proj-a"],
        cross_project_count=2,
        global_source_count=5,
        state="active",
    )
    db.add(gp)
    await db.flush()

    opt = Optimization(raw_prompt="test", status="completed", cluster_id=cluster.id)
    db.add(opt)
    await db.flush()

    # Create provenance record (mimics what auto_inject_patterns does)
    prov = OptimizationPattern(
        optimization_id=opt.id,
        cluster_id=cluster.id,
        global_pattern_id=gp.id,
        relationship="global_injected",
        similarity=0.85,
    )
    db.add(prov)
    await db.flush()

    result = await db.execute(
        select(OptimizationPattern).where(
            OptimizationPattern.relationship == "global_injected"
        )
    )
    records = result.scalars().all()
    assert len(records) == 1
    assert records[0].global_pattern_id == gp.id
    assert records[0].cluster_id == cluster.id


@pytest.mark.asyncio
async def test_provenance_record_references_correct_fields(db: AsyncSession):
    """OptimizationPattern with global_injected relationship stores all FK fields correctly."""
    cluster = PromptCluster(
        label="coding-cluster", state="active", domain="backend",
        task_type="coding", member_count=3,
    )
    db.add(cluster)
    await db.flush()

    gp = GlobalPattern(
        pattern_text="Always include expected output format",
        source_cluster_ids=[cluster.id],
        source_project_ids=["proj-x", "proj-y"],
        cross_project_count=2,
        global_source_count=8,
        state="active",
    )
    db.add(gp)
    await db.flush()

    opt = Optimization(raw_prompt="write a parser", status="completed", cluster_id=cluster.id)
    db.add(opt)
    await db.flush()

    prov = OptimizationPattern(
        optimization_id=opt.id,
        cluster_id=cluster.id,
        global_pattern_id=gp.id,
        relationship="global_injected",
        similarity=0.92,
    )
    db.add(prov)
    await db.flush()

    fetched = await db.get(OptimizationPattern, prov.id)
    assert fetched is not None
    assert fetched.optimization_id == opt.id
    assert fetched.cluster_id == cluster.id
    assert fetched.global_pattern_id == gp.id
    assert fetched.relationship == "global_injected"
    assert abs(fetched.similarity - 0.92) < 1e-5


@pytest.mark.asyncio
async def test_multiple_global_injections_create_separate_provenance_records(db: AsyncSession):
    """Multiple GlobalPatterns each create their own provenance record."""
    cluster = PromptCluster(
        label="multi-test", state="active", domain="general",
        task_type="analysis", member_count=2,
    )
    db.add(cluster)
    await db.flush()

    gp1 = GlobalPattern(
        pattern_text="Pattern one",
        source_cluster_ids=[cluster.id],
        source_project_ids=["proj-a"],
        cross_project_count=2,
        global_source_count=5,
        state="active",
    )
    gp2 = GlobalPattern(
        pattern_text="Pattern two",
        source_cluster_ids=[cluster.id],
        source_project_ids=["proj-b"],
        cross_project_count=3,
        global_source_count=7,
        state="active",
    )
    db.add(gp1)
    db.add(gp2)
    await db.flush()

    opt = Optimization(raw_prompt="analyze data", status="completed", cluster_id=cluster.id)
    db.add(opt)
    await db.flush()

    for gp, sim in [(gp1, 0.80), (gp2, 0.75)]:
        db.add(OptimizationPattern(
            optimization_id=opt.id,
            cluster_id=cluster.id,
            global_pattern_id=gp.id,
            relationship="global_injected",
            similarity=sim,
        ))
    await db.flush()

    result = await db.execute(
        select(OptimizationPattern).where(
            OptimizationPattern.optimization_id == opt.id,
            OptimizationPattern.relationship == "global_injected",
        )
    )
    records = result.scalars().all()
    assert len(records) == 2
    gp_ids = {r.global_pattern_id for r in records}
    assert gp_ids == {gp1.id, gp2.id}
