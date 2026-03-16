"""Tests for AdaptationTracker — TDD: tests written before implementation."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.adaptation_tracker import AdaptationTracker

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_update_affinity_thumbs_up(db_session: AsyncSession) -> None:
    """update_affinity with thumbs_up creates a row with thumbs_up=1 and approval_rate=1.0."""
    tracker = AdaptationTracker(db_session)

    await tracker.update_affinity("generation", "chain-of-thought", "thumbs_up")

    affinities = await tracker.get_affinities("generation")
    assert "chain-of-thought" in affinities
    data = affinities["chain-of-thought"]
    assert data["thumbs_up"] == 1
    assert data["thumbs_down"] == 0
    assert data["approval_rate"] == 1.0


async def test_update_affinity_thumbs_down(db_session: AsyncSession) -> None:
    """update_affinity with thumbs_down creates a row with thumbs_down=1 and approval_rate=0.0."""
    tracker = AdaptationTracker(db_session)

    await tracker.update_affinity("classification", "few-shot", "thumbs_down")

    affinities = await tracker.get_affinities("classification")
    assert "few-shot" in affinities
    data = affinities["few-shot"]
    assert data["thumbs_up"] == 0
    assert data["thumbs_down"] == 1
    assert data["approval_rate"] == 0.0


async def test_approval_rate_computed(db_session: AsyncSession) -> None:
    """2 thumbs_up + 1 thumbs_down yields approval_rate ~0.667."""
    tracker = AdaptationTracker(db_session)

    await tracker.update_affinity("generation", "zero_shot", "thumbs_up")
    await tracker.update_affinity("generation", "zero_shot", "thumbs_up")
    await tracker.update_affinity("generation", "zero_shot", "thumbs_down")

    affinities = await tracker.get_affinities("generation")
    data = affinities["zero_shot"]
    assert data["thumbs_up"] == 2
    assert data["thumbs_down"] == 1
    assert abs(data["approval_rate"] - 2 / 3) < 1e-6


async def test_get_affinities_empty(db_session: AsyncSession) -> None:
    """get_affinities returns {} for an unknown task_type."""
    tracker = AdaptationTracker(db_session)

    result = await tracker.get_affinities("nonexistent_task_type")

    assert result == {}


async def test_render_adaptation_state(db_session: AsyncSession) -> None:
    """render_adaptation_state returns a non-empty string containing the strategy name."""
    tracker = AdaptationTracker(db_session)
    await tracker.update_affinity("generation", "chain-of-thought", "thumbs_up")

    rendered = await tracker.render_adaptation_state("generation")

    assert rendered is not None
    assert isinstance(rendered, str)
    assert len(rendered) > 0
    assert "chain-of-thought" in rendered


async def test_render_returns_none_when_no_data(db_session: AsyncSession) -> None:
    """render_adaptation_state returns None when there is no data for the task_type."""
    tracker = AdaptationTracker(db_session)

    rendered = await tracker.render_adaptation_state("unknown_task_type")

    assert rendered is None


async def test_degenerate_detection(db_session: AsyncSession) -> None:
    """check_degenerate returns True when 11 feedbacks are all the same rating."""
    tracker = AdaptationTracker(db_session)

    for _ in range(11):
        await tracker.update_affinity("generation", "few-shot", "thumbs_up")

    result = await tracker.check_degenerate("generation", "few-shot")

    assert result is True


async def test_not_degenerate_with_mixed_feedback(db_session: AsyncSession) -> None:
    """check_degenerate returns False with 8 thumbs_up and 3 thumbs_down (not >90% same)."""
    tracker = AdaptationTracker(db_session)

    for _ in range(8):
        await tracker.update_affinity("generation", "chain-of-thought", "thumbs_up")
    for _ in range(3):
        await tracker.update_affinity("generation", "chain-of-thought", "thumbs_down")

    result = await tracker.check_degenerate("generation", "chain-of-thought")

    assert result is False
