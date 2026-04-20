"""Tests for GlobalPattern promotion (Phase 2B)."""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern, MetaPattern, Optimization, OptimizationPattern, PromptCluster


def test_global_pattern_constants():
    """All Phase 2B constants exist."""
    from app.services.taxonomy._constants import (
        GLOBAL_PATTERN_CAP,
        GLOBAL_PATTERN_CYCLE_INTERVAL,
        GLOBAL_PATTERN_DEDUP_COSINE,
        GLOBAL_PATTERN_DEMOTION_SCORE,
        GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES,
        GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS,
        GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS,
        GLOBAL_PATTERN_PROMOTION_MIN_SCORE,
        GLOBAL_PATTERN_RELEVANCE_BOOST,
    )
    assert GLOBAL_PATTERN_RELEVANCE_BOOST == 1.3
    assert GLOBAL_PATTERN_CAP == 500
    assert GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS == 5
    # ADR-005 B8: raised from 1 → 2 so Legacy-only patterns can't be
    # promoted as "Global" — they may be excellent but are single-project
    # by definition, not cross-project.
    assert GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS == 2
    assert GLOBAL_PATTERN_PROMOTION_MIN_SCORE == 6.0
    assert GLOBAL_PATTERN_DEMOTION_SCORE == 5.0
    assert GLOBAL_PATTERN_DEDUP_COSINE == 0.90
    assert GLOBAL_PATTERN_CYCLE_INTERVAL == 10
    assert GLOBAL_PATTERN_MIN_WALL_CLOCK_MINUTES == 30


def test_optimization_pattern_has_global_pattern_id():
    """OptimizationPattern has global_pattern_id column."""
    assert hasattr(OptimizationPattern, "global_pattern_id")


