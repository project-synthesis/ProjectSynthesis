"""Tests for OptimizationService — TDD: write tests first, then implement."""

import math
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization
from app.services.optimization_service import OptimizationService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_opt(
    *,
    raw_prompt: str = "Test prompt",
    task_type: str = "generation",
    status: str = "completed",
    overall_score: float | None = None,
    score_clarity: float | None = None,
    score_specificity: float | None = None,
    score_structure: float | None = None,
    score_faithfulness: float | None = None,
    score_conciseness: float | None = None,
    strategy_used: str | None = None,
    duration_ms: int | None = None,
    trace_id: str | None = None,
) -> Optimization:
    """Helper to construct an Optimization instance with sensible defaults."""
    return Optimization(
        id=str(uuid.uuid4()),
        created_at=datetime.now(timezone.utc),
        raw_prompt=raw_prompt,
        task_type=task_type,
        status=status,
        overall_score=overall_score,
        score_clarity=score_clarity,
        score_specificity=score_specificity,
        score_structure=score_structure,
        score_faithfulness=score_faithfulness,
        score_conciseness=score_conciseness,
        strategy_used=strategy_used,
        duration_ms=duration_ms,
        trace_id=trace_id,
    )


@pytest.fixture
async def sample_opts(db_session: AsyncSession) -> list[Optimization]:
    """Persist 3 Optimization rows and return them."""
    rows = [
        _make_opt(
            raw_prompt="First prompt",
            task_type="generation",
            status="completed",
            overall_score=0.9,
            score_clarity=0.85,
            score_specificity=0.90,
            score_structure=0.88,
            score_faithfulness=0.92,
            score_conciseness=0.87,
            strategy_used="chain-of-thought",
            duration_ms=1200,
            trace_id="trace-aaa",
        ),
        _make_opt(
            raw_prompt="Second prompt",
            task_type="classification",
            status="completed",
            overall_score=0.7,
            score_clarity=0.65,
            score_specificity=0.70,
            score_structure=0.72,
            score_faithfulness=0.68,
            score_conciseness=0.75,
            strategy_used="few-shot",
            duration_ms=800,
            trace_id="trace-bbb",
        ),
        _make_opt(
            raw_prompt="Third prompt",
            task_type="generation",
            status="failed",
            overall_score=0.5,
            score_clarity=0.45,
            score_specificity=0.50,
            score_structure=0.52,
            score_faithfulness=0.48,
            score_conciseness=0.55,
            strategy_used="zero_shot",
            duration_ms=400,
            trace_id="trace-ccc",
        ),
    ]
    for row in rows:
        db_session.add(row)
    await db_session.commit()
    # Refresh to get server-side defaults
    for row in rows:
        await db_session.refresh(row)
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_get_by_id(db_session: AsyncSession, sample_opts: list[Optimization]) -> None:
    """get_by_id returns the correct Optimization when the id exists."""
    svc = OptimizationService(db_session)
    target = sample_opts[0]

    result = await svc.get_by_id(target.id)

    assert result is not None
    assert result.id == target.id
    assert result.raw_prompt == "First prompt"


async def test_get_by_id_not_found(db_session: AsyncSession) -> None:
    """get_by_id returns None when no row matches the id."""
    svc = OptimizationService(db_session)

    result = await svc.get_by_id("nonexistent-id-00000")

    assert result is None


async def test_list_all(db_session: AsyncSession, sample_opts: list[Optimization]) -> None:
    """list_optimizations with no filters returns all 3 rows."""
    svc = OptimizationService(db_session)

    page = await svc.list_optimizations()

    assert page["total"] == 3
    assert page["count"] == 3
    assert page["offset"] == 0
    assert len(page["items"]) == 3
    assert page["has_more"] is False
    assert page["next_offset"] is None


async def test_list_with_offset_limit(
    db_session: AsyncSession, sample_opts: list[Optimization]
) -> None:
    """Pagination: offset=1, limit=1 returns exactly 1 item with correct metadata."""
    svc = OptimizationService(db_session)

    page = await svc.list_optimizations(offset=1, limit=1)

    assert page["total"] == 3
    assert page["count"] == 1
    assert page["offset"] == 1
    assert len(page["items"]) == 1
    assert page["has_more"] is True
    assert page["next_offset"] == 2


