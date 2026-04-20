"""Tests for GlobalPattern validation lifecycle (Phase 2B).

Dedicated tests for _validate_existing_patterns: demotion, re-promotion,
hysteresis, retirement, and last_validated_at behavior.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern, PromptCluster
from app.services.taxonomy._constants import (
    GLOBAL_PATTERN_DEMOTION_SCORE,
    GLOBAL_PATTERN_PROMOTION_MIN_SCORE,
    _utcnow,
)
from app.services.taxonomy.global_patterns import _validate_existing_patterns

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_vec(seed: int = 42) -> np.ndarray:
    """Deterministic L2-normalised 384-dim float32 vector."""
    rng = np.random.RandomState(seed)
    v = rng.randn(384).astype(np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def _make_gp(
    db: AsyncSession,
    *,
    state: str = "active",
    source_cluster_ids: list[str] | None = None,
    source_project_ids: list[str] | None = None,
    avg_cluster_score: float = 7.0,
    last_validated_at=None,
    seed: int = 42,
) -> GlobalPattern:
    """Create and add a GlobalPattern to the session (not yet flushed).

    Defaults to two distinct projects so the ADR-005 B8 breadth gate
    (``GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS=2``) is satisfied for tests
    that don't specifically exercise project breadth.
    """
    _project_ids = source_project_ids if source_project_ids is not None else ["p1", "p2"]
    gp = GlobalPattern(
        pattern_text="test pattern",
        embedding=_unit_vec(seed).tobytes(),
        source_cluster_ids=source_cluster_ids or [],
        source_project_ids=_project_ids,
        cross_project_count=len(_project_ids),
        global_source_count=len(source_cluster_ids or []),
        avg_cluster_score=avg_cluster_score,
        state=state,
        last_validated_at=last_validated_at,
    )
    db.add(gp)
    return gp


# ---------------------------------------------------------------------------
# Demotion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_active_pattern_demoted_below_threshold(db: AsyncSession):
    """Active pattern with avg_cluster_score < 5.0 gets demoted."""
    c = PromptCluster(label="low", state="active", domain="general", avg_score=3.0)
    db.add(c)
    await db.flush()

    gp = _make_gp(db, state="active", source_cluster_ids=[c.id], avg_cluster_score=7.0)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert demoted == 1
    assert re_promoted == 0
    assert retired == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"
    # Recomputed score should reflect live cluster score (3.0)
    assert gp.avg_cluster_score < GLOBAL_PATTERN_DEMOTION_SCORE


# ---------------------------------------------------------------------------
# Re-promotion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_demoted_pattern_re_promoted_above_threshold(db: AsyncSession):
    """Demoted pattern with avg_cluster_score >= 6.0 gets re-promoted."""
    c = PromptCluster(label="recovered", state="active", domain="general", avg_score=7.5)
    db.add(c)
    await db.flush()

    gp = _make_gp(db, state="demoted", source_cluster_ids=[c.id], avg_cluster_score=4.0)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert re_promoted == 1
    assert demoted == 0
    assert retired == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "active"
    assert gp.avg_cluster_score >= GLOBAL_PATTERN_PROMOTION_MIN_SCORE


# ---------------------------------------------------------------------------
# Hysteresis
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hysteresis_active_stays_active_between_thresholds(db: AsyncSession):
    """Active pattern at 5.5 (above demotion=5.0, below re-promotion=6.0) stays active."""
    c = PromptCluster(label="mid", state="active", domain="general", avg_score=5.5)
    db.add(c)
    await db.flush()

    gp = _make_gp(db, state="active", source_cluster_ids=[c.id], avg_cluster_score=7.0)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert demoted == 0
    assert re_promoted == 0
    assert retired == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "active"


@pytest.mark.asyncio
async def test_hysteresis_demoted_stays_demoted_between_thresholds(db: AsyncSession):
    """Demoted pattern at 5.5 (above demotion=5.0, below re-promotion=6.0) stays demoted."""
    c = PromptCluster(label="mid", state="active", domain="general", avg_score=5.5)
    db.add(c)
    await db.flush()

    gp = _make_gp(db, state="demoted", source_cluster_ids=[c.id], avg_cluster_score=4.0)
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert demoted == 0
    assert re_promoted == 0
    assert retired == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"


# ---------------------------------------------------------------------------
# Retirement
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retirement_all_clusters_archived_and_stale(db: AsyncSession):
    """All source clusters archived + last validated >30 days ago -> retired."""
    c = PromptCluster(label="gone", state="archived", domain="general", avg_score=6.0)
    db.add(c)
    await db.flush()

    gp = _make_gp(
        db,
        state="demoted",
        source_cluster_ids=[c.id],
        avg_cluster_score=6.0,
        last_validated_at=_utcnow() - timedelta(days=35),
    )
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert retired == 1
    assert demoted == 0
    assert re_promoted == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "retired"


@pytest.mark.asyncio
async def test_retirement_missing_clusters_not_archived(db: AsyncSession):
    """Missing clusters (deleted from DB) don't count as archived -> no retirement."""
    # source_cluster_ids point to non-existent cluster IDs
    gp = _make_gp(
        db,
        state="demoted",
        source_cluster_ids=["nonexistent-1", "nonexistent-2"],
        avg_cluster_score=6.0,
        last_validated_at=_utcnow() - timedelta(days=35),
    )
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert retired == 0

    await db.flush()
    await db.refresh(gp)
    # Score recomputed to 0.0 (no live clusters), so it stays demoted
    # (not re-promoted because 0.0 < 6.0)
    assert gp.state == "demoted"


