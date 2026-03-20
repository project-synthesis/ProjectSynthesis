"""Tests for MCP sampling capability detection, disconnect detection, and force toggles.

Covers:
- RoutingManager.on_mcp_initialize: MCP server tracks client capabilities in routing state
- Health endpoint: reads RoutingManager.state and surfaces sampling_capable + mcp_disconnected
- Preferences REST API: force_passthrough / force_sampling mutual exclusion via HTTP
- Integration: cross-system consistency (health + preferences + passthrough endpoints)
- _CapabilityDetectionMiddleware: ASGI middleware intercepts MCP initialize + activity tracking
- _touch_activity: throttled activity tracking, reconnection detection
- mcp_disconnected: dual-window staleness (30min capability + 5min activity)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.mcp_session_file import MCPSessionFile

VALID_PROMPT = "Write a Python function that sorts a list of integers using merge sort"


def _patch_mcp_data_dir(monkeypatch, tmp_path: Path) -> None:
    """Patch DATA_DIR in mcp_server and replace _session_file with one using tmp_path."""
    monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.mcp_server._session_file", MCPSessionFile(tmp_path))


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-memory rate limiter storage before each test."""
    from app.dependencies.rate_limit import _storage
    _storage.reset()
    yield
    _storage.reset()


# ---------------------------------------------------------------------------
# RoutingManager.on_mcp_initialize — MCP server tracks client capabilities
# ---------------------------------------------------------------------------


