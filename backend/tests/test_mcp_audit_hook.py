"""v0.4.36 Rider A — MCP-process read-engine audit hook (spec §4.3, AC-13)."""
from __future__ import annotations

import inspect


def test_mcp_lifespan_installs_audit_hook_inside_singleton_guard() -> None:
    """Static invariant (pattern: test_main_lifespan_no_ddl) — the install
    call lives inside ``_mcp_lifespan``'s ``if not _process_initialized``
    block, so it runs exactly once per process across N session lifespans."""
    import app.mcp_server as mcp_mod

    src = inspect.getsource(mcp_mod._mcp_lifespan)
    assert "_install_audit_hook_once()" in src
    guard_idx = src.index("if not _process_initialized")
    assert src.index("_install_audit_hook_once()") > guard_idx


def test_install_audit_hook_second_call_swallowed(monkeypatch) -> None:
    """The helper swallows the already-installed RuntimeError on a second
    call (mirroring main.py:1131-1142). The exactly-once-per-process
    guarantee itself is pinned by the static guard test above — this test
    pins the helper's re-entry safety."""
    import app.database as database_mod
    import app.mcp_server as mcp_mod

    calls = {"n": 0}

    def _fake_install(engine):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("already installed")

    monkeypatch.setattr(
        database_mod, "install_read_engine_audit_hook", _fake_install,
    )
    mcp_mod._install_audit_hook_once()
    mcp_mod._install_audit_hook_once()  # must not raise
    assert calls["n"] == 2  # called twice, second swallowed as already-installed
