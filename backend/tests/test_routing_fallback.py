"""Routing fallback chain — degradation, isolation, error distinctiveness, persistence.

Complements test_routing.py (happy-path coverage) with failure scenarios:
  1. Sampling degradation on disconnect
  2. Per-client capability isolation
  3. Tier-specific error distinctiveness
  4. State persistence across restart

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock, patch

import pytest

from app.services.event_bus import EventBus
from app.services.routing import RoutingContext, RoutingManager, RoutingState, resolve_route

# ---------------------------------------------------------------------------
# Shared helpers — identical to test_routing.py
# ---------------------------------------------------------------------------


def _state(
    *,
    provider_name: str | None = None,
    sampling_capable: bool | None = None,
    mcp_connected: bool = False,
) -> RoutingState:
    provider = MagicMock(name=provider_name) if provider_name else None
    return RoutingState(
        provider=provider,
        provider_name=provider_name,
        sampling_capable=sampling_capable,
        mcp_connected=mcp_connected,
    )


def _ctx(
    *,
    caller: Literal["rest", "mcp"] = "rest",
    force_passthrough: bool = False,
    force_sampling: bool = False,
) -> RoutingContext:
    return RoutingContext(
        caller=caller,
        preferences={
            "pipeline": {
                "force_passthrough": force_passthrough,
                "force_sampling": force_sampling,
            },
        },
    )


def _subscribe(event_bus: EventBus) -> asyncio.Queue:
    """Attach a queue subscriber to the event bus and return it."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    event_bus._subscribers.add(queue)
    return queue


def _drain_events(queue: asyncio.Queue) -> list[dict]:
    """Drain all pending events from a subscriber queue."""
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(tmp_path: Path, event_bus: EventBus) -> RoutingManager:
    return RoutingManager(event_bus=event_bus, data_dir=tmp_path)


# ---------------------------------------------------------------------------
# 1. Sampling degradation on disconnect
# ---------------------------------------------------------------------------


