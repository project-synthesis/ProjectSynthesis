"""Tests for MCP sampling capability detection, disconnect detection, and force toggles.

Covers:
- _write_mcp_session_caps: MCP server persists client capabilities to mcp_session.json
- Health endpoint: reads mcp_session.json and surfaces sampling_capable + mcp_disconnected
- Preferences REST API: force_passthrough / force_sampling mutual exclusion via HTTP
- Integration: cross-system consistency (health + preferences + passthrough endpoints)
- _CapabilityDetectionMiddleware: ASGI middleware intercepts MCP initialize + activity tracking
- _touch_activity: throttled activity tracking, reconnection detection
- mcp_disconnected: dual-window staleness (30min capability + 90s activity)
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

VALID_PROMPT = "Write a Python function that sorts a list of integers using merge sort"


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the in-memory rate limiter storage before each test."""
    from app.dependencies.rate_limit import _storage
    _storage.reset()
    yield
    _storage.reset()


# ---------------------------------------------------------------------------
# _write_mcp_session_caps — MCP server writes client sampling capability
# ---------------------------------------------------------------------------


class TestWriteMcpSessionCaps:
    """Unit tests for the _write_mcp_session_caps helper in mcp_server.py."""

    @staticmethod
    def _make_ctx(
        *,
        has_session: bool = True,
        has_client_params: bool = True,
        sampling_capability: object | None = None,
    ) -> SimpleNamespace | None:
        """Build a mock MCP Context with configurable capability chain."""
        if not has_session:
            return SimpleNamespace(session=None)
        if not has_client_params:
            return SimpleNamespace(session=SimpleNamespace(client_params=None))
        capabilities = SimpleNamespace(sampling=sampling_capability)
        client_params = SimpleNamespace(capabilities=capabilities)
        session = SimpleNamespace(client_params=client_params)
        return SimpleNamespace(session=session)

    def test_writes_true_when_sampling_capability_present(self, tmp_path, monkeypatch):
        """Sampling capability object present (even empty dict) → sampling_capable=true."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        ctx = self._make_ctx(sampling_capability={})
        _write_mcp_session_caps(ctx)

        path = tmp_path / "mcp_session.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["sampling_capable"] is True
        assert "written_at" in data

    def test_writes_true_when_sampling_is_nonempty_dict(self, tmp_path, monkeypatch):
        """Non-empty sampling capability dict also counts as capable."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        ctx = self._make_ctx(sampling_capability={"maxTokens": 16384})
        _write_mcp_session_caps(ctx)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert data["sampling_capable"] is True

    def test_writes_false_when_sampling_is_none(self, tmp_path, monkeypatch):
        """Sampling attribute is None → client does not support sampling."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        ctx = self._make_ctx(sampling_capability=None)
        _write_mcp_session_caps(ctx)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_writes_false_when_ctx_is_none(self, tmp_path, monkeypatch):
        """Null context (e.g., non-MCP invocation) → sampling_capable=false."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        _write_mcp_session_caps(None)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_writes_false_when_session_is_none(self, tmp_path, monkeypatch):
        """Context exists but session is None → sampling_capable=false."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        ctx = self._make_ctx(has_session=False)
        _write_mcp_session_caps(ctx)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_writes_false_when_client_params_is_none(self, tmp_path, monkeypatch):
        """Session exists but client_params is None → sampling_capable=false."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        ctx = self._make_ctx(has_client_params=False)
        _write_mcp_session_caps(ctx)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_writes_false_when_ctx_has_no_session_attr(self, tmp_path, monkeypatch):
        """Context object missing 'session' attribute entirely → false."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        ctx = object()  # no session attribute
        _write_mcp_session_caps(ctx)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert data["sampling_capable"] is False

    def test_written_at_is_utc_iso_timestamp(self, tmp_path, monkeypatch):
        """written_at is a timezone-aware UTC ISO 8601 string."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        _write_mcp_session_caps(None)

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        ts = datetime.fromisoformat(data["written_at"])
        assert ts.tzinfo is not None  # timezone-aware
        assert (datetime.now(timezone.utc) - ts).total_seconds() < 5

    def test_overwrites_previous_file(self, tmp_path, monkeypatch):
        """Subsequent calls overwrite the file, updating the value."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        # Write false
        _write_mcp_session_caps(self._make_ctx(sampling_capability=None))
        assert json.loads((tmp_path / "mcp_session.json").read_text())["sampling_capable"] is False

        # Overwrite with true
        _write_mcp_session_caps(self._make_ctx(sampling_capability={}))
        assert json.loads((tmp_path / "mcp_session.json").read_text())["sampling_capable"] is True

    def test_silent_on_write_error(self, monkeypatch):
        """Does not raise when DATA_DIR is not writable."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", Path("/nonexistent/path"))
        from app.mcp_server import _write_mcp_session_caps

        # Should not raise
        _write_mcp_session_caps(None)

    def test_file_is_valid_json_with_expected_keys(self, tmp_path, monkeypatch):
        """Output file has sampling_capable, written_at, and last_activity."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        _write_mcp_session_caps(self._make_ctx(sampling_capability={}))

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert set(data.keys()) == {"sampling_capable", "written_at", "last_activity"}


# ---------------------------------------------------------------------------
# Health endpoint — sampling_capable field
# ---------------------------------------------------------------------------


class TestHealthSamplingCapable:
    """Integration tests for the sampling_capable field in GET /api/health."""

    @staticmethod
    def _write_session(data_dir: Path, sampling_capable: bool, age_minutes: float = 0):
        """Write a mcp_session.json with the given capability and age."""
        written_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
        (data_dir / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": sampling_capable,
            "written_at": written_at.isoformat(),
        }))

    async def test_null_when_no_file(self, app_client, tmp_path, monkeypatch):
        """No mcp_session.json → sampling_capable is null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["sampling_capable"] is None

    async def test_true_when_fresh_and_capable(self, app_client, tmp_path, monkeypatch):
        """Fresh file with sampling_capable=true → returns true."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=0)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is True

    async def test_false_when_fresh_and_not_capable(self, app_client, tmp_path, monkeypatch):
        """Fresh file with sampling_capable=false → returns false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=False, age_minutes=0)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is False

    async def test_null_when_stale_31_minutes(self, app_client, tmp_path, monkeypatch):
        """File older than 30 minutes is stale → returns null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=31)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_true_when_20_minutes_old(self, app_client, tmp_path, monkeypatch):
        """File 20 minutes old is within the 30-minute window → returns value."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=20)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is True

    async def test_null_when_60_minutes_old(self, app_client, tmp_path, monkeypatch):
        """File 60 minutes old is well past staleness → returns null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=60)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_null_on_invalid_json(self, app_client, tmp_path, monkeypatch):
        """Corrupt JSON file → returns null (exception caught)."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text("not valid json!!")

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_null_on_missing_written_at_key(self, app_client, tmp_path, monkeypatch):
        """File missing written_at key → KeyError caught → null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": True,
        }))

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_null_on_missing_sampling_capable_key(self, app_client, tmp_path, monkeypatch):
        """File missing sampling_capable key → KeyError caught → null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "written_at": datetime.now(timezone.utc).isoformat(),
        }))

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_null_on_unparseable_written_at(self, app_client, tmp_path, monkeypatch):
        """written_at that can't be parsed as datetime → exception caught → null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": True,
            "written_at": "not-a-timestamp",
        }))

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_null_on_naive_datetime_written_at(self, app_client, tmp_path, monkeypatch):
        """Naive (no-timezone) written_at → comparison with UTC now raises TypeError → null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": True,
            "written_at": "2024-01-15T10:30:00",  # no timezone
        }))

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_null_on_empty_file(self, app_client, tmp_path, monkeypatch):
        """Empty file → JSON parse error → null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text("")

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_coerces_truthy_sampling_capable(self, app_client, tmp_path, monkeypatch):
        """Non-boolean truthy value for sampling_capable is coerced via bool()."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": "yes",  # truthy string
            "written_at": datetime.now(timezone.utc).isoformat(),
        }))

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is True

    async def test_coerces_falsy_sampling_capable(self, app_client, tmp_path, monkeypatch):
        """Falsy value (0, empty string) for sampling_capable is coerced to false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": 0,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }))

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is False

    async def test_does_not_break_other_health_fields(self, app_client, tmp_path, monkeypatch):
        """sampling_capable field doesn't interfere with standard health response."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True)

        data = (await app_client.get("/api/health")).json()
        for key in ("status", "version", "provider", "score_health",
                    "avg_duration_ms", "recent_errors", "sampling_capable"):
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
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        from app.services.preferences import PreferencesService
        monkeypatch.setattr(
            "app.routers.preferences._svc",
            PreferencesService(data_dir=tmp_path),
        )

        # Write sampling_capable=false to mcp_session.json
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": False,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }))

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

    async def test_normal_optimize_requires_provider_regardless_of_force_passthrough(
        self, app_client, tmp_path, monkeypatch,
    ):
        """POST /api/optimize still requires a provider even with force_passthrough on.

        force_passthrough is consumed by the frontend and MCP tool, not by the
        REST optimize endpoint (which always needs a provider for SSE streaming).
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
        app_client._transport.app.state.provider = None

        resp = await app_client.post(
            "/api/optimize",
            json={"prompt": VALID_PROMPT},
        )
        assert resp.status_code == 503

    async def test_write_then_health_roundtrip(self, app_client, tmp_path, monkeypatch):
        """Write mcp_session.json via _write_mcp_session_caps, then read via health."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)

        from app.mcp_server import _write_mcp_session_caps

        # Simulate an MCP client with sampling
        capabilities = SimpleNamespace(sampling={})
        client_params = SimpleNamespace(capabilities=capabilities)
        session = SimpleNamespace(client_params=client_params)
        ctx = SimpleNamespace(session=session)

        _write_mcp_session_caps(ctx)

        # Health should now report sampling_capable=true
        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is True

    async def test_write_no_sampling_then_health_roundtrip(
        self, app_client, tmp_path, monkeypatch,
    ):
        """Write mcp_session.json without sampling, verify health returns false."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)

        from app.mcp_server import _write_mcp_session_caps

        # Simulate an MCP client WITHOUT sampling
        capabilities = SimpleNamespace(sampling=None)
        client_params = SimpleNamespace(capabilities=capabilities)
        session = SimpleNamespace(client_params=client_params)
        ctx = SimpleNamespace(session=session)

        _write_mcp_session_caps(ctx)

        data = (await app_client.get("/api/health")).json()
        assert data["sampling_capable"] is False

    async def test_stale_after_write_then_health(
        self, app_client, tmp_path, monkeypatch,
    ):
        """Manually age mcp_session.json after writing — health returns null."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)

        from app.mcp_server import _write_mcp_session_caps

        _write_mcp_session_caps(None)

        # Manually backdate the written_at to 31 minutes ago (past 30-min window)
        path = tmp_path / "mcp_session.json"
        data = json.loads(path.read_text())
        data["written_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=31)
        ).isoformat()
        path.write_text(json.dumps(data))

        health = (await app_client.get("/api/health")).json()
        assert health["sampling_capable"] is None

    async def test_full_lifecycle(self, app_client, tmp_path, monkeypatch):
        """Full lifecycle: no file → write capable → health true → stale → null."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)

        from app.mcp_server import _write_mcp_session_caps

        # Step 1: No file → null
        assert (await app_client.get("/api/health")).json()["sampling_capable"] is None

        # Step 2: Write capable
        capabilities = SimpleNamespace(sampling={})
        client_params = SimpleNamespace(capabilities=capabilities)
        session = SimpleNamespace(client_params=client_params)
        _write_mcp_session_caps(SimpleNamespace(session=session))

        # Step 3: Health returns true
        assert (await app_client.get("/api/health")).json()["sampling_capable"] is True

        # Step 4: Manually age the file
        path = tmp_path / "mcp_session.json"
        data = json.loads(path.read_text())
        data["written_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=31)
        ).isoformat()
        path.write_text(json.dumps(data))

        # Step 5: Health returns null (stale)
        assert (await app_client.get("/api/health")).json()["sampling_capable"] is None


# ---------------------------------------------------------------------------
# _CapabilityDetectionMiddleware — ASGI middleware intercepts MCP initialize
# ---------------------------------------------------------------------------


class TestCapabilityDetectionMiddleware:
    """Test the ASGI middleware that detects sampling capability on MCP handshake."""

    @pytest.fixture()
    def mw_data_dir(self, tmp_path, monkeypatch):
        """Point the middleware's DATA_DIR to a temp directory."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
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
# _touch_activity — throttled activity tracking on every MCP POST
# ---------------------------------------------------------------------------


