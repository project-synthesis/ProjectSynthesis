"""Tests for the routing decision engine.

Covers all 5 tiers of the priority chain:
  force passthrough > force sampling > internal provider > auto sampling > passthrough fallback
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.event_bus import EventBus
from app.services.routing import RoutingContext, RoutingManager, RoutingState, resolve_route

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _state(
    *,
    provider_name: str | None = None,
    sampling_capable: bool | None = None,
    mcp_connected: bool = False,
    rate_limited: bool = False,
) -> RoutingState:
    provider = MagicMock(name=provider_name) if provider_name else None
    return RoutingState(
        provider=provider,
        provider_name=provider_name,
        sampling_capable=sampling_capable,
        mcp_connected=mcp_connected,
        rate_limited=rate_limited,
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


# ---------------------------------------------------------------------------
# Tier 1 — force_passthrough always wins
# ---------------------------------------------------------------------------


class TestForcePassthrough:
    """force_passthrough=True should always return tier='passthrough'."""

    def test_with_provider(self) -> None:
        state = _state(provider_name="claude-cli")
        ctx = _ctx(force_passthrough=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.reason
        assert decision.provider is None

    def test_without_provider(self) -> None:
        state = _state()
        ctx = _ctx(force_passthrough=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"

    def test_with_sampling_available(self) -> None:
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp", force_passthrough=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"


# ---------------------------------------------------------------------------
# Tier 2 — force_sampling (may degrade)
# ---------------------------------------------------------------------------


class TestForceSampling:
    """force_sampling=True should return sampling when possible, degrade otherwise."""

    def test_mcp_caller_with_sampling(self) -> None:
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "sampling"
        assert decision.degraded_from is None

    def test_rest_caller_degrades_to_internal(self) -> None:
        # REST caller cannot sample → force_sampling degrades past sampling to internal
        state = _state(provider_name="anthropic-api", sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="rest", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"

    def test_rest_caller_degrades_to_passthrough(self) -> None:
        # REST caller cannot sample, no provider → force_sampling degrades to passthrough
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="rest", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "sampling"

    def test_mcp_not_connected_degrades(self) -> None:
        state = _state(provider_name="claude-cli", sampling_capable=True, mcp_connected=False)
        ctx = _ctx(caller="mcp", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"

    def test_sampling_none_degrades(self) -> None:
        state = _state(provider_name="claude-cli", sampling_capable=None, mcp_connected=True)
        ctx = _ctx(caller="mcp", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"


# ---------------------------------------------------------------------------
# Tier 3 — internal provider
# ---------------------------------------------------------------------------


class TestInternalProvider:
    """When a provider exists and nothing is forced, use internal."""

    def test_cli_provider(self) -> None:
        state = _state(provider_name="claude-cli")
        ctx = _ctx()
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.provider is not None
        assert decision.degraded_from is None  # internal is the natural tier, no degradation

    def test_api_provider(self) -> None:
        state = _state(provider_name="anthropic-api")
        ctx = _ctx()
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.provider is not None

    def test_internal_preferred_over_auto_sampling(self) -> None:
        # Internal (tier 3) wins over auto-sampling (tier 4) when provider exists
        state = _state(provider_name="claude-cli", sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"


# ---------------------------------------------------------------------------
# Tier 4 — auto sampling
# ---------------------------------------------------------------------------


class TestAutoSampling:
    """MCP caller with sampling available but no provider → auto sampling."""

    def test_mcp_no_provider_gets_sampling(self) -> None:
        # MCP caller + no provider → auto-sampling with degraded_from="internal"
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "sampling"
        assert decision.degraded_from == "internal"  # sampling is fallback when no provider

    def test_rest_never_reaches_sampling(self) -> None:
        # REST callers cannot use sampling tier — falls to passthrough
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="rest")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.tier != "sampling"


# ---------------------------------------------------------------------------
# Tier 5 — passthrough fallback
# ---------------------------------------------------------------------------


class TestPassthroughFallback:
    """Nothing available → passthrough."""

    def test_nothing_available(self) -> None:
        state = _state()
        ctx = _ctx()
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"  # tried internal first, fell through

    def test_sampling_not_connected(self) -> None:
        state = _state(sampling_capable=True, mcp_connected=False)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"  # no provider, sampling not connected

    def test_sampling_none(self) -> None:
        state = _state(sampling_capable=None, mcp_connected=True)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"  # sampling_capable=None → not available


# ---------------------------------------------------------------------------
# Rate Limiting Behavior
# ---------------------------------------------------------------------------


class TestRateLimitRouting:
    """When rate_limited is True, internal tier is disconnected, falling back gracefully."""

    def test_rest_caller_degrades_to_passthrough(self) -> None:
        # Rate limited + REST caller (cannot sample) -> must degrade to passthrough
        state = _state(provider_name="claude-cli", rate_limited=True)
        ctx = _ctx(caller="rest")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"

    def test_mcp_caller_degrades_to_sampling(self) -> None:
        # Rate limited + MCP caller + sampling available -> degrade to sampling
        state = _state(
            provider_name="claude-cli", 
            sampling_capable=True, 
            mcp_connected=True, 
            rate_limited=True
        )
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "sampling"
        assert decision.degraded_from == "internal"

    def test_force_sampling_overrides_rate_limit(self) -> None:
        # force_sampling uses sampling anyway, independent of rate limits
        state = _state(
            provider_name="claude-cli", 
            sampling_capable=True, 
            mcp_connected=True, 
            rate_limited=True
        )
        ctx = _ctx(caller="mcp", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "sampling"
        assert decision.degraded_from is None


# ---------------------------------------------------------------------------
# Dataclass properties
# ---------------------------------------------------------------------------


class TestDecisionProperties:
    """Verify immutability of decision and state objects."""

    def test_decision_is_frozen(self) -> None:
        state = _state(provider_name="claude-cli")
        ctx = _ctx()
        decision = resolve_route(state, ctx)
        with pytest.raises(AttributeError):
            decision.tier = "passthrough"  # type: ignore[misc]

    def test_state_is_frozen(self) -> None:
        state = _state(provider_name="claude-cli")
        with pytest.raises(AttributeError):
            state.provider_name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge-case scenarios."""

    def test_mcp_no_provider_not_connected_force_sampling(self) -> None:
        """force_sampling with sampling_capable but MCP disconnected and no provider → passthrough degraded."""
        state = _state(sampling_capable=True, mcp_connected=False)
        ctx = _ctx(caller="mcp", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "sampling"

    def test_both_force_flags_passthrough_wins(self) -> None:
        """When both force flags are set, force_passthrough (tier 1) wins."""
        state = _state(provider_name="claude-cli", sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp", force_passthrough=True, force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from is None


# ---------------------------------------------------------------------------
# RoutingManager integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def manager(tmp_path: Path, event_bus: EventBus) -> RoutingManager:
    return RoutingManager(event_bus=event_bus, data_dir=tmp_path)


class TestManagerSetProvider:
    def test_initial_state_no_provider(self, manager: RoutingManager) -> None:
        assert manager.state.provider is None
        assert manager.state.provider_name is None

    def test_set_provider(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        assert manager.state.provider is mock_provider
        assert manager.state.provider_name == "claude_cli"

    def test_set_provider_fires_event(self, manager: RoutingManager, event_bus: EventBus) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)
        mock_provider = MagicMock()
        mock_provider.name = "anthropic_api"
        manager.set_provider(mock_provider)
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["provider"] == "anthropic_api"

    def test_clear_provider(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.set_provider(None)
        assert manager.state.provider is None
        assert manager.state.provider_name is None


class TestManagerRateLimitSync:
    def test_initial_state_not_limited(self, manager: RoutingManager) -> None:
        assert manager.state.rate_limited is False

    def test_sync_rate_limit_updates_state(self, manager: RoutingManager) -> None:
        manager.sync_rate_limit(True)
        assert manager.state.rate_limited is True
        manager.sync_rate_limit(False)
        assert manager.state.rate_limited is False

    def test_sync_rate_limit_fires_event(self, manager: RoutingManager, event_bus: EventBus) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)
        manager.sync_rate_limit(True)
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["trigger"] == "rate_limit_state_changed"
        # Tiers should omit internal
        assert "internal" not in event["data"]["available_tiers"]


class TestManagerMcpInitialize:
    def test_sampling_detected(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        assert manager.state.mcp_connected is True

    def test_no_sampling(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=False)
        assert manager.state.sampling_capable is False
        assert manager.state.mcp_connected is True

    def test_false_overwrites_true(self, manager: RoutingManager) -> None:
        """A False initialize immediately downgrades a previous True (no optimistic buffer)."""
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        manager.on_mcp_initialize(sampling_capable=False)
        assert manager.state.sampling_capable is False

    def test_initialize_fires_event(self, manager: RoutingManager, event_bus: EventBus) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)
        manager.on_mcp_initialize(sampling_capable=True)
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["sampling_capable"] is True


class TestDisconnectDebounce:
    """Issues 2 + 3: debounced disconnect broadcast and matching initialize suppression.

    These tests run inside an event loop because the deferred broadcast
    is implemented as ``asyncio.create_task`` — without a loop the
    debounce path falls back to immediate broadcast (legacy sync behaviour).
    """

    @pytest.mark.asyncio
    async def test_disconnect_then_quick_reinitialize_emits_no_events(
        self, tmp_path: Path,
    ) -> None:
        """Issue-2 + Issue-3: per-tool-call cycle is fully silenced.

        Models the Claude-Code-style pattern: SSE stream closes after a
        tool call, then a fresh stream opens within the debounce window
        for the next tool call.  Capability is unchanged across the
        cycle, so neither the disconnect nor the re-initialize should
        produce a ``routing_state_changed`` event for the frontend.
        """
        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path)
        mgr.on_mcp_initialize(sampling_capable=True)

        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        eb._subscribers.add(queue)

        # Disconnect → schedules deferred broadcast (no event yet).
        mgr.on_mcp_disconnect()
        assert queue.empty(), "disconnect must not broadcast immediately"
        assert mgr._pending_disconnect_task is not None

        # Quick re-initialize within debounce window → cancel pending,
        # AND suppress the matching initialize broadcast because the
        # capability snapshot matches the incoming value.
        mgr.on_mcp_initialize(sampling_capable=True)
        # Yield once so the cancelled task settles.
        await asyncio.sleep(0)

        assert queue.empty(), (
            "no externally-visible state change across the debounce — "
            "neither disconnect nor initialize should broadcast"
        )
        assert mgr.state.mcp_connected is True
        assert mgr.state.sampling_capable is True

    @pytest.mark.asyncio
    async def test_disconnect_then_reinitialize_with_changed_capability_broadcasts(
        self, tmp_path: Path,
    ) -> None:
        """Issue-3 negative case: capability change breaks suppression.

        If the re-initialize sees a different ``sampling_capable``, the
        externally-visible state DID change, so the initialize broadcast
        must still fire (only the disconnect side stays suppressed).
        """
        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path)
        mgr.on_mcp_initialize(sampling_capable=True)

        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        eb._subscribers.add(queue)

        mgr.on_mcp_disconnect()
        mgr.on_mcp_initialize(sampling_capable=False)  # Changed!
        await asyncio.sleep(0)

        # Disconnect was suppressed, but the changed-capability
        # initialize is honestly broadcast.
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        assert len(events) == 1
        assert events[0]["data"]["trigger"] == "mcp_initialize"
        assert events[0]["data"]["sampling_capable"] is False

    @pytest.mark.asyncio
    async def test_sustained_disconnect_broadcasts_after_window(
        self, tmp_path: Path,
    ) -> None:
        """Issue-2: a real, sustained disconnect propagates after the debounce.

        Uses a tiny debounce override so the test stays fast.
        """
        from app.services import routing as _routing

        original = _routing.DISCONNECT_DEBOUNCE_SECONDS
        _routing.DISCONNECT_DEBOUNCE_SECONDS = 0.05
        try:
            eb = EventBus()
            mgr = RoutingManager(event_bus=eb, data_dir=tmp_path)
            mgr.on_mcp_initialize(sampling_capable=True)

            queue: asyncio.Queue = asyncio.Queue(maxsize=10)
            eb._subscribers.add(queue)

            mgr.on_mcp_disconnect()
            assert queue.empty(), "no immediate broadcast"

            # Wait past the debounce — broadcast must commit.
            await asyncio.sleep(0.15)

            events = []
            while not queue.empty():
                events.append(queue.get_nowait())
            assert any(
                e["data"]["trigger"] == "mcp_disconnect" for e in events
            ), "sustained disconnect must propagate"
            assert mgr.state.mcp_connected is False
            assert mgr.state.sampling_capable is None
            assert mgr._pre_disconnect_sampling is None, (
                "snapshot must clear once the disconnect commits"
            )
        finally:
            _routing.DISCONNECT_DEBOUNCE_SECONDS = original

    @pytest.mark.asyncio
    async def test_session_invalidated_during_debounce_cancels_pending(
        self, tmp_path: Path,
    ) -> None:
        """Issue-3 follow-up: a 400/404 landing inside the debounce window
        must cancel the pending deferred-broadcast and clear the snapshot
        so subscribers see exactly ONE event (session_invalidated), not
        TWO (deferred mcp_disconnect + session_invalidated).
        """
        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path)
        mgr.on_mcp_initialize(sampling_capable=True)

        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        eb._subscribers.add(queue)

        mgr.on_mcp_disconnect()
        assert mgr._pending_disconnect_task is not None
        assert mgr._pre_disconnect_sampling is True

        mgr.on_session_invalidated()
        await asyncio.sleep(0)

        assert mgr._pending_disconnect_task is None
        assert mgr._pre_disconnect_sampling is None

        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        triggers = [e["data"]["trigger"] for e in events]
        assert triggers.count("mcp_disconnect") == 0, (
            "deferred disconnect must be suppressed by session invalidation"
        )
        assert "session_invalidated" in triggers