async def test_list_sort_by_score_desc(
    db_session: AsyncSession, sample_opts: list[Optimization]
) -> None:
    """sort_by=overall_score, sort_order=desc returns rows in descending score order."""
    svc = OptimizationService(db_session)

    page = await svc.list_optimizations(sort_by="overall_score", sort_order="desc")

    scores = [item.overall_score for item in page["items"]]
    assert scores == sorted(scores, reverse=True), f"Expected descending, got {scores}"


async def test_list_filter_by_task_type(
    db_session: AsyncSession, sample_opts: list[Optimization]
) -> None:
    """task_type filter returns only rows with matching task_type."""
    svc = OptimizationService(db_session)

    page = await svc.list_optimizations(task_type="classification")

    assert page["total"] == 1
    assert page["count"] == 1
    assert page["items"][0].task_type == "classification"


async def test_list_filter_by_status(
    db_session: AsyncSession, sample_opts: list[Optimization]
) -> None:
    """status filter returns only rows with matching status."""
    svc = OptimizationService(db_session)

    page = await svc.list_optimizations(status="failed")

    assert page["total"] == 1
    assert page["count"] == 1
    assert page["items"][0].status == "failed"


async def test_invalid_sort_column_rejected(db_session: AsyncSession) -> None:
    """An unrecognised sort column raises ValueError."""
    svc = OptimizationService(db_session)

    with pytest.raises(ValueError, match="Invalid sort column"):
        await svc.list_optimizations(sort_by="nonexistent_column")


async def test_get_by_trace_id(db_session: AsyncSession, sample_opts: list[Optimization]) -> None:
    """get_by_trace_id returns the correct Optimization when trace_id matches."""
    svc = OptimizationService(db_session)

    result = await svc.get_by_trace_id("trace-bbb")

    assert result is not None
    assert result.trace_id == "trace-bbb"
    assert result.raw_prompt == "Second prompt"


async def test_score_distribution(
    db_session: AsyncSession, sample_opts: list[Optimization]
) -> None:
    """get_score_distribution returns per-dimension count/mean/stddev."""
    svc = OptimizationService(db_session)

    dist = await svc.get_score_distribution()

    # Must include overall_score and all score_* columns
    expected_dimensions = {
        "overall_score",
        "score_clarity",
        "score_specificity",
        "score_structure",
        "score_faithfulness",
        "score_conciseness",
    }
    assert expected_dimensions.issubset(dist.keys()), (
        f"Missing dimensions: {expected_dimensions - dist.keys()}"
    )

    overall = dist["overall_score"]
    assert overall["count"] == 3
    # Mean of 0.9, 0.7, 0.5 = 0.7
    assert abs(overall["mean"] - 0.7) < 1e-6

    # Stddev of population [0.9, 0.7, 0.5]:
    # variance = ((0.2)^2 + 0^2 + (-0.2)^2) / 3 = 0.04/3 + 0 + 0.04/3 = 0.08/3
    expected_stddev = math.sqrt(((0.9 - 0.7) ** 2 + (0.7 - 0.7) ** 2 + (0.5 - 0.7) ** 2) / 3)
    assert abs(overall["stddev"] - expected_stddev) < 1e-4

    # Each dimension entry must have all three keys
    for dim, stats in dist.items():
        assert "count" in stats, f"{dim} missing count"
        assert "mean" in stats, f"{dim} missing mean"
        assert "stddev" in stats, f"{dim} missing stddev"


# ---------------------------------------------------------------------------
# get_enrichment_profile_effectiveness (E2 — #9)
# ---------------------------------------------------------------------------


