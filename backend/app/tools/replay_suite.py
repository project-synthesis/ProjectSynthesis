"""MCP tool handler: ``synthesis_replay_suite`` — kick off a replay run.

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
      §4 (MCP tool table line 327) + §10 Cycle 10.
Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 10.

Pipeline:

1. Fetch the suite via :class:`ValidationSuiteService.get` (short read
   session) — raises ``ValueError("suite_not_found")`` on missing id.
2. Pre-dispatch precondition: ``retired_at IS NOT NULL`` raises
   ``ValueError("suite_retired")`` BEFORE any RunRow is INSERTed. Pinned
   by Cycle 10 RED test 6 + spec §4 line 344.
3. Mint ``run_id`` + ``started_at`` for the 202-style response body.
4. Dispatch through :meth:`RunOrchestrator.run` with ``mode='replay_run'``
   so the unified run substrate handles the placeholder INSERT
   (``operation_label='run_orchestrator.create_row[replay_run]'``), the
   ``ReplayRunGenerator`` execution, and the terminal persist
   (``operation_label='replay_run_persist'`` per spec §4 line 474).
5. Build :class:`ReplayInitiatedOutput` from the row id + suite id.

Cycle 10 INTEGRATE contract A2: the handler delegates to the orchestrator
+ service — never re-implements placeholder-INSERT or replay-loop logic
inline. This keeps the MCP surface and REST surface
(``POST /api/suites/{id}/replay``) byte-identical in observable behaviour.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.schemas.mcp_models import ReplayInitiatedOutput
from app.schemas.runs import RunRequest
from app.services.validation_suite_service import ValidationSuiteService
from app.tools._shared import get_run_orchestrator


async def handle_replay_suite(*, suite_id: str) -> ReplayInitiatedOutput:
    """Kick off a replay run for the given ValidationSuite.

    Surfaces structured error codes via bare ``ValueError`` raises (matches
    the canonical service-layer convention used by
    :class:`ValidationSuiteService`). The MCP runtime / FastMCP error
    plumbing renders ``str(exc)`` into the error envelope so callers
    pattern-matching on ``suite_retired`` / ``suite_not_found`` see the
    code without inspecting ``.reason``/``.code`` attributes.

    Parameters
    ----------
    suite_id : str
        Target :class:`ValidationSuite.id` to replay.

    Returns
    -------
    ReplayInitiatedOutput
        Body shape per spec §4 line 327: ``run_id``, ``suite_id``,
        ``mode='replay_run'``, ``status='running'``, ``poll_url``,
        ``started_at``.

    Raises
    ------
    ValueError
        * ``suite_not_found`` — suite_id does not resolve.
        * ``suite_retired`` — suite exists but ``retired_at IS NOT NULL``.
          Re-raised BEFORE the orchestrator dispatch so no placeholder
          RunRow is INSERTed for a retired suite (pinned by Test 6).
    """
    # ============ Step 1: fetch + retire precondition ============
    # ValidationSuiteService.get opens its own short read session and
    # raises ValueError("suite_not_found") on missing id; we re-raise
    # without wrapping so the canonical code surfaces in str(exc).
    service = ValidationSuiteService()
    suite = await service.get(suite_id)

    if suite.retired_at is not None:
        # Retire check fires BEFORE dispatch — a retired suite cannot
        # kick off a new replay run (spec §4 line 344 + Cycle 10 RED
        # test 6). No RunRow is INSERTed for retired suites.
        raise ValueError("suite_retired")

    # ============ Step 2: mint identifiers + dispatch ============
    run_id = uuid.uuid4().hex
    started_at = datetime.now(UTC)

    orchestrator = get_run_orchestrator()
    request = RunRequest(
        mode="replay_run",
        # Generator reads suite_id out of the payload; RunOrchestrator's
        # _create_row threads it onto the placeholder RunRow's suite_id
        # column so the regression-alarm JOIN can find this replay.
        payload={"suite_id": suite_id},
    )

    # RunOrchestrator.run() is the synchronous-style entry point: it
    # awaits dispatch_async + the spawned _run_to_completion task so the
    # terminal _persist_final write (operation_label='replay_run_persist'
    # per spec §4 line 474) commits BEFORE this method returns. The
    # 202-style response semantics are preserved by the orchestrator's
    # initial INSERT being awaited inside dispatch_async — the placeholder
    # row is queryable the moment dispatch_async returns. Cycle 10 Tests
    # 7 + 8 pin both behaviours.
    await orchestrator.run("replay_run", request, run_id=run_id)

    # ============ Step 3: build response ============
    poll_url = f"/api/runs/{run_id}"
    return ReplayInitiatedOutput(
        run_id=run_id,
        suite_id=suite_id,
        mode="replay_run",
        status="running",
        poll_url=poll_url,
        started_at=started_at,
    )
