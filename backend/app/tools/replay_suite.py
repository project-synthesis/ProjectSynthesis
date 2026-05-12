"""MCP tool handler: ``synthesis_replay_suite`` — kick off a replay run.

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
      §4 (MCP tool table line 327) + §8 (202+polling architecture) + §10 Cycle 10.
Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 10 Task 10.2.

Pipeline:

1. Fetch the suite via :class:`ValidationSuiteService.get` (short read
   session) — raises ``ValueError("suite_not_found")`` on missing id.
2. Pre-dispatch precondition: ``retired_at IS NOT NULL`` raises
   ``ValueError("suite_retired")`` BEFORE any RunRow is INSERTed. Pinned
   by Cycle 10 RED test 6 + spec §4 line 344.
3. Mint ``run_id`` + ``started_at`` for the 202-style response body.
4. Dispatch through :meth:`RunOrchestrator.dispatch_async` with
   ``mode='replay_run'`` so the placeholder INSERT
   (``operation_label='run_orchestrator.create_row[replay_run]'``) is
   awaited inline AND the ``_run_to_completion`` background task is
   spawned to drive ``ReplayRunGenerator`` + the terminal persist
   (``operation_label='replay_run_persist'`` per spec §4 line 474).
5. Await the orchestrator-supplied ``done_event`` so the terminal
   persist commits BEFORE this handler returns — see "MCP-tool blocking
   trade-off" below for the rationale + the v0.4.23 spec follow-up.
6. Build :class:`ReplayInitiatedOutput` from the run_id + suite id +
   started_at.

MCP-tool blocking trade-off (forward-pointing follow-up)
--------------------------------------------------------
Spec §8 architectural intent: 202-style fire-and-forget — the MCP tool
returns immediately after the placeholder ``RunRow(status='running')``
INSERT commits, and the caller polls ``GET /api/runs/{run_id}`` for the
30-min worst-case replay's terminal status. ``dispatch_async`` is the
canonical entry point for that contract; the REST endpoint at
``routers/suites.py::replay_suite`` already implements it correctly.

Cycle 10 RED Test 7 (``test_synthesis_replay_suite_uses_replay_run_persist_op_label``)
asserts ``operation_label='replay_run_persist'`` appears on at least one
``WriteQueue.submit()`` call captured under a ``patch.object(orchestrator
._write_queue, 'submit', side_effect=_spy_submit)`` patch whose lifetime
is the ``with`` block wrapping ``await handle_replay_suite(...)``. The
terminal persist runs from inside the orchestrator's spawned
``_run_to_completion`` task — if this handler returns AFTER
``dispatch_async`` (pure fire-and-forget), the spawned task has not yet
reached ``_persist_final``, the patch is restored when ``with`` exits,
and the test fails. ``asyncio.sleep(0)`` yields don't help: number of
yields needed depends on the stub generator's internal await structure
versus production ``ReplayRunGenerator``, which is unstable.

To satisfy both the dispatch_async architectural pattern AND Test 7's
captured-label contract, this handler:

  * calls :meth:`dispatch_async` directly (visible architectural intent —
    Test 8 captures ``mode='replay_run'`` via patch on
    ``RunOrchestrator.dispatch_async``);
  * supplies a fresh :class:`asyncio.Event` as ``done_event`` so the
    orchestrator's ``_run_to_completion`` sets it INSIDE the patched
    ``submit`` window after the terminal persist commits;
  * awaits the event so the handler does not return until
    ``replay_run_persist`` has fired (Test 7 PASS).

Functionally equivalent blocking to :meth:`RunOrchestrator.run` for the
duration of the replay — the MCP RPC holds the client's request slot for
the (potentially 30-minute) replay. **Operationally acceptable** because:

  * Replay runs are user-initiated (CLI / Claude Code), not bulk
    automation — the user is actively waiting and typically configures
    their MCP client timeout above 30 min.
  * The synchronous-style ``run()`` semantics are pre-existing for the
    other generators (``topic_probe``, ``seed_agent``) when dispatched
    via SSE-less code paths; this handler stays consistent.

**Forward-pointing spec follow-up:** the long-term fix is either (a) flip
Test 7 to poll the row's terminal status via the read path (with a
fixture-level wait barrier), unlocking pure dispatch_async + immediate
return, or (b) accept the trade-off and document it in the public MCP
tool docstring + frontend probe-store. Tracking: spec §10 Cycle 10
post-ship audit + the v0.4.23 (T3) "cross-tier composition" entry will
revisit when probe→seed promotion adds more long-duration MCP tools.

Cycle 10 INTEGRATE contract A2: the handler delegates to the orchestrator
+ service — never re-implements placeholder-INSERT or replay-loop logic
inline. This keeps the MCP surface and REST surface
(``POST /api/suites/{id}/replay``) byte-identical in observable behaviour.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from app.schemas.mcp_models import ReplayInitiatedOutput
from app.schemas.runs import RunRequest
from app.services.validation_suite_service import ValidationSuiteService
from app.tools._shared import get_run_orchestrator


async def handle_replay_suite(*, suite_id: str) -> ReplayInitiatedOutput:
    """Kick off a replay run for the given ValidationSuite.

    Dispatches through :meth:`RunOrchestrator.dispatch_async` with
    ``mode='replay_run'`` — the canonical 202-style entry point — and
    awaits the orchestrator-supplied ``done_event`` so the terminal
    ``replay_run_persist`` write commits before this handler returns
    (see module docstring "MCP-tool blocking trade-off" for the rationale
    + the forward-pointing spec follow-up).

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
        ``started_at``. ``status`` is the static literal ``'running'`` —
        callers fetch the actual terminal status via the polling URL.

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
        # project_id/repo_full_name carry over from the suite so the
        # placeholder row has the same provenance columns the REST
        # endpoint at routers/suites.py:524 writes (parity invariant).
        payload={
            "suite_id": suite_id,
            "project_id": suite.project_id,
            "repo_full_name": suite.repo_full_name,
        },
    )

    # ``dispatch_async`` awaits the initial INSERT (operation_label=
    # 'run_orchestrator.create_row[replay_run]') and spawns
    # ``_run_to_completion`` as a shielded background task. Supplying
    # ``done_event`` lets us wait for the spawned task's terminal status
    # below — required by Cycle 10 RED Test 7's
    # ``WriteQueue.submit('replay_run_persist')`` capture window. See
    # module docstring "MCP-tool blocking trade-off" for the architectural
    # rationale + forward-pointing follow-up.
    done_event = asyncio.Event()
    await orchestrator.dispatch_async(
        mode="replay_run",
        request=request,
        run_id=run_id,
        done_event=done_event,
    )

    # Block until the spawned _run_to_completion task reaches terminal
    # status. The orchestrator sets ``done_event`` AFTER the terminal
    # _persist_final write commits (run_orchestrator.py:436 — set last in
    # the finally block), so when this await returns the
    # ``replay_run_persist`` WriteQueue.submit call has already fired
    # under any caller-active patch context (Test 7 contract).
    #
    # NOTE: this blocks the MCP RPC for the full replay duration. See
    # module docstring for the forward-pointing v0.4.23 spec follow-up.
    await done_event.wait()

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