def test_engine_has_last_global_pattern_check():
    """TaxonomyEngine has _last_global_pattern_check attribute."""
    from unittest.mock import MagicMock

    from app.services.taxonomy.engine import TaxonomyEngine
    engine = TaxonomyEngine(embedding_service=MagicMock(), provider=MagicMock())
    assert hasattr(engine, "_last_global_pattern_check")
    assert engine._last_global_pattern_check == 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_vec(seed: int = 42) -> np.ndarray:
    """Deterministic L2-normalised 384-dim float32 vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(384).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


# ---------------------------------------------------------------------------
# Promotion integration test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promote_cross_project_pattern(db: AsyncSession):
    """MetaPattern present in 5 clusters across 2 projects gets promoted."""
    from app.services.taxonomy.global_patterns import run_global_pattern_phase

    # Shared embedding — all MetaPatterns are "siblings" (cosine ~1.0)
    shared_emb = _unit_vec(seed=99)
    emb_bytes = shared_emb.tobytes()

    project_ids = ["proj-A", "proj-B"]
    clusters: list[PromptCluster] = []

    # Create 5 active clusters across 2 projects, each avg_score >= 6.0
    for i in range(5):
        c = PromptCluster(
            label=f"cluster-{i}",
            state="active",
            domain="general",
            avg_score=7.0 + i * 0.1,
        )
        db.add(c)
        clusters.append(c)

    await db.flush()

    # Create an Optimization per cluster to establish project_id linkage
    for i, c in enumerate(clusters):
        opt = Optimization(
            raw_prompt=f"prompt-{i}",
            cluster_id=c.id,
            project_id=project_ids[i % 2],
        )
        db.add(opt)

    # Create MetaPatterns: one candidate with global_source_count=5 + 4 siblings
    candidate = MetaPattern(
        cluster_id=clusters[0].id,
        pattern_text="Use chain-of-thought reasoning",
        embedding=emb_bytes,
        source_count=5,
        global_source_count=5,
    )
    db.add(candidate)

    for i in range(1, 5):
        mp = MetaPattern(
            cluster_id=clusters[i].id,
            pattern_text="Use chain-of-thought reasoning",
            embedding=emb_bytes,
            source_count=3,
            global_source_count=1,
        )
        db.add(mp)

    await db.flush()

    # Run the promotion phase
    stats = await run_global_pattern_phase(db, warm_path_age=0.0)

    assert stats["promoted"] == 1
    assert stats["updated"] == 0

    # Verify the GlobalPattern was created correctly
    gp_result = await db.execute(select(GlobalPattern))
    gps = list(gp_result.scalars().all())
    assert len(gps) == 1

    gp = gps[0]
    assert gp.state == "active"
    assert gp.cross_project_count == 2
    assert gp.global_source_count == 5
    assert gp.avg_cluster_score >= 7.0
    assert set(gp.source_project_ids) == {"proj-A", "proj-B"}
    assert len(gp.source_cluster_ids) == 5


@pytest.mark.asyncio
async def test_promote_deduplicates_existing(db: AsyncSession):
    """Second promotion pass updates existing GlobalPattern rather than creating dupe."""
    from app.services.taxonomy.global_patterns import run_global_pattern_phase

    shared_emb = _unit_vec(seed=77)
    emb_bytes = shared_emb.tobytes()

    # Create 5 clusters + optimizations across 2 projects
    clusters = []
    for i in range(5):
        c = PromptCluster(label=f"c-{i}", state="active", domain="general", avg_score=7.5)
        db.add(c)
        clusters.append(c)
    await db.flush()

    for i, c in enumerate(clusters):
        db.add(Optimization(raw_prompt=f"p-{i}", cluster_id=c.id, project_id=f"proj-{i % 2}"))

    for i, c in enumerate(clusters):
        db.add(MetaPattern(
            cluster_id=c.id, pattern_text="structured output",
            embedding=emb_bytes, source_count=2,
            global_source_count=5 if i == 0 else 1,
        ))
    await db.flush()

    # First pass — creates new GP
    stats1 = await run_global_pattern_phase(db, warm_path_age=0.0)
    assert stats1["promoted"] == 1

    # Add a 6th cluster in a 3rd project
    c6 = PromptCluster(label="c-5", state="active", domain="general", avg_score=8.0)
    db.add(c6)
    await db.flush()
    db.add(Optimization(raw_prompt="p-5", cluster_id=c6.id, project_id="proj-2"))
    db.add(MetaPattern(
        cluster_id=c6.id, pattern_text="structured output",
        embedding=emb_bytes, source_count=1, global_source_count=6,
    ))
    await db.flush()

    # Second pass — should update, not create new
    stats2 = await run_global_pattern_phase(db, warm_path_age=0.0)
    assert stats2["promoted"] == 0
    assert stats2["updated"] == 1

    gp_result = await db.execute(select(GlobalPattern))
    gps = list(gp_result.scalars().all())
    assert len(gps) == 1
    assert gps[0].cross_project_count == 3


@pytest.mark.asyncio
async def test_validate_demotes_low_score(db: AsyncSession):
    """Active GlobalPattern with avg_cluster_score < 5.0 gets demoted."""
    from app.services.taxonomy.global_patterns import _validate_existing_patterns

    emb_bytes = _unit_vec(seed=10).tobytes()

    # Create a cluster with low score
    c = PromptCluster(label="low", state="active", domain="general", avg_score=3.0)
    db.add(c)
    await db.flush()

    gp = GlobalPattern(
        pattern_text="test", embedding=emb_bytes,
        source_cluster_ids=[c.id], source_project_ids=["p1"],
        cross_project_count=1, global_source_count=1,
        avg_cluster_score=7.0, state="active",
    )
    db.add(gp)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)
    assert demoted == 1
    assert re_promoted == 0

    # Flush to persist in-session mutations, then verify from DB
    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"


@pytest.mark.asyncio
async def test_validate_re_promotes_recovered_score(db: AsyncSession):
    """Demoted GlobalPattern with recovered avg_cluster_score >= 6.0 AND
    distinct project breadth >= MIN_PROJECTS (ADR-005 B8) gets re-promoted."""
    from app.services.taxonomy.global_patterns import _validate_existing_patterns

    emb_bytes = _unit_vec(seed=11).tobytes()

    c = PromptCluster(label="recovered", state="active", domain="general", avg_score=7.5)
    db.add(c)
    await db.flush()

    gp = GlobalPattern(
        pattern_text="test", embedding=emb_bytes,
        source_cluster_ids=[c.id],
        # B8: two distinct projects — required for (re-)promotion
        source_project_ids=["p1", "p2"],
        cross_project_count=2, global_source_count=1,
        avg_cluster_score=4.0, state="demoted",
    )
    db.add(gp)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)
    assert re_promoted == 1
    assert demoted == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "active"


@pytest.mark.asyncio
async def test_validate_retires_all_archived(db: AsyncSession):
    """GlobalPattern retires when all source clusters are archived and stale."""
    from datetime import timedelta

    from app.services.taxonomy._constants import _utcnow
    from app.services.taxonomy.global_patterns import _validate_existing_patterns

    emb_bytes = _unit_vec(seed=12).tobytes()

    c = PromptCluster(label="gone", state="archived", domain="general", avg_score=6.0)
    db.add(c)
    await db.flush()

    gp = GlobalPattern(
        pattern_text="test", embedding=emb_bytes,
        source_cluster_ids=[c.id], source_project_ids=["p1"],
        cross_project_count=1, global_source_count=1,
        avg_cluster_score=6.0, state="demoted",  # already demoted — retirement eligible
        last_validated_at=_utcnow() - timedelta(days=35),
    )
    db.add(gp)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)
    assert retired == 1

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "retired"


@pytest.mark.asyncio
async def test_retention_cap_evicts_demoted_first(db: AsyncSession):
    """Retention cap evicts demoted patterns before active ones."""
    from app.services.taxonomy._constants import _utcnow
    from app.services.taxonomy.global_patterns import _enforce_retention_cap

    emb_bytes = _unit_vec(seed=13).tobytes()
    now = _utcnow()

    # We can't easily create 501 patterns, so we'll monkeypatch the cap
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        # Temporarily set cap to 2
        gp_mod.GLOBAL_PATTERN_CAP = 2

        # Create 2 active + 1 demoted = 3 total, cap at 2
        for i in range(2):
            db.add(GlobalPattern(
                pattern_text=f"active-{i}", embedding=emb_bytes,
                source_cluster_ids=[], source_project_ids=[],
                cross_project_count=0, global_source_count=0,
                state="active", last_validated_at=now,
            ))
        db.add(GlobalPattern(
            pattern_text="demoted-0", embedding=emb_bytes,
            source_cluster_ids=[], source_project_ids=[],
            cross_project_count=0, global_source_count=0,
            state="demoted", last_validated_at=now,
        ))
        await db.flush()

        evicted = await _enforce_retention_cap(db)
        assert evicted == 1

        # The demoted one should have been evicted
        result = await db.execute(
            select(GlobalPattern).where(GlobalPattern.state == "retired")
        )
        retired_gps = list(result.scalars().all())
        assert len(retired_gps) == 1
        assert retired_gps[0].pattern_text == "demoted-0"
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap


@pytest.mark.asyncio
async def test_no_promotion_single_project_breadth(db: AsyncSession):
    """ADR-005 B8: pattern in 5 clusters within only 1 project must NOT be promoted.

    Cluster breadth alone is no longer sufficient — the Global tier now
    requires ``GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS`` distinct projects
    so that Legacy-only (single-project) patterns can't graduate.
    """
    from app.services.taxonomy.global_patterns import run_global_pattern_phase

    shared_emb = _unit_vec(seed=50)
    emb_bytes = shared_emb.tobytes()

    clusters = []
    for i in range(5):
        c = PromptCluster(label=f"c-{i}", state="active", domain="general", avg_score=8.0)
        db.add(c)
        clusters.append(c)
    await db.flush()

    # All optimizations in the same project — fails B8 project gate
    for i, c in enumerate(clusters):
        db.add(Optimization(raw_prompt=f"p-{i}", cluster_id=c.id, project_id="proj-A"))

    for i, c in enumerate(clusters):
        db.add(MetaPattern(
            cluster_id=c.id, pattern_text="single-project pattern",
            embedding=emb_bytes, source_count=2,
            global_source_count=5 if i == 0 else 1,
        ))
    await db.flush()

    stats = await run_global_pattern_phase(db, warm_path_age=0.0)
    assert stats["promoted"] == 0, "B8 gate should block single-project promotions"


@pytest.mark.asyncio
async def test_promotion_cross_project_breadth(db: AsyncSession):
    """ADR-005 B8: pattern in 5 clusters spanning 2+ projects IS promoted."""
    from app.services.taxonomy.global_patterns import run_global_pattern_phase

    shared_emb = _unit_vec(seed=52)
    emb_bytes = shared_emb.tobytes()

    clusters = []
    for i in range(5):
        c = PromptCluster(label=f"c-{i}", state="active", domain="general", avg_score=8.0)
        db.add(c)
        clusters.append(c)
    await db.flush()

    # Alternate projects — 3 in proj-A, 2 in proj-B → 2 distinct projects
    for i, c in enumerate(clusters):
        db.add(Optimization(
            raw_prompt=f"p-{i}", cluster_id=c.id,
            project_id="proj-A" if i % 2 == 0 else "proj-B",
        ))

    for i, c in enumerate(clusters):
        db.add(MetaPattern(
            cluster_id=c.id, pattern_text="cross-project pattern",
            embedding=emb_bytes, source_count=2,
            global_source_count=5 if i == 0 else 1,
        ))
    await db.flush()

    stats = await run_global_pattern_phase(db, warm_path_age=0.0)
    assert stats["promoted"] >= 1


@pytest.mark.asyncio
async def test_no_promotion_below_min_clusters(db: AsyncSession):
    """Pattern in only 4 clusters (below min 5) within 1 project is not promoted."""
    from app.services.taxonomy.global_patterns import run_global_pattern_phase

    shared_emb = _unit_vec(seed=51)
    emb_bytes = shared_emb.tobytes()

    clusters = []
    for i in range(4):
        c = PromptCluster(label=f"c-{i}", state="active", domain="general", avg_score=8.0)
        db.add(c)
        clusters.append(c)
    await db.flush()

    # All optimizations in the same project
    for i, c in enumerate(clusters):
        db.add(Optimization(raw_prompt=f"p-{i}", cluster_id=c.id, project_id="proj-A"))

    for i, c in enumerate(clusters):
        db.add(MetaPattern(
            cluster_id=c.id, pattern_text="narrow pattern",
            embedding=emb_bytes, source_count=2,
            global_source_count=5 if i == 0 else 1,
        ))
    await db.flush()

    stats = await run_global_pattern_phase(db, warm_path_age=0.0)
    assert stats["promoted"] == 0
    assert stats["updated"] == 0
