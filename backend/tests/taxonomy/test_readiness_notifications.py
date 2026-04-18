"""Tests for readiness tier-crossing detection + notification emission."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.schemas.sub_domain_readiness import (
    DomainReadinessReport,
    DomainStabilityGuards,
    DomainStabilityReport,
    SubDomainEmergenceReport,
)
from app.services.event_bus import event_bus
from app.services.taxonomy.sub_domain_readiness import (
    _detect_crossings,
    clear_cache,
    clear_tier_history,
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
    clear_tier_history()
    clear_cache()
    yield
    clear_tier_history()
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


def test_detect_crossings_oscillation_resets_hysteresis_counter():
    """A tier bouncing back to stable mid-streak must reset the pending count,
    so the next candidate tier needs a fresh HYSTERESIS_CYCLES run to fire.
    This is the core anti-bounce property the hysteresis gate exists for.
    """
    # Baseline: inert
    _detect_crossings(_report(emergence="inert"), now=0.0)
    # First warming observation — pending count = 1, not yet firing
    assert _detect_crossings(_report(emergence="warming"), now=10.0) == []
    # Bounce back to inert — pending streak must clear
    assert _detect_crossings(_report(emergence="inert"), now=20.0) == []
    # Single warming observation again — if the streak had NOT reset, this
    # would be count=2 and fire.  With reset, it must stay silent.
    assert _detect_crossings(_report(emergence="warming"), now=30.0) == []
    # Second consecutive warming — now (and only now) the crossing fires.
    fired = _detect_crossings(_report(emergence="warming"), now=40.0)
    assert len(fired) == 1
    assert fired[0]["axis"] == "emergence"
    assert fired[0]["from_tier"] == "inert"
    assert fired[0]["to_tier"] == "warming"


@pytest.mark.asyncio
async def test_publish_crossings_emits_event_bus_event():
    """End-to-end: calling `_publish_crossings` after a qualifying streak
    publishes a `domain_readiness_changed` event exactly once.
    """
    from app.services.taxonomy import sub_domain_readiness as r

    # Subscribe first so we capture the publish synchronously.
    received: list[dict] = []

    async def _drain():
        async for payload in event_bus.subscribe():
            received.append(payload)
            if len(received) >= 1:
                return

    drain_task = asyncio.create_task(_drain())
    # Give the subscriber a chance to register.
    await asyncio.sleep(0)

    # Sequence: inert (baseline) → warming (pending) → warming (satisfies hysteresis).
    r._publish_crossings(_report(emergence="inert"), now=0.0)
    r._publish_crossings(_report(emergence="warming"), now=10.0)
    r._publish_crossings(_report(emergence="warming"), now=20.0)

    # Wait for the SSE bus to deliver (bounded).
    try:
        await asyncio.wait_for(drain_task, timeout=1.0)
    except asyncio.TimeoutError:
        drain_task.cancel()
        raise

    assert len(received) == 1
    envelope = received[0]
    assert envelope["event"] == "domain_readiness_changed"
    body = envelope["data"]
    assert body["domain_id"] == "d1"
    assert body["axis"] == "emergence"
    assert body["from_tier"] == "inert"
    assert body["to_tier"] == "warming"


def test_cooldown_suppression_emits_observability_event(monkeypatch):
    """A crossing gated by the 10-minute cooldown must still leave a
    structured observability breadcrumb (``readiness_crossing_suppressed``),
    otherwise spurious-but-real transitions are silently lost from the
    event log and can't be diagnosed later.
    """
    from app.services.taxonomy import event_logger as ev
    from app.services.taxonomy import sub_domain_readiness as r

    # Install a throwaway event logger (no disk I/O) to capture decisions.
    captured: list[dict] = []

    class _Capture:
        def log_decision(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(ev, "_instance", _Capture())

    # First full firing: inert → warming with hysteresis.
    r._detect_crossings(_report(emergence="inert"), now=0.0)
    r._detect_crossings(_report(emergence="warming"), now=10.0)
    fired = r._detect_crossings(_report(emergence="warming"), now=20.0)
    assert len(fired) == 1

    # Second transition within cooldown window: warming → ready with
    # hysteresis satisfied. The inner `_process_axis_crossing` must take
    # the cooldown branch (line 867-874) — it must emit the structured
    # suppressed event.
    r._detect_crossings(_report(emergence="ready"), now=25.0)
    r._detect_crossings(_report(emergence="ready"), now=35.0)

    suppressed = [
        entry for entry in captured
        if entry.get("op") == "readiness_crossing_suppressed"
    ]
    assert len(suppressed) >= 1, (
        "cooldown suppression must log a `readiness_crossing_suppressed` "
        "event so the drop is visible in observability"
    )
    ctx = suppressed[0]["context"]
    assert ctx["axis"] == "emergence"
    assert ctx["from_tier"] == "warming"
    assert ctx["to_tier"] == "ready"
    assert ctx["domain_id"] == "d1"
