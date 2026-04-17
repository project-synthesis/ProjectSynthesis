"""Integration smoke test: `_publish_crossings` → real `event_bus` → subscriber.

Proves the detector and publish wiring hook up end-to-end without mocking the
event bus.  Unit coverage of the detector (hysteresis, cooldown, oscillation
reset) lives in ``tests/taxonomy/test_readiness_notifications.py``; here we
only assert that a qualifying tier crossing produces a single, correctly
shaped ``domain_readiness_changed`` payload on the shared bus, and that a
non-crossing observation produces nothing.
"""
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
    _publish_crossings,
    clear_cache,
    clear_tier_history,
)


def _report(
    domain_id: str = "d1",
    domain_label: str = "backend",
    stability: str = "healthy",
    emergence: str = "inert",
) -> DomainReadinessReport:
    """Build a minimal, valid readiness report with configurable axis tiers."""
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


async def _collect_one(queue: asyncio.Queue, event_type: str) -> dict:
    """Consume from the bus via ``subscribe()`` until we see ``event_type``.

    The shared bus can carry noise from other tests/subsystems — filter so we
    assert on the first matching event, not arbitrarily the first event.
    """
    async for envelope in event_bus.subscribe():
        if envelope.get("event") == event_type:
            await queue.put(envelope)
            return envelope


async def test_publish_crossings_delivers_payload_through_event_bus():
    """Sequence that satisfies hysteresis on the stability axis must publish
    a single ``domain_readiness_changed`` event with the full 9-field shape.
    """
    queue: asyncio.Queue = asyncio.Queue()
    drain_task = asyncio.create_task(_collect_one(queue, "domain_readiness_changed"))
    # Yield so the subscriber registers before publishing.
    await asyncio.sleep(0)

    # Baseline (healthy) → guarded (pending) → guarded (fires).
    _publish_crossings(_report(stability="healthy"), now=0.0)
    _publish_crossings(_report(stability="guarded"), now=10.0)
    _publish_crossings(_report(stability="guarded"), now=20.0)

    try:
        envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
    finally:
        drain_task.cancel()

    assert envelope["event"] == "domain_readiness_changed"
    body = envelope["data"]
    # All 9 documented fields are present.
    expected_fields = {
        "domain_id", "domain_label", "axis", "from_tier", "to_tier",
        "consistency", "gap_to_threshold", "would_dissolve", "ts",
    }
    assert expected_fields.issubset(body.keys())
    # Tier-axis values match the transition we produced.
    assert body["axis"] == "stability"
    assert body["from_tier"] == "healthy"
    assert body["to_tier"] == "guarded"
    # ``ts`` is an ISO-8601 string (fromisoformat round-trips cleanly).
    assert isinstance(body["ts"], str)
    datetime.fromisoformat(body["ts"])


async def test_publish_crossings_no_event_when_no_crossing():
    """Two consecutive reports with identical tiers must not publish."""
    queue: asyncio.Queue = asyncio.Queue()
    drain_task = asyncio.create_task(_collect_one(queue, "domain_readiness_changed"))
    await asyncio.sleep(0)

    # Two identical healthy/inert observations — baseline record, no crossing.
    _publish_crossings(_report(stability="healthy"), now=0.0)
    _publish_crossings(_report(stability="healthy"), now=10.0)

    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.1)
    finally:
        drain_task.cancel()