class TestManagerActivity:
    def test_activity_updates_timestamp(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        old_activity = manager.state.last_activity
        import time
        time.sleep(0.01)  # Ensure time advances
        manager.on_mcp_activity()
        assert manager.state.last_activity > old_activity  # type: ignore[operator]

    def test_reconnection_detected(self, manager: RoutingManager, event_bus: EventBus) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        # Simulate disconnect
        manager._update_state(mcp_connected=False)
        assert manager.state.mcp_connected is False
        # Activity triggers reconnection
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)
        manager.on_mcp_activity()
        assert manager.state.mcp_connected is True
        # Should have fired reconnection event
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["trigger"] == "mcp_reconnect"


class TestManagerResolve:
    def test_delegates_to_resolver(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        ctx = RoutingContext(preferences={"pipeline": {}}, caller="rest")
        decision = manager.resolve(ctx)
        assert decision.tier == "internal"
        assert decision.provider_name == "claude_cli"

    def test_passthrough_when_nothing_available(self, manager: RoutingManager) -> None:
        ctx = RoutingContext(preferences={"pipeline": {}}, caller="rest")
        decision = manager.resolve(ctx)
        assert decision.tier == "passthrough"


class TestManagerRecovery:
    def test_no_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """No mcp_session.json — starts with safe defaults."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is None
        assert mgr.state.mcp_connected is False

    def test_fresh_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """Issue-4: a fresh session file (live MCP server) IS trusted on recovery.

        Scenario: FastAPI backend restarts (uvicorn auto-reload) while the MCP
        server is still up with an active client.  ``mcp_session.json`` was
        written within the capability-fresh window AND ``sse_streams > 0``
        (or activity is recent on legacy files), so we trust the file
        instead of waiting ~60s for the disconnect-checker reconnection
        poll to notice the live MCP server.
        """
        from datetime import datetime, timezone
        session_file = tmp_path / "mcp_session.json"
        now = datetime.now(timezone.utc).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True, "written_at": now, "last_activity": now,
            "sse_streams": 1,
        }))
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is True
        assert mgr.state.mcp_connected is True

    def test_capability_fresh_but_disconnected(
        self, tmp_path: Path, event_bus: EventBus,
    ) -> None:
        """Issue-4: capability is fresh but ``sse_streams=0`` — do NOT trust.

        ``written_at`` within the capability window is necessary but not
        sufficient: if ``detect_disconnect`` returns True (no active SSE
        streams), recovery falls back to the conservative "wait for
        handshake" behaviour.
        """
        from datetime import datetime, timezone
        session_file = tmp_path / "mcp_session.json"
        now = datetime.now(timezone.utc).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True, "written_at": now, "last_activity": now,
            "sse_streams": 0,
        }))
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is None
        assert mgr.state.mcp_connected is False

    def test_stale_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """Old mcp_session.json — sampling goes to None (unknown)."""
        from datetime import datetime, timedelta, timezone
        session_file = tmp_path / "mcp_session.json"
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True, "written_at": old, "last_activity": old,
        }))
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is None  # stale → unknown
        assert mgr.state.mcp_connected is False      # activity stale


class TestManagerDisconnectLoop:
    """Test the background disconnect checker."""

    @pytest.mark.asyncio
    async def test_disconnect_after_staleness(self, tmp_path: Path) -> None:
        """Activity going stale triggers disconnect and SSE event."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import patch

        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)
        assert mgr.state.mcp_connected is True

        # Set last_activity to far in the past (beyond staleness threshold)
        # Both in-memory state AND session file must be stale for disconnect
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        mgr._update_state(last_activity=stale_time)
        if mgr._session_file:
            mgr._session_file.update(last_activity=stale_time.isoformat())

        # Subscribe to events
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        eb._subscribers.add(queue)

        # Patch sleep to return immediately, then cancel after one iteration
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
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["trigger"] == "disconnect"

    @pytest.mark.asyncio
    async def test_no_disconnect_when_activity_fresh(self, tmp_path: Path) -> None:
        """Fresh activity does not trigger disconnect."""
        from unittest.mock import patch

        eb = EventBus()
        mgr = RoutingManager(event_bus=eb, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)

        # Activity is fresh (just set by on_mcp_initialize)
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        eb._subscribers.add(queue)

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

        assert mgr.state.mcp_connected is True
        # No disconnect event should have been published
        # (only the on_mcp_initialize event from setup, before subscription)
        assert queue.empty()