class TestRoutingMcpInitialize:
    """Unit tests for MCP capability tracking via RoutingManager.

    These replace the old _write_mcp_session_caps tests — capability
    detection now goes through RoutingManager.on_mcp_initialize() which
    updates in-memory state and persists to mcp_session.json as write-through.
    """

    @staticmethod
    def _make_routing(tmp_path, *, is_mcp_process: bool = True):
        """Create a RoutingManager for testing (defaults to MCP process)."""
        from app.services.event_bus import EventBus
        from app.services.routing import RoutingManager
        return RoutingManager(event_bus=EventBus(), data_dir=tmp_path, is_mcp_process=is_mcp_process)

    def test_sampling_true_updates_state(self, tmp_path):
        """on_mcp_initialize(True) sets sampling_capable=True and mcp_connected=True."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=True)
        assert routing.state.sampling_capable is True
        assert routing.state.mcp_connected is True

    def test_sampling_false_updates_state(self, tmp_path):
        """on_mcp_initialize(False) sets sampling_capable=False and mcp_connected=True."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=False)
        assert routing.state.sampling_capable is False
        assert routing.state.mcp_connected is True

    def test_persists_to_session_file(self, tmp_path):
        """on_mcp_initialize write-through persists to mcp_session.json."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=True)

        path = tmp_path / "mcp_session.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sampling_capable"] is True
        assert "written_at" in data

    def test_optimistic_does_not_downgrade_fresh_true(self, tmp_path):
        """A False initialize does NOT downgrade a fresh True (optimistic strategy)."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=True)
        routing.on_mcp_initialize(sampling_capable=False)
        # Optimistic: should still be True
        assert routing.state.sampling_capable is True

    def test_overwrites_with_sequential_calls(self, tmp_path):
        """False → True updates correctly."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=False)
        assert routing.state.sampling_capable is False
        routing.on_mcp_initialize(sampling_capable=True)
        assert routing.state.sampling_capable is True

    def test_last_capability_update_set(self, tmp_path):
        """on_mcp_initialize sets last_capability_update timestamp."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=True)
        assert routing.state.last_capability_update is not None
        assert (datetime.now(timezone.utc) - routing.state.last_capability_update).total_seconds() < 5

    def test_last_activity_set(self, tmp_path):
        """on_mcp_initialize sets last_activity timestamp."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=True)
        assert routing.state.last_activity is not None
        assert (datetime.now(timezone.utc) - routing.state.last_activity).total_seconds() < 5

    def test_available_tiers_includes_sampling(self, tmp_path):
        """After sampling_capable=True, available_tiers includes sampling."""
        routing = self._make_routing(tmp_path)
        routing.on_mcp_initialize(sampling_capable=True)
        assert "sampling" in routing.available_tiers

    def test_available_tiers_without_sampling(self, tmp_path):
        """Without sampling, available_tiers is just passthrough."""
        routing = self._make_routing(tmp_path)
        assert routing.available_tiers == ["passthrough"]

    def test_set_provider_adds_internal_tier(self, tmp_path):
        """set_provider adds internal tier to available_tiers."""
        routing = self._make_routing(tmp_path)
        mock_provider = SimpleNamespace(name="mock-provider")
        routing.set_provider(mock_provider)
        assert "internal" in routing.available_tiers


# ---------------------------------------------------------------------------
# Health endpoint — sampling_capable field
# ---------------------------------------------------------------------------


class TestHealthSamplingCapable:
    """Integration tests for the sampling_capable field in GET /api/health.

    Health now reads from RoutingManager.state instead of mcp_session.json.
    """

    @staticmethod
    def _set_routing_state(app_client, **kwargs):
        """Update routing state fields for testing."""
        from dataclasses import replace as _replace
        routing = app_client._transport.app.state.routing
        routing._state = _replace(routing._state, **kwargs)

    async def test_null_when_no_mcp_session(self, app_client):
        """Default routing state → sampling_capable is null."""
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["sampling_capable"] is None

    async def test_true_when_sampling_capable(self, app_client):
        """Routing state with sampling_capable=True → returns true."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is True

    async def test_false_when_not_sampling_capable(self, app_client):
        """Routing state with sampling_capable=False → returns false."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=False)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is False

    async def test_mcp_disconnected_when_connected_false(self, app_client):
        """sampling_capable set but mcp_connected=False → mcp_disconnected=True."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)
        self._set_routing_state(app_client, mcp_connected=False)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is True

    async def test_mcp_not_disconnected_when_connected(self, app_client):
        """sampling_capable and mcp_connected → mcp_disconnected=False."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is False

    async def test_available_tiers_with_provider(self, app_client):
        """With provider, available_tiers includes internal and passthrough."""
        data = (await app_client.get("/api/health")).json()
        assert "internal" in data["available_tiers"]
        assert "passthrough" in data["available_tiers"]

    async def test_available_tiers_without_provider(self, app_client):
        """Without provider, available_tiers is passthrough only."""
        app_client._transport.app.state.routing.set_provider(None)

        data = (await app_client.get("/api/health")).json()
        assert data["available_tiers"] == ["passthrough"]

    async def test_available_tiers_with_sampling(self, app_client):
        """With sampling capable, available_tiers includes sampling."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)

        data = (await app_client.get("/api/health")).json()
        assert "sampling" in data["available_tiers"]

    async def test_does_not_break_other_health_fields(self, app_client):
        """sampling_capable field doesn't interfere with standard health response."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)

        data = (await app_client.get("/api/health")).json()
        for key in ("status", "version", "provider", "score_health",
                    "avg_duration_ms", "recent_errors", "sampling_capable",
                    "available_tiers"):
            assert key in data


# ---------------------------------------------------------------------------
# Preferences REST API — force_passthrough / force_sampling mutual exclusion
# ---------------------------------------------------------------------------


class TestPreferencesForceTogglesAPI:
    """Integration tests for the force toggle preferences via the HTTP API."""

    @pytest.fixture(autouse=True)
    def _isolated_prefs(self, tmp_path, monkeypatch):
        """Replace the preferences service singleton with one in tmp_path."""
        from app.services.preferences import PreferencesService
        svc = PreferencesService(data_dir=tmp_path)
        monkeypatch.setattr("app.routers.preferences._svc", svc)

    async def test_defaults_both_false(self, app_client):
        """Fresh preferences have both force toggles disabled."""
        data = (await app_client.get("/api/preferences")).json()
        assert data["pipeline"]["force_passthrough"] is False
        assert data["pipeline"]["force_sampling"] is False

    async def test_patch_force_passthrough_true(self, app_client):
        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )
        assert resp.status_code == 200
        assert resp.json()["pipeline"]["force_passthrough"] is True
        assert resp.json()["pipeline"]["force_sampling"] is False

    async def test_patch_force_sampling_true(self, app_client):
        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_sampling": True}},
        )
        assert resp.status_code == 200
        assert resp.json()["pipeline"]["force_sampling"] is True
        assert resp.json()["pipeline"]["force_passthrough"] is False

    async def test_mutual_exclusion_both_true_returns_422(self, app_client):
        """Setting both to True in one patch is rejected."""
        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_sampling": True, "force_passthrough": True}},
        )
        assert resp.status_code == 422
        assert "mutually exclusive" in resp.json()["detail"]

    async def test_mutual_exclusion_sequential_conflict(self, app_client):
        """Setting passthrough=True, then sampling=True without clearing, fails."""
        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )
        assert resp.status_code == 200

        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_sampling": True}},
        )
        assert resp.status_code == 422
        assert "mutually exclusive" in resp.json()["detail"]

    async def test_mutual_exclusion_swap_in_single_patch(self, app_client):
        """Clearing one and enabling the other in a single patch succeeds."""
        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )

        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": False, "force_sampling": True}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline"]["force_sampling"] is True
        assert data["pipeline"]["force_passthrough"] is False

    async def test_roundtrip_get_after_patch(self, app_client):
        """GET returns the patched value, not a stale cache."""
        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )

        data = (await app_client.get("/api/preferences")).json()
        assert data["pipeline"]["force_passthrough"] is True

    async def test_non_boolean_rejected(self, app_client):
        """Non-boolean value for force toggle is rejected with 422."""
        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": "yes"}},
        )
        assert resp.status_code == 422

    async def test_patch_does_not_affect_other_toggles(self, app_client):
        """Patching force_passthrough does not change enable_explore etc."""
        data_before = (await app_client.get("/api/preferences")).json()

        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )
        data_after = (await app_client.get("/api/preferences")).json()

        for key in ("enable_explore", "enable_scoring", "enable_adaptation"):
            assert data_after["pipeline"][key] == data_before["pipeline"][key]

    async def test_disable_both_simultaneously(self, app_client):
        """Setting both to False is always valid."""
        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_sampling": True}},
        )

        resp = await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_sampling": False, "force_passthrough": False}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline"]["force_sampling"] is False
        assert data["pipeline"]["force_passthrough"] is False


# ---------------------------------------------------------------------------
# Integration: cross-system consistency
# ---------------------------------------------------------------------------


class TestSamplingDetectionIntegration:
    """Cross-system integration tests verifying health + preferences + endpoints."""

    async def test_health_sampling_independent_of_preferences(
        self, app_client, tmp_path, monkeypatch,
    ):
        """Health sampling_capable and preferences force toggles are independent systems."""
        from app.services.preferences import PreferencesService
        monkeypatch.setattr(
            "app.routers.preferences._svc",
            PreferencesService(data_dir=tmp_path),
        )

        # Set routing state to sampling_capable=False
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=False)

        # Enable force_passthrough in preferences
        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )

        # Verify both reflect correctly and independently
        health = (await app_client.get("/api/health")).json()
        prefs = (await app_client.get("/api/preferences")).json()

        assert health["sampling_capable"] is False
        assert prefs["pipeline"]["force_passthrough"] is True
        assert prefs["pipeline"]["force_sampling"] is False

    async def test_passthrough_prepare_works_with_force_passthrough_enabled(
        self, app_client, tmp_path, monkeypatch,
    ):
        """Passthrough prepare works regardless of force_passthrough setting."""
        from app.services.preferences import PreferencesService
        monkeypatch.setattr(
            "app.routers.preferences._svc",
            PreferencesService(data_dir=tmp_path),
        )

        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )

        resp = await app_client.post(
            "/api/optimize/passthrough",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert "assembled_prompt" in resp.json()

    async def test_force_passthrough_routes_to_passthrough_tier(
        self, app_client, tmp_path, monkeypatch,
    ):
        """POST /api/optimize with force_passthrough routes to passthrough tier.

        The routing manager reads force_passthrough from preferences and returns
        passthrough tier regardless of provider availability.
        """
        from app.services.preferences import PreferencesService
        monkeypatch.setattr(
            "app.routers.preferences._svc",
            PreferencesService(data_dir=tmp_path),
        )

        await app_client.patch(
            "/api/preferences",
            json={"pipeline": {"force_passthrough": True}},
        )
        app_client._transport.app.state.routing.set_provider(None)

        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

    async def test_routing_sampling_reflected_in_health(self, app_client):
        """RoutingManager sampling state is reflected in health endpoint."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is True

    async def test_routing_no_sampling_reflected_in_health(self, app_client):
        """RoutingManager with sampling_capable=False is reflected in health."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=False)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is False

    async def test_routing_no_mcp_session_health_returns_none(self, app_client):
        """When no MCP session has been established, health returns sampling_capable=None."""
        # Default routing state has sampling_capable=None
        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is None

    async def test_routing_lifecycle_via_health(self, app_client):
        """Full lifecycle: no session → MCP initialize → health reflects state."""
        routing = app_client._transport.app.state.routing

        # Step 1: No MCP session → null
        assert (await app_client.get("/api/health")).json()["sampling_capable"] is None

        # Step 2: MCP client connects with sampling
        routing.on_mcp_initialize(sampling_capable=True)

        # Step 3: Health returns true
        assert (await app_client.get("/api/health")).json()["sampling_capable"] is True

        # Step 4: MCP client disconnects (simulate by marking disconnected)
        from dataclasses import replace as _replace
        routing._state = _replace(routing._state, mcp_connected=False)

        # Step 5: Health still returns sampling_capable (the value), but mcp_disconnected=True
        health = (await app_client.get("/api/health")).json()
        assert health["sampling_capable"] is True
        assert health["mcp_disconnected"] is True


# ---------------------------------------------------------------------------
# _CapabilityDetectionMiddleware — ASGI middleware intercepts MCP initialize
# ---------------------------------------------------------------------------


class TestCapabilityDetectionMiddleware:
    """Test the ASGI middleware that detects sampling capability on MCP handshake.

    These tests set ``_routing = None`` so the middleware falls through to the
    legacy session-file path.  The new routing-based path is tested via
    ``TestCapabilityDetectionMiddlewareWithRouting`` below.
    """

    @pytest.fixture()
    def mw_data_dir(self, tmp_path, monkeypatch):
        """Point the middleware's DATA_DIR to a temp directory.

        Sets ``_routing = None`` so middleware uses the fallback file-write path.
        """
        _patch_mcp_data_dir(monkeypatch, tmp_path)
        monkeypatch.setattr("app.mcp_server._routing", None)
        return tmp_path

    @staticmethod
    def _make_initialize_body(capabilities: dict | None = None) -> bytes:
        """Build a JSON-RPC initialize request body."""
        caps = capabilities if capabilities is not None else {"sampling": {}}
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": caps,
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        }).encode()

    @staticmethod
    def _make_tool_call_body(tool: str = "synthesis_analyze") -> bytes:
        """Build a JSON-RPC tool call body (non-initialize)."""
        return json.dumps({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {"prompt": "test"}},
        }).encode()

    def test_detects_sampling_true(self, mw_data_dir):
        """Middleware writes sampling_capable=true when capabilities include sampling."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = self._make_initialize_body({"sampling": {}})
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        session_path = mw_data_dir / "mcp_session.json"
        assert session_path.exists()
        data = json.loads(session_path.read_text())
        assert data["sampling_capable"] is True
        assert "written_at" in data

    def test_detects_sampling_false(self, mw_data_dir):
        """Middleware writes sampling_capable=false when capabilities lack sampling."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = self._make_initialize_body({"roots": {"listChanged": True}})
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        data = json.loads((mw_data_dir / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_detects_sampling_with_nonempty_dict(self, mw_data_dir):
        """Sampling with config dict is still detected as capable."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = self._make_initialize_body({"sampling": {"maxTokens": 8192}})
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        data = json.loads((mw_data_dir / "mcp_session.json").read_text())
        assert data["sampling_capable"] is True

    def test_ignores_non_initialize_method(self, mw_data_dir):
        """Non-initialize JSON-RPC methods do not write session file."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = self._make_tool_call_body()
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        assert not (mw_data_dir / "mcp_session.json").exists()

    def test_ignores_invalid_json(self, mw_data_dir):
        """Invalid JSON body does not crash or write session file."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        _CapabilityDetectionMiddleware._inspect_initialize(b"not json{{{")
        assert not (mw_data_dir / "mcp_session.json").exists()

    def test_ignores_non_dict_body(self, mw_data_dir):
        """JSON array body is silently ignored."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        _CapabilityDetectionMiddleware._inspect_initialize(b'[1,2,3]')
        assert not (mw_data_dir / "mcp_session.json").exists()

    def test_empty_capabilities(self, mw_data_dir):
        """Empty capabilities dict → sampling_capable=false."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = self._make_initialize_body({})
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        data = json.loads((mw_data_dir / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_missing_capabilities_key(self, mw_data_dir):
        """Initialize without capabilities key → sampling_capable=false."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26",
                       "clientInfo": {"name": "t", "version": "1"}},
        }).encode()
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        data = json.loads((mw_data_dir / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_optimistic_does_not_downgrade_fresh_true(self, mw_data_dir):
        """A False initialize does NOT overwrite a fresh True (optimistic strategy)."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # First: sampling capable
        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"sampling": {}})
        )
        assert json.loads((mw_data_dir / "mcp_session.json").read_text())["sampling_capable"] is True

        # Second: no sampling — should be ignored because True is fresh
        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        assert json.loads((mw_data_dir / "mcp_session.json").read_text())["sampling_capable"] is True

    def test_overwrites_stale_true_with_false(self, mw_data_dir):
        """A False initialize DOES overwrite a stale True (past staleness window)."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # Write a stale True (past 30-min window)
        path = mw_data_dir / "mcp_session.json"
        path.write_text(json.dumps({
            "sampling_capable": True,
            "written_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
        }))

        # False should overwrite because True is stale
        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        assert json.loads(path.read_text())["sampling_capable"] is False

    def test_false_overwrites_existing_false(self, mw_data_dir):
        """A False initialize overwrites an existing False (refreshes timestamp)."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # Write an existing False
        path = mw_data_dir / "mcp_session.json"
        old_time = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        path.write_text(json.dumps({"sampling_capable": False, "written_at": old_time}))

        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        data = json.loads(path.read_text())
        assert data["sampling_capable"] is False
        assert data["written_at"] != old_time  # timestamp refreshed

    def test_written_at_is_recent_utc(self, mw_data_dir):
        """written_at timestamp is parseable and within a few seconds of now."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"sampling": {}})
        )
        data = json.loads((mw_data_dir / "mcp_session.json").read_text())
        ts = datetime.fromisoformat(data["written_at"])
        assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 5

    def test_middleware_is_attached_to_mcp_app(self):
        """The patched streamable_http_app includes the middleware."""
        from app.mcp_server import mcp as mcp_instance

        app = mcp_instance.streamable_http_app()
        middleware_classes = [m.cls for m in app.user_middleware]
        from app.mcp_server import _CapabilityDetectionMiddleware
        assert _CapabilityDetectionMiddleware in middleware_classes

    def test_initialize_writes_last_activity(self, mw_data_dir):
        """Middleware writes last_activity field on initialize."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        body = self._make_initialize_body({"sampling": {}})
        _CapabilityDetectionMiddleware._inspect_initialize(body)

        data = json.loads((mw_data_dir / "mcp_session.json").read_text())
        assert "last_activity" in data
        ts = datetime.fromisoformat(data["last_activity"])
        assert abs((datetime.now(timezone.utc) - ts).total_seconds()) < 5

    def test_returns_result_on_initialize_write(self, mw_data_dir):
        """_inspect_initialize returns sampling_capable dict when a file is written."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        result = _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"sampling": {}})
        )
        assert result == {"sampling_capable": True}

        # Non-sampling initialize also returns a result
        result2 = _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        # Second call: fresh True on file, so False is suppressed → returns None
        assert result2 is None

    def test_returns_none_for_non_initialize(self, mw_data_dir):
        """_inspect_initialize returns None for non-initialize messages."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        result = _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_tool_call_body()
        )
        assert result is None

    def test_returns_none_for_invalid_json(self, mw_data_dir):
        """_inspect_initialize returns None for unparseable body."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        result = _CapabilityDetectionMiddleware._inspect_initialize(b"not json{{{")
        assert result is None

    def test_returns_false_capable_when_stale_true_overwritten(self, mw_data_dir):
        """_inspect_initialize returns sampling_capable=False when overwriting stale True."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # Write a stale True
        path = mw_data_dir / "mcp_session.json"
        path.write_text(json.dumps({
            "sampling_capable": True,
            "written_at": (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
        }))

        result = _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        assert result == {"sampling_capable": False}


# ---------------------------------------------------------------------------
# _inspect_initialize with RoutingManager — primary path
# ---------------------------------------------------------------------------


class TestCapabilityDetectionMiddlewareWithRouting:
    """Test _inspect_initialize when RoutingManager is active (primary path)."""

    @pytest.fixture()
    def routing(self, tmp_path, monkeypatch):
        """Set up a RoutingManager and wire it into mcp_server._routing."""
        _patch_mcp_data_dir(monkeypatch, tmp_path)
        from app.services.event_bus import EventBus
        from app.services.routing import RoutingManager
        rm = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
        monkeypatch.setattr("app.mcp_server._routing", rm)
        return rm

    @staticmethod
    def _make_initialize_body(capabilities: dict | None = None) -> bytes:
        caps = capabilities if capabilities is not None else {"sampling": {}}
        return json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": caps,
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        }).encode()

    def test_updates_routing_state_sampling_true(self, routing):
        """Initialize with sampling → routing state sampling_capable=True."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"sampling": {}})
        )
        assert routing.state.sampling_capable is True
        assert routing.state.mcp_connected is True

    def test_updates_routing_state_sampling_false(self, routing):
        """Initialize without sampling → routing state sampling_capable=False."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        assert routing.state.sampling_capable is False
        assert routing.state.mcp_connected is True

    def test_returns_result_dict(self, routing):
        """_inspect_initialize returns sampling_capable dict via routing path."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        result = _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"sampling": {}})
        )
        assert result == {"sampling_capable": True}

    def test_optimistic_via_routing(self, routing):
        """Routing's optimistic strategy prevents downgrade."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"sampling": {}})
        )
        assert routing.state.sampling_capable is True

        # Second: no sampling — routing's optimistic strategy keeps True
        result = _CapabilityDetectionMiddleware._inspect_initialize(
            self._make_initialize_body({"roots": {}})
        )
        # Result still returned (routing always returns a result now)
        assert result == {"sampling_capable": False}
        # But routing state should still be True (optimistic)
        assert routing.state.sampling_capable is True


