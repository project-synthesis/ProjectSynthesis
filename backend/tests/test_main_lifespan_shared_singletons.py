"""Pin backend lifespan wiring of every ``app.tools._shared`` singleton
that REST-callable MCP tool handlers depend on.

Surfaced during v0.4.22 soak-gate Day 1 (2026-05-12). The MCP server
process wires its tool-handler singletons via ``mcp_server.py`` startup —
but the backend process (``main.py``) historically wired only a subset,
even though REST endpoints (``POST /api/refine``, ``POST /api/optimize``,
``POST /api/optimize/passthrough``, etc.) delegate to MCP tool handlers
via ``app.tools.<name>.handle_<name>`` which call ``get_routing()`` /
``get_context_service()`` / ``get_taxonomy_engine()`` / etc. on the
``_shared`` singleton.

Before the fix, ``POST /api/refine`` emitted
``{"event": "error", "error": "Routing service not initialized"}`` in
production because ``_shared._routing`` stayed ``None`` in the backend
process. Tests passed because ``conftest.py:279`` stubs the singleton.

This file is a STATIC-TEXT guard (same pattern as
``test_main_lifespan_no_ddl.py``). It greps the live ``main.py`` source
for the required ``_shared.set_X`` import + invocation pairs. Pure
parse-and-grep — no DB, no FastAPI app spin-up needed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]  # …/backend
_MAIN_PY = _BACKEND_DIR / "app" / "main.py"


# Singletons that MUST be wired by backend lifespan. Format:
#
#     (setter_name, [caller_module_path, ...])
#
# Each setter is a function defined in ``app.tools._shared``. Each
# ``caller_module_path`` is a tool handler called from REST in the backend
# process that would raise ``ValueError("X not initialized")`` without
# the corresponding ``_shared.set_X`` invocation in ``main.py`` lifespan.
_REQUIRED_WIRING = [
    ("set_routing", [
        "tools/refine.py",
        "tools/save_result.py",
        "tools/health.py",
        "tools/analyze.py",
        "services/batch_pipeline.py",
    ]),
    ("set_taxonomy_engine", [
        "tools/optimize.py",
        "tools/match.py",
    ]),
    ("set_context_service", [
        "tools/refine.py",
        "tools/prepare.py",
        "tools/optimize.py",
    ]),
    ("set_domain_resolver", [
        "tools/optimize.py",
        "tools/analyze.py",
        "services/sampling_pipeline.py",
    ]),
    ("set_signal_loader", [
        "services/heuristic_analyzer.py (parity with MCP)",
    ]),
    ("set_write_queue", [
        "(every persist path — pre-v0.4.22 regression guard)",
    ]),
    ("set_run_orchestrator", [
        "tools/probe.py",
        "tools/seed.py",
        "tools/replay_suite.py",
    ]),
]


@pytest.mark.parametrize("setter_name,callers", _REQUIRED_WIRING)
def test_main_py_wires_shared_singleton(setter_name: str, callers: list[str]) -> None:
    """``app/main.py`` lifespan must import and call ``_shared.{setter}``.

    Args:
        setter_name: e.g. ``"set_routing"``.
        callers: One or more REST-callable handler paths that depend on
            the wired singleton.
    """
    src = _MAIN_PY.read_text(encoding="utf-8")

    # Match either of two valid forms:
    #   1. ``from app.tools._shared import set_X``
    #   2. ``from app.tools._shared import set_X as _set_shared_X``
    import_pattern = re.compile(
        rf"from\s+app\.tools\._shared\s+import.*\b{re.escape(setter_name)}\b",
        re.MULTILINE | re.DOTALL,
    )
    assert import_pattern.search(src), (
        f"main.py lifespan must import `{setter_name}` from "
        f"`app.tools._shared`. Without this wiring, the following "
        f"REST-callable tool handlers will raise "
        f"`ValueError(\"{setter_name.replace('set_', '').replace('_', ' ')} "
        f"not initialized\")` in production:\n"
        + "\n".join(f"  - {c}" for c in callers)
        + "\n\nMCP server process wires this via `mcp_server.py`; backend "
        "process must do the same to keep singleton coherence."
    )

    # Match either of two valid invocation forms (alias OR direct call):
    #   1. ``set_X(...)``      (direct call)
    #   2. ``_set_shared_X(...)``  (aliased call)
    #   3. ``_shared.set_X(...)``  (qualified call — MCP server style)
    call_pattern = re.compile(
        rf"\b({re.escape(setter_name)}|_set_shared_\w+|_shared\.{re.escape(setter_name)})\s*\(",
        re.MULTILINE,
    )
    matches = call_pattern.findall(src)
    assert matches, (
        f"main.py lifespan must call `{setter_name}` after creating the "
        f"underlying service. Without this wiring, the singleton stays "
        f"``None`` and the following REST-callable handlers fail in "
        f"production:\n"
        + "\n".join(f"  - {c}" for c in callers)
    )


def test_main_py_singleton_wiring_in_lifespan_scope() -> None:
    """Sanity check: every ``_shared.set_X`` invocation must live inside
    the lifespan function (not module-level imports, not test helpers).

    Module-level wiring would fire at import time before services are
    constructed — the setter would receive ``None`` and the singleton
    would stay broken.
    """
    src = _MAIN_PY.read_text(encoding="utf-8")

    # Find the lifespan function span. Anchor on the canonical async
    # context-manager decorator signature.
    lifespan_match = re.search(
        r"@asynccontextmanager\s*\n\s*async\s+def\s+lifespan\s*\(.*?\)\s*:",
        src,
        re.DOTALL,
    )
    assert lifespan_match, "main.py must declare an @asynccontextmanager lifespan function"

    lifespan_start = lifespan_match.start()

    # Find every _shared singleton setter invocation in the file
    setter_pattern = re.compile(
        r"\b(set_routing|set_taxonomy_engine|set_context_service|"
        r"set_domain_resolver|set_signal_loader|set_write_queue|"
        r"set_run_orchestrator|_set_shared_\w+)\s*\(",
        re.MULTILINE,
    )

    for match in setter_pattern.finditer(src):
        position = match.start()
        # The setter call MUST be after the lifespan function declaration.
        # Otherwise it fires at import time with a None reference.
        assert position > lifespan_start, (
            f"`{match.group(1)}` is invoked at position {position} but "
            f"the lifespan function starts at position {lifespan_start}. "
            f"Singleton setters MUST be called inside the lifespan body, "
            f"not at module import time (the underlying service does not "
            f"exist yet)."
        )
