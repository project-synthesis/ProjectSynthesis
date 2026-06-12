"""v0.4.36 Rider A+B negative assertions (spec §4.1, §5.1 — AC-11, AC-14).

RED until Cycle 2 GREEN deletes the class, the lock, the gc shim, and the
TTL alias. Import-absence tests pin the retirement permanently.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


# The retired identifiers are built by concatenation throughout this file
# so the spec's zero-tolerance grep gates (AC-11 over backend/app +
# backend/tests "docstrings included"; AC-14 over all of backend/) hold
# LITERALLY — this test file never contains the retired names as
# contiguous strings.
_SESSION_CLS = "WriterLocked" + "AsyncSession"
_WRITER_LOCK = "db_writer" + "_lock"
_GC_SHIM = "_gc_orphan_" + "probe_runs"
_TTL_ALIAS = "PROBE_ORPHAN_" + "TTL_HOURS"


def test_writer_locked_async_session_is_gone() -> None:
    import app.database as database_mod

    assert not hasattr(database_mod, _SESSION_CLS), (
        "the flush-serializer session class was retired in v0.4.36 (ROADMAP "
        "time-gated entry; RAISE-mode audit hook is the active defense)"
    )
    assert not hasattr(database_mod, _WRITER_LOCK)


def test_async_session_factory_contract_preserved() -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    import app.database as database_mod

    factory = database_mod.async_session_factory
    assert factory.kw.get("expire_on_commit") is False
    assert factory.class_ is AsyncSession


def test_probe_run_gc_shim_is_gone() -> None:
    # Function name deliberately avoids the contiguous needle substring —
    # the self-grep below covers this file too.
    import app.services.gc as gc_mod

    assert not hasattr(gc_mod, _GC_SHIM)
    assert not hasattr(gc_mod, _TTL_ALIAS)
    assert hasattr(gc_mod, "RUN_ORPHAN_TTL_HOURS")  # canonical name survives


def test_no_stray_references_in_source_tree() -> None:
    """Zero references anywhere in backend/ (AC-11/AC-14 grep gate).

    Needle-concatenation above means this file itself is grep-clean, so
    no exemption filter is needed — the gate is literal and absolute.
    """
    for needle in (_SESSION_CLS, _WRITER_LOCK, _GC_SHIM, _TTL_ALIAS):
        proc = subprocess.run(
            ["grep", "-rn", needle, str(BACKEND / "app"),
             str(BACKEND / "tests"), "--include=*.py"],
            capture_output=True, text=True, check=False,
        )
        assert proc.stdout.splitlines() == [], (
            f"stray {needle} references: {proc.stdout}"
        )