# ---------------------------------------------------------------------------
# _touch_activity with RoutingManager — primary path
# ---------------------------------------------------------------------------


class TestTouchActivityWithRouting:
    """Test _touch_activity when RoutingManager is active."""

    @pytest.fixture(autouse=True)
    def _reset_throttle(self):
        from app.mcp_server import _CapabilityDetectionMiddleware
        _CapabilityDetectionMiddleware._last_activity_write = 0.0
        yield
        _CapabilityDetectionMiddleware._last_activity_write = 0.0

    @pytest.fixture()
    def routing(self, tmp_path, monkeypatch):
        _patch_mcp_data_dir(monkeypatch, tmp_path)
        from app.services.event_bus import EventBus
        from app.services.routing import RoutingManager
        rm = RoutingManager(event_bus=EventBus(), data_dir=tmp_path)
        monkeypatch.setattr("app.mcp_server._routing", rm)
        return rm

    def test_touch_calls_routing_on_mcp_activity(self, routing):
        """_touch_activity calls routing.on_mcp_activity(), not file I/O."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        routing.on_mcp_initialize(sampling_capable=True)

        result = _CapabilityDetectionMiddleware._touch_activity()
        # Not a reconnect since mcp_connected was already True
        assert result is False
        assert routing.state.mcp_connected is True
        assert routing.state.last_activity is not None

    def test_touch_detects_reconnection_via_routing(self, routing):
        """_touch_activity returns True when routing transitions from disconnected."""
        from dataclasses import replace as _replace

        from app.mcp_server import _CapabilityDetectionMiddleware

        routing.on_mcp_initialize(sampling_capable=True)
        # Simulate disconnect
        routing._state = _replace(routing._state, mcp_connected=False)

        result = _CapabilityDetectionMiddleware._touch_activity()
        assert result is True
        assert routing.state.mcp_connected is True


# ---------------------------------------------------------------------------
# _touch_activity — throttled activity tracking on every MCP POST
# ---------------------------------------------------------------------------


class TestTouchActivity:
    """Tests for the _touch_activity classmethod in the middleware (fallback path).

    Sets ``_routing = None`` so middleware uses the legacy file-write path.
    """

    @pytest.fixture(autouse=True)
    def _reset_throttle(self):
        """Reset the class-level throttle between tests."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        _CapabilityDetectionMiddleware._last_activity_write = 0.0
        yield
        _CapabilityDetectionMiddleware._last_activity_write = 0.0

    @pytest.fixture()
    def data_dir(self, tmp_path, monkeypatch):
        _patch_mcp_data_dir(monkeypatch, tmp_path)
        monkeypatch.setattr("app.mcp_server._routing", None)
        return tmp_path

    @staticmethod
    def _write_session(data_dir: Path, sampling_capable: bool = True, activity_age_seconds: float = 0):
        """Write a mcp_session.json with the given state."""
        now = datetime.now(timezone.utc)
        written_at = now.isoformat()
        last_activity = (now - timedelta(seconds=activity_age_seconds)).isoformat()
        (data_dir / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": sampling_capable,
            "written_at": written_at,
            "last_activity": last_activity,
        }))

    def test_updates_last_activity(self, data_dir):
        """Touch activity updates the last_activity field."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        self._write_session(data_dir)
        before = json.loads((data_dir / "mcp_session.json").read_text())

        _CapabilityDetectionMiddleware._touch_activity()

        after = json.loads((data_dir / "mcp_session.json").read_text())
        assert after["last_activity"] != before["last_activity"] or \
            abs((datetime.fromisoformat(after["last_activity"]) - datetime.now(timezone.utc)).total_seconds()) < 2

    def test_preserves_sampling_capable(self, data_dir):
        """Touch activity preserves the sampling_capable and written_at fields."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        self._write_session(data_dir, sampling_capable=True)
        before = json.loads((data_dir / "mcp_session.json").read_text())

        _CapabilityDetectionMiddleware._touch_activity()

        after = json.loads((data_dir / "mcp_session.json").read_text())
        assert after["sampling_capable"] == before["sampling_capable"]
        assert after["written_at"] == before["written_at"]

    def test_throttled_within_window(self, data_dir):
        """Second call within throttle window is a no-op."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        self._write_session(data_dir)
        _CapabilityDetectionMiddleware._touch_activity()
        first = json.loads((data_dir / "mcp_session.json").read_text())["last_activity"]

        # Second call — should be throttled (returns False, doesn't write)
        result = _CapabilityDetectionMiddleware._touch_activity()
        second = json.loads((data_dir / "mcp_session.json").read_text())["last_activity"]

        assert result is False
        assert first == second

    def test_returns_false_when_no_file(self, data_dir):
        """Returns False when mcp_session.json doesn't exist."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        result = _CapabilityDetectionMiddleware._touch_activity()
        assert result is False

    def test_detects_reconnection(self, data_dir):
        """Returns True when previous activity is older than staleness window."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # Write session with activity 400s ago (> 300s threshold)
        self._write_session(data_dir, sampling_capable=True, activity_age_seconds=400)

        result = _CapabilityDetectionMiddleware._touch_activity()
        assert result is True

    def test_no_reconnection_for_fresh_activity(self, data_dir):
        """Returns False when previous activity is within staleness window."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # Write session with activity 60s ago (< 300s threshold)
        self._write_session(data_dir, sampling_capable=True, activity_age_seconds=60)

        result = _CapabilityDetectionMiddleware._touch_activity()
        assert result is False

    def test_no_reconnection_when_not_sampling_capable(self, data_dir):
        """No reconnection event when session was not sampling-capable."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        # Write session with stale activity but sampling_capable=false
        self._write_session(data_dir, sampling_capable=False, activity_age_seconds=400)

        result = _CapabilityDetectionMiddleware._touch_activity()
        assert result is False

    def test_handles_corrupt_file(self, data_dir):
        """Returns False on corrupt JSON (no crash)."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        (data_dir / "mcp_session.json").write_text("not json!")
        result = _CapabilityDetectionMiddleware._touch_activity()
        assert result is False