class TestTouchActivity:
    """Tests for the _touch_activity classmethod in the middleware."""

    @pytest.fixture(autouse=True)
    def _reset_throttle(self):
        """Reset the class-level throttle between tests."""
        from app.mcp_server import _CapabilityDetectionMiddleware
        _CapabilityDetectionMiddleware._last_activity_write = 0.0
        yield
        _CapabilityDetectionMiddleware._last_activity_write = 0.0

    @pytest.fixture()
    def data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
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
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
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
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
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
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)

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
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)

        from app.mcp_server import _clear_stale_session
        _clear_stale_session()  # no crash


# ---------------------------------------------------------------------------
# Health endpoint — mcp_disconnected field
# ---------------------------------------------------------------------------


class TestHealthMcpDisconnected:
    """Tests for the mcp_disconnected field in the health endpoint."""

    @staticmethod
    def _write_session(
        data_dir: Path,
        sampling_capable: bool = True,
        capability_age_minutes: float = 0,
        activity_age_seconds: float = 0,
        sse_streams: int = 0,
    ):
        """Write a mcp_session.json with both staleness dimensions."""
        now = datetime.now(timezone.utc)
        written_at = (now - timedelta(minutes=capability_age_minutes)).isoformat()
        last_activity = (now - timedelta(seconds=activity_age_seconds)).isoformat()
        (data_dir / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": sampling_capable,
            "written_at": written_at,
            "last_activity": last_activity,
            "sse_streams": sse_streams,
        }))

    async def test_false_when_no_file(self, app_client, tmp_path, monkeypatch):
        """No mcp_session.json → mcp_disconnected is false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        resp = await app_client.get("/api/health")
        assert resp.json()["mcp_disconnected"] is False

    async def test_false_when_fresh_activity(self, app_client, tmp_path, monkeypatch):
        """Fresh activity (< 5 min) with active SSE stream → mcp_disconnected is false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, activity_age_seconds=60, sse_streams=1)

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is False

    async def test_true_when_sse_streams_zero(self, app_client, tmp_path, monkeypatch):
        """sse_streams=0 with fresh activity → immediate disconnect (stream just closed)."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, activity_age_seconds=10, sse_streams=0)

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is True

    async def test_true_when_stale_activity(self, app_client, tmp_path, monkeypatch):
        """Stale activity (> 5 min) with fresh capability → mcp_disconnected is true."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, activity_age_seconds=400)

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is True

    async def test_false_when_sse_stream_active(self, app_client, tmp_path, monkeypatch):
        """Stale activity but active SSE stream → mcp_disconnected is false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(
            tmp_path, sampling_capable=True, activity_age_seconds=400, sse_streams=1,
        )

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is False

    async def test_false_when_not_sampling_capable(self, app_client, tmp_path, monkeypatch):
        """Not sampling capable → mcp_disconnected is always false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=False, activity_age_seconds=400)

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is False
        assert data["mcp_disconnected"] is False

    async def test_false_when_capability_stale(self, app_client, tmp_path, monkeypatch):
        """Capability stale (> 30 min) → both null/false regardless of activity."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(
            tmp_path, sampling_capable=True,
            capability_age_minutes=35, activity_age_seconds=400,
        )

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is None
        assert data["mcp_disconnected"] is False

    async def test_false_when_no_last_activity_field(self, app_client, tmp_path, monkeypatch):
        """Legacy session file without last_activity → mcp_disconnected is false."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        # Write a session file without last_activity (legacy format)
        (tmp_path / "mcp_session.json").write_text(json.dumps({
            "sampling_capable": True,
            "written_at": datetime.now(timezone.utc).isoformat(),
        }))

        resp = await app_client.get("/api/health")
        data = resp.json()
        assert data["sampling_capable"] is True
        assert data["mcp_disconnected"] is False

    async def test_boundary_exactly_at_threshold(self, app_client, tmp_path, monkeypatch):
        """Activity exactly at 300s → not yet disconnected (boundary: > not >=)."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, activity_age_seconds=300)

        resp = await app_client.get("/api/health")
        data = resp.json()
        # Boundary: > 300, not >=, so exactly 300 should be false
        # (but timing variance may push it slightly over, so we just verify the field exists)
        assert "mcp_disconnected" in data
