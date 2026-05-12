"""RunOrchestrator — central dispatch for unified run substrate.

Foundation P3 (v0.4.18) introduced the unified run substrate.
T2 Cycle 8 (v0.4.22) added the 202+polling dispatch architecture: an
async :meth:`RunOrchestrator.dispatch_async` entry point that returns
immediately after the initial INSERT commits, with a thin
:meth:`RunOrchestrator.run` synchronous-style wrapper for SSE callers +
sync tests.

Responsibilities:
    - Allocate run_id (or accept caller-supplied)
    - Create RunRow row via WriteQueue at start (status='running'),
      AWAIT the INSERT before returning from ``dispatch_async`` so
      caller's first poll always finds the row
    - Spawn a background ``_run_to_completion`` task to drive the
      generator + persist terminal state
    - Catch exceptions + cancellation in the spawned task; mark row
      failed under :func:`asyncio.shield` (terminal write survives
      caller HTTP disconnect)
    - Set/reset ``current_run_id`` ContextVar INSIDE the spawned task
      body (parent task's context is NOT inherited correctly for
      taxonomy event correlation)
    - Optional ``done_event`` parameter so :meth:`run` can block until
      terminal state — single dispatch_async codepath, no
      two-implementation drift

Generators NEVER touch RunRow — RunOrchestrator is the only legitimate writer.

Specs:
    - Foundation P3 § 5.2 — initial substrate design
    - T2 Cycle 8 § 5 — ``dispatch_async`` + ``_run_to_completion``
      + ``run()`` refactor
    - T2 Cycle 8 § 8 — 202 Accepted + polling response contract
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app import database as _database
from app.models import RunRow
from app.schemas.runs import RunRequest
from app.services.generators.base import GeneratorResult, RunGenerator
from app.services.probe_common import current_run_id
from app.services.write_queue import WriteQueue

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.utcnow()


class RunOrchestrator:
    """Top-level dispatcher for the unified run substrate.

    Owns RunRow lifecycle (create at start → persist final → mark failed on
    error) so generators stay focused on mode-specific work. All RunRow
    writes route through ``WriteQueue.submit()``.
    """

    def __init__(
        self,
        write_queue: WriteQueue,
        generators: dict[str, RunGenerator],
    ) -> None:
        self._write_queue = write_queue
        self._generators = generators

    async def run(
        self,
        mode: str,
        request: RunRequest,
        *,
        run_id: str | None = None,
    ) -> RunRow:
        """Top-level dispatch — thin wrapper over :meth:`dispatch_async`.

        Spec § 5 (Cycle 8): ``run()`` becomes a thin wrapper that calls
        :meth:`dispatch_async` then ``await``s an ``asyncio.Event`` set by
        the spawned task at terminal status. This preserves the legacy
        "blocks until terminal" contract for SSE callers + sync tests
        while sharing the dispatch_async body — single codepath, no
        two-implementation divergence.

        Exception propagation: in addition to ``done_event``, the wrapper
        supplies a private ``_done_future`` to :meth:`dispatch_async` so
        that any exception raised inside the spawned task
        (generator-raised ``Exception``, ``asyncio.CancelledError`` from
        the caller's task being cancelled) is captured onto the Future
        and re-raised here. Preserves the legacy synchronous-``run()``
        contract that the pre-Cycle-8 implementation provided.

        ``run_id`` is optional caller-supplied id. Race-sensitive callers
        (e.g., the probes router constructing an SSE response) pre-mint
        the id and supply it so they can register event subscriptions
        BEFORE the orchestrator starts. When ``None``, an id is minted
        internally.
        """
        if mode not in self._generators:
            # Cannot mark failed — row not yet created.
            raise ValueError(f"unknown mode: {mode}")

        if run_id is None:
            run_id = str(uuid.uuid4())

        # ``done_event`` keeps the public ``dispatch_async`` signature
        # consistent for 202+polling callers (they want a "spawn and
        # forget" no-return entry point). ``_done_future`` is the
        # private exception-propagation channel used exclusively by this
        # synchronous wrapper. The spawned task resolves it with ``None``
        # on success and with ``set_exception(exc)`` on failure or
        # cancellation; ``await done_future`` blocks the wrapper AND
        # re-raises any captured exception.
        loop = asyncio.get_event_loop()
        done_event = asyncio.Event()
        done_future: asyncio.Future[None] = loop.create_future()
        # ``_spawned_task_holder`` is a single-element list (a Python
        # closure of a mutable cell) populated by ``dispatch_async``
        # immediately after the spawn. We use a list so the spawn-side
        # write is observable from this method's scope without the
        # ceremony of an asyncio.Future for the task reference itself.
        spawned_task_holder: list[asyncio.Task[None]] = []
        await self.dispatch_async(
            mode=mode,
            request=request,
            run_id=run_id,
            done_event=done_event,
            _done_future=done_future,
            _spawned_task_holder=spawned_task_holder,
        )
        try:
            # Block until the spawned task signals terminal state. If
            # the generator raised, ``done_future.set_exception(exc)``
            # has been called and ``await`` re-raises it; otherwise the
            # future resolves with ``None`` and we proceed to ``_reload``.
            await done_future
        except asyncio.CancelledError:
            # Legacy ``run()`` contract: when the caller cancels the
            # task that is awaiting ``run()``, the underlying generator
            # MUST be cancelled too. The 202 dispatch path explicitly
            # shields the spawned task from caller cancellation by
            # constructing the spawn outside any task's structured
            # ownership; but here, the spawned task IS the caller's
            # logical work, so we forward the cancellation.
            #
            # ``_run_to_completion``'s CancelledError handler then runs
            # the shielded ``_mark_failed`` write, sets the future, and
            # the original ``CancelledError`` re-raises here for the
            # caller to observe. The await below also propagates any
            # exception captured during the cleanup write.
            if spawned_task_holder and not spawned_task_holder[0].done():
                spawned_task_holder[0].cancel()
                with contextlib.suppress(BaseException):
                    await spawned_task_holder[0]
            raise
        finally:
            # Defense-in-depth: ensure the event is set even if the
            # spawned task failed before reaching the ``finally`` block
            # that normally sets it. ``done_event.is_set()`` is the
            # signal external observers (e.g., a future SSE-cleanup
            # variant) might wait on.
            if not done_event.is_set():
                done_event.set()
            # Drain the spawned task so asyncio doesn't log a noisy
            # "Task exception was never retrieved" warning. The exception
            # was already re-raised through ``done_future`` so the caller
            # observed it; this awaits the task to its terminal state
            # and accesses ``.exception()`` to mark the exception
            # retrieved. Suppress everything from the await — the
            # original exception already surfaced to the caller above.
            if spawned_task_holder:
                with contextlib.suppress(BaseException):
                    await spawned_task_holder[0]
        return await self._reload(run_id)

    async def dispatch_async(
        self,
        *,
        mode: str,
        request: RunRequest,
        run_id: str,
        done_event: asyncio.Event | None = None,
        _done_future: asyncio.Future[None] | None = None,
        _spawned_task_holder: list[asyncio.Task[None]] | None = None,
    ) -> None:
        """Spawn a run-to-completion background task after the initial INSERT.

        Race-free: the initial ``RunRow`` ``INSERT`` is awaited BEFORE this
        method returns. A caller that immediately
        ``GET /api/probes/{run_id}`` after dispatch is guaranteed to find
        the row (spec § 8 "Race-safety"; spec § 10 Cycle 8 OPERATE O1).

        Cancellation: the spawned ``_run_to_completion`` task wraps its
        terminal-status writes under :func:`asyncio.shield` so the
        ``RunRow`` reaches a terminal state (``failed`` or ``partial``)
        even if the caller's HTTP connection closes mid-iteration.

        Parameters
        ----------
        mode:
            Generator key (``topic_probe`` / ``seed_agent`` / ``replay_run``).
        request:
            ``RunRequest`` for the generator.
        run_id:
            Caller-supplied run id. Spec § 5 makes this required on the
            async entry point — the caller mints the id BEFORE dispatch
            so it can wire up event subscriptions race-free.
        done_event:
            Optional ``asyncio.Event`` set by the spawned task at
            terminal status. Used by the thin :meth:`run` wrapper to
            block until the run finishes.
        _done_future:
            **Private** ``asyncio.Future`` used by :meth:`run` exclusively
            to propagate generator-raised exceptions back to the
            synchronous caller. 202+polling callers MUST NOT supply this
            argument — they observe the run's terminal state by polling
            ``GET /api/probes/{run_id}`` instead. The leading underscore
            documents the private contract; the signature stays keyword-
            only so accidental positional use is impossible.
        _spawned_task_holder:
            **Private** single-element list populated with the spawned
            ``asyncio.Task`` so :meth:`run` can forward caller-task
            cancellation. 202+polling callers leave this ``None`` —
            their spawned task is intentionally un-referenced so a
            closed HTTP connection cannot cancel it. This is what
            satisfies the dual contract: ``dispatch_async()`` callers
            get shielded spawning while ``run()`` callers get
            cancellation propagation.
        """
        if mode not in self._generators:
            # Cannot mark failed — row not yet created. Surfaces to the
            # caller immediately (router translates to 4xx).
            raise ValueError(f"unknown mode: {mode}")

        # Await the initial INSERT BEFORE spawning the run-to-completion
        # task. This is the race-safety guarantee — the caller's first
        # GET on /api/probes/{run_id} always finds the row.
        await self._create_row(mode, request, run_id=run_id)

        # Spawn the rest as a background task. The task name is
        # observability-friendly under ``asyncio.all_tasks()`` so failed
        # runs are easy to spot in a debugger or in the GC sweep.
        spawned = asyncio.create_task(
            self._run_to_completion(
                mode=mode,
                request=request,
                run_id=run_id,
                done_event=done_event,
                done_future=_done_future,
            ),
            name=f"run-to-completion-{run_id}",
        )
        # Expose the spawned task to the private ``run()`` wrapper so it
        # can forward caller cancellation. 202+polling callers don't
        # pass a holder — they intentionally leave the spawned task
        # un-referenced so a closed HTTP connection cannot cancel it.
        if _spawned_task_holder is not None:
            _spawned_task_holder.append(spawned)

    async def _run_to_completion(
        self,
        *,
        mode: str,
        request: RunRequest,
        run_id: str,
        done_event: asyncio.Event | None = None,
        done_future: asyncio.Future[None] | None = None,
    ) -> None:
        """Drive generator + persist final, with cancellation cleanup.

        Sets ``current_run_id`` ContextVar INSIDE the spawned task body
        — Python's ``contextvars.copy_context()`` inheritance copies the
        parent's context at task-spawn time, but the parent then
        resets/changes its own context before the child reads. Explicit
        ``set/reset`` keeps replay/topic-probe taxonomy events correctly
        stamped with ``run_id`` (spec § 5 ContextVar lifecycle).

        Cancellation handler: under ``asyncio.shield`` so the terminal
        write reaches the row even when the caller's task is cancelled
        mid-iteration (spec § 5 cancellation contract diff; § 10 Cycle 8
        OPERATE O8). Exceptions from the cleanup path are suppressed so
        the original ``CancelledError`` surfaces faithfully through both
        the spawned task itself AND the optional ``done_future``.

        Parameters
        ----------
        done_event:
            Optional event; if provided, set at the end of the ``finally``
            so the synchronous :meth:`run` wrapper can wake up regardless
            of whether the run completed, raised, or was cancelled.
        done_future:
            Optional Future supplied by :meth:`run` (private). Resolved
            with ``None`` on success and with ``set_exception(exc)`` on
            generator-raised exceptions or ``CancelledError``. The
            wrapper then re-awaits it to surface the exception to the
            original caller. 202+polling dispatches don't supply this —
            they observe terminal state via the polling endpoint.
        """
        captured_exc: BaseException | None = None
        token = current_run_id.set(run_id)
        try:
            try:
                await self._run_generator_and_persist(mode, request, run_id)
            except asyncio.CancelledError as exc:
                # Shielded cleanup write — terminal status reaches DB
                # even if the dispatching caller's task is cancelled.
                # Exceptions from the cleanup path itself are suppressed
                # so the CancelledError surfaces unmodified to the caller.
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        self._mark_failed(run_id, error="cancelled")
                    )
                captured_exc = exc
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        self._mark_failed(
                            run_id, error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                captured_exc = exc
                raise
        finally:
            current_run_id.reset(token)
            # Resolve the private future BEFORE setting the event so the
            # ``await done_future`` side in :meth:`run` raises before
            # ``done_event.wait()`` would have re-entered the wrapper.
            if done_future is not None and not done_future.done():
                if captured_exc is not None:
                    done_future.set_exception(captured_exc)
                else:
                    done_future.set_result(None)
            if done_event is not None:
                # Set last so :meth:`run` only unblocks after the
                # terminal write has committed. Order is critical:
                # _persist_final + _mark_failed are awaited above before
                # we reach this point, so the ``done_event.wait()`` side
                # observes a committed row.
                done_event.set()

    async def _run_generator_and_persist(
        self,
        mode: str,
        request: RunRequest,
        run_id: str,
    ) -> GeneratorResult:
        """Run the generator + persist its terminal-status result.

        Helper extracted from the legacy :meth:`run` body so
        :meth:`_run_to_completion` can keep its
        cancellation/exception handlers concise. Caller MUST be inside
        the ``current_run_id.set(run_id)`` window (the ContextVar is
        consumed by every event published from the generator).
        """
        generator = self._generators[mode]
        result = await generator.run(request, run_id=run_id)
        await self._persist_final(run_id, result, mode=mode)
        return result

    # ----------------------- internal helpers -----------------------

    async def _create_row(
        self, mode: str, request: RunRequest, *, run_id: str,
    ) -> None:
        """Insert run_row(status='running') via WriteQueue.

        All work_fn lambdas passed to ``WriteQueue.submit`` MUST commit before
        returning per the queue contract (write_queue.py: ``submit`` docstring).
        """

        async def _work(write_db: AsyncSession) -> None:
            row = RunRow(
                id=run_id,
                mode=mode,
                status="running",
                started_at=_utcnow(),
                project_id=request.payload.get("project_id"),
                repo_full_name=request.payload.get("repo_full_name"),
                topic=request.payload.get("topic"),
                intent_hint=request.payload.get("intent_hint"),
                # T2 Cycle 6: replay runs carry ``suite_id`` so the
                # regression-alarm JOIN (run_row.suite_id → suite.id)
                # finds the latest replay per suite. Other modes don't
                # use suite linkage so the column stays NULL.
                suite_id=(
                    request.payload.get("suite_id")
                    if mode == "replay_run"
                    else None
                ),
                topic_probe_meta=self._extract_probe_meta(mode, request),
                seed_agent_meta=self._extract_seed_meta(mode, request),
            )
            write_db.add(row)
            await write_db.commit()  # required by WriteQueue contract

        await self._write_queue.submit(
            _work,
            timeout=30,
            operation_label=f"run_orchestrator.create_row[{mode}]",
        )

    @staticmethod
    def _extract_probe_meta(mode: str, request: RunRequest) -> dict | None:
        """Return the ``topic_probe_meta`` JSON payload (or ``None``).

        Gated on ``mode == 'topic_probe'`` per spec §4 — the column is
        topic-probe-specific metadata, not a generic run-meta field.

        T2 Cycle 6: ``grounding_mode`` is read out of the request payload
        (defaulting to ``"codebase"`` when absent) so future grounding
        strategies (``"none"``, ``"manual"``, ...) can be stored without
        a schema migration.
        """
        if mode != "topic_probe":
            return None
        return {
            "scope": request.payload.get("scope", "**/*"),
            "commit_sha": request.payload.get("commit_sha"),
            "grounding_mode": request.payload.get(
                "grounding_mode", "codebase",
            ),
        }

    @staticmethod
    def _extract_seed_meta(mode: str, request: RunRequest) -> dict | None:
        if mode != "seed_agent":
            return None
        return {
            "project_description": request.payload.get("project_description"),
            "workspace_path": request.payload.get("workspace_path"),
            "agents": request.payload.get("agents"),
            "prompt_count": request.payload.get("prompt_count"),
            "prompts_provided": bool(request.payload.get("prompts")),
            "batch_id": request.payload.get("batch_id"),
            "tier": request.payload.get("tier"),
            "estimated_cost_usd": request.payload.get("estimated_cost_usd"),
        }

    async def _set_run_status(
        self, run_id: str, status: str, **fields: Any,
    ) -> None:
        """Update run_row.status (+ optional completed_at, error, etc.)
        via WriteQueue. The wrapped work_fn MUST commit before returning."""

        async def _work(write_db: AsyncSession) -> None:
            row = await write_db.get(RunRow, run_id)
            if row is None:
                return
            row.status = status
            for key, value in fields.items():
                setattr(row, key, value)
            await write_db.commit()

        await self._write_queue.submit(
            _work,
            timeout=30,
            operation_label=f"run_orchestrator.set_status[{status}]",
        )

    async def _persist_final(
        self, run_id: str, result: GeneratorResult, *, mode: str,
    ) -> None:
        """Write GeneratorResult fields + status from ``result.terminal_status``.

        Generator-classified status preserves today's 4-value contract on
        ``RunRow.status`` (running | completed | partial | failed).

        T2 Cycle 6: ``mode`` selects a mode-keyed ``operation_label`` so
        the WriteQueue audit-log + telemetry can attribute terminal
        persists per-mode. ``replay_run`` uses ``replay_run_persist`` per
        spec §4 + §10 Cycle 6 GREEN step 4; other modes keep the legacy
        generic label to avoid an audit-log schema churn.
        """

        async def _work(write_db: AsyncSession) -> None:
            row = await write_db.get(RunRow, run_id)
            if row is None:
                return
            row.status = result.terminal_status
            row.completed_at = _utcnow()
            row.prompts_generated = result.prompts_generated
            row.prompt_results = result.prompt_results
            row.aggregate = result.aggregate
            row.taxonomy_delta = result.taxonomy_delta
            row.final_report = result.final_report
            await write_db.commit()

        op_label = (
            "replay_run_persist"
            if mode == "replay_run"
            else "run_orchestrator.persist_final"
        )
        await self._write_queue.submit(
            _work,
            timeout=60,
            operation_label=op_label,
        )

    async def _mark_failed(self, run_id: str, *, error: str) -> None:
        """Mark row failed (orchestrator-caught exceptions only).

        Used for cancellation and uncaught generator exceptions.
        Generator-returned ``terminal_status='failed'`` flows through
        ``_persist_final`` instead.
        """
        # Truncate error to 2000 chars to keep DB rows bounded; the
        # type prefix (e.g. ``ValueError: ``) is part of the error string
        # before truncation, so the prefix is preserved when the message
        # alone exceeds the cap.
        truncated = error[:2000] if len(error) > 2000 else error
        await self._set_run_status(
            run_id,
            status="failed",
            error=truncated,
            completed_at=_utcnow(),
        )

    async def _reload(self, run_id: str) -> RunRow:
        """Read row back through standard read path.

        Looks up ``async_session_factory`` from ``app.database`` at call-time
        (not import-time) so test fixtures can monkey-patch it onto a
        shared in-memory engine.
        """
        async with _database.async_session_factory() as db:
            row = await db.get(RunRow, run_id)
            if row is None:
                raise RuntimeError(f"run row {run_id} not found after persist")
            return row


__all__ = ["RunOrchestrator"]