# ---------------------------------------------------------------------------
# _invalidate_stale_session — cleanup on failed reconnection (400/404)
# ---------------------------------------------------------------------------


class TestInvalidateStaleSession:
    """Tests for the _invalidate_stale_session static method."""

    @pytest.fixture()
    def data_dir(self, tmp_path, monkeypatch):
        _patch_mcp_data_dir(monkeypatch, tmp_path)
        return tmp_path

    def test_removes_existing_file(self, data_dir):
        """Removes mcp_session.json and returns True."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        path = data_dir / "mcp_session.json"
        path.write_text(json.dumps({"sampling_capable": True, "written_at": "x"}))
        assert path.exists()

        result = _CapabilityDetectionMiddleware._invalidate_stale_session()
        assert result is True
        assert not path.exists()

    def test_returns_false_when_no_file(self, data_dir):
        """Returns False when no file to remove."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        result = _CapabilityDetectionMiddleware._invalidate_stale_session()
        assert result is False

    def test_returns_false_on_permission_error(self, data_dir, monkeypatch):
        """Returns False on unlink failure (no crash)."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        path = data_dir / "mcp_session.json"
        path.write_text("{}")

        def _raise(*a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "unlink", _raise)
        result = _CapabilityDetectionMiddleware._invalidate_stale_session()
        assert result is False


# ---------------------------------------------------------------------------
# _write_optimistic_session — session-less GET reconnection
# ---------------------------------------------------------------------------


class TestWriteOptimisticSession:
    """Tests for the _write_optimistic_session static method."""

    @pytest.fixture()
    def data_dir(self, tmp_path, monkeypatch):
        _patch_mcp_data_dir(monkeypatch, tmp_path)
        return tmp_path

    def test_writes_sampling_capable_true(self, data_dir):
        """Creates mcp_session.json with sampling_capable=True."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        _CapabilityDetectionMiddleware._write_optimistic_session()
        path = data_dir / "mcp_session.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sampling_capable"] is True
        assert "written_at" in data
        assert "last_activity" in data

    def test_overwrites_existing_false(self, data_dir):
        """Overwrites an existing file even if it had sampling_capable=False."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        path = data_dir / "mcp_session.json"
        path.write_text(json.dumps({"sampling_capable": False, "written_at": "old"}))

        _CapabilityDetectionMiddleware._write_optimistic_session()
        data = json.loads(path.read_text())
        assert data["sampling_capable"] is True

    def test_no_crash_on_write_error(self, data_dir, monkeypatch):
        """Doesn't crash on write failure."""
        from app.mcp_server import _CapabilityDetectionMiddleware

        def _raise(*a, **kw):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "write_text", _raise)
        _CapabilityDetectionMiddleware._write_optimistic_session()  # no crash


