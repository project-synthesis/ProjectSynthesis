"""Tests for readiness tier-crossing detection + notification emission."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    DomainStabilityGuards,
    DomainStabilityReport,
    SubDomainEmergenceReport,
)
from app.services.taxonomy.sub_domain_readiness import (
    _detect_crossings,
    _tier_history,
    clear_cache,
)


def _report(
    domain_id: str = "d1",
    domain_label: str = "backend",
    stability: str = "healthy",
    emergence: str = "inert",
) -> DomainReadinessReport:
    return DomainReadinessReport(
        domain_id=domain_id,
        domain_label=domain_label,
        member_count=30,
        stability=DomainStabilityReport(
            consistency=0.5, dissolution_floor=0.15,
            hysteresis_creation_threshold=0.6,
            age_hours=72, min_age_hours=48, member_count=30, member_ceiling=5,
            sub_domain_count=0, total_opts=100,
            guards=DomainStabilityGuards(
                general_protected=False, has_sub_domain_anchor=False,
                age_eligible=True, above_member_ceiling=True,
                consistency_above_floor=True,
            ),
            tier=stability,  # type: ignore[arg-type]
            dissolution_risk=0.5, would_dissolve=False,
        ),
        emergence=SubDomainEmergenceReport(
            threshold=0.5, threshold_formula="x", min_member_count=8,
            total_opts=100, top_candidate=None, gap_to_threshold=None,
            ready=False, blocked_reason="none", runner_ups=[],
            tier=emergence,  # type: ignore[arg-type]
        ),
        computed_at=datetime.now(timezone.utc),
    )


@pytest.fixture(autouse=True)
def _reset_state():
    _tier_history.clear()
    clear_cache()
    yield
    _tier_history.clear()
    clear_cache()


def test_detect_crossings_no_history_records_baseline_no_crossing():
    crossings = _detect_crossings(_report(emergence="inert"), now=100.0)
    assert crossings == []
    # Baseline recorded; second observation also returns nothing
    crossings = _detect_crossings(_report(emergence="inert"), now=110.0)
    assert crossings == []


def test_detect_crossings_requires_hysteresis_cycles():
    # Baseline = inert
    _detect_crossings(_report(emergence="inert"), now=100.0)
    # First warming observation — hysteresis not yet satisfied
    crossings = _detect_crossings(_report(emergence="warming"), now=110.0)
    assert crossings == []
    # Second warming observation — crossing fires
    crossings = _detect_crossings(_report(emergence="warming"), now=120.0)
    assert len(crossings) == 1
    assert crossings[0]["axis"] == "emergence"
    assert crossings[0]["from_tier"] == "inert"
    assert crossings[0]["to_tier"] == "warming"


def test_detect_crossings_cooldown_suppresses_repeat():
    _detect_crossings(_report(emergence="inert"), now=0.0)
    _detect_crossings(_report(emergence="warming"), now=10.0)
    fired = _detect_crossings(_report(emergence="warming"), now=20.0)
    assert len(fired) == 1
    # Within cooldown, same-tier re-fire suppressed (no new crossing since
    # tier didn't change again anyway).
    again = _detect_crossings(_report(emergence="warming"), now=30.0)
    assert again == []
    # After cooldown, still same tier — no re-fire (crossings fire on
    # transitions, not on persistence).
    later = _detect_crossings(_report(emergence="warming"), now=700.0)
    assert later == []


def test_detect_crossings_independently_tracks_stability_axis():
    _detect_crossings(_report(stability="healthy", emergence="inert"), now=0.0)
    _detect_crossings(_report(stability="guarded", emergence="inert"), now=10.0)
    fired = _detect_crossings(_report(stability="guarded", emergence="inert"), now=20.0)
    assert len(fired) == 1
    assert fired[0]["axis"] == "stability"
    assert fired[0]["from_tier"] == "healthy"
    assert fired[0]["to_tier"] == "guarded"
