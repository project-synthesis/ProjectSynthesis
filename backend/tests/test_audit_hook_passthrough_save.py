"""Regression tests for v0.4.22 audit-hook flip — REST passthrough save path.

Surfaces a v0.4.14 oversight uncovered by the v0.4.22
``WRITE_QUEUE_AUDIT_HOOK_RAISE=True`` default flip during pre-merge soak-gate
validation (2026-05-12 Day 1).

Background
----------
v0.4.14 declared that REST routers + MCP optimize-passthrough + sampling-
pipeline persist + audit logs all route their short-lived writes through
``WriteQueue.submit()`` (see :file:`backend/CLAUDE.md` "Write queue contract").

The REST endpoint ``POST /api/optimize/passthrough/save`` at
:func:`app.routers.optimize.passthrough_save` declared queue-routing only at
the COMMIT layer:

.. code-block:: python

    async def _do_pt_save_commit(write_db):
        await write_db.commit()
    await write_queue.submit(_do_pt_save_commit, ...)

— but applied ALL ORM mutations on the request-bound read-engine session
PRIOR to the queue submit. Under v0.4.21 WARN-mode the read-engine
autoflush triggered by ``await db.refresh(opt)`` after submit emitted a
``read-engine audit:`` WARN line but the response still returned 200.

Under v0.4.22 RAISE-mode, the same autoflush trips
``WriteOnReadEngineError`` → ``PendingRollbackError`` → HTTP 500
``error_type=PendingRollbackError`` propagated to the caller. The endpoint
becomes user-visible broken the instant the flip ships.

This is precisely the long-tail bug class the SG-2026-05-11 soak gate was
designed to surface (per :file:`docs/SOAK_GATES.md` "Why this gate exists"):
*"A forgotten direct db.execute() in a less-traveled branch."*

Fix
---
:func:`app.routers.optimize.passthrough_save` was restructured to apply
ALL ORM mutations inside the ``write_queue.submit()`` closure on the
write-engine session — matches the Foundation P4 canonical pattern at
:func:`app.tools.save_result._persist_save_result`. Read session is now
used ONLY for read-only existence + value snapshotting + analyzer
historical-stats fetching.

Test surface
-----------
1. ``test_passthrough_save_emits_zero_warn_under_raise_mode`` — happy path.
   Prepares a passthrough optimization, saves it, asserts the response is
   200 + valid OptimizationDetail + zero ``read-engine audit:`` WARN lines
   in caplog over the full prepare → save round-trip.

2. ``test_passthrough_save_returns_completed_status_under_raise_mode`` —
   confirms the row transitions ``pending → completed`` correctly (the
   v0.4.22 flip was previously causing the row to stay pending because
   the autoflush rolled back).
"""
from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Optimization

pytestmark = pytest.mark.asyncio


