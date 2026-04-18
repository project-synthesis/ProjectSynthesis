"""Integration smoke test: `_publish_crossings` → real `event_bus` → subscriber.

Proves the detector and publish wiring hook up end-to-end without mocking the
event bus.  Unit coverage of the detector (hysteresis, cooldown, oscillation
reset) lives in ``tests/taxonomy/test_readiness_notifications.py``; here we
only assert that a qualifying tier crossing produces a single, correctly
shaped ``domain_readiness_changed`` payload on the shared bus, and that a
non-crossing observation produces nothing.

The subscriber is registered synchronously against ``event_bus._subscribers``
rather than via the ``subscribe()`` async generator: ``asyncio.create_task``
offers no guarantee about when the generator body runs, and scheduler
behavior varies across Python versions (3.12 vs 3.14) — that made the
previous "start task, sleep(0), publish" pattern flaky in CI.  Direct queue
registration removes the race entirely; we're testing the detector + bus
contract, not the generator's consumer ergonomics.
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
    # Prior tests that exercise lifespan may have flipped ``_shutting_down``
    # on the module-level ``event_bus`` singleton; once set, ``publish()``
    # becomes a no-op for the rest of the session.  Reset it so these tests
    # are order-independent.
    event_bus._shutting_down = False  # type: ignore[attr-defined]
    clear_tier_history()
    clear_cache()
    yield
    clear_tier_history()
    clear_cache()


def _register_bus_subscriber() -> asyncio.Queue:
    """Register a subscriber queue synchronously against the live event bus.

    ``event_bus.subscribe()`` is an async generator — registration is gated
    on the event loop actually advancing the generator past its first
    ``self._subscribers.add(queue)`` line.  For tests we just want a queue on
    the bus *right now* so the next synchronous ``publish()`` writes into it.
    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    event_bus._subscribers.add(queue)  # type: ignore[attr-defined]
    return queue


def _drain_matching(
    queue: asyncio.Queue, event_type: str, max_events: int = 50
) -> dict | None:
    """Pop up to ``max_events`` from ``queue`` (non-blocking); return first
    envelope whose ``event`` field equals ``event_type``, else ``None``.

    The shared bus can carry noise from other tests/subsystems — filter so
    we assert on the right event, not arbitrarily the first enqueued one.
    """
    for _ in range(max_events):
        try:
            envelope = queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
        if envelope.get("event") == event_type:
            return envelope
    return None


async def test_publish_crossings_delivers_payload_through_event_bus():
    """Sequence that satisfies hysteresis on the stability axis must publish
    a single ``domain_readiness_changed`` event with the full 9-field shape.
    """
    queue = _register_bus_subscriber()
    try:
        # Baseline (healthy) → guarded (pending) → guarded (fires).
        _publish_crossings(_report(stability="healthy"), now=0.0)
        _publish_crossings(_report(stability="guarded"), now=10.0)
        _publish_crossings(_report(stability="guarded"), now=20.0)

        envelope = _drain_matching(queue, "domain_readiness_changed")
    finally:
        event_bus._subscribers.discard(queue)  # type: ignore[attr-defined]

    assert envelope is not None, (
        "expected domain_readiness_changed event on bus; "
        f"queue held {queue.qsize()} other events"
    )
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
    queue = _register_bus_subscriber()
    try:
        _publish_crossings(_report(stability="healthy"), now=0.0)
        _publish_crossings(_report(stability="healthy"), now=10.0)

        envelope = _drain_matching(queue, "domain_readiness_changed")
    finally:
        event_bus._subscribers.discard(queue)  # type: ignore[attr-defined]

    assert envelope is None, (
        "unexpected domain_readiness_changed event published for "
        "no-transition sequence"
    )