class TestManagerPersistGating:
    """Verify _persist() only writes when is_mcp_process=True."""

    def test_non_mcp_does_not_persist(self, tmp_path: Path, event_bus: EventBus) -> None:
        """FastAPI process (is_mcp_process=False) should not write to session file."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=False)
        mgr.on_mcp_initialize(sampling_capable=True)
        session_file = tmp_path / "mcp_session.json"
        # File should not have been written by _persist()
        assert not session_file.exists()


# ---------------------------------------------------------------------------
# Reconnection edge cases
# ---------------------------------------------------------------------------


class TestManagerReconnectionEdgeCases:
    """Edge cases around MCP disconnect → reconnect sequences."""

    def test_disconnect_then_activity_broadcasts_reconnect(
        self, manager: RoutingManager, event_bus: EventBus,
    ) -> None:
        """Activity after disconnect must broadcast mcp_reconnect event."""
        manager.on_mcp_initialize(sampling_capable=True)
        manager.on_mcp_disconnect()
        assert manager.state.mcp_connected is False

        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)

        manager.on_mcp_activity()
        assert manager.state.mcp_connected is True
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["trigger"] == "mcp_reconnect"

    def test_disconnect_clears_sampling_capable(
        self, manager: RoutingManager,
    ) -> None:
        """Disconnect clears sampling_capable — no stale capability after client leaves."""
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        manager.on_mcp_disconnect()
        assert manager.state.sampling_capable is None
        assert manager.state.mcp_connected is False

    def test_session_invalidation_then_reinitialize_recovers(
        self, manager: RoutingManager, event_bus: EventBus,
    ) -> None:
        """Full recovery: initialize → invalidate → re-initialize."""
        manager.on_mcp_initialize(sampling_capable=True)
        manager.on_session_invalidated()
        assert manager.state.sampling_capable is None
        assert manager.state.mcp_connected is False

        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)

        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        assert manager.state.mcp_connected is True
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["data"]["trigger"] == "mcp_initialize"

    def test_mcp_process_persists(self, tmp_path: Path, event_bus: EventBus) -> None:
        """MCP process (is_mcp_process=True) should write to session file."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)
        session_file = tmp_path / "mcp_session.json"
        assert session_file.exists()


