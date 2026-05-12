"""MCP tool handler: ``synthesis_save_suite`` — fork a topic_probe RunRow.

Spec: ``docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md``
      §4 (MCP tool table line 326) + §10 Cycle 10.
Plan: ``docs/superpowers/plans/2026-05-11-topic-probe-tier-2.md`` Cycle 10.

Thin dispatch shim — all persistence + validation logic lives in
:class:`app.services.validation_suite_service.ValidationSuiteService`.
Cycle 10 INTEGRATE contract A2: the handler MUST delegate via
``create_from_run`` and NEVER re-implement snapshot construction or the
write-queue submission path.

Error envelope (spec §4 lines 337-342): service raises bare-code
``ValueError`` instances (e.g. ``ValueError("run_not_completed")``); the
handler re-raises them unchanged so the FastMCP runtime surfaces the code
in ``str(exc)`` per the canonical error-envelope convention used by
``synthesis_probe`` (``ProbeError(reason='link_repo_first')``). Pinned by
Cycle 10 RED tests 2 + 3.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.mcp_models import SaveSuiteOutput
from app.services.validation_suite_service import ValidationSuiteService

if TYPE_CHECKING:
    from app.services.write_queue import WriteQueue


async def handle_save_suite(
    *,
    run_id: str,
    label: str,
    tolerance_abs: float = 0.5,
    write_queue: WriteQueue,
) -> SaveSuiteOutput:
    """Fork a completed topic_probe RunRow into an immutable ValidationSuite.

    Delegates to :meth:`ValidationSuiteService.create_from_run` per Cycle 10
    INTEGRATE A2 (handler MUST NOT re-implement service logic). The service
    opens its own short read session via ``async_session_factory``, runs the
    precondition checks (``run_not_found`` / ``run_not_completed`` /
    ``not_a_probe_run`` / ``run_missing_aggregate``), builds the frozen
    snapshot, and submits the terminal write through ``write_queue`` with
    ``operation_label='validation_suite_create'``.

    Parameters
    ----------
    run_id : str
        Source ``RunRow.id``. Must reference a row whose
        ``mode='topic_probe'``, ``status='completed'``, and whose
        ``aggregate`` carries ``mean_overall``.
    label : str
        Human-readable label (1-120 chars). FastMCP layer's
        ``Field(min_length=1, max_length=120)`` annotation enforces this at
        the tool boundary — the service trusts the validation.
    tolerance_abs : float, default 0.5
        Absolute score tolerance (0.1-5.0). FastMCP layer's ``Field(ge=0.1,
        le=5.0)`` annotation enforces this at the tool boundary.
    write_queue : WriteQueue
        Required collaborator — terminal write routes through this queue.

    Returns
    -------
    SaveSuiteOutput
        ``*Output`` Pydantic envelope per spec §4 line 326. Populated from
        the ``ValidationSuiteOut`` returned by the service — no extra DB
        re-read needed.

    Raises
    ------
    ValueError
        Re-raised from the service. The exception's string form carries
        the canonical error code (``run_not_completed``,
        ``run_missing_aggregate``, ``run_not_found``, ``not_a_probe_run``)
        so MCP callers / tests pattern-matching on the code surface see
        it without inspecting ``.reason``/``.code``/``.detail`` attributes.
    """
    service = ValidationSuiteService()
    suite_out = await service.create_from_run(
        run_id=run_id,
        label=label,
        tolerance_abs=tolerance_abs,
        write_queue=write_queue,
    )

    return SaveSuiteOutput(
        suite_id=suite_out.id,
        source_run_id=suite_out.source_run_id,
        label=suite_out.label,
        # baseline_mean is read out of the nested BaselineScoresPayload — its
        # mean_overall key is canonical per spec §3.
        baseline_mean=suite_out.baseline_scores.mean_overall,
        tolerance_abs=suite_out.tolerance_abs,
        prompts_count=len(suite_out.prompts_snapshot),
        created_at=suite_out.created_at,
    )
