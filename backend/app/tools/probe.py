"""synthesis_probe MCP tool — Foundation P3 cycle 13 dispatch shim.

Refactored under Foundation P3 (v0.4.18) to dispatch through
``RunOrchestrator.run('topic_probe', ...)`` instead of the legacy
``ProbeService.run()`` async-generator path. The response shape
(``ProbeRunResult``) is preserved byte-for-byte: the ``probe_id`` /
``id`` field name is unchanged, but the value is now ``RunRow.id``
(spec § 6.1 + § 7.1 backward-compat).

Pre-stream gates preserved:
  - Missing ``repo_full_name`` → ``ProbeError('link_repo_first')``
  - Pydantic ``ProbeRunRequest`` validation runs at the boundary

Construction unification (post-C6 REFACTOR retained): the validated
``ProbeRunRequest`` lives in this shim long enough to surface the
canonical reason codes; once the ``link_repo_first`` gate has fired,
the request payload is forwarded into the orchestrator as a plain dict.

The ``_orchestrator`` parameter is for test injection only (``_`` prefix
flags it as private); production callers leave it ``None`` and the
MCP-runtime singleton resolves via ``_shared.get_run_orchestrator``.

Copyright 2025-2026 Project Synthesis contributors.
"""
from __future__ import annotations

from typing import Any, Literal

from app.schemas.probes import (
    ProbeAggregate,
    ProbeError,
    ProbePromptResult,
    ProbeRunRequest,
    ProbeRunResult,
    ProbeTaxonomyDelta,
)
from app.schemas.runs import RunRequest


def _resolve_orchestrator() -> Any:
    """Return the MCP-process RunOrchestrator singleton.

    Raises ``ProbeError('run_orchestrator_unavailable')`` if the lifespan
    failed to register one. Mirrors the canonical reason-code surface so
    the FastMCP runtime maps it to a remediation message.
    """
    from app.tools._shared import get_run_orchestrator

    try:
        return get_run_orchestrator()
    except ValueError as exc:
        raise ProbeError(
            "run_orchestrator_unavailable",
            message=(
                "RunOrchestrator not initialized; the MCP server lifespan "
                "did not register one (likely WriteQueue init failure)."
            ),
        ) from exc


def _row_to_probe_run_result(row: Any) -> ProbeRunResult:
    """Hydrate a ``RunRow`` (mode='topic_probe') into a ``ProbeRunResult``.

    Mirrors ``routers/probes._serialize_full`` — same logic, same
    ``topic_probe_meta`` extraction, identical None-guards on the JSON
    columns. Kept inline here rather than imported from the router to
    avoid the cross-layer ``tools → routers`` import.
    """
    prompt_results = [
        ProbePromptResult(**r) for r in (row.prompt_results or [])
    ]
    agg_dict = row.aggregate or {
        "scoring_formula_version": 4,  # SCORING_FORMULA_VERSION default
    }
    agg = ProbeAggregate(**agg_dict)
    delta = ProbeTaxonomyDelta(**(row.taxonomy_delta or {}))

    meta = row.topic_probe_meta or {}
    scope = meta.get("scope") or "**/*"
    commit_sha = meta.get("commit_sha")

    return ProbeRunResult(
        id=row.id,
        topic=row.topic or "",
        scope=scope,
        intent_hint=row.intent_hint or "",
        repo_full_name=row.repo_full_name or "",
        project_id=row.project_id,
        commit_sha=commit_sha,
        started_at=row.started_at,
        completed_at=row.completed_at,
        prompts_generated=row.prompts_generated or 0,
        prompt_results=prompt_results,
        aggregate=agg,
        taxonomy_delta=delta,
        final_report=row.final_report or "",
        status=row.status,
        suite_id=row.suite_id,
    )


async def handle_probe(
    topic: str,
    scope: str | None = None,
    intent_hint: Literal["audit", "refactor", "explore", "regression-test"] | None = None,
    n_prompts: int | None = None,
    repo_full_name: str | None = None,
    ctx=None,  # FastMCP Context | None — reserved for future progress reports
    _orchestrator: Any | None = None,
) -> ProbeRunResult:
    """MCP tool handler for ``synthesis_probe`` — dispatch via RunOrchestrator.

    Pipeline:
      1. Validate inputs through ``ProbeRunRequest`` (Pydantic enforces
         topic length 3-500, n_prompts range 5-25)
      2. Pre-flight gate: missing ``repo_full_name`` → ``ProbeError``
         (mirrors the REST router's ``link_repo_first`` short-circuit)
      3. Build ``RunRequest`` and dispatch via
         ``RunOrchestrator.run('topic_probe', ...)``
      4. Read the resulting ``RunRow`` and shape into ``ProbeRunResult``

    The response shape is preserved byte-for-byte (spec § 7.1).

    Notes:
      - ``ctx.report_progress`` is NOT plumbed through Cycle 13 — the
        TopicProbeGenerator publishes progress to ``event_bus``, not
        through the MCP context. The ``ctx`` parameter is retained for
        signature parity and future re-introduction (e.g., bridging
        bus events to MCP progress in a follow-up cycle).
    """
    # Pydantic boundary validation — keeps the production contract enforced
    # even when callers bypass the ``@mcp.tool`` Field constraints.
    request = ProbeRunRequest(
        topic=topic,
        scope=scope,
        intent_hint=intent_hint,
        n_prompts=n_prompts,
        repo_full_name=repo_full_name,
    )

    if not request.repo_full_name:
        # Carry the reason code in the exception message so callers
        # matching on ``link_repo_first`` (e.g. existing C5/C6 tests +
        # Cycle 13 cat-8 tests) see it without inspecting the `.reason`
        # attribute. Mirrors the pre-Foundation handler's surface.
        raise ProbeError(
            "link_repo_first",
            message=(
                "link_repo_first: Link a GitHub repo before running a topic probe."
            ),
        )

    if _orchestrator is None:
        _orchestrator = _resolve_orchestrator()

    payload = request.model_dump()
    run_request = RunRequest(mode="topic_probe", payload=payload)
    run_row = await _orchestrator.run("topic_probe", run_request)
    return _row_to_probe_run_result(run_row)
