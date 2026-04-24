"""Tests for ``services/signal_adjuster.py`` (plan item #3).

The adjuster turns ``TaskTypeTelemetry`` rows into durable heuristic-signal
additions via an active-learning oracle — it reads recent telemetry,
tallies ``(token, task_type)`` pairs, and merges frequent pairs into
``_TASK_TYPE_SIGNALS`` at a conservative weight.  These tests pin the
core behaviours:

1. Frequent token → task_type pairs → merged into signal table.
2. Below-threshold tokens ignored.
3. Tokens already registered aren't overwritten.
4. Graceful degradation when telemetry table is missing.
5. Observability events emitted per change.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, TaskTypeTelemetry
from app.services.signal_adjuster import (
    SIGNAL_ADJUSTER_MIN_FREQUENCY,
    SIGNAL_ADJUSTER_WEIGHT,
    adjust_signals_from_telemetry,
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


@pytest.fixture(autouse=True)
def _reset_signals():
    """Snapshot and restore ``_TASK_TYPE_SIGNALS`` around each test —
    ``set_task_type_signals()`` mutates module state.
    """
    from app.services import task_type_classifier as ttc
    original = {k: list(v) for k, v in ttc._TASK_TYPE_SIGNALS.items()}
    yield
    ttc.set_task_type_signals(original)


async def _seed_telemetry(
    db: AsyncSession,
    prompt: str,
    task_type: str,
    *,
    n: int = 1,
    age_days: float = 0.0,
) -> None:
    """Insert ``n`` TaskTypeTelemetry rows dated ``age_days`` ago."""
    when = datetime.now(timezone.utc) - timedelta(days=age_days)
    for _ in range(n):
        db.add(TaskTypeTelemetry(
            id=str(uuid.uuid4()),
            created_at=when,
            raw_prompt=prompt,
            task_type=task_type,
            domain="general",
            source="haiku_fallback",
        ))
    await db.commit()


# ---------------------------------------------------------------------------
# Core merging behaviour
# ---------------------------------------------------------------------------


async def test_frequent_token_pair_merged_into_signal_table(db: AsyncSession):
    """A token that appears in ≥SIGNAL_ADJUSTER_MIN_FREQUENCY rows for the
    same task_type gets added to that task_type's signal list at
    SIGNAL_ADJUSTER_WEIGHT.
    """
    from app.services import task_type_classifier as ttc

    # Seed 4 telemetry rows all classified as 'coding' containing
    # "kubecontrol" — made-up jargon not in the static table.
    for i in range(4):
        await _seed_telemetry(
            db,
            prompt=f"Deploy kubecontrol cluster {i} with sharded replicas",
            task_type="coding",
        )

    result = await adjust_signals_from_telemetry(db)

    assert result.rows_processed == 4
    assert result.signals_added >= 1
    assert "coding" in result.task_types_touched

    # The new signal is present in the merged signal table.
    coding_signals = dict(ttc.get_task_type_signals()["coding"])
    assert "kubecontrol" in coding_signals
    assert coding_signals["kubecontrol"] == SIGNAL_ADJUSTER_WEIGHT


async def test_below_threshold_tokens_ignored(db: AsyncSession):
    """Tokens that appear in fewer than MIN_FREQUENCY telemetry rows
    must not be merged — noise guard.
    """
    from app.services import task_type_classifier as ttc

    # Only 2 occurrences (below default MIN_FREQUENCY=3).
    for i in range(SIGNAL_ADJUSTER_MIN_FREQUENCY - 1):
        await _seed_telemetry(
            db,
            prompt=f"Test zebrastack topology {i}",
            task_type="analysis",
        )

    result = await adjust_signals_from_telemetry(db)

    assert result.signals_added == 0
    analysis_signals = dict(ttc.get_task_type_signals()["analysis"])
    assert "zebrastack" not in analysis_signals


async def test_existing_keyword_not_overwritten(db: AsyncSession):
    """Tokens already in ``_TASK_TYPE_SIGNALS`` keep their original
    weight — the adjuster appends only novel tokens.  Prevents the
    oracle from eroding hand-tuned compound-signal weights.
    """
    from app.services import task_type_classifier as ttc

    # "api" is already a coding keyword at weight 0.8 (see task_type_classifier.py).
    original_weight = dict(ttc.get_task_type_signals()["coding"]).get("api")
    assert original_weight is not None, "precondition: 'api' must exist"

    for i in range(5):
        await _seed_telemetry(
            db,
            prompt=f"Build an api endpoint number {i}",
            task_type="coding",
        )

    await adjust_signals_from_telemetry(db)

    # Weight unchanged.
    coding_signals_after = dict(ttc.get_task_type_signals()["coding"])
    assert coding_signals_after.get("api") == original_weight, (
        f"existing 'api' weight changed from {original_weight} to "
        f"{coding_signals_after.get('api')}"
    )


async def test_stopwords_excluded_from_merging(db: AsyncSession):
    """Structural words (``the``, ``a``, ``is``) must never become signals
    regardless of frequency.
    """
    from app.services import task_type_classifier as ttc

    for i in range(10):
        await _seed_telemetry(
            db,
            prompt="the data is the answer the question",
            task_type="analysis",
        )

    await adjust_signals_from_telemetry(db)

    analysis_signals = dict(ttc.get_task_type_signals()["analysis"])
    assert "the" not in analysis_signals
    assert "is" not in analysis_signals


async def test_lookback_filter_excludes_old_rows(db: AsyncSession):
    """Rows older than ``lookback_days`` must be excluded from the tally."""
    from app.services import task_type_classifier as ttc

    # Seed rows OUTSIDE the default 7-day window.
    for i in range(5):
        await _seed_telemetry(
            db,
            prompt=f"Archaeology grammar {i}",  # unique-ish tokens
            task_type="writing",
            age_days=30,
        )

    result = await adjust_signals_from_telemetry(db, lookback_days=7)

    assert result.rows_processed == 0
    writing_signals = dict(ttc.get_task_type_signals()["writing"])
    assert "archaeology" not in writing_signals


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


async def test_missing_telemetry_table_returns_empty_result(db: AsyncSession):
    """``OperationalError`` (table missing — fresh install before migration)
    returns an empty result without crashing the caller.
    """
    with patch(
        "app.services.signal_adjuster.select",
        side_effect=Exception("synthetic — forces exception path"),
    ):
        # Patch select to raise OperationalError-like exception via our helper
        pass  # The direct OperationalError path is exercised manually below.

    # Simulate OperationalError by patching db.execute.
    from sqlalchemy.exc import OperationalError

    async def raise_op_err(*args, **kwargs):
        raise OperationalError("synthetic", None, Exception("no such table"))

    original_execute = db.execute
    db.execute = raise_op_err  # type: ignore[method-assign]
    try:
        result = await adjust_signals_from_telemetry(db)
    finally:
        db.execute = original_execute  # type: ignore[method-assign]

    assert result.rows_processed == 0
    assert result.signals_added == 0


async def test_empty_telemetry_is_noop(db: AsyncSession):
    """No rows → no-op, no crash, empty result."""
    result = await adjust_signals_from_telemetry(db)
    assert result.rows_processed == 0
    assert result.signals_added == 0


# ---------------------------------------------------------------------------
# Observability
# ---------------------------------------------------------------------------


async def test_signal_adjusted_event_emitted_per_change(db: AsyncSession):
    """Each merged signal produces one ``signal_adjusted`` event so the
    ActivityPanel surfaces the active-learning cycle.
    """
    for i in range(4):
        await _seed_telemetry(
            db,
            prompt=f"Run quokkastream aggregation pass {i}",
            task_type="data",
        )

    mock_logger = MagicMock()
    with patch(
        "app.services.signal_adjuster.get_event_logger",
        return_value=mock_logger,
    ):
        # Import locally inside the patch scope so `get_event_logger` is
        # looked up off the patched module.
        from app.services.signal_adjuster import (
            adjust_signals_from_telemetry as _adjust,
        )
        result = await _adjust(db)

    # One log_decision call per signal added.
    assert mock_logger.log_decision.call_count >= 1
    assert mock_logger.log_decision.call_count == result.signals_added

    # Check payload shape on first call.
    first_call = mock_logger.log_decision.call_args_list[0]
    assert first_call.kwargs["op"] == "signal_adjuster"
    assert first_call.kwargs["decision"] == "signal_adjusted"
    ctx = first_call.kwargs["context"]
    assert "token" in ctx
    assert "task_type" in ctx
    assert "telemetry_count" in ctx
    assert "weight" in ctx
