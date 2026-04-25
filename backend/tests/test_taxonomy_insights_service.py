"""Service-level tests for aggregate_pattern_density.

Router-level tests will exercise the full HTTP flow separately. This file
pins the aggregator's pure-Python behavior against the database.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, GlobalPattern, MetaPattern, Optimization, OptimizationPattern, PromptCluster


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    SessionMaker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionMaker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_aggregate_pattern_density_one_row_per_domain(db: AsyncSession):
    """PD1: one row per active domain node."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    for label in ("backend", "frontend", "database"):
        db.add(PromptCluster(
            id=str(uuid.uuid4()), label=label, state="domain",
            domain=label, task_type="general",
            color_hex="#b44aff", persistence=1.0,
            member_count=0, usage_count=0, prune_flag_count=0,
            created_at=datetime.now(timezone.utc),
        ))
    await db.commit()

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    rows = await aggregate_pattern_density(db, start, end)
    assert len(rows) == 3
    assert {r.domain_label for r in rows} == {"backend", "frontend", "database"}


@pytest.mark.asyncio
async def test_cluster_count_filters_to_active_mature_candidate(db: AsyncSession):
    """PD2: cluster_count counts children in {active, mature, candidate}; archived excluded."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dom)
    for i, state in enumerate(("active", "mature", "candidate", "archived")):
        db.add(PromptCluster(
            id=str(uuid.uuid4()), label=f"c{i}", state=state, domain="backend",
            task_type="coding", color_hex="#b44aff", persistence=0.7,
            member_count=5, usage_count=1, prune_flag_count=0,
            centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
            parent_id=dom.id,
            created_at=datetime.now(timezone.utc),
        ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
    )
    assert rows[0].cluster_count == 3  # archived excluded


@pytest.mark.asyncio
async def test_meta_pattern_count_and_avg_score(db: AsyncSession):
    """PD3 + PD6: meta_pattern_count aggregates children's MetaPatterns;
    avg_score is mean of avg_score across clusters with >=1 MetaPattern."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dom)
    cA = PromptCluster(
        id=str(uuid.uuid4()), label="cA", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=6, usage_count=1, prune_flag_count=0, avg_score=7.0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    cB = PromptCluster(
        id=str(uuid.uuid4()), label="cB", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=4, usage_count=1, prune_flag_count=0, avg_score=8.0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    cC = PromptCluster(
        id=str(uuid.uuid4()), label="cC", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=1, usage_count=0, prune_flag_count=0, avg_score=1.0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    db.add(cA); db.add(cB); db.add(cC)
    for cluster_id in (cA.id, cA.id, cB.id):
        db.add(MetaPattern(
            id=str(uuid.uuid4()), cluster_id=cluster_id,
            pattern_text="p", source_count=1, global_source_count=0,
            embedding=np.random.rand(384).astype(np.float32).tobytes(),
        ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
    )
    assert rows[0].meta_pattern_count == 3
    # Mean of cA (7.0) + cB (8.0) — cC excluded (no MetaPatterns) — = 7.5
    assert abs(rows[0].meta_pattern_avg_score - 7.5) < 1e-6


@pytest.mark.asyncio
async def test_global_pattern_count_via_containment(db: AsyncSession):
    """PD4: global_pattern_count counts GlobalPattern rows whose
    source_cluster_ids overlap with the domain's clusters (Python-side)."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(dom)
    c1 = PromptCluster(
        id=str(uuid.uuid4()), label="c1", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=3, usage_count=1, prune_flag_count=0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    c2 = PromptCluster(
        id=str(uuid.uuid4()), label="c2", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=3, usage_count=1, prune_flag_count=0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    db.add(c1); db.add(c2)
    db.add(GlobalPattern(
        id=str(uuid.uuid4()), pattern_text="gp1",
        source_cluster_ids=[c1.id], source_project_ids=[],
        cross_project_count=1, global_source_count=1, avg_cluster_score=7.5,
        embedding=np.random.rand(384).astype(np.float32).tobytes(),
    ))
    db.add(GlobalPattern(
        id=str(uuid.uuid4()), pattern_text="gp2",
        source_cluster_ids=[c1.id, c2.id], source_project_ids=[],
        cross_project_count=1, global_source_count=2, avg_cluster_score=8.0,
        embedding=np.random.rand(384).astype(np.float32).tobytes(),
    ))
    db.add(GlobalPattern(
        id=str(uuid.uuid4()), pattern_text="gp3",
        source_cluster_ids=[str(uuid.uuid4())],  # unrelated cluster
        source_project_ids=[],
        cross_project_count=1, global_source_count=1, avg_cluster_score=6.0,
        embedding=np.random.rand(384).astype(np.float32).tobytes(),
    ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, datetime.now(timezone.utc) - timedelta(days=7), datetime.now(timezone.utc)
    )
    assert rows[0].global_pattern_count == 2


@pytest.mark.asyncio
async def test_injection_rate_filters_to_period(db: AsyncSession):
    """PD5: cross_cluster_injection_rate counts only events in [period_start, period_end)."""
    from app.services.taxonomy_insights import aggregate_pattern_density

    dom = PromptCluster(
        id=str(uuid.uuid4()), label="backend", state="domain", domain="backend",
        task_type="general", color_hex="#b44aff", persistence=1.0,
        member_count=0, usage_count=0, prune_flag_count=0,
        created_at=datetime.now(timezone.utc),
    )
    child = PromptCluster(
        id=str(uuid.uuid4()), label="c", state="active", domain="backend",
        task_type="coding", color_hex="#b44aff", persistence=0.8,
        member_count=3, usage_count=1, prune_flag_count=0,
        centroid_embedding=np.random.rand(384).astype(np.float32).tobytes(),
        parent_id=dom.id, created_at=datetime.now(timezone.utc),
    )
    db.add(dom); db.add(child)
    opt_id = str(uuid.uuid4())
    db.add(Optimization(
        id=opt_id, raw_prompt="x", status="completed",
        created_at=datetime.now(timezone.utc),
    ))
    now = datetime.now(timezone.utc)
    db.add(OptimizationPattern(
        optimization_id=opt_id, cluster_id=child.id,
        relationship="injected", created_at=now - timedelta(days=3),
    ))
    db.add(OptimizationPattern(
        optimization_id=opt_id, cluster_id=child.id,
        relationship="global_injected", created_at=now - timedelta(days=5),
    ))
    db.add(OptimizationPattern(
        optimization_id=opt_id, cluster_id=child.id,
        relationship="injected", created_at=now - timedelta(days=60),  # outside
    ))
    await db.commit()

    rows = await aggregate_pattern_density(
        db, now - timedelta(days=7), now
    )
    # 2 in-period events touching this domain, total in-period = 2 → rate = 1.0
    assert abs(rows[0].cross_cluster_injection_rate - 1.0) < 1e-6
