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
    app_client_with_audit_hook: AsyncClient,
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
        prepare_resp = await app_client_with_audit_hook.post(
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
        save_resp = await app_client_with_audit_hook.post(
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
    app_client_with_audit_hook: AsyncClient,
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
        prepare_resp = await app_client_with_audit_hook.post(
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

        save_resp = await app_client_with_audit_hook.post(
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
        patch_resp = await app_client_with_audit_hook.patch(
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
async def test_rollback_seeds_initial_turn_so_subsequent_refine_works(
    app_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """v0.4.22 soak-gate Day 1 finding: rollback used to create a new
    branch with zero turns. The next ``POST /api/refine`` without an
    explicit ``branch_id`` defaulted to the empty rollback branch and
    raised ``ValueError("invoke_refinement_pipeline requires
    latest_turn_snapshot; caller must seed via build_initial_turn_payload
    + queue submit")``.

    Post-fix: rollback also seeds an initial ``RefinementTurn`` (version 1)
    on the new branch carrying the content of the version being rolled
    back to. Subsequent refines find that seed turn and proceed normally.

    This test asserts the structural fix at the DB layer (no LLM call).
    The end-to-end live verification happens via ``./init.sh restart`` +
    operator probe (see ``docs/SOAK_GATES.md`` Day 1 finding).
    """
    import uuid as _uuid

    from app.models import (
        Optimization,
        RefinementBranch,
        RefinementTurn,
    )
    from app.services.refinement_service import RefinementService

    # Seed: optimization + branch with 2 turns
    opt_id = str(_uuid.uuid4())
    branch_id = str(_uuid.uuid4())
    db_session.add(Optimization(
        id=opt_id,
        trace_id=str(_uuid.uuid4()),
        raw_prompt="raw",
        optimized_prompt="opt-v2-content",
        task_type="general",
        domain="general",
        strategy_used="auto",
        status="completed",
    ))
    db_session.add(RefinementBranch(
        id=branch_id,
        optimization_id=opt_id,
        parent_branch_id=None,
        forked_at_version=0,
    ))
    db_session.add(RefinementTurn(
        id=str(_uuid.uuid4()),
        optimization_id=opt_id,
        branch_id=branch_id,
        version=1,
        parent_version=0,
        prompt="v1-prompt-content",
        refinement_request="seed",
        scores={"clarity": 7.0, "specificity": 7.0},
        strategy_used="chain-of-thought",
        trace_id=str(_uuid.uuid4()),
    ))
    db_session.add(RefinementTurn(
        id=str(_uuid.uuid4()),
        optimization_id=opt_id,
        branch_id=branch_id,
        version=2,
        parent_version=1,
        prompt="v2-prompt-content",
        refinement_request="r2",
        scores={"clarity": 8.0, "specificity": 8.0},
        strategy_used="auto",
        trace_id=str(_uuid.uuid4()),
    ))
    await db_session.commit()

    # Invoke rollback service method directly (router-equivalent path);
    # asserts payload carries both branch_kwargs AND seed_turn_kwargs.
    from app.config import PROMPTS_DIR
    svc = RefinementService(prompts_dir=PROMPTS_DIR)  # No LLM exercised here

    payload = await svc.rollback(db_session, opt_id, to_version=1)

    # Branch kwargs intact
    assert payload.branch_kwargs["optimization_id"] == opt_id
    assert payload.branch_kwargs["parent_branch_id"] == branch_id
    assert payload.branch_kwargs["forked_at_version"] == 1

    # Seed turn kwargs carry v1's content
    assert payload.seed_turn_kwargs["optimization_id"] == opt_id
    assert payload.seed_turn_kwargs["branch_id"] == (
        payload.branch_kwargs["id"]
    )
    assert payload.seed_turn_kwargs["version"] == 1
    assert payload.seed_turn_kwargs["prompt"] == "v1-prompt-content"
    assert payload.seed_turn_kwargs["strategy_used"] == "chain-of-thought"
    assert payload.seed_turn_kwargs["scores"] == {
        "clarity": 7.0, "specificity": 7.0,
    }
    assert payload.seed_turn_kwargs["refinement_request"] == "Rollback to v1"


@pytest.mark.integration
async def test_audit_hook_fixture_catches_synthetic_regression(
    app_client_with_audit_hook,  # noqa: ARG001 — fixture installs hook
    db_session: AsyncSession,
) -> None:
    """Self-test: prove the ``app_client_with_audit_hook`` fixture actually
    catches a regression that would have slipped past the plain ``app_client``.

    Background — code-review HIGH finding (2026-05-12 SG-2026-05-11 Day 1):
    the audit-hook regression tests were running against the
    ``app_client`` fixture's separate in-memory engine that has NO audit
    hook installed (lifespan never runs in tests), so ``caplog`` assertions
    for ``"read-engine audit:"`` were vacuously empty regardless of whether
    the code had regressed. This test pins the FIXTURE's contract: under
    the new ``app_client_with_audit_hook`` fixture, a deliberate write on
    the read-session OUTSIDE the writer-queue closure MUST trip the audit
    hook RAISE.

    If this test ever fails, it means the fixture has regressed back to
    the dead-coverage state and EVERY other ``app_client_with_audit_hook``
    test in this file (and ``test_audit_hook_full_t2_pipeline.py``) becomes
    vacuous. Investigate ``conftest.py::app_client_with_audit_hook``.
    """
    from sqlalchemy import text

    from app.config import settings as _settings
    from app.database import WriteOnReadEngineError

    _prior_raise = _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE
    _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = True

    try:
        # Deliberately issue an UPDATE on the read session WITHOUT going
        # through the writer-queue closure. This mimics a code regression
        # (e.g. an empty-commit-closure router that mutates ``opt.field``
        # on the read session before submit).
        with pytest.raises(WriteOnReadEngineError, match=r"write statement on read engine"):
            await db_session.execute(
                text(
                    "UPDATE optimizations SET task_type='synthetic_regression' "
                    "WHERE 1=0"
                )
            )
    finally:
        _settings.WRITE_QUEUE_AUDIT_HOOK_RAISE = _prior_raise


@pytest.mark.integration
async def test_passthrough_save_returns_completed_status_under_raise_mode(
    app_client_with_audit_hook: AsyncClient,
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
        prepare_resp = await app_client_with_audit_hook.post(
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

        save_resp = await app_client_with_audit_hook.post(
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