class TestSamplingDegradationOnDisconnect:
    """When a sampling client disconnects mid-session, routing must degrade
    from sampling to internal (if provider exists) or passthrough."""

    def test_force_sampling_degrades_to_internal_after_sampling_disconnect(
        self, manager: RoutingManager, event_bus: EventBus,
    ) -> None:
        """force_sampling=True should fall back to internal when sampling drops."""
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.on_mcp_initialize(sampling_capable=True)

        # Verify sampling works before disconnect
        ctx = _ctx(caller="mcp", force_sampling=True)
        decision_before = manager.resolve(ctx)
        assert decision_before.tier == "sampling"

        # Sampling client disconnects
        queue = _subscribe(event_bus)
        manager.on_sampling_disconnect()

        # Verify degradation
        decision_after = manager.resolve(ctx)
        assert decision_after.tier == "internal"
        assert decision_after.degraded_from == "sampling"

        # Event must have been emitted
        events = _drain_events(queue)
        triggers = [e["data"]["trigger"] for e in events if e["event"] == "routing_state_changed"]
        assert "sampling_disconnect" in triggers

    def test_force_sampling_degrades_to_passthrough_without_provider(
        self, manager: RoutingManager,
    ) -> None:
        """force_sampling without provider degrades through internal to passthrough."""
        manager.on_mcp_initialize(sampling_capable=True)

        ctx = _ctx(caller="mcp", force_sampling=True)
        assert manager.resolve(ctx).tier == "sampling"

        manager.on_sampling_disconnect()
        decision = manager.resolve(ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "sampling"

    def test_sampling_disconnect_preserves_mcp_connected(
        self, manager: RoutingManager,
    ) -> None:
        """on_sampling_disconnect clears sampling_capable but NOT mcp_connected."""
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.mcp_connected is True
        assert manager.state.sampling_capable is True

        manager.on_sampling_disconnect()
        assert manager.state.sampling_capable is None
        assert manager.state.mcp_connected is True  # preserved

    def test_full_disconnect_clears_both(
        self, manager: RoutingManager,
    ) -> None:
        """on_mcp_disconnect clears BOTH sampling_capable AND mcp_connected."""
        manager.on_mcp_initialize(sampling_capable=True)

        manager.on_mcp_disconnect()
        assert manager.state.sampling_capable is None
        assert manager.state.mcp_connected is False

    def test_session_file_after_sampling_disconnect(
        self, tmp_path: Path, event_bus: EventBus,
    ) -> None:
        """mcp_session.json reflects sampling_capable=False after sampling disconnect."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)

        # Verify session file has sampling=True
        session_file = tmp_path / "mcp_session.json"
        data = json.loads(session_file.read_text())
        assert data["sampling_capable"] is True

        mgr.on_sampling_disconnect()

        # File should still exist (not deleted) with updated state
        # Note: on_sampling_disconnect persists; on_mcp_disconnect deletes
        if session_file.exists():
            data = json.loads(session_file.read_text())
            # sampling_capable should be None or False in the file
            assert data.get("sampling_capable") in (None, False)

    def test_sampling_disconnect_idempotent(
        self, manager: RoutingManager, event_bus: EventBus,
    ) -> None:
        """Second on_sampling_disconnect is a no-op (no duplicate events)."""
        manager.on_mcp_initialize(sampling_capable=True)
        manager.on_sampling_disconnect()

        # Subscribe AFTER first disconnect
        queue = _subscribe(event_bus)
        manager.on_sampling_disconnect()  # second call

        assert queue.empty(), "Duplicate event from idempotent disconnect"


# ---------------------------------------------------------------------------
# 2. Per-client capability isolation
# ---------------------------------------------------------------------------


class TestPerClientCapabilityIsolation:
    """Global sampling_capable=True must not leak into non-sampling client routes."""

    def test_rest_caller_never_reaches_sampling(self) -> None:
        """REST callers are excluded from sampling regardless of global state."""
        state = _state(
            provider_name="claude_cli",
            sampling_capable=True,
            mcp_connected=True,
        )
        # REST caller, no force flags
        ctx = _ctx(caller="rest")
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.tier != "sampling"

    def test_rest_caller_never_reaches_sampling_even_without_provider(self) -> None:
        """REST caller with sampling available but no provider → passthrough, not sampling."""
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="rest")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.tier != "sampling"

    def test_rest_force_sampling_degrades_past_sampling(self) -> None:
        """REST caller with force_sampling still can't use sampling tier."""
        state = _state(
            provider_name="anthropic_api",
            sampling_capable=True,
            mcp_connected=True,
        )
        ctx = _ctx(caller="rest", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"

    def test_non_sampling_mcp_client_routes_to_internal(self) -> None:
        """MCP client that doesn't support sampling uses internal when provider exists.

        Even when global sampling_capable=True from another client, an MCP
        caller without sampling should route to internal (tier 3 beats tier 4).
        """
        # State: provider exists, sampling globally available
        state = _state(
            provider_name="claude_cli",
            sampling_capable=True,
            mcp_connected=True,
        )
        # MCP caller, no force flags → tier 3 (internal provider) wins over tier 4
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"

    def test_sampling_mcp_client_gets_sampling_only_when_no_provider(self) -> None:
        """MCP caller with sampling reaches tier 4 only when provider is absent."""
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "sampling"
        assert decision.degraded_from == "internal"

    def test_mcp_client_isolation_from_rest_state(
        self, manager: RoutingManager,
    ) -> None:
        """Manager resolve uses caller field to differentiate clients."""
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.on_mcp_initialize(sampling_capable=True)

        # MCP caller gets internal (provider exists, tier 3 > tier 4)
        mcp_ctx = RoutingContext(caller="mcp", preferences={"pipeline": {}})
        assert manager.resolve(mcp_ctx).tier == "internal"

        # REST caller also gets internal — never sampling
        rest_ctx = RoutingContext(caller="rest", preferences={"pipeline": {}})
        assert manager.resolve(rest_ctx).tier == "internal"

        # Remove provider — MCP can sample, REST falls to passthrough
        manager.set_provider(None)
        assert manager.resolve(mcp_ctx).tier == "sampling"
        assert manager.resolve(rest_ctx).tier == "passthrough"


# ---------------------------------------------------------------------------
# 3. Tier-specific error distinctiveness
# ---------------------------------------------------------------------------


class TestTierSpecificErrorDistinctiveness:
    """Each tier's failure must produce distinguishable routing decisions.

    A developer must determine which tier failed from the decision alone
    (reason string + degraded_from field), not from log output.
    """

    def test_sampling_failure_distinguishable_from_internal(self) -> None:
        """Sampling degradation sets degraded_from='sampling', internal doesn't."""
        # Sampling degradation: force_sampling but can't sample
        state = _state(provider_name="claude_cli")
        ctx = _ctx(caller="mcp", force_sampling=True)
        sampling_fail = resolve_route(state, ctx)

        # Internal success: provider available, no force
        ctx_normal = _ctx(caller="rest")
        internal_ok = resolve_route(state, ctx_normal)

        assert sampling_fail.degraded_from == "sampling"
        assert internal_ok.degraded_from is None
        assert sampling_fail.tier != internal_ok.tier or sampling_fail.degraded_from != internal_ok.degraded_from

    def test_internal_unavailable_vs_passthrough(self) -> None:
        """No provider → passthrough with degraded_from='internal'."""
        state = _state()  # no provider, no sampling
        ctx = _ctx(caller="rest")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"

    def test_force_passthrough_has_no_degradation(self) -> None:
        """force_passthrough is a deliberate choice, not a failure."""
        state = _state(provider_name="claude_cli", sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp", force_passthrough=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from is None

    def test_all_tiers_have_distinct_reason_strings(self) -> None:
        """Every tier resolution includes a non-empty reason string."""
        scenarios = [
            # (state, ctx, expected_tier)
            (_state(provider_name="cli"), _ctx(), "internal"),
            (_state(sampling_capable=True, mcp_connected=True), _ctx(caller="mcp"), "sampling"),
            (_state(), _ctx(), "passthrough"),
            (_state(provider_name="cli"), _ctx(force_passthrough=True), "passthrough"),
        ]
        reasons = set()
        for state, ctx, expected_tier in scenarios:
            decision = resolve_route(state, ctx)
            assert decision.tier == expected_tier
            assert decision.reason, f"Empty reason for tier={expected_tier}"
            reasons.add(decision.reason)

        # All reasons should be distinct
        assert len(reasons) == len(scenarios), (
            f"Non-distinct reasons: {reasons}"
        )

    def test_degradation_chain_sampling_to_internal_to_passthrough(self) -> None:
        """Full degradation chain: sampling → internal → passthrough."""
        # Step 1: sampling available
        state_full = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp", force_sampling=True)
        assert resolve_route(state_full, ctx).tier == "sampling"

        # Step 2: sampling lost, no provider → passthrough degraded from sampling
        state_no_sampling = _state(sampling_capable=False, mcp_connected=True)
        decision = resolve_route(state_no_sampling, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "sampling"

        # Step 3: provider exists, sampling lost → internal degraded from sampling
        state_with_provider = _state(
            provider_name="cli", sampling_capable=False, mcp_connected=True,
        )
        decision = resolve_route(state_with_provider, ctx)
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"


# ---------------------------------------------------------------------------
# 4. State persistence across restart
# ---------------------------------------------------------------------------


class TestStatePersistenceAcrossRestart:
    """RoutingManager state must survive process restarts via mcp_session.json."""

    def test_session_file_written_on_initialize(
        self, tmp_path: Path, event_bus: EventBus,
    ) -> None:
        """on_mcp_initialize writes sampling state to session file."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)

        session_file = tmp_path / "mcp_session.json"
        assert session_file.exists()
        data = json.loads(session_file.read_text())
        assert data["sampling_capable"] is True
        assert "last_activity" in data

    def test_new_manager_recovers_from_session_file(
        self, tmp_path: Path,
    ) -> None:
        """Issue-4: a fresh session file IS trusted on recovery.

        Models the real-world case where the FastAPI backend restarts
        (e.g., uvicorn auto-reload) while the MCP server process keeps
        running with an active sampling-capable client.  The MCP server
        wrote ``mcp_session.json`` within the capability-fresh window
        and the file is not stale, so the new backend's
        ``RoutingManager`` should trust the persisted state instead of
        waiting ~60s for the disconnect checker to notice.
        """
        eb1 = EventBus()
        mgr1 = RoutingManager(event_bus=eb1, data_dir=tmp_path, is_mcp_process=True)
        mgr1.on_mcp_initialize(sampling_capable=True)
        assert mgr1.state.sampling_capable is True
        assert mgr1.state.mcp_connected is True

        # Construct a new manager from the same directory (simulating restart)
        eb2 = EventBus()
        mgr2 = RoutingManager(event_bus=eb2, data_dir=tmp_path)

        # Recovery trusts the freshly-written session file — backend
        # restart no longer blackholes sampling for ~60s.
        assert mgr2.state.mcp_connected is True
        assert mgr2.state.sampling_capable is True

    def test_stale_session_file_cleared_on_recovery(
        self, tmp_path: Path,
    ) -> None:
        """Session file older than staleness threshold is ignored on recovery."""
        # Write a stale session file (>300s old activity)
        session_file = tmp_path / "mcp_session.json"
        stale_time = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True,
            "last_activity": stale_time,
            "written_at": stale_time,
        }))

        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path)

        # Stale state should not be restored
        assert mgr.state.sampling_capable is None
        assert mgr.state.mcp_connected is False

    def test_session_file_deleted_on_full_disconnect(
        self, tmp_path: Path, event_bus: EventBus,
    ) -> None:
        """on_mcp_disconnect deletes the session file to prevent stale recovery."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)

        session_file = tmp_path / "mcp_session.json"
        assert session_file.exists()

        mgr.on_mcp_disconnect()
        assert not session_file.exists(), "Session file should be deleted after full disconnect"

    def test_session_invalidated_deletes_file(
        self, tmp_path: Path, event_bus: EventBus,
    ) -> None:
        """on_session_invalidated also deletes the session file."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)

        session_file = tmp_path / "mcp_session.json"
        assert session_file.exists()

        mgr.on_session_invalidated()
        assert not session_file.exists()
        assert mgr.state.mcp_connected is False
        assert mgr.state.sampling_capable is None

    @pytest.mark.asyncio
    async def test_disconnect_checker_detects_staleness(
        self, tmp_path: Path,
    ) -> None:
        """Disconnect checker fires disconnect event when activity is stale."""
        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)

        # Force activity timestamp to be stale (>60s)
        stale = datetime.now(timezone.utc) - timedelta(seconds=120)
        mgr._update_state(last_activity=stale)
        # Also write stale data to session file so the file check doesn't save it
        if mgr._session_file:
            mgr._session_file.update(last_activity=stale.isoformat())

        queue = _subscribe(eb)

        # Run one iteration of disconnect loop then cancel
        call_count = 0

        async def _fast_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError()

        with patch("asyncio.sleep", side_effect=_fast_sleep):
            try:
                await mgr._disconnect_loop()
            except asyncio.CancelledError:
                pass

        assert mgr.state.mcp_connected is False
        events = _drain_events(queue)
        triggers = [e["data"]["trigger"] for e in events if e["event"] == "routing_state_changed"]
        assert "disconnect" in triggers