# ---------------------------------------------------------------------------
# _clear_stale_session — startup cleanup
# ---------------------------------------------------------------------------


class TestClearStaleSession:
    """Tests that _clear_stale_session removes stale session state on startup."""

    def test_removes_existing_session_file(self, tmp_path, monkeypatch):
        """mcp_session.json is removed on startup."""
        _patch_mcp_data_dir(monkeypatch, tmp_path)

        path = tmp_path / "mcp_session.json"
        path.write_text(json.dumps({
            "sampling_capable": True,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }))

        from app.mcp_server import _clear_stale_session
        _clear_stale_session()
        assert not path.exists()

    def test_no_crash_when_no_file(self, tmp_path, monkeypatch):
        """Doesn't crash when mcp_session.json doesn't exist."""
        _patch_mcp_data_dir(monkeypatch, tmp_path)

        from app.mcp_server import _clear_stale_session
        _clear_stale_session()  # no crash


# ---------------------------------------------------------------------------
# Health endpoint — mcp_disconnected field
# ---------------------------------------------------------------------------


class TestHealthMcpDisconnected:
    """Tests for the mcp_disconnected field in the health endpoint.

    Health now reads from RoutingManager.state instead of mcp_session.json.
    Disconnect detection is managed by RoutingManager._disconnect_loop().
    """

    @staticmethod
    def _set_routing_state(app_client, **kwargs):
        """Update routing state fields for testing."""
        from dataclasses import replace as _replace
        routing = app_client._transport.app.state.routing
        routing._state = _replace(routing._state, **kwargs)

    async def test_false_when_no_mcp_session(self, app_client):
        """Default routing state → mcp_disconnected is false."""
        resp = await app_client.get("/api/health")
        assert resp.json()["mcp_disconnected"] is False

    async def test_false_when_connected(self, app_client):
        """sampling_capable=True + mcp_connected=True → mcp_disconnected=False."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is False

    async def test_true_when_disconnected(self, app_client):
        """sampling_capable=True + mcp_connected=False → mcp_disconnected=True."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)
        self._set_routing_state(app_client, mcp_connected=False)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is True

    async def test_false_when_not_sampling_capable(self, app_client):
        """sampling_capable=False → mcp_disconnected is always false."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=False)
        self._set_routing_state(app_client, mcp_connected=False)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is False
        assert data["mcp_disconnected"] is False

    async def test_false_when_sampling_none(self, app_client):
        """sampling_capable=None → mcp_disconnected is false."""
        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is None
        assert data["mcp_disconnected"] is False

    async def test_reconnect_clears_disconnected(self, app_client):
        """MCP reconnect clears mcp_disconnected."""
        routing = app_client._transport.app.state.routing
        routing.on_mcp_initialize(sampling_capable=True)
        self._set_routing_state(app_client, mcp_connected=False)

        # Verify disconnected
        data = (await app_client.get("/api/health")).json()
        assert data["mcp_disconnected"] is True

        # Reconnect
        routing.on_mcp_activity()

        # Should be connected again
        data = (await app_client.get("/api/health")).json()
        assert data["mcp_disconnected"] is False
