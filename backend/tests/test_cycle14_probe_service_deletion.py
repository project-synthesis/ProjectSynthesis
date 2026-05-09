"""Cycle 14 — ProbeService class delete + ProbeRun alias removal (cat-12, 6 tests).

Foundation P3 (v0.4.18, PR2). Per spec § 8.3 + § 9 (cat 12, "ProbeRun ORM
removal — refactor coverage", 6 tests):

  1. ``class ProbeService`` deleted from ``backend/app/services/probe_service.py``
  2. ``class ProbeRun(RunRow)`` Python-alias deleted from ``backend/app/models.py``
  3. ``RunRow`` model still loadable + intact (no collateral damage)
  4. ``TopicProbeGenerator`` (the canonical replacement) still importable + dispatchable
  5. No ``from app.models import ProbeRun`` import remains anywhere in ``backend/app/``
  6. Module-level helpers from P2 Path A (probe_common, probe_phases, probe_phase_5)
     stay importable — they're consumed by ``TopicProbeGenerator``

Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 14
Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 8.3
"""
from __future__ import annotations

import importlib
import subprocess
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Test 1: ProbeService class deleted
# ---------------------------------------------------------------------------


def test_probe_service_class_deleted() -> None:
    """``from app.services.probe_service import ProbeService`` must raise
    ``ImportError`` — the class body is gone post-Cycle 14.

    The module itself remains (it re-exports ``current_probe_id`` for
    backward-compat per spec § 8.3 + the ``test_run_id_contextvar`` and
    ``test_probe_service_module_split_v0_4_17`` invariants), but the
    class identity is removed.
    """
    module = importlib.import_module("app.services.probe_service")
    assert not hasattr(module, "ProbeService"), (
        "ProbeService class still present in probe_service.py after Cycle 14 — "
        "expected deletion per spec § 8.3"
    )

    with pytest.raises(ImportError):
        from app.services.probe_service import ProbeService  # noqa: F401


# ---------------------------------------------------------------------------
# Test 2: ProbeRun alias deleted from models
# ---------------------------------------------------------------------------


def test_probe_run_alias_deleted_from_models() -> None:
    """``from app.models import ProbeRun`` must raise ``ImportError`` —
    the Python subclass is gone post-Cycle 14.

    Per spec § 8.3: "ProbeRun ORM class — Removed in PR2". PR1 retained the
    Python-alias as a thin forwarder; PR2 retires it entirely. All in-tree
    callers use ``RunRow`` directly with ``mode='topic_probe'`` filter.
    """
    module = importlib.import_module("app.models")
    assert not hasattr(module, "ProbeRun"), (
        "ProbeRun alias still present in models.py after Cycle 14 — "
        "expected deletion per spec § 8.3"
    )

    with pytest.raises(ImportError):
        from app.models import ProbeRun  # noqa: F401


# ---------------------------------------------------------------------------
# Test 3: RunRow remains intact
# ---------------------------------------------------------------------------


def test_run_row_remains_intact() -> None:
    """``RunRow`` must remain importable + carry the full P3 column set —
    deletion of ``ProbeRun`` alias must not collateral-damage the unified
    substrate.
    """
    from app.models import RunRow

    assert RunRow.__tablename__ == "run_row"

    cols = {c.name for c in RunRow.__table__.columns}
    expected_required = {
        "id", "mode", "status", "started_at", "completed_at", "error",
        "project_id", "repo_full_name", "topic", "intent_hint",
        "prompts_generated", "prompt_results", "aggregate", "taxonomy_delta",
        "final_report", "suite_id", "topic_probe_meta", "seed_agent_meta",
    }
    missing = expected_required - cols
    assert not missing, f"RunRow missing expected columns: {missing}"


# ---------------------------------------------------------------------------
# Test 4: TopicProbeGenerator still works
# ---------------------------------------------------------------------------


def test_topic_probe_generator_still_works() -> None:
    """``TopicProbeGenerator`` (the canonical replacement for ProbeService)
    must import + carry the ``async def run`` dispatch surface.

    Cycle 6 introduced ``TopicProbeGenerator`` as the post-P3 home for the
    5-phase orchestration logic. After Cycle 14 retires ``ProbeService``,
    ``TopicProbeGenerator`` is the sole implementation.
    """
    from app.services.generators.topic_probe_generator import TopicProbeGenerator

    assert isinstance(TopicProbeGenerator, type)

    run_method = getattr(TopicProbeGenerator, "run", None)
    assert run_method is not None, (
        "TopicProbeGenerator missing required `run` method"
    )

    # Quick parameter-shape check — the run signature must accept run_id kwarg.
    import inspect
    sig = inspect.signature(run_method)
    assert "run_id" in sig.parameters, (
        f"TopicProbeGenerator.run missing run_id parameter: {sig}"
    )


# ---------------------------------------------------------------------------
# Test 5: No remaining ProbeRun imports in app/
# ---------------------------------------------------------------------------


