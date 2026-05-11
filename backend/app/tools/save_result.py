"""Handler for synthesis_save_result MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mcp.server.fastmcp import Context
from sqlalchemy import select

from app.config import PROMPTS_DIR, settings
from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import SaveResultOutput
from app.services.event_notification import notify_event_bus
from app.services.heuristic_suggestions import generate_heuristic_suggestions
from app.services.pipeline_constants import (
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    VALID_TASK_TYPES,
)
from app.services.preferences import PreferencesService
from app.services.project_service import resolve_repo_project
from app.services.strategy_loader import StrategyLoader
from app.tools._shared import (
    DATA_DIR,
    _fetch_historical_stats,
    get_domain_resolver,
    get_routing,
    get_write_queue,
)
from app.utils.text_cleanup import parse_domain, split_prompt_and_changes, title_case_label, validate_intent_label

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def handle_save_result(
    trace_id: str,
    optimized_prompt: str,
    changes_summary: str | None,
    task_type: str | None,
    strategy_used: str | None,
    scores: dict | None,
    model: str | None,
    codebase_context: str | None,
    ctx: Context | None,
    *,
    domain: str | None = None,
    intent_label: str | None = None,
) -> SaveResultOutput:
    """Persist an optimization result from an external LLM.

    Foundation P4 Cycle 1 restructure: read step opens a short session
    (closes before LLM); LLM step runs analyze_no_session + score_passthrough
    with no session held; persist step submits via WriteQueue.
    """
    from dataclasses import dataclass

    logger.info(
        "synthesis_save_result called: trace_id=%s model=%s",
        trace_id, model,
    )

    # Validate output length
    if len(optimized_prompt) > settings.MAX_RAW_PROMPT_CHARS:
        raise ValueError(
            f"optimized_prompt too long ({len(optimized_prompt)} chars). "
            f"Maximum is {settings.MAX_RAW_PROMPT_CHARS} characters."
        )

    # Normalize strategy_used
    if strategy_used:
        strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
        strategy_used = strategy_loader.normalize_strategy(strategy_used)

    # Scoring preference
    prefs = PreferencesService(DATA_DIR)
    scoring_enabled = prefs.get("pipeline.enable_scoring")
    if scoring_enabled is None:
        scoring_enabled = True

    # Determine clean_scores (passthrough cleaning)
    clean_scores: dict[str, float] = {}
    if scores and scoring_enabled:
        for k, v in scores.items():
            try:
                clean_scores[k] = max(1.0, min(10.0, float(v)))
            except (ValueError, TypeError):
                clean_scores[k] = 5.0

    # Resolve repo → project chain (uses module-level singletons, no DB)
    _resolved_repo, _resolved_project_id = await resolve_repo_project()

    # ============ STEP 1: short read session ============
    # Snapshot dataclass — frozen capture of pending row fields used post-session.
    # Field set matches spec §3.1 `PendingSnapshot` exactly (5 fields). Scope
    # rule: only fields needed by post-`_load_pending_optimization` code paths
    # OR fields the spec mandates for cross-cycle precedent. Do NOT add fields
    # opportunistically — Cycles 2/3 follow this same scoping pattern.
    @dataclass(frozen=True)
    class _PendingSnapshot:
        id: str
        raw_prompt: str
        strategy_used: str | None
        domain_raw: str | None
        intent_label: str | None

    pending_snapshot: _PendingSnapshot | None = None
    historical_stats: dict | None = None
    raw_for_analysis: str = ""

    async with async_session_factory() as db:
        # Load pending optimization by trace_id
        result = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        pending = result.scalar_one_or_none()

        if pending:
            pending_snapshot = _PendingSnapshot(
                id=pending.id,
                raw_prompt=pending.raw_prompt or "",
                strategy_used=pending.strategy_used,
                domain_raw=pending.domain_raw,
                intent_label=pending.intent_label,
            )
            raw_for_analysis = pending.raw_prompt or ""

        # Pre-fetch historical_stats for scoring (no LLM here — pure DB read).
        # `_fetch_historical_stats` + `get_write_queue` imported at module top
        # (see Step 3) — DO NOT re-import inline; tests monkeypatch the bound
        # module-level name, and a function-local import would shadow them.
        if scoring_enabled:
            historical_stats = await _fetch_historical_stats(
                db,
                exclude_scoring_modes=["heuristic", "hybrid_passthrough"],
            )

    # ============ STEP 2: LLM (no session held) ============

    # Clean external LLM output BEFORE scoring
    optimized_prompt, extracted_changes = split_prompt_and_changes(optimized_prompt)
    if not changes_summary and extracted_changes:
        changes_summary = extracted_changes

    # Score — pure compute on injected stats
    from app.services.scoring_service import score_passthrough
    score_result = await score_passthrough(
        raw_prompt=pending_snapshot.raw_prompt if pending_snapshot else None,
        optimized_prompt=optimized_prompt,
        external_scores=clean_scores if clean_scores else None,
        historical_stats=historical_stats,
        scoring_enabled=scoring_enabled,
    )

    # A4-fallback heuristic analysis — no session held.
    # NOTE: `analyze_no_session` runs primarily for `analysis.weaknesses`
    # (consumed by `generate_heuristic_suggestions` below). The caller-supplied
    # `task_type`/`domain`/`intent_label` arguments (from the external LLM)
    # remain the source of truth for the persisted row in passthrough mode —
    # we deliberately do NOT overwrite them with `analysis.task_type` etc.
    # Round-3 F10: documenting this intentional behavior for future maintainers.
    from app.services.heuristic_analyzer import HeuristicAnalyzer
    try:
        _routing = get_routing()
        _provider = _routing.state.provider
    except ValueError:
        _provider = None
    analyzer = HeuristicAnalyzer()
    analysis = await analyzer.analyze_no_session(
        raw_for_analysis, provider=_provider,
    )

    # Generate heuristic suggestions (zero-LLM)
    suggestions = generate_heuristic_suggestions(
        dimension_scores=score_result.optimized_scores,
        weaknesses=analysis.weaknesses,
        strategy_used=strategy_used or (
            pending_snapshot.strategy_used if pending_snapshot else "auto"
        ) or "auto",
    )

    # Truncate codebase context if provided
    context_snapshot = (
        codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]
        if codebase_context else None
    )

    # Domain validation — uses module-level singleton (no DB)
    domain_primary, _ = parse_domain(domain)
    try:
        _resolver = get_domain_resolver()
        validated_domain = await _resolver.resolve(
            domain or (
                pending_snapshot.domain_raw if pending_snapshot else None
            ) or "general",
            confidence=1.0,
        )
    except (ValueError, Exception):
        validated_domain = "general"

    # ============ STEP 3: persist via WriteQueue ============
    async def _persist_save_result(db: "AsyncSession") -> dict:
        """Persist callback — runs on writer-engine session via queue worker.

        Re-loads the pending Optimization (if any) inside this writer session,
        either mutates it in place OR inserts a new row. Returns the SSE event
        payload as plain scalars (no ORM access leaks out of the callback).
        """
        import uuid as _uuid

        result = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        persisted_opt = result.scalar_one_or_none()

        if persisted_opt:
            opt = persisted_opt  # rebind for uniform field assignment + return
            opt.optimized_prompt = optimized_prompt
            _raw_tt = task_type or opt.task_type or "general"
            opt.task_type = (
                _raw_tt if _raw_tt in VALID_TASK_TYPES else "general"
            )
            opt.strategy_used = strategy_used or opt.strategy_used or "auto"
            opt.changes_summary = changes_summary or ""
            opt.domain = validated_domain
            opt.domain_raw = (
                domain or opt.domain_raw or "general"
            )[:MAX_DOMAIN_RAW_LENGTH]
            _raw_il = (
                intent_label or opt.intent_label or "general"
            )[:MAX_INTENT_LABEL_LENGTH]
            opt.intent_label = validate_intent_label(
                title_case_label(_raw_il), opt.raw_prompt,
            )
            opt.score_clarity = score_result.optimized_scores.get("clarity")
            opt.score_specificity = score_result.optimized_scores.get("specificity")
            opt.score_structure = score_result.optimized_scores.get("structure")
            opt.score_faithfulness = score_result.optimized_scores.get("faithfulness")
            opt.score_conciseness = score_result.optimized_scores.get("conciseness")
            opt.overall_score = score_result.overall
            opt.original_scores = score_result.original_scores
            opt.score_deltas = score_result.deltas
            opt.model_used = model or "external"
            opt.models_by_phase = {"optimize": model or "external"}
            opt.scoring_mode = score_result.scoring_mode
            opt.heuristic_flags = (
                score_result.divergence_flags
                if score_result.divergence_flags else None
            )
            opt.suggestions = suggestions
            opt.status = "completed"
            if context_snapshot:
                opt.codebase_context_snapshot = context_snapshot
            if not opt.project_id and _resolved_project_id:
                opt.project_id = _resolved_project_id
            if not opt.repo_full_name and _resolved_repo:
                opt.repo_full_name = _resolved_repo
            opt_id = opt.id
            strategy_compliance = (
                "matched" if (opt.strategy_used == strategy_used)
                else "partial"
            ) if strategy_used else "matched"
        else:
            opt_id = str(_uuid.uuid4())
            opt = Optimization(
                id=opt_id,
                raw_prompt="",
                optimized_prompt=optimized_prompt,
                task_type=(
                    (task_type or "general")
                    if (task_type or "general") in VALID_TASK_TYPES
                    else "general"
                ),
                strategy_used=strategy_used or "auto",
                changes_summary=changes_summary or "",
                domain=validated_domain,
                domain_raw=(domain or "general")[:MAX_DOMAIN_RAW_LENGTH],
                intent_label=validate_intent_label(
                    title_case_label(
                        (intent_label or "general")[:MAX_INTENT_LABEL_LENGTH]
                    ),
                    optimized_prompt,
                ),
                score_clarity=score_result.optimized_scores.get("clarity"),
                score_specificity=score_result.optimized_scores.get("specificity"),
                score_structure=score_result.optimized_scores.get("structure"),
                score_faithfulness=score_result.optimized_scores.get("faithfulness"),
                score_conciseness=score_result.optimized_scores.get("conciseness"),
                overall_score=score_result.overall,
                provider="mcp_passthrough",
                routing_tier="passthrough",
                model_used=model or "external",
                models_by_phase={"optimize": model or "external"},
                scoring_mode=score_result.scoring_mode,
                heuristic_flags=(
                    score_result.divergence_flags
                    if score_result.divergence_flags else None
                ),
                suggestions=suggestions,
                status="completed",
                trace_id=trace_id,
                repo_full_name=_resolved_repo,
                project_id=_resolved_project_id,
                codebase_context_snapshot=context_snapshot,
            )
            db.add(opt)
            strategy_compliance = "matched" if strategy_used else "unknown"

        await db.commit()
        return {
            "id": opt_id,
            "trace_id": trace_id,
            "task_type": opt.task_type,
            "intent_label": opt.intent_label or "general",
            "domain": opt.domain or "general",
            "domain_raw": opt.domain_raw or "general",
            "strategy_used": opt.strategy_used,
            "overall_score": score_result.overall,
            "provider": opt.provider,
            "status": "completed",
            "_strategy_compliance": strategy_compliance,
        }

    # `get_write_queue` imported at module top (Step 3). Tests monkeypatch
    # `save_result_module.get_write_queue` AND `app.tools._shared.get_write_queue`;
    # both bindings refer to the same module-level name after Step 3, so a
    # function-local import would shadow the test patch — DO NOT re-import.
    event_payload = await get_write_queue().submit(
        _persist_save_result,
        operation_label="save_result_persist",
    )

    strategy_compliance = event_payload.pop("_strategy_compliance", "unknown")

    # Emit SSE event (payload is already a plain dict — no ORM access)
    await notify_event_bus("optimization_created", event_payload)

    logger.info(
        "synthesis_save_result completed: optimization_id=%s "
        "strategy_compliance=%s flags=%d",
        event_payload["id"],
        strategy_compliance,
        len(score_result.divergence_flags),
    )

    return SaveResultOutput(
        optimization_id=event_payload["id"],
        scoring_mode=score_result.scoring_mode,
        scores=(
            {k: round(v, 2) for k, v in score_result.optimized_scores.items()}
            if score_result.optimized_scores else {}
        ),
        original_scores=score_result.original_scores,
        score_deltas=score_result.deltas,
        overall_score=score_result.overall,
        strategy_compliance=strategy_compliance,
        heuristic_flags=score_result.divergence_flags,
        suggestions=suggestions,
    )
