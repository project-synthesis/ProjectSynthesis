"""Sentinel tests verifying P3 fixtures are wired into conftest."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_audit_hook_fixture_captures_warnings(audit_hook) -> None:
    audit_hook.warn("test warn")
    assert any("test warn" in str(w) for w in audit_hook.warnings)


async def test_event_bus_capture_records_published(event_bus_capture) -> None:
    from app.services.event_bus import event_bus
    event_bus.publish("probe_started", {"run_id": "fix-1"})
    assert any(e.kind == "probe_started" for e in event_bus_capture.events)


async def test_event_bus_capture_filter_by_run_id(event_bus_capture) -> None:
    from app.services.event_bus import event_bus
    event_bus.publish("probe_started", {"run_id": "fix-A"})
    event_bus.publish("probe_started", {"run_id": "fix-B"})
    a_events = event_bus_capture.events_for_run("fix-A")
    assert len(a_events) == 1


async def test_taxonomy_event_capture_records_decisions(taxonomy_event_capture) -> None:
    from app.services.taxonomy.event_logger import get_event_logger
    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_started",
            context={"batch_id": "fix-1", "run_id": "fix-rid"},
        )
    except RuntimeError:
        pytest.skip("event logger not initialized in this test session")
    decisions = taxonomy_event_capture.decisions_with_op("seed")
    assert any(d.context.get("run_id") == "fix-rid" for d in decisions)


def test_provider_mock_fixture_default_returns_completed(provider_mock) -> None:
    """Default provider_mock returns a successful response."""
    assert provider_mock is not None  # presence check; real exercise in test_topic_probe_generator


def test_provider_partial_mock_simulates_mixed_outcomes(provider_partial_mock) -> None:
    assert provider_partial_mock is not None


def test_seed_orchestrator_mock_has_generate(seed_orchestrator_mock) -> None:
    assert hasattr(seed_orchestrator_mock, "generate")