class TestManagerSamplingDisconnect:
    """on_sampling_disconnect: partial disconnect when bridge leaves but CC stays."""

    def test_clears_sampling_keeps_connected(self, manager: RoutingManager) -> None:
        """Only sampling_capable is cleared; mcp_connected stays True."""
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        assert manager.state.mcp_connected is True

        manager.on_sampling_disconnect()

        assert manager.state.sampling_capable is None
        assert manager.state.mcp_connected is True  # NOT cleared

    def test_fires_event(self, manager: RoutingManager, event_bus: EventBus) -> None:
        """Broadcasts sampling_disconnect trigger."""
        manager.on_mcp_initialize(sampling_capable=True)
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)

        manager.on_sampling_disconnect()

        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["trigger"] == "sampling_disconnect"
        assert event["data"]["sampling_capable"] is None
        assert event["data"]["mcp_connected"] is True

    def test_idempotent_when_already_cleared(
        self, manager: RoutingManager, event_bus: EventBus,
    ) -> None:
        """Calling twice does NOT emit a duplicate event."""
        manager.on_mcp_initialize(sampling_capable=True)
        manager.on_sampling_disconnect()

        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)

        # Second call — already None, should be a no-op
        manager.on_sampling_disconnect()
        assert queue.empty()

    def test_tiers_lose_sampling(self, manager: RoutingManager) -> None:
        """Available tiers must drop 'sampling' after partial disconnect."""
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.on_mcp_initialize(sampling_capable=True)
        assert "sampling" in manager.available_tiers

        manager.on_sampling_disconnect()
        assert "sampling" not in manager.available_tiers
        assert "internal" in manager.available_tiers

    def test_persists_to_session_file(self, tmp_path: Path, event_bus: EventBus) -> None:
        """MCP process should persist the cleared sampling state to disk."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)
        mgr.on_sampling_disconnect()
        session_file = tmp_path / "mcp_session.json"
        assert session_file.exists()
        data = json.loads(session_file.read_text())
        assert data["sampling_capable"] is False  # None → persisted as False

    def test_full_disconnect_after_sampling_disconnect(
        self, manager: RoutingManager,
    ) -> None:
        """Full disconnect after partial disconnect also clears mcp_connected."""
        manager.on_mcp_initialize(sampling_capable=True)
        manager.on_sampling_disconnect()
        assert manager.state.mcp_connected is True  # still connected (CC)

        manager.on_mcp_disconnect()
        assert manager.state.mcp_connected is False
        assert manager.state.sampling_capable is None


class TestManagerAvailableTiers:
    def test_only_passthrough_when_nothing(self, manager: RoutingManager) -> None:
        assert manager.available_tiers == ["passthrough"]

    def test_internal_and_passthrough(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        assert "internal" in manager.available_tiers
        assert "passthrough" in manager.available_tiers

    def test_all_tiers(self, manager: RoutingManager) -> None:
        mock_provider = MagicMock()
        mock_provider.name = "claude_cli"
        manager.set_provider(mock_provider)
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.available_tiers == ["internal", "sampling", "passthrough"]


# ---------------------------------------------------------------------------
# E2E smoke test — optimize endpoint emits routing SSE event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimize_emits_routing_event(tmp_path: Path, db_session) -> None:
    """POST /api/optimize should emit a 'routing' SSE event as its first event.

    Uses passthrough tier (no provider) to get a clean, self-contained SSE stream.
    """
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.main import app

    # Create a routing manager with NO provider → passthrough tier
    no_provider_routing = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
    app.state.routing = no_provider_routing

    # Mock context enrichment service — returns minimal EnrichedContext
    from app.services.context_enrichment import EnrichedContext

    mock_context_service = MagicMock()
    mock_context_service.enrich = AsyncMock(
        return_value=EnrichedContext(raw_prompt="test"),
    )
    app.state.context_service = mock_context_service

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/optimize",
            json={"prompt": "Explain how to build a REST API with FastAPI and SQLAlchemy"},
        )

    app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")

    # Parse SSE events — format_sse() embeds event name in JSON: data: {"event": "...", ...}
    import json as _json

    body = response.text
    events: list[dict] = []
    for line in body.strip().split("\n"):
        if line.startswith("data: "):
            events.append(_json.loads(line[6:]))

    assert len(events) >= 2, f"Expected at least 2 SSE events, got {len(events)}"
    assert events[0]["event"] == "routing"
    assert events[0]["tier"] == "passthrough"
    assert events[0]["reason"]  # non-empty reason string
    assert events[1]["event"] == "passthrough"
    assert "assembled_prompt" in events[1]
