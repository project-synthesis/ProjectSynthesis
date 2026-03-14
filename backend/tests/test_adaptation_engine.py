"""Tests for the adaptation engine — feedback → pipeline parameter tuning."""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.adaptation_event import AdaptationEvent
from app.services.adaptation_engine import (
    DEFAULT_WEIGHTS,
    MAX_DAMPING,
    WEIGHT_LOWER_BOUND,
    WEIGHT_UPPER_BOUND,
    _purge_old_events,
    adjust_weights_from_deltas,
    compute_override_deltas,
    compute_threshold_from_feedback,
)


class TestDefaultWeights:
    def test_sum_to_one(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9

    def test_five_dimensions(self):
        assert len(DEFAULT_WEIGHTS) == 5


class TestComputeOverrideDeltas:
    def test_basic_override(self):
        feedbacks = [
            {"dimension_overrides": {"clarity_score": 8}, "scores": {"clarity_score": 6}},
        ]
        deltas = compute_override_deltas(feedbacks)
        assert "clarity_score" in deltas
        assert deltas["clarity_score"] > 0  # user says it's better than validator thought

    def test_no_overrides_returns_empty(self):
        feedbacks = [{"dimension_overrides": None, "scores": {}}]
        deltas = compute_override_deltas(feedbacks)
        assert deltas == {}


class TestAdjustWeights:
    def test_no_deltas_returns_defaults(self):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, {}, damping=0.15, min_samples=3)
        assert weights == DEFAULT_WEIGHTS

    def test_sum_to_one_after_adjustment(self):
        deltas = {"clarity_score": 2.0, "faithfulness_score": -1.5}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        assert abs(sum(weights.values()) - 1.0) < 1e-9

    def test_weights_within_bounds(self):
        # Extreme deltas
        deltas = {"clarity_score": 10.0, "specificity_score": -10.0}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        for w in weights.values():
            assert WEIGHT_LOWER_BOUND <= w <= WEIGHT_UPPER_BOUND

    def test_damping_limits_shift(self):
        deltas = {"clarity_score": 10.0}
        weights = adjust_weights_from_deltas(
            DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1,
        )
        shift = abs(weights["clarity_score"] - DEFAULT_WEIGHTS["clarity_score"])
        assert shift <= MAX_DAMPING + 0.01  # small epsilon for float math


class TestComputeThreshold:
    def test_default_with_no_feedback(self):
        t = compute_threshold_from_feedback([], default=5.0, bounds=(3.0, 8.0))
        assert t == 5.0

    def test_bounded_low(self):
        # All negative feedback on high-scoring prompts → lower threshold
        feedbacks = [{"rating": -1, "overall_score": 8.0}] * 10
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert t >= 3.0

    def test_bounded_high(self):
        feedbacks = [{"rating": 1, "overall_score": 3.0}] * 10
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert t <= 8.0


h_settings.register_profile("ci", max_examples=200, deadline=5000)
h_settings.register_profile("dev", max_examples=1000, deadline=10000)
h_settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "ci"))


class TestAdaptationPropertyBased:
    @given(
        deltas=st.dictionaries(
            keys=st.sampled_from(list(DEFAULT_WEIGHTS.keys())),
            values=st.floats(min_value=-5.0, max_value=5.0, allow_nan=False),
            max_size=5,
        )
    )
    def test_weights_always_sum_to_one(self, deltas):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1)
        assert abs(sum(weights.values()) - 1.0) < 1e-6

    @given(
        deltas=st.dictionaries(
            keys=st.sampled_from(list(DEFAULT_WEIGHTS.keys())),
            values=st.floats(min_value=-10.0, max_value=10.0, allow_nan=False),
            max_size=5,
        )
    )
    def test_all_weights_within_bounds(self, deltas):
        weights = adjust_weights_from_deltas(DEFAULT_WEIGHTS, deltas, damping=0.15, min_samples=1)
        for w in weights.values():
            assert WEIGHT_LOWER_BOUND - 1e-3 <= w <= WEIGHT_UPPER_BOUND + 1e-3

    @given(
        ratings=st.lists(
            st.tuples(
                st.sampled_from([-1, 0, 1]),
                st.floats(min_value=1.0, max_value=10.0, allow_nan=False),
            ),
            min_size=0, max_size=20,
        )
    )
    def test_threshold_always_bounded(self, ratings):
        feedbacks = [{"rating": r, "overall_score": s} for r, s in ratings]
        t = compute_threshold_from_feedback(feedbacks, default=5.0, bounds=(3.0, 8.0))
        assert 3.0 <= t <= 8.0


# ── _purge_old_events tests ─────────────────────────────────────────


@pytest.fixture
async def _purge_db():
    """In-memory async DB with AdaptationEvent table created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
    await engine.dispose()


class TestPurgeOldEvents:
    """Tests for _purge_old_events retention boundary logic.

    The function deletes AdaptationEvent rows older than
    ADAPTATION_EVENT_RETENTION_DAYS (default 90 days).
    """

    @pytest.mark.asyncio
    async def test_purge_deletes_old_events(self, _purge_db):
        """Events older than retention period should be deleted."""
        db = _purge_db
        user_id = "test-user"
        now = datetime.now(timezone.utc)

        # Insert an old event (100 days ago — beyond 90-day retention)
        old_event = AdaptationEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type="recompute",
            created_at=now - timedelta(days=100),
        )
        db.add(old_event)

        # Insert a recent event (10 days ago — within retention)
        recent_event = AdaptationEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type="recompute",
            created_at=now - timedelta(days=10),
        )
        db.add(recent_event)
        await db.flush()

        await _purge_old_events(user_id, db)
        await db.flush()

        # Verify: only the recent event survives
        from sqlalchemy import select
        result = await db.execute(
            select(AdaptationEvent).where(AdaptationEvent.user_id == user_id)
        )
        remaining = result.scalars().all()
        assert len(remaining) == 1
        assert remaining[0].id == recent_event.id

    @pytest.mark.asyncio
    async def test_purge_keeps_boundary_event(self, _purge_db):
        """Event exactly at the retention boundary should NOT be deleted."""
        db = _purge_db
        user_id = "test-user"
        now = datetime.now(timezone.utc)

        # Event exactly at the boundary (89 days ago — just within 90-day retention)
        boundary_event = AdaptationEvent(
            id=str(uuid.uuid4()),
            user_id=user_id,
            event_type="recompute",
            created_at=now - timedelta(days=89),
        )
        db.add(boundary_event)
        await db.flush()

        await _purge_old_events(user_id, db)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(
            select(AdaptationEvent).where(AdaptationEvent.user_id == user_id)
        )
        remaining = result.scalars().all()
        assert len(remaining) == 1

    @pytest.mark.asyncio
    async def test_purge_only_affects_target_user(self, _purge_db):
        """Purge for user A should not delete user B's events."""
        db = _purge_db
        now = datetime.now(timezone.utc)

        old_event_a = AdaptationEvent(
            id=str(uuid.uuid4()),
            user_id="user-a",
            event_type="recompute",
            created_at=now - timedelta(days=100),
        )
        old_event_b = AdaptationEvent(
            id=str(uuid.uuid4()),
            user_id="user-b",
            event_type="recompute",
            created_at=now - timedelta(days=100),
        )
        db.add(old_event_a)
        db.add(old_event_b)
        await db.flush()

        await _purge_old_events("user-a", db)
        await db.flush()

        from sqlalchemy import select
        result = await db.execute(select(AdaptationEvent))
        remaining = result.scalars().all()
        # user-a's old event deleted, user-b's old event preserved
        assert len(remaining) == 1
        assert remaining[0].user_id == "user-b"
