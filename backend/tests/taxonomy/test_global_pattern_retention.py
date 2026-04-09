"""Tests for GlobalPattern retention cap enforcement (Phase 2B).

Dedicated tests for _enforce_retention_cap: eviction order, cap boundary,
state transitions, and event logging.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GlobalPattern
from app.services.taxonomy._constants import _utcnow
from app.services.taxonomy.global_patterns import _enforce_retention_cap

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
    pattern_text: str = "pattern",
    state: str = "active",
    last_validated_at=None,
    seed: int = 42,
) -> GlobalPattern:
    """Create and add a GlobalPattern to the session (not yet flushed)."""
    gp = GlobalPattern(
        pattern_text=pattern_text,
        embedding=_unit_vec(seed).tobytes(),
        source_cluster_ids=[],
        source_project_ids=[],
        cross_project_count=0,
        global_source_count=0,
        avg_cluster_score=7.0,
        state=state,
        last_validated_at=last_validated_at,
    )
    db.add(gp)
    return gp


# ---------------------------------------------------------------------------
# Cap boundary: no eviction when at or below cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_eviction_at_or_below_cap(db: AsyncSession):
    """No eviction when active+demoted count <= 500 (the cap)."""
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        gp_mod.GLOBAL_PATTERN_CAP = 3

        # Create exactly 3 (= cap): 2 active + 1 demoted
        now = _utcnow()
        _make_gp(db, pattern_text="a-0", state="active", last_validated_at=now, seed=1)
        _make_gp(db, pattern_text="a-1", state="active", last_validated_at=now, seed=2)
        _make_gp(db, pattern_text="d-0", state="demoted", last_validated_at=now, seed=3)
        await db.flush()

        evicted = await _enforce_retention_cap(db)
        assert evicted == 0

        # All still non-retired
        result = await db.execute(
            select(GlobalPattern).where(GlobalPattern.state.in_(["active", "demoted"]))
        )
        assert len(list(result.scalars().all())) == 3
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap


# ---------------------------------------------------------------------------
# Eviction order: demoted LRU first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evict_demoted_lru_first(db: AsyncSession):
    """Demoted patterns are evicted before active ones, oldest first."""
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        gp_mod.GLOBAL_PATTERN_CAP = 2

        now = _utcnow()
        # 2 active (recent) + 2 demoted (one older than the other)
        _make_gp(db, pattern_text="active-0", state="active", last_validated_at=now, seed=10)
        _make_gp(db, pattern_text="active-1", state="active", last_validated_at=now, seed=11)
        _make_gp(
            db, pattern_text="demoted-old", state="demoted",
            last_validated_at=now - timedelta(hours=2), seed=12,
        )
        _make_gp(
            db, pattern_text="demoted-new", state="demoted",
            last_validated_at=now - timedelta(hours=1), seed=13,
        )
        await db.flush()

        # 4 total, cap=2 -> evict 2. Should evict both demoted (LRU order).
        evicted = await _enforce_retention_cap(db)
        assert evicted == 2

        await db.flush()

        # Only active patterns should remain non-retired
        result = await db.execute(
            select(GlobalPattern).where(GlobalPattern.state.in_(["active", "demoted"]))
        )
        remaining = list(result.scalars().all())
        assert len(remaining) == 2
        assert all(gp.state == "active" for gp in remaining)

        # Both demoted should be retired
        result = await db.execute(
            select(GlobalPattern).where(GlobalPattern.state == "retired")
        )
        retired = list(result.scalars().all())
        assert len(retired) == 2
        retired_texts = {gp.pattern_text for gp in retired}
        assert retired_texts == {"demoted-old", "demoted-new"}
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap


# ---------------------------------------------------------------------------
# Eviction order: then active LRU if still over cap
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evict_active_lru_after_demoted_exhausted(db: AsyncSession):
    """Active LRU evicted when demoted pool is insufficient to reach cap."""
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        gp_mod.GLOBAL_PATTERN_CAP = 1

        now = _utcnow()
        # 2 active (one older) + 1 demoted = 3 total, cap=1 -> evict 2
        _make_gp(
            db, pattern_text="active-old", state="active",
            last_validated_at=now - timedelta(hours=3), seed=20,
        )
        _make_gp(
            db, pattern_text="active-new", state="active",
            last_validated_at=now, seed=21,
        )
        _make_gp(
            db, pattern_text="demoted-0", state="demoted",
            last_validated_at=now - timedelta(hours=1), seed=22,
        )
        await db.flush()

        evicted = await _enforce_retention_cap(db)
        assert evicted == 2

        await db.flush()

        # Only the newest active should remain
        result = await db.execute(
            select(GlobalPattern).where(GlobalPattern.state.in_(["active", "demoted"]))
        )
        remaining = list(result.scalars().all())
        assert len(remaining) == 1
        assert remaining[0].pattern_text == "active-new"
        assert remaining[0].state == "active"
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap


# ---------------------------------------------------------------------------
# Eviction sets state='retired' (not DELETE)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eviction_sets_retired_not_deleted(db: AsyncSession):
    """Evicted patterns get state='retired', not deleted from DB."""
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        gp_mod.GLOBAL_PATTERN_CAP = 1

        now = _utcnow()
        _make_gp(db, pattern_text="keep", state="active", last_validated_at=now, seed=30)
        _make_gp(
            db, pattern_text="evict-me", state="demoted",
            last_validated_at=now - timedelta(hours=1), seed=31,
        )
        await db.flush()

        evicted = await _enforce_retention_cap(db)
        assert evicted == 1

        await db.flush()

        # Total records should still be 2 (not deleted)
        result = await db.execute(select(GlobalPattern))
        all_gps = list(result.scalars().all())
        assert len(all_gps) == 2

        # The evicted one is retired, not gone
        evicted_gp = next(gp for gp in all_gps if gp.pattern_text == "evict-me")
        assert evicted_gp.state == "retired"
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap


# ---------------------------------------------------------------------------
# Retired patterns excluded from cap count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retired_excluded_from_cap_count(db: AsyncSession):
    """Retired patterns don't count toward active+demoted cap."""
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        gp_mod.GLOBAL_PATTERN_CAP = 2

        now = _utcnow()
        # 2 active + 3 retired = 5 total, but only 2 count toward cap
        _make_gp(db, pattern_text="active-0", state="active", last_validated_at=now, seed=40)
        _make_gp(db, pattern_text="active-1", state="active", last_validated_at=now, seed=41)
        _make_gp(db, pattern_text="retired-0", state="retired", last_validated_at=now, seed=42)
        _make_gp(db, pattern_text="retired-1", state="retired", last_validated_at=now, seed=43)
        _make_gp(db, pattern_text="retired-2", state="retired", last_validated_at=now, seed=44)
        await db.flush()

        evicted = await _enforce_retention_cap(db)
        assert evicted == 0

        # All active patterns remain
        result = await db.execute(
            select(GlobalPattern).where(GlobalPattern.state == "active")
        )
        assert len(list(result.scalars().all())) == 2
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap


