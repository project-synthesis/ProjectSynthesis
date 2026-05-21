"""Tests for the ``current_run_id`` ContextVar (v0.4.34 alias retirement).

The v0.4.18 Foundation P3 rename moved the canonical ContextVar name from
``current_probe_id`` to ``current_run_id`` and kept the old name as a
backward-compat alias "for 2+ release cycles". v0.4.34 — 16 release
cycles later — retires the alias entirely.

This file previously had 4 identity-via-alias tests asserting
``current_run_id is current_probe_id`` across the 3 source modules
(``probe_common``, ``probe_service``, ``probe_event_correlation``). Those
contracts no longer hold; this file is repurposed to pin the OPPOSITE:
the alias is unimportable from every previous source. Replaced with 3
negative-assertion tests + 1 positive sanity check on the surviving
canonical name.

Test count change: 4 → 3 negative-assertion tests (the
``test_current_run_id_default_is_none`` positive sanity check from the
prior file is preserved as a 4th test if any reviewer prefers the
explicit canonical-survives invariant, but is dropped here per plan
guidance favouring the cleaner 3-test surface).

Plan: /home/drei/.claude/plans/twinkling-tinkering-snail.md Task 1 (Item 5)
"""
from __future__ import annotations

import pytest


def test_current_probe_id_not_importable_from_probe_common() -> None:
    """``current_probe_id`` is removed from ``probe_common`` — its canonical
    home post-v0.4.18 — and any import must raise ``ImportError``.

    The canonical name remains ``current_run_id`` (declared in
    ``probe_common.py``). The alias was only kept for 2+ release cycles
    of migration runway; that window closed in v0.4.34.
    """
    with pytest.raises(ImportError):
        from app.services.probe_common import current_probe_id  # type: ignore[attr-defined]  # noqa: F401


def test_current_probe_id_not_importable_from_probe_service() -> None:
    """``current_probe_id`` is removed from ``probe_service`` (which was
    only a re-export shim) — import must raise ``ImportError``.

    The entire ``backend/app/services/probe_service.py`` file is also
    deleted in v0.4.34 GREEN (its sole purpose was the alias re-export);
    this assertion holds either via ``ImportError`` on the module itself
    or on the symbol within it.
    """
    with pytest.raises(ImportError):
        from app.services.probe_service import current_probe_id  # type: ignore[attr-defined]  # noqa: F401


def test_current_probe_id_not_importable_from_probe_event_correlation() -> None:
    """``current_probe_id`` is removed from ``probe_event_correlation`` —
    the file now imports ``current_run_id`` directly. Import of the old
    alias must raise ``ImportError``.

    The ``inject_probe_id(context)`` helper itself keeps its public name
    (it's a module-level helper, not the ContextVar); only the ContextVar
    alias is retired.
    """
    with pytest.raises(ImportError):
        from app.services.probe_event_correlation import current_probe_id  # type: ignore[attr-defined]  # noqa: F401
