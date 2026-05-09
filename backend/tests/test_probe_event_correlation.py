"""Tests for SSE event taxonomy + probe_id correlation (Topic Probe Tier 1).

AC-C7-1 through AC-C7-4 per docs/specs/topic-probe-2026-04-29.md sec 8 Cycle 7.

Drives the ``backend/app/services/probe_event_correlation.py`` module into
existence via TDD plus the docstring + ``inject_probe_id(context)`` wiring
in ``services/taxonomy/event_logger.py::log_decision``.

The C7 design (per spec sec 4.8 + plan sec Cycle 7) is:

1. ``probe_event_correlation`` re-exports the ``current_probe_id`` ContextVar
   declared canonically in ``probe_common.py`` (Foundation P3 Cycle 14
   moved the canonical home; pre-P3 it lived in ``probe_service.py``,
   which now re-imports for backward-compat) and exposes
   ``inject_probe_id(context: dict) -> dict``.
2. ``log_decision`` calls ``inject_probe_id(context)`` BEFORE persisting,
   so any taxonomy event fired while a probe is in flight carries
   ``context.probe_id`` automatically (no per-call-site wiring).
3. The accepted ``path: str`` values gain ``"probe"`` in the docstring.

Foundation P3 Cycle 14 retired the ``ProbeService`` class entirely; its
event-emission contract is now exercised by
``tests/test_topic_probe_generator.py`` against ``TopicProbeGenerator``.
The probe_* event coverage previously asserted here under
``TestProbeEventsEmitViaLogDecision`` is fully covered there. This file
keeps the ``inject_probe_id`` helper coverage which is independent of
the orchestrator implementation.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.taxonomy.event_logger import (
    TaxonomyEventLogger,
    reset_event_logger,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def local_logger(tmp_path: Path) -> TaxonomyEventLogger:
    """Local-process event logger (publishes to bus disabled, sync writes)."""
    return TaxonomyEventLogger(
        events_dir=tmp_path, publish_to_bus=False, cross_process=False,
    )


@pytest.fixture
def cross_process_logger(tmp_path: Path) -> TaxonomyEventLogger:
    """Cross-process logger (MCP-server-shaped — forwards via HTTP POST)."""
    return TaxonomyEventLogger(
        events_dir=tmp_path, publish_to_bus=False, cross_process=True,
    )


@pytest.fixture(autouse=True)
def _reset_logger_singleton() -> None:
    """Clear the module singleton between tests so set_event_logger races.

    Mirrors the cleanup pattern in test_event_delivery.py.
    """
    reset_event_logger()
    yield
    reset_event_logger()


# ---------------------------------------------------------------------------
# AC-C7-2: probe_id correlation on existing taxonomy events when ContextVar set
# ---------------------------------------------------------------------------


class TestProbeIdCorrelation:
    """When ``current_probe_id`` ContextVar is set, ``log_decision`` should
    inject ``probe_id`` into ``context`` so downstream consumers can filter
    Activity-Panel events by originating probe."""

    def test_probe_id_correlation_when_var_set(
        self, local_logger: TaxonomyEventLogger,
    ) -> None:
        """ContextVar set -> taxonomy event context gains ``probe_id``."""
        # Import via the C7 module — RED-phase failure mode is ImportError
        # because probe_event_correlation.py does not yet exist.
        from app.services.probe_event_correlation import (
            current_probe_id,
            inject_probe_id,
        )

        # Sanity: helper is a callable that returns a dict.
        assert callable(inject_probe_id)

        token = current_probe_id.set("p-corr-set")
        try:
            local_logger.log_decision(
                path="warm",
                op="domain_created",
                decision="accepted",
                cluster_id="c-domain-1",
                context={"label": "embeddings", "member_count": 7},
            )
        finally:
            current_probe_id.reset(token)

        # Inspect ring buffer — the most-recent event should carry probe_id.
        recent = local_logger.get_recent(limit=1, op="domain_created")
        assert len(recent) == 1, (
            "domain_created event missing from ring buffer"
        )
        ctx = recent[0].get("context") or {}
        assert ctx.get("probe_id") == "p-corr-set", (
            f"probe_id missing from context: {ctx}"
        )
        # Original payload preserved alongside the injected key.
        assert ctx.get("label") == "embeddings"
        assert ctx.get("member_count") == 7

    def test_no_probe_id_when_var_unset(
        self, local_logger: TaxonomyEventLogger,
    ) -> None:
        """ContextVar unset -> taxonomy event context has NO ``probe_id``.

        Backward-compat: existing ring-buffer / JSONL consumers must
        tolerate events whose context dict lacks the new key.
        """
        # Trigger the GREEN-phase code path (so test fails at RED via
        # ImportError, not via stale assertion semantics).
        from app.services.probe_event_correlation import (
            current_probe_id,
            inject_probe_id,
        )

        assert callable(inject_probe_id)
        # Defensive: ensure the ContextVar default is None outside any
        # set/reset window so this test reflects production reality.
        assert current_probe_id.get() is None

        local_logger.log_decision(
            path="warm",
            op="domain_created",
            decision="accepted",
            cluster_id="c-domain-2",
            context={"label": "frontend", "member_count": 12},
        )

        recent = local_logger.get_recent(limit=1, op="domain_created")
        assert len(recent) == 1
        ctx = recent[0].get("context") or {}
        assert "probe_id" not in ctx, (
            f"probe_id leaked into context when ContextVar unset: {ctx}"
        )
        # Original payload still rendered verbatim.
        assert ctx.get("label") == "frontend"


# ---------------------------------------------------------------------------
# AC-C7-3: Cross-process forwarding via notify_event_bus carries probe_id
# ---------------------------------------------------------------------------


class TestCrossProcessForwarding:
    """When the MCP server process emits a probe event under a probe-set
    ContextVar, the forwarded payload (delivered via
    ``notify_event_bus`` HTTP POST to /api/events/_publish) must carry
    ``context.probe_id`` so the backend ring buffer is populated with
    correlation intact."""

    @pytest.mark.asyncio
    async def test_cross_process_forward_via_notify_event_bus(
        self, cross_process_logger: TaxonomyEventLogger,
    ) -> None:
        """MCP-process probe event -> notify_event_bus payload carries probe_id."""
        from app.services.probe_event_correlation import (
            current_probe_id,
            inject_probe_id,
        )

        assert callable(inject_probe_id)

        notify_calls: list[tuple[str, dict]] = []

        async def _capture(event_type: str, data: dict) -> None:
            notify_calls.append((event_type, data))

        token = current_probe_id.set("p-cross-1")
        try:
            with patch(
                "app.services.event_notification.notify_event_bus",
                new=_capture,
            ):
                cross_process_logger.log_decision(
                    path="probe",
                    op="probe_grounding",
                    decision="emitted",
                    context={
                        "retrieved_files_count": 3,
                        "has_explore_synthesis": True,
                        "dominant_stack": ["python"],
                    },
                )
                # Drain the asyncio task spawned inside log_decision so
                # the patched notify_event_bus has time to record the call.
                await cross_process_logger.drain_pending(timeout=5.0)
        finally:
            current_probe_id.reset(token)

        assert len(notify_calls) >= 1, (
            "notify_event_bus was never called for cross-process probe event"
        )
        # The forwarded payload is the full event dict with embedded
        # context — verify probe_id rode along through inject_probe_id.
        event_type, data = notify_calls[0]
        assert event_type == "taxonomy_activity"
        ctx = (data.get("context") or {})
        assert ctx.get("probe_id") == "p-cross-1", (
            f"Cross-process forward dropped probe_id: data={data}"
        )
        assert data.get("op") == "probe_grounding"