# ---------------------------------------------------------------------------
# Eviction fires _log_event("retired") with reason="evicted"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_eviction_fires_log_event_with_reason_evicted(db: AsyncSession):
    """Eviction fires _log_event('retired') with reason='evicted'."""
    import app.services.taxonomy.global_patterns as gp_mod
    original_cap = gp_mod.GLOBAL_PATTERN_CAP
    try:
        gp_mod.GLOBAL_PATTERN_CAP = 1

        now = _utcnow()
        _make_gp(db, pattern_text="keep", state="active", last_validated_at=now, seed=50)
        _make_gp(
            db, pattern_text="evict", state="demoted",
            last_validated_at=now - timedelta(hours=1), seed=51,
        )
        await db.flush()

        logged_events: list[tuple[str, str, dict]] = []

        def _capture_event(decision: str, pattern_id: str, context: dict) -> None:
            logged_events.append((decision, pattern_id, context))

        with patch.object(gp_mod, "_log_event", side_effect=_capture_event):
            evicted = await _enforce_retention_cap(db)

        assert evicted == 1
        assert len(logged_events) == 1

        decision, _pattern_id, context = logged_events[0]
        assert decision == "retired"
        assert context["reason"] == "evicted"
        assert context["was_state"] == "demoted"
    finally:
        gp_mod.GLOBAL_PATTERN_CAP = original_cap
