"""Backward-compat shim — Foundation P3 Cycle 14 (v0.4.18).

The ``ProbeService`` class is GONE. Its 5-phase orchestration logic now
lives in ``app/services/generators/topic_probe_generator.py`` (the
``TopicProbeGenerator`` introduced in Cycle 6); the row-state lifecycle
moved to ``app/services/run_orchestrator.py:RunOrchestrator`` (Cycle 5).
Module-level helpers (`_apply_scope_filter`, `_truncate`,
`_commit_with_retry`, `_stub_dimension_scores`, `_render_final_report`,
`_resolve_followups`, `_resolve_curated_files`, `_resolve_curated_synthesis`,
`_resolve_dominant_stack`) live in ``probe_common.py``, ``probe_phases.py``,
and ``probe_phase_5.py``.

Why this module still exists:

The ``current_probe_id`` ContextVar is canonically declared in
``probe_common.py``; this module re-imports it so the legacy
``from app.services.probe_service import current_probe_id`` import path
keeps working. Two test invariants depend on this:

  - ``tests/test_probe_service_module_split_v0_4_17.py`` asserts
    ``probe_service.current_probe_id is probe_common.current_probe_id``.
  - ``tests/test_run_id_contextvar.py`` exercises the full identity
    chain across all three call sites (``probe_common`` /
    ``probe_event_correlation`` / ``probe_service``).

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 8.3
Plan: docs/superpowers/plans/2026-05-06-foundation-p3-substrate-unification.md Cycle 14
"""
from __future__ import annotations

# Re-export the ContextVar (canonical home: probe_common) so legacy imports
# of the form ``from app.services.probe_service import current_probe_id``
# continue to resolve. The ``noqa`` keeps the linter from flagging the
# bare F401 — the re-export IS the public API.
from app.services.probe_common import current_probe_id  # noqa: F401

__all__ = ["current_probe_id"]
