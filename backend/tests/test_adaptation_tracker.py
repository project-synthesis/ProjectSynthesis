"""Tests for AdaptationTracker — TDD: tests written before implementation."""

from unittest.mock import patch

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


async def test_cleanup_orphaned_affinities(db_session: AsyncSession) -> None:
    """cleanup_orphaned_affinities removes rows for strategies not on disk."""
    tracker = AdaptationTracker(db_session)

    # Create affinities for a real strategy and a phantom one
    await tracker.update_affinity("coding", "chain-of-thought", "thumbs_up")
    await tracker.update_affinity("coding", "phantom-strategy", "thumbs_up")

    affinities = await tracker.get_affinities("coding")
    assert "phantom-strategy" in affinities
    assert "chain-of-thought" in affinities

    # Mock list_strategies to return only the real strategy
    with patch(
        "app.services.strategy_loader.StrategyLoader.list_strategies",
        return_value=["auto", "chain-of-thought", "few-shot"],
    ):
        deleted = await tracker.cleanup_orphaned_affinities()

    assert deleted == 1

    affinities = await tracker.get_affinities("coding")
    assert "phantom-strategy" not in affinities
    assert "chain-of-thought" in affinities


async def test_cleanup_no_orphans(db_session: AsyncSession) -> None:
    """cleanup_orphaned_affinities returns 0 when all strategies exist on disk."""
    tracker = AdaptationTracker(db_session)
    await tracker.update_affinity("coding", "auto", "thumbs_up")

    with patch(
        "app.services.strategy_loader.StrategyLoader.list_strategies",
        return_value=["auto", "chain-of-thought"],
    ):
        deleted = await tracker.cleanup_orphaned_affinities()

    assert deleted == 0


async def test_cleanup_skips_when_no_strategies_on_disk(db_session: AsyncSession) -> None:
    """cleanup_orphaned_affinities skips cleanup when no strategies exist on disk."""
    tracker = AdaptationTracker(db_session)
    await tracker.update_affinity("coding", "auto", "thumbs_up")

    with patch(
        "app.services.strategy_loader.StrategyLoader.list_strategies",
        return_value=[],
    ):
        deleted = await tracker.cleanup_orphaned_affinities()

    assert deleted == 0
    # Row should still exist
    affinities = await tracker.get_affinities("coding")
    assert "auto" in affinities


async def test_get_blocked_strategies(db_session: AsyncSession) -> None:
    """get_blocked_strategies returns strategies with approval_rate < 0.3 and ≥5 feedbacks."""
    tracker = AdaptationTracker(db_session)

    # 5 thumbs_down for few-shot → approval_rate = 0.0 → should be blocked
    for _ in range(5):
        await tracker.update_affinity("coding", "few-shot", "thumbs_down")

    # 5 thumbs_up for chain-of-thought → approval_rate = 1.0 → should NOT be blocked
    for _ in range(5):
        await tracker.update_affinity("coding", "chain-of-thought", "thumbs_up")

    # 3 thumbs_down for auto → only 3 feedbacks → below MIN_FEEDBACK threshold
    for _ in range(3):
        await tracker.update_affinity("coding", "auto", "thumbs_down")

    blocked = await tracker.get_blocked_strategies("coding")

    assert "few-shot" in blocked
    assert "chain-of-thought" not in blocked
    assert "auto" not in blocked  # Not enough feedbacks


async def test_get_blocked_strategies_empty(db_session: AsyncSession) -> None:
    """get_blocked_strategies returns empty set when no data exists."""
    tracker = AdaptationTracker(db_session)
    blocked = await tracker.get_blocked_strategies("nonexistent")
    assert blocked == set()


async def test_get_blocked_strategies_mixed_approval(db_session: AsyncSession) -> None:
    """Strategy with approval_rate exactly at threshold (0.3) is NOT blocked."""
    tracker = AdaptationTracker(db_session)

    # 3 thumbs_up + 7 thumbs_down → approval_rate = 0.3 → exactly at threshold, not blocked
    for _ in range(3):
        await tracker.update_affinity("writing", "meta-prompting", "thumbs_up")
    for _ in range(7):
        await tracker.update_affinity("writing", "meta-prompting", "thumbs_down")

    blocked = await tracker.get_blocked_strategies("writing")
    assert "meta-prompting" not in blocked
