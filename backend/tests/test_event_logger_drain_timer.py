"""RED tests for AC-13 — retry-queue periodic drain timer.

Spec: docs/superpowers/specs/2026-06-12-v0.4.38-sub-domain-telemetry-design.md §4.3.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.services.taxonomy.event_logger import TaxonomyEventLogger

pytestmark = pytest.mark.asyncio


async def test_ac13_parked_event_delivered_by_drain_with_no_new_emission(tmp_path):
    """One failed forward parks; periodic drain delivers within one interval."""
    tel = TaxonomyEventLogger(
        events_dir=tmp_path / "events",
        publish_to_bus=False,
        cross_process=True,
    )
    # Speed up the drain interval for the test.
    with patch.object(TaxonomyEventLogger, "_DRAIN_INTERVAL_SECONDS", 0.05):
        calls = {"posts": 0, "fail_first_n": 1}
        delivered: list[dict] = []

        async def fake_notify(event_type: str, data: dict) -> None:
            calls["posts"] += 1
            if calls["posts"] <= calls["fail_first_n"]:
                raise RuntimeError("bridge down")
            delivered.append(data)

        with patch(
            "app.services.event_notification.notify_event_bus",
            side_effect=fake_notify,
        ):
            # Park one event by triggering a failing forward.
            tel.log_decision(
                path="warm", op="discover", decision="x",
                context={"k": "v"},
            )
            # Wait long enough for the immediate forward task to fail.
            await asyncio.sleep(0.1)
            assert tel.retry_queue_size == 1, (
                "first emission failed → must be parked"
            )

            # Start the drain explicitly and wait one interval.
            tel.start_drain_loop()
            # Drain interval is 0.05s; allow several iterations.
            for _ in range(40):
                if delivered:
                    break
                await asyncio.sleep(0.05)

            assert delivered, "expected drain to redeliver the parked event"
            assert tel.retry_queue_size == 0


async def test_ac13_drain_failure_reparks(tmp_path):
    """Drain attempt whose POST fails re-parks for the next drain interval."""
    tel = TaxonomyEventLogger(
        events_dir=tmp_path / "events",
        publish_to_bus=False,
        cross_process=True,
    )
    with patch.object(TaxonomyEventLogger, "_DRAIN_INTERVAL_SECONDS", 0.05):
        posts = {"n": 0}

        async def always_fail(event_type: str, data: dict) -> None:
            posts["n"] += 1
            raise RuntimeError("still down")

        with patch(
            "app.services.event_notification.notify_event_bus",
            side_effect=always_fail,
        ):
            tel.log_decision(
                path="warm", op="discover", decision="y",
                context={"k": "v2"},
            )
            await asyncio.sleep(0.1)
            assert tel.retry_queue_size == 1

            tel.start_drain_loop()
            await asyncio.sleep(0.3)  # several drain attempts
            # Event still parked; multiple drain attempts incremented posts.
            assert tel.retry_queue_size == 1
            assert posts["n"] >= 2  # immediate + ≥1 drain attempt


async def test_ac13_no_duplicate_when_immediate_succeeds(tmp_path):
    """Successful immediate forward must not be drained again later."""
    tel = TaxonomyEventLogger(
        events_dir=tmp_path / "events",
        publish_to_bus=False,
        cross_process=True,
    )
    with patch.object(TaxonomyEventLogger, "_DRAIN_INTERVAL_SECONDS", 0.05):
        calls: list[dict] = []

        async def ok(event_type: str, data: dict) -> None:
            calls.append(data)

        with patch(
            "app.services.event_notification.notify_event_bus",
            side_effect=ok,
        ):
            tel.start_drain_loop()
            tel.log_decision(
                path="warm", op="discover", decision="z",
                context={"k": "v3"},
            )
            await asyncio.sleep(0.2)
            # Exactly one delivery, no duplicate from the drain loop.
            assert len(calls) == 1
            assert tel.retry_queue_size == 0


async def test_ac13_in_process_logger_untouched(tmp_path):
    """publish_to_bus=True path is byte-identical pre/post the timer."""
    tel = TaxonomyEventLogger(
        events_dir=tmp_path / "events",
        publish_to_bus=True,
        cross_process=False,
    )
    # cross_process=False path must not start a drain loop.
    tel.log_decision(
        path="warm", op="discover", decision="local",
        context={"k": "v"},
    )
    # No drain task started (no cross-process emission has occurred).
    assert tel._drain_task is None
    assert tel.retry_queue_size == 0
