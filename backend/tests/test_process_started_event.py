"""RED test for AC-14 — process_started decision at logger init.

Spec: docs/superpowers/specs/2026-06-12-v0.4.38-sub-domain-telemetry-design.md §4.4.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _reset_process_started_flag():
    """Reset the module-level singleton guard between tests in this file.

    ``app.mcp_server._process_started_emitted`` is a MODULE-LEVEL bool that
    persists across tests within the same pytest process. Without this
    fixture, the second test that calls ``_emit_process_started_decision_mcp()``
    finds the flag already True (set by the first test that called it) and
    returns 0 events — breaking the singleton-guarded test.

    Backend rationale (no equivalent fixture needed for ``app.main``):
    ``_emit_process_started_decision`` in ``app/main.py`` is the helper used
    by the backend lifespan and is intentionally NOT singleton-guarded — the
    backend lifespan executes exactly once per process so the guard would be
    redundant. Verified by the ``grep -n "_process_started_emitted" app/main.py``
    check in C2-INTEGRATE.1 (expected: no hits). If a future revision adds a
    backend-side flag, extend this fixture to reset both.
    """
    import app.mcp_server as _mcp

    _mcp._process_started_emitted = False
    yield
    _mcp._process_started_emitted = False


def test_ac14_backend_lifespan_emits_process_started(tmp_path, monkeypatch):
    """Importing main + starting lifespan emits one process_started."""
    from app.services.taxonomy.event_logger import (
        TaxonomyEventLogger,
        set_event_logger,
    )

    tel = TaxonomyEventLogger(events_dir=tmp_path / "events", publish_to_bus=False)
    set_event_logger(tel)

    # Simulate the helper that the lifespan code calls.
    from app.main import _emit_process_started_decision

    _emit_process_started_decision("backend")
    events = [e for e in tel.get_recent() if e.get("decision") == "process_started"]
    assert len(events) == 1
    ctx = events[0]["context"]
    assert ctx["process"] == "backend"
    assert isinstance(ctx["pid"], int)
    assert isinstance(ctx["version"], str)


def test_ac14_mcp_lifespan_emits_process_started(tmp_path):
    """The MCP helper emits one process_started under the _process_initialized guard."""
    from app.services.taxonomy.event_logger import (
        TaxonomyEventLogger,
        set_event_logger,
    )

    tel = TaxonomyEventLogger(
        events_dir=tmp_path / "events",
        publish_to_bus=False,
        cross_process=True,
    )
    set_event_logger(tel)

    # The MCP helper lives in mcp_server.py module
    from app.mcp_server import _emit_process_started_decision_mcp

    _emit_process_started_decision_mcp()
    events = [e for e in tel.get_recent() if e.get("decision") == "process_started"]
    assert len(events) == 1
    assert events[0]["context"]["process"] == "mcp"


def test_ac14_singleton_guarded(tmp_path):
    """The MCP guard prevents duplicate emission across repeat _process_initialized entries."""
    from app.services.taxonomy.event_logger import (
        TaxonomyEventLogger,
        set_event_logger,
    )

    tel = TaxonomyEventLogger(
        events_dir=tmp_path / "events",
        publish_to_bus=False,
        cross_process=True,
    )
    set_event_logger(tel)

    from app.mcp_server import _emit_process_started_decision_mcp

    _emit_process_started_decision_mcp()
    _emit_process_started_decision_mcp()
    _emit_process_started_decision_mcp()
    # The helper is idempotent — three calls produce one event.
    events = [e for e in tel.get_recent() if e.get("decision") == "process_started"]
    assert len(events) == 1
