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
from app.services.taxonomy.readiness_history import record_snapshot


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