def test_no_remaining_probe_run_imports_in_app() -> None:
    """Grep guard — no live ``ProbeRun`` symbol references anywhere under
    ``backend/app/``.

    Strict on:
      - ``from app.models import ProbeRun`` (any comma-listed variant)
      - ``ProbeRun(`` instantiation calls
      - ``ProbeRun.`` attribute access (e.g., ``ProbeRun.scope``)
      - ``select(ProbeRun)`` / ``db.get(ProbeRun, ...)`` ORM lookups

    Permissive on:
      - Docstring / comment mentions of the historical name (these are
        prose context, not live code, and are addressed organically as
        files get edited)

    The strictness boundary is "could a reader plausibly call
    ``ProbeRun`` from this code path" — a docstring that explains the
    history of the symbol cannot. An ``import``, ``ProbeRun(...)`` call,
    or ``ProbeRun.attr`` lookup can.
    """
    import re

    app_dir = _BACKEND_DIR / "app"
    assert app_dir.is_dir(), f"app dir missing: {app_dir}"

    # Find every line containing ProbeRun anywhere under app/.
    result = subprocess.run(
        ["grep", "-rn", "ProbeRun", str(app_dir), "--include=*.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 1 and not result.stdout.strip():
        return  # success: nothing to inspect

    # Patterns that indicate a LIVE code reference (not prose context).
    live_patterns = [
        re.compile(r"\bimport\b.*\bProbeRun\b"),
        re.compile(r"\bProbeRun\("),
        re.compile(r"\bProbeRun\."),
        re.compile(r"\bselect\s*\(\s*ProbeRun\b"),
    ]

    offenders: list[str] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        # Format: "<path>:<lineno>:<content>"
        try:
            _path, _lineno, content = line.split(":", 2)
        except ValueError:
            continue
        # Strip trailing comment portion so a comment that mentions
        # ProbeRun doesn't taint a clean code line.
        content_no_inline_comment = content.split("#", 1)[0]

        # Erase docstring-style backtick-fenced inline code so a docstring
        # mention like ``ProbeRun(...)`` or ``select(ProbeRun)`` doesn't
        # taint a clean live-code line. Both single-backtick and
        # double-backtick fences (rST + markdown) are erased.
        no_fenced = re.sub(r"``[^`]*``", "", content_no_inline_comment)
        no_fenced = re.sub(r"`[^`]*`", "", no_fenced)

        for pat in live_patterns:
            if pat.search(no_fenced):
                offenders.append(line.strip())
                break

    assert not offenders, (
        "Live ProbeRun references found in backend/app/:\n  "
        + "\n  ".join(offenders)
        + "\n\nExpected zero live references after Cycle 14 deletion. "
        "Docstring/comment mentions are permitted; imports, "
        "instantiations, attribute access, and ORM lookups are not."
    )


# ---------------------------------------------------------------------------
# Test 6: Module-level helpers from P2 Path A still importable
# ---------------------------------------------------------------------------


def test_probe_service_module_helpers_still_accessible() -> None:
    """``current_probe_id`` and the 9 helper symbols from P2 Path A must
    remain importable from their canonical homes
    (``probe_common``/``probe_phases``/``probe_phase_5``).

    Per spec § 8.3 + plan Cycle 14 Constraints: deleting ``ProbeService``
    must not retire the leaf modules — they're consumed by
    ``TopicProbeGenerator``.
    """
    # probe_common — ContextVar + 4 helpers
    common = importlib.import_module("app.services.probe_common")
    for name in (
        "current_probe_id",
        "current_run_id",
        "_apply_scope_filter",
        "_truncate",
        "_commit_with_retry",
        "_stub_dimension_scores",
    ):
        assert hasattr(common, name), (
            f"probe_common missing post-Cycle 14: {name}"
        )

    # probe_phases — 3 grounding helpers
    phases = importlib.import_module("app.services.probe_phases")
    for name in (
        "_resolve_curated_files",
        "_resolve_curated_synthesis",
        "_resolve_dominant_stack",
    ):
        assert hasattr(phases, name), (
            f"probe_phases missing post-Cycle 14: {name}"
        )

    # probe_phase_5 — 2 reporting helpers
    phase_5 = importlib.import_module("app.services.probe_phase_5")
    for name in ("_resolve_followups", "_render_final_report"):
        assert hasattr(phase_5, name), (
            f"probe_phase_5 missing post-Cycle 14: {name}"
        )

    # Spec § 8.3 compatibility: current_probe_id must still be accessible
    # via probe_service.py (re-exported), even though ProbeService class
    # is gone — test_probe_service_module_split_v0_4_17 + test_run_id_contextvar
    # depend on this identity invariant.
    legacy = importlib.import_module("app.services.probe_service")
    assert hasattr(legacy, "current_probe_id"), (
        "probe_service.current_probe_id no longer importable — breaks "
        "test_run_id_contextvar identity invariant"
    )
    assert legacy.current_probe_id is common.current_probe_id, (
        "ContextVar identity broken — probe_service.current_probe_id "
        "is not the same object as probe_common.current_probe_id"
    )
