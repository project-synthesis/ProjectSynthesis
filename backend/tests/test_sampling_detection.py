"""Tests for MCP sampling capability detection and force_passthrough/force_sampling toggles.

Covers:
- _write_mcp_session_caps: MCP server persists client capabilities to mcp_session.json
- Health endpoint: reads mcp_session.json and surfaces sampling_capable field
- Preferences REST API: force_passthrough / force_sampling mutual exclusion via HTTP
- Integration: cross-system consistency (health + preferences + passthrough endpoints)
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

    def test_file_is_valid_json_with_exactly_two_keys(self, tmp_path, monkeypatch):
        """Output file has exactly sampling_capable and written_at."""
        monkeypatch.setattr("app.mcp_server.DATA_DIR", tmp_path)
        from app.mcp_server import _write_mcp_session_caps

        _write_mcp_session_caps(self._make_ctx(sampling_capability={}))

        data = json.loads((tmp_path / "mcp_session.json").read_text())
        assert set(data.keys()) == {"sampling_capable", "written_at"}


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

    async def test_null_when_stale_6_minutes(self, app_client, tmp_path, monkeypatch):
        """File older than 5 minutes is stale → returns null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=6)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is None

    async def test_true_when_4_minutes_old(self, app_client, tmp_path, monkeypatch):
        """File 4 minutes old is within the 5-minute window → returns value."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=4)

        resp = await app_client.get("/api/health")
        assert resp.json()["sampling_capable"] is True

    async def test_null_when_10_minutes_old(self, app_client, tmp_path, monkeypatch):
        """File 10 minutes old is well past staleness → returns null."""
        monkeypatch.setattr("app.routers.health.DATA_DIR", tmp_path)
        self._write_session(tmp_path, sampling_capable=True, age_minutes=10)

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

        # Manually backdate the written_at to 10 minutes ago
        path = tmp_path / "mcp_session.json"
        data = json.loads(path.read_text())
        data["written_at"] = (
            datetime.now(timezone.utc) - timedelta(minutes=10)
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
            datetime.now(timezone.utc) - timedelta(minutes=6)
        ).isoformat()
        path.write_text(json.dumps(data))

        # Step 5: Health returns null (stale)
        assert (await app_client.get("/api/health")).json()["sampling_capable"] is None