async def test_enrichment_profile_effectiveness_aggregates_by_profile(
    db_session: AsyncSession,
) -> None:
    """Aggregates recent completed optimizations by ``enrichment_profile``
    and reports per-profile count + avg overall_score + avg improvement_score.

    The profile is nested at ``context_sources["enrichment_meta"]["enrichment_profile"]``
    so the aggregation is Python-side (portable across SQLite/PostgreSQL
    without JSON-path extraction dialects).
    """
    # 3 code_aware rows
    for score, improvement in [(8.0, 2.1), (7.5, 1.8), (8.5, 2.5)]:
        row = _make_opt(
            status="completed", overall_score=score,
        )
        row.improvement_score = improvement
        row.context_sources = {"enrichment_meta": {"enrichment_profile": "code_aware"}}
        db_session.add(row)
    # 2 knowledge_work rows
    for score, improvement in [(7.0, 1.5), (7.2, 1.7)]:
        row = _make_opt(
            status="completed", overall_score=score,
        )
        row.improvement_score = improvement
        row.context_sources = {"enrichment_meta": {"enrichment_profile": "knowledge_work"}}
        db_session.add(row)
    # 1 cold_start row
    row = _make_opt(status="completed", overall_score=6.5)
    row.improvement_score = 0.9
    row.context_sources = {"enrichment_meta": {"enrichment_profile": "cold_start"}}
    db_session.add(row)
    # 1 row with no profile — must be ignored
    row = _make_opt(status="completed", overall_score=7.7)
    row.improvement_score = 2.0
    row.context_sources = None
    db_session.add(row)
    # 1 failed row — must be ignored
    row = _make_opt(status="failed", overall_score=3.0)
    row.context_sources = {"enrichment_meta": {"enrichment_profile": "code_aware"}}
    db_session.add(row)
    await db_session.commit()

    svc = OptimizationService(db_session)
    result = await svc.get_enrichment_profile_effectiveness()

    assert set(result.keys()) == {"code_aware", "knowledge_work", "cold_start"}, (
        f"Unexpected profile set: {set(result.keys())}"
    )

    # code_aware: 3 rows, avg 8.0, avg improvement 2.133
    ca = result["code_aware"]
    assert ca["count"] == 3
    assert abs(ca["avg_overall_score"] - 8.0) < 1e-6
    assert abs(ca["avg_improvement_score"] - 2.1333) < 1e-3

    # knowledge_work: 2 rows
    kw = result["knowledge_work"]
    assert kw["count"] == 2
    assert abs(kw["avg_overall_score"] - 7.1) < 1e-6

    # cold_start: 1 row
    cs = result["cold_start"]
    assert cs["count"] == 1
    assert abs(cs["avg_overall_score"] - 6.5) < 1e-6


async def test_enrichment_profile_effectiveness_empty_when_no_data(
    db_session: AsyncSession,
) -> None:
    """Returns an empty dict when no optimizations have enrichment_profile."""
    # One completed row without enrichment_profile
    row = _make_opt(status="completed", overall_score=7.0)
    row.context_sources = None
    db_session.add(row)
    await db_session.commit()

    svc = OptimizationService(db_session)
    result = await svc.get_enrichment_profile_effectiveness()

    assert result == {}


async def test_enrichment_profile_effectiveness_handles_missing_improvement_score(
    db_session: AsyncSession,
) -> None:
    """Rows with NULL ``improvement_score`` must not crash the aggregation;
    they contribute to count + avg_overall_score but are excluded from the
    improvement average.
    """
    # 2 rows with improvement_score, 1 without
    for score, improvement in [(8.0, 2.0), (7.5, 1.5)]:
        row = _make_opt(status="completed", overall_score=score)
        row.improvement_score = improvement
        row.context_sources = {"enrichment_meta": {"enrichment_profile": "code_aware"}}
        db_session.add(row)
    row = _make_opt(status="completed", overall_score=7.0)
    row.improvement_score = None
    row.context_sources = {"enrichment_meta": {"enrichment_profile": "code_aware"}}
    db_session.add(row)
    await db_session.commit()

    svc = OptimizationService(db_session)
    result = await svc.get_enrichment_profile_effectiveness()

    ca = result["code_aware"]
    assert ca["count"] == 3
    # avg_overall_score averages all 3 rows: (8.0 + 7.5 + 7.0) / 3 = 7.5
    assert abs(ca["avg_overall_score"] - 7.5) < 1e-6
    # avg_improvement_score averages only the 2 rows with improvement set
    assert abs(ca["avg_improvement_score"] - 1.75) < 1e-6
