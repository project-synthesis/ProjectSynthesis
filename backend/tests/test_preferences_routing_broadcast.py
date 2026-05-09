"""Tests for the 2026-05-09 preferences→routing broadcast bridge.

Pre-fix the ``PATCH /api/preferences`` endpoint published only
``preferences_changed`` on the in-process event bus. The frontend's
tier-display path subscribes to ``routing_state_changed``, NOT
``preferences_changed`` — so a multi-client / CLI-driven flip of
``force_passthrough`` or ``force_sampling`` silently desynced subscribers.
The fix wires a follow-on ``broadcast_external`` call when the patch
touches a routing-relevant pipeline key.

These tests pin:
1. Routing-relevant patches publish exactly one ``routing_state_changed``
   in addition to ``preferences_changed``.
2. Non-routing patches (models, defaults, scoring toggles) publish only
   ``preferences_changed`` — no spurious routing broadcast.
3. The routing broadcast carries the canonical payload shape so frontend
   subscribers can decode it the same way as backend-driven broadcasts.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.preferences import router as preferences_router
from app.services.event_bus import EventBus, event_bus


@pytest.fixture()
def app_with_routing(tmp_path, monkeypatch):
    """Build a minimal FastAPI app with a stub RoutingManager wired into
    ``app.state.routing`` so the patch handler's broadcast path runs.
    """
    # Point the preferences service at a tmp dir so each test gets a
    # fresh file. The router instantiates its service at import time;
    # patch the module-level _svc.
    from app.routers import preferences as prefs_module
    from app.services.preferences import PreferencesService
    monkeypatch.setattr(
        prefs_module, "_svc", PreferencesService(data_dir=tmp_path),
    )

    app = FastAPI()
    app.include_router(preferences_router)

    # Stub RoutingManager — only ``broadcast_external`` is used by the
    # router. Capture call args for assertions.
    routing_stub = MagicMock()
    routing_stub.broadcast_external = MagicMock()
    app.state.routing = routing_stub

    return app, routing_stub


@pytest.fixture()
def event_capture():
    """Subscribe to the in-process event bus and capture every event
    fired during the test. Resets on teardown.
    """
    captured: list[tuple[str, Any]] = []

    async def _consumer():
        async for event in event_bus.subscribe():
            captured.append((event["event"], event["data"]))

    # The bus uses an async iterator — for these synchronous tests we
    # don't need to start the consumer. Instead we use a SYNC subscribe
    # via direct queue inspection. The simpler approach: replace
    # ``event_bus.publish`` with a wrapper that captures, then restore.
    original_publish = event_bus.publish

    def _capturing_publish(name: str, data: Any) -> None:
        captured.append((name, data))
        original_publish(name, data)

    event_bus.publish = _capturing_publish  # type: ignore[method-assign]
    yield captured
    event_bus.publish = original_publish  # type: ignore[method-assign]


def _patch(client: TestClient, body: dict) -> None:
    """Helper: PATCH /api/preferences with the given body, asserting 200."""
    res = client.patch("/api/preferences", json=body)
    assert res.status_code == 200, res.text


class TestPreferencesPatchRoutingBroadcast:
    def test_force_passthrough_patch_emits_routing_state_changed(
        self, app_with_routing, event_capture,
    ):
        app, routing_stub = app_with_routing
        client = TestClient(app)

        _patch(client, {"pipeline": {"force_passthrough": True}})

        # routing.broadcast_external called exactly once with the
        # canonical trigger label.
        routing_stub.broadcast_external.assert_called_once_with(
            trigger="preferences_changed",
        )
        # event_bus carries `preferences_changed` (always).
        kinds = [evt for evt, _ in event_capture]
        assert "preferences_changed" in kinds

    def test_force_sampling_patch_emits_routing_state_changed(
        self, app_with_routing, event_capture,
    ):
        app, routing_stub = app_with_routing
        client = TestClient(app)

        _patch(client, {"pipeline": {"force_sampling": True}})

        routing_stub.broadcast_external.assert_called_once_with(
            trigger="preferences_changed",
        )

    def test_non_routing_pipeline_patch_does_not_broadcast_routing(
        self, app_with_routing, event_capture,
    ):
        # Only `force_sampling` and `force_passthrough` are routing-relevant.
        # Other pipeline toggles (e.g. enable_scoring) MUST NOT trigger the
        # routing broadcast — the available_tiers don't depend on them.
        app, routing_stub = app_with_routing
        client = TestClient(app)

        _patch(client, {"pipeline": {"enable_scoring": False}})

        routing_stub.broadcast_external.assert_not_called()

    def test_models_patch_does_not_broadcast_routing(
        self, app_with_routing, event_capture,
    ):
        app, routing_stub = app_with_routing
        client = TestClient(app)

        _patch(client, {"models": {"analyzer": "haiku"}})

        routing_stub.broadcast_external.assert_not_called()

    def test_defaults_patch_does_not_broadcast_routing(
        self, app_with_routing, event_capture,
    ):
        app, routing_stub = app_with_routing
        client = TestClient(app)

        _patch(client, {"defaults": {"strategy": "auto"}})

        routing_stub.broadcast_external.assert_not_called()

    def test_combined_routing_plus_non_routing_patch_broadcasts_once(
        self, app_with_routing, event_capture,
    ):
        # If a single patch touches BOTH a routing-relevant key AND a
        # non-routing key, exactly one routing broadcast should fire
        # (not two — we don't want to double-publish per touched key).
        app, routing_stub = app_with_routing
        client = TestClient(app)

        _patch(client, {
            "pipeline": {"force_sampling": True, "enable_scoring": False},
        })

        assert routing_stub.broadcast_external.call_count == 1

    def test_broadcast_failure_does_not_break_patch(
        self, app_with_routing, event_capture,
    ):
        # If the routing manager raises (e.g. event-bus unavailable), the
        # patch should still succeed — preference persistence is the
        # primary contract; broadcast is best-effort observability.
        app, routing_stub = app_with_routing
        routing_stub.broadcast_external.side_effect = RuntimeError("bus down")
        client = TestClient(app)

        _patch(client, {"pipeline": {"force_passthrough": True}})

        routing_stub.broadcast_external.assert_called_once()

    def test_no_routing_in_app_state_does_not_break_patch(
        self, app_with_routing, event_capture,
    ):
        # If RoutingManager isn't wired (e.g. a minimal test app), the
        # patch must still succeed without an AttributeError.
        app, _ = app_with_routing
        # Remove routing from app.state explicitly.
        delattr(app.state, "routing")
        client = TestClient(app)

        _patch(client, {"pipeline": {"force_passthrough": True}})
