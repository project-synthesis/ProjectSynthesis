"""Tests for the routing decision engine.

Covers all 5 tiers of the priority chain:
  force passthrough > force sampling > internal provider > auto sampling > passthrough fallback
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal
from unittest.mock import MagicMock

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
        state = _state(provider_name="anthropic-api", sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="rest", force_sampling=True)
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.degraded_from == "sampling"

    def test_rest_caller_degrades_to_passthrough(self) -> None:
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
        assert decision.degraded_from is None

    def test_api_provider(self) -> None:
        state = _state(provider_name="anthropic-api")
        ctx = _ctx()
        decision = resolve_route(state, ctx)
        assert decision.tier == "internal"
        assert decision.provider is not None

    def test_provider_preferred_over_sampling(self) -> None:
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
        state = _state(sampling_capable=True, mcp_connected=True)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "sampling"
        assert decision.degraded_from == "internal"

    def test_rest_never_reaches_sampling(self) -> None:
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
        assert decision.degraded_from == "internal"

    def test_sampling_not_connected(self) -> None:
        state = _state(sampling_capable=True, mcp_connected=False)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"

    def test_sampling_none(self) -> None:
        state = _state(sampling_capable=None, mcp_connected=True)
        ctx = _ctx(caller="mcp")
        decision = resolve_route(state, ctx)
        assert decision.tier == "passthrough"
        assert decision.degraded_from == "internal"


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


class TestManagerMcpInitialize:
    def test_sampling_detected(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        assert manager.state.mcp_connected is True

    def test_no_sampling(self, manager: RoutingManager) -> None:
        manager.on_mcp_initialize(sampling_capable=False)
        assert manager.state.sampling_capable is False
        assert manager.state.mcp_connected is True

    def test_optimistic_skip_no_downgrade(self, manager: RoutingManager) -> None:
        """Fresh True should not be overwritten by False within staleness window."""
        manager.on_mcp_initialize(sampling_capable=True)
        assert manager.state.sampling_capable is True
        manager.on_mcp_initialize(sampling_capable=False)
        # Should still be True (optimistic strategy)
        assert manager.state.sampling_capable is True

    def test_initialize_fires_event(self, manager: RoutingManager, event_bus: EventBus) -> None:
        queue: asyncio.Queue = asyncio.Queue(maxsize=10)
        event_bus._subscribers.add(queue)
        manager.on_mcp_initialize(sampling_capable=True)
        assert not queue.empty()
        event = queue.get_nowait()
        assert event["event"] == "routing_state_changed"
        assert event["data"]["sampling_capable"] is True


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
        """Recent mcp_session.json with sampling=True — recovers correctly."""
        from datetime import datetime, timezone
        session_file = tmp_path / "mcp_session.json"
        now = datetime.now(timezone.utc).isoformat()
        session_file.write_text(json.dumps({
            "sampling_capable": True, "written_at": now, "last_activity": now,
        }))
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path)
        assert mgr.state.sampling_capable is True
        assert mgr.state.mcp_connected is True

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
        stale_time = datetime.now(timezone.utc) - timedelta(seconds=600)
        mgr._update_state(last_activity=stale_time)

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

    def test_mcp_process_persists(self, tmp_path: Path, event_bus: EventBus) -> None:
        """MCP process (is_mcp_process=True) should write to session file."""
        mgr = RoutingManager(event_bus=event_bus, data_dir=tmp_path, is_mcp_process=True)
        mgr.on_mcp_initialize(sampling_capable=True)
        session_file = tmp_path / "mcp_session.json"
        assert session_file.exists()


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
