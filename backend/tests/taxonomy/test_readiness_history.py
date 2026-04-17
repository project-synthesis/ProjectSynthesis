"""Tests for readiness history persistence + query."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    DomainStabilityGuards,
    DomainStabilityReport,
    SubDomainEmergenceReport,
)
from app.services.taxonomy.readiness_history import (
    prune_old_snapshots,
    query_history,
    record_snapshot,
)


def _build_report(
    domain_id: str = "d1",
    domain_label: str = "backend",
    consistency: float = 0.5,
    dissolution_risk: float = 0.25,
    stability_tier: str = "healthy",
    emergence_tier: str = "warming",
    gap: float | None = 0.08,
    member_count: int = 30,
    total_opts: int = 100,
) -> DomainReadinessReport:
    return DomainReadinessReport(
        domain_id=domain_id,
        domain_label=domain_label,
        member_count=member_count,
        stability=DomainStabilityReport(
            consistency=consistency,
            dissolution_floor=0.15,
            hysteresis_creation_threshold=0.60,
            age_hours=72.0,
            min_age_hours=48,
            member_count=member_count,
            member_ceiling=5,
            sub_domain_count=0,
            total_opts=total_opts,
            guards=DomainStabilityGuards(
                general_protected=False,
                has_sub_domain_anchor=False,
                age_eligible=True,
                above_member_ceiling=member_count > 5,
                consistency_above_floor=consistency >= 0.15,
            ),
            tier=stability_tier,  # type: ignore[arg-type]
            dissolution_risk=dissolution_risk,
            would_dissolve=False,
        ),
        emergence=SubDomainEmergenceReport(
            threshold=0.50,
            threshold_formula="dummy",
            min_member_count=8,
            total_opts=total_opts,
            top_candidate=None,
            gap_to_threshold=gap,
            ready=False,
            blocked_reason="below_threshold",
            runner_ups=[],
            tier=emergence_tier,  # type: ignore[arg-type]
        ),
        computed_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_record_snapshot_writes_jsonl_row(tmp_path: Path) -> None:
    report = _build_report()
    await record_snapshot(report, base_dir=tmp_path)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    file_path = tmp_path / f"snapshots-{today}.jsonl"
    assert file_path.exists(), "snapshot file not created"

    lines = file_path.read_text().strip().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["domain_id"] == "d1"
    assert row["domain_label"] == "backend"
    assert row["consistency"] == 0.5
    assert row["dissolution_risk"] == 0.25
    assert row["stability_tier"] == "healthy"
    assert row["emergence_tier"] == "warming"
    assert row["top_candidate_gap"] == 0.08
    assert row["member_count"] == 30
    assert row["total_opts"] == 100
    assert "ts" in row


@pytest.mark.asyncio
async def test_query_history_24h_returns_raw_points(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    # Three snapshots: now, 1h ago, 25h ago (last is outside 24h window)
    for delta_h, cons in [(0, 0.42), (1, 0.40), (25, 0.30)]:
        ts = now - timedelta(hours=delta_h)
        snap = _build_report(consistency=cons)
        snap = snap.model_copy(update={"computed_at": ts})
        await record_snapshot(snap, base_dir=tmp_path)

    response = await query_history(
        domain_id="d1",
        domain_label="backend",
        window="24h",
        base_dir=tmp_path,
    )
    assert response.window == "24h"
    assert response.bucketed is False
    assert len(response.points) == 2
    # Newest first ordering
    assert response.points[0].consistency == 0.42
    assert response.points[1].consistency == 0.40


@pytest.mark.asyncio
async def test_query_history_7d_buckets_to_hourly_means(tmp_path: Path) -> None:
    # Anchor to the top of the current hour and add small positive offsets
    # so all three timestamps fall in the SAME hour bucket regardless of
    # when the test runs (previously used negative offsets from now(), which
    # crossed the hour boundary whenever now().minute < 45 → flaky).
    hour_start = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0,
    )
    for delta_min, cons in [(5, 0.40), (20, 0.50), (50, 0.60)]:
        ts = hour_start + timedelta(minutes=delta_min)
        snap = _build_report(consistency=cons).model_copy(update={"computed_at": ts})
        await record_snapshot(snap, base_dir=tmp_path)

    response = await query_history(
        domain_id="d1",
        domain_label="backend",
        window="7d",
        base_dir=tmp_path,
    )
    assert response.bucketed is True
    assert len(response.points) == 1
    assert response.points[0].is_bucket_mean is True
    assert response.points[0].consistency == pytest.approx(0.50, abs=0.001)


def test_prune_old_snapshots_drops_files_past_retention(tmp_path: Path) -> None:
    """Files older than READINESS_HISTORY_RETENTION_DAYS must be deleted."""
    from app.services.taxonomy._constants import READINESS_HISTORY_RETENTION_DAYS

    now = datetime.now(timezone.utc)
    keep = tmp_path / f"snapshots-{(now - timedelta(days=5)).strftime('%Y-%m-%d')}.jsonl"
    drop = tmp_path / f"snapshots-{(now - timedelta(days=READINESS_HISTORY_RETENTION_DAYS + 2)).strftime('%Y-%m-%d')}.jsonl"
    keep.write_text("{}\n")
    drop.write_text("{}\n")

    removed = prune_old_snapshots(base_dir=tmp_path)
    assert removed == 1
    assert keep.exists()
    assert not drop.exists()