@pytest.mark.integration
async def test_passthrough_save_emits_zero_warn_under_raise_mode(
    app_client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REST passthrough save must not trip the audit hook under RAISE mode.

    Spec §7 + soak-gate finding 2026-05-12 D1: the prepare → save round-trip
    is one of the canonical user-visible paths for externally-optimized
    prompts (web UI manual passthrough + MCP-less workflow). It MUST route
    every write through the WriteQueue so the v0.4.22 audit-hook flip does
    not surface as HTTP 500.
    """
    caplog.set_level(logging.WARNING, logger="app.database")
    caplog.clear()

    from app.config import settings as _settings
    _prior_raise = _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
    _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True

    try:
        # Stage 1 — passthrough prepare creates the pending Optimization row.
        prepare_resp = await app_client.post(
            "/api/optimize/passthrough",
            json={
                "prompt": (
                    "audit-hook regression — describe the SQLite write-queue "
                    "audit hook semantics under the v0.4.22 RAISE flip"
                ),
                "strategy": "clarity",
            },
        )
        assert prepare_resp.status_code == 200, (
            f"prepare must succeed for audit-hook coverage; got "
            f"{prepare_resp.status_code}: {prepare_resp.text!r}"
        )
        prepare_body = prepare_resp.json()
        trace_id = prepare_body["trace_id"]
        assert trace_id, "prepare response must include trace_id"

        # Stage 2 — passthrough save commits the row via WriteQueue closure.
        # This is the v0.4.22 audit-hook flip surface — prior to the fix
        # this raised PendingRollbackError → HTTP 500.
        save_resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": trace_id,
                "optimized_prompt": (
                    "# Audit-hook regression\n\n"
                    "Describe the SQLite write-queue audit hook semantics "
                    "under the v0.4.22 RAISE flip in detail."
                ),
                "changes_summary": "Added structure + section header",
                "task_type": "writing",
                "strategy_used": "clarity",
            },
        )
        assert save_resp.status_code == 200, (
            f"save must NOT trip the audit hook under RAISE mode; got "
            f"{save_resp.status_code}: {save_resp.text!r}"
        )

        body = save_resp.json()
        assert body.get("id"), (
            f"response must include the optimization id; got: {body!r}"
        )
        assert body.get("trace_id") == trace_id

        # Stage 3 — verify zero audit-hook WARN lines emitted.
        # Match pattern at test_tools_optimize.py:907-908.
        audit_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.getMessage().startswith("read-engine audit:")
        ]
        assert len(audit_warnings) == 0, (
            "REST /api/optimize/passthrough/save must route ALL writes "
            "through WriteQueue.submit() — found "
            f"{len(audit_warnings)} audit-hook WARN line(s):\n"
            + "\n".join(f"  - {r.getMessage()[:200]}" for r in audit_warnings)
        )
    finally:
        _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = _prior_raise


@pytest.mark.integration
async def test_update_optimization_intent_rename_emits_zero_warn_under_raise_mode(
    app_client: AsyncClient,
    db_session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PATCH /api/optimize/{id} (intent rename) must not trip the audit hook.

    Same v0.4.14 oversight class as :func:`passthrough_save` — the rename
    mutation landed on the read-engine session before the empty-commit
    closure submitted, autoflush → ``WriteOnReadEngineError`` → HTTP 500.

    The fix restructures the rename to apply the mutation inside the
    WriteQueue closure on the write-engine session.
    """
    caplog.set_level(logging.WARNING, logger="app.database")
    caplog.clear()

    from app.config import settings as _settings
    _prior_raise = _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
    _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True

    try:
        # Stage 1 — create a passthrough optimization to rename.
        prepare_resp = await app_client.post(
            "/api/optimize/passthrough",
            json={
                "prompt": (
                    "audit-hook regression — rename target prompt for the "
                    "PATCH endpoint v0.4.22 flip coverage"
                ),
                "strategy": "clarity",
            },
        )
        assert prepare_resp.status_code == 200
        trace_id = prepare_resp.json()["trace_id"]

        save_resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": trace_id,
                "optimized_prompt": (
                    "# Rename target\nA seed prompt for PATCH "
                    "intent_label coverage."
                ),
                "task_type": "writing",
                "strategy_used": "clarity",
            },
        )
        assert save_resp.status_code == 200
        opt_id = save_resp.json()["id"]

        # Stage 2 — rename via PATCH (the audit-hook flip surface).
        patch_resp = await app_client.patch(
            f"/api/optimize/{opt_id}",
            json={"intent_label": "renamed under RAISE flip"},
        )
        assert patch_resp.status_code == 200, (
            f"PATCH must NOT trip the audit hook under RAISE mode; got "
            f"{patch_resp.status_code}: {patch_resp.text!r}"
        )
        body = patch_resp.json()
        assert body["intent_label"] == "Renamed Under Raise Flip", (
            f"intent_label must reflect title-cased rename; got "
            f"{body.get('intent_label')!r}"
        )

        # Stage 3 — verify zero audit-hook WARN lines.
        audit_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and r.getMessage().startswith("read-engine audit:")
        ]
        assert len(audit_warnings) == 0, (
            "PATCH /api/optimize/{id} must route all writes through "
            "WriteQueue.submit() — found "
            f"{len(audit_warnings)} audit-hook WARN line(s):\n"
            + "\n".join(f"  - {r.getMessage()[:200]}" for r in audit_warnings)
        )
    finally:
        _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = _prior_raise


@pytest.mark.integration
async def test_passthrough_save_returns_completed_status_under_raise_mode(
    app_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """The persisted row must transition pending → completed under RAISE.

    Prior to the fix, ``PendingRollbackError`` left the row in ``pending``
    state because the write-engine commit ran on an empty transaction.
    Verify the post-fix behavior: the row is observable as ``completed``
    via the read engine after the WriteQueue closure commits.
    """
    from app.config import settings as _settings
    _prior_raise = _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
    _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True

    try:
        prepare_resp = await app_client.post(
            "/api/optimize/passthrough",
            json={
                "prompt": (
                    "audit-hook regression status check — verify pending → "
                    "completed under v0.4.22 flip"
                ),
                "strategy": "clarity",
            },
        )
        assert prepare_resp.status_code == 200
        trace_id = prepare_resp.json()["trace_id"]

        save_resp = await app_client.post(
            "/api/optimize/passthrough/save",
            json={
                "trace_id": trace_id,
                "optimized_prompt": (
                    "# Status check\n\nVerify pending → completed under "
                    "v0.4.22 audit-hook flip."
                ),
                "task_type": "writing",
                "strategy_used": "clarity",
            },
        )
        assert save_resp.status_code == 200, save_resp.text

        # Cross-check via direct DB read on the read session.
        result = await db_session.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        opt = result.scalar_one_or_none()
        assert opt is not None, "saved row must be observable via read session"
        assert opt.status == "completed", (
            f"row must transition to completed under RAISE mode; "
            f"got status={opt.status!r}"
        )
        assert opt.optimized_prompt and opt.optimized_prompt.startswith(
            "# Status check"
        )
    finally:
        _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = _prior_raise