@pytest.mark.asyncio
async def test_retirement_empty_source_cids_not_retired(db: AsyncSession):
    """Pattern with empty source_cids doesn't get retired (all_archived starts False)."""
    gp = _make_gp(
        db,
        state="demoted",
        source_cluster_ids=[],
        avg_cluster_score=6.0,
        last_validated_at=_utcnow() - timedelta(days=35),
    )
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    assert retired == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"


# ---------------------------------------------------------------------------
# last_validated_at behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_last_validated_at_updated_for_non_retired(db: AsyncSession):
    """last_validated_at is updated for patterns that don't retire."""
    c = PromptCluster(label="ok", state="active", domain="general", avg_score=7.0)
    db.add(c)
    await db.flush()

    old_time = _utcnow() - timedelta(hours=2)
    gp = _make_gp(
        db,
        state="active",
        source_cluster_ids=[c.id],
        avg_cluster_score=7.0,
        last_validated_at=old_time,
    )
    await db.flush()

    await _validate_existing_patterns(db)

    await db.flush()
    await db.refresh(gp)
    assert gp.last_validated_at > old_time


@pytest.mark.asyncio
async def test_last_validated_at_not_updated_for_retired(db: AsyncSession):
    """last_validated_at is NOT updated for retired patterns (skipped via continue)."""
    c = PromptCluster(label="gone", state="archived", domain="general", avg_score=6.0)
    db.add(c)
    await db.flush()

    old_time = _utcnow() - timedelta(days=35)
    gp = _make_gp(
        db,
        state="demoted",
        source_cluster_ids=[c.id],
        avg_cluster_score=6.0,
        last_validated_at=old_time,
    )
    await db.flush()

    _, _, retired = await _validate_existing_patterns(db)
    assert retired == 1

    await db.flush()
    await db.refresh(gp)
    # The continue statement skips the last_validated_at = now assignment
    assert gp.last_validated_at == old_time


# ---------------------------------------------------------------------------
# Mutual exclusion: demotion and retirement don't both fire
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_demotion_and_retirement_mutually_exclusive(db: AsyncSession):
    """if/elif chain: demotion and retirement don't both fire in same pass.

    An active pattern with low score AND all archived clusters should only
    be demoted (not also retired) because the elif prevents double-counting.
    """
    c = PromptCluster(label="gone-low", state="archived", domain="general", avg_score=3.0)
    db.add(c)
    await db.flush()

    gp = _make_gp(
        db,
        state="active",
        source_cluster_ids=[c.id],
        avg_cluster_score=7.0,
        last_validated_at=_utcnow() - timedelta(days=35),
    )
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)

    # Demotion fires first (active + score < 5.0), retirement is elif'd away
    assert demoted == 1
    assert retired == 0
    assert re_promoted == 0

    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"


# ---------------------------------------------------------------------------
# ADR-005 B8 — project-breadth gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_b8_active_demoted_when_project_breadth_collapses(db: AsyncSession):
    """Active GlobalPattern with single-project history gets demoted on validate."""
    c = PromptCluster(label="c", state="active", domain="general", avg_score=8.0)
    db.add(c)
    await db.flush()

    gp = _make_gp(
        db, state="active", source_cluster_ids=[c.id],
        source_project_ids=["p1"],  # violates B8 gate
        avg_cluster_score=8.0,
    )
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)
    assert demoted == 1
    assert re_promoted == 0
    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"


@pytest.mark.asyncio
async def test_b8_demoted_not_re_promoted_when_project_breadth_insufficient(
    db: AsyncSession,
):
    """Demoted single-project pattern does NOT re-promote even at high score."""
    c = PromptCluster(label="c", state="active", domain="general", avg_score=9.0)
    db.add(c)
    await db.flush()

    gp = _make_gp(
        db, state="demoted", source_cluster_ids=[c.id],
        source_project_ids=["p1"],  # violates B8 gate
        avg_cluster_score=4.0,
    )
    await db.flush()

    demoted, re_promoted, retired = await _validate_existing_patterns(db)
    assert re_promoted == 0, "B8 blocks re-promotion when breadth is insufficient"
    await db.flush()
    await db.refresh(gp)
    assert gp.state == "demoted"


@pytest.mark.asyncio
async def test_b8_startup_repair_demotes_and_retires(db: AsyncSession):
    """repair_legacy_only_promotions: demote active, retire demoted, skip healthy."""
    from app.services.taxonomy.global_patterns import repair_legacy_only_promotions

    # Legacy-admitted active pattern with single project → should demote
    gp_active_narrow = _make_gp(
        db, state="active", source_cluster_ids=[],
        source_project_ids=["p1"], avg_cluster_score=8.0, seed=71,
    )
    # Legacy-admitted demoted pattern with single project → should retire
    gp_demoted_narrow = _make_gp(
        db, state="demoted", source_cluster_ids=[],
        source_project_ids=["p1"], avg_cluster_score=3.0, seed=72,
    )
    # Healthy cross-project pattern → should be untouched
    gp_ok = _make_gp(
        db, state="active", source_cluster_ids=[],
        source_project_ids=["p1", "p2", "p3"],
        avg_cluster_score=8.0, seed=73,
    )
    await db.flush()

    stats = await repair_legacy_only_promotions(db)
    assert stats["demoted"] == 1
    assert stats["retired"] == 1

    await db.refresh(gp_active_narrow)
    await db.refresh(gp_demoted_narrow)
    await db.refresh(gp_ok)
    assert gp_active_narrow.state == "demoted"
    assert gp_demoted_narrow.state == "retired"
    assert gp_ok.state == "active", "healthy cross-project patterns must be untouched"
