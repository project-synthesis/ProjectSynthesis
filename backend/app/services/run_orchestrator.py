"""RunOrchestrator — central dispatch for unified run substrate (Foundation P3).

Responsibilities:
    - Allocate run_id (or accept caller-supplied)
    - Create RunRow row via WriteQueue at start (status='running')
    - Dispatch to mode-specific RunGenerator
    - Persist final state (status from GeneratorResult.terminal_status)
    - Catch exceptions + cancellation; mark row failed under asyncio.shield()
    - Set/reset current_run_id ContextVar around generator invocation

Generators NEVER touch RunRow — RunOrchestrator is the only legitimate writer.

Spec: docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md § 5.2
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
        """Top-level dispatch. Creates row → runs generator → persists result.

        ``run_id`` is optional caller-supplied id. Race-sensitive callers (e.g.,
        the probes router constructing an SSE response) pre-mint the id and
        supply it so they can register event subscriptions BEFORE the
        orchestrator starts. When ``None``, an id is minted internally.
        """
        if mode not in self._generators:
            # Cannot mark failed — row not yet created.
            raise ValueError(f"unknown mode: {mode}")

        if run_id is None:
            run_id = str(uuid.uuid4())

        await self._create_row(mode, request, run_id=run_id)
        generator = self._generators[mode]

        # Set the ContextVar so taxonomy events fired during the run get
        # correlated with this run_id.
        token = current_run_id.set(run_id)
        try:
            try:
                result = await generator.run(request, run_id=run_id)
                await self._persist_final(run_id, result)
            except asyncio.CancelledError:
                # Mark failed under shield so the cancellation cannot
                # interrupt the cleanup write itself. Suppress any error
                # from the cleanup path so the original CancelledError
                # surfaces faithfully to the caller.
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        self._mark_failed(run_id, error="cancelled")
                    )
                raise
            except Exception as exc:
                with contextlib.suppress(Exception):
                    await self._mark_failed(
                        run_id, error=f"{type(exc).__name__}: {exc}"
                    )
                raise
        finally:
            current_run_id.reset(token)

        return await self._reload(run_id)

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
        if mode != "topic_probe":
            return None
        return {
            "scope": request.payload.get("scope", "**/*"),
            "commit_sha": request.payload.get("commit_sha"),
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

    async def _persist_final(self, run_id: str, result: GeneratorResult) -> None:
        """Write GeneratorResult fields + status from ``result.terminal_status``.

        Generator-classified status preserves today's 4-value contract on
        ``RunRow.status`` (running | completed | partial | failed).
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

        await self._write_queue.submit(
            _work,
            timeout=60,
            operation_label="run_orchestrator.persist_final",
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
