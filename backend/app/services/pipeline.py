"""Pipeline orchestrator.

Runs the full optimization pipeline (up to 5 stages) and yields SSE events.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, AsyncGenerator

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.base import MODEL_ROUTING, LLMProvider, select_model
from app.services.adaptation_engine import load_adaptation
from app.services.analyzer import run_analyze
from app.services.codebase_explorer import run_explore
from app.services.context_builders import (
    MAX_FILE_CONTEXTS,
    MAX_INSTRUCTIONS,
    MAX_URL_CONTEXTS,
)
from app.services.framework_profiles import get_profile
from app.services.issue_guardrails import (
    build_issue_guardrails,
    build_issue_verification_prompt,
)
from app.services.issue_suggestions import suggest_likely_issues
from app.services.optimizer import build_adaptation_hints, run_optimize
from app.services.refinement_service import create_trunk_branch
from app.services.result_intelligence import compute_result_assessment
from app.services.retry_oracle import RetryOracle
from app.services.settings_service import load_settings
from app.services.strategy import run_strategy
from app.services.validator import compute_effective_weights, run_validate

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text using the ~4 chars/token heuristic.

    This is a fallback approximation used when ``provider.get_last_usage()``
    returns None. Actual token counts vary by model tokenizer (Claude's
    tokenizer averages ~3.5-4.5 chars/token for English text).  When real
    usage data is available (H2), the pipeline prefers it over this estimate.
    """
    if not text:
        return 0
    return max(1, len(text) // 4)


def _skip_remaining(remaining_stages: list[str]):
    """Yield skipped events for stages that won't run due to earlier failure."""
    for stage_name in remaining_stages:
        yield ("stage", {"stage": stage_name, "status": "skipped"})


async def _load_framework_perf(
    user_id: str,
    task_type: str,
    framework: str,
    db: AsyncSession,
) -> dict | None:
    """Load framework performance for a user/task/framework triple."""
    from sqlalchemy import select as sa_select

    from app.models.framework_performance import FrameworkPerformance
    from app.utils.json_fields import parse_json_column

    stmt = (
        sa_select(FrameworkPerformance)
        .where(FrameworkPerformance.user_id == user_id)
        .where(FrameworkPerformance.task_type == task_type)
        .where(FrameworkPerformance.framework == framework)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if not row:
        return None
    return {
        "avg_scores": parse_json_column(row.avg_scores) if row.avg_scores else None,
        "user_rating_avg": row.user_rating_avg,
        "sample_count": row.sample_count,
        "issue_frequency": (
            parse_json_column(row.issue_frequency) if row.issue_frequency else None
        ),
        "elasticity_snapshot": (
            parse_json_column(row.elasticity_snapshot)
            if row.elasticity_snapshot
            else None
        ),
        "last_updated": row.last_updated,
    }


async def _load_all_framework_perfs(
    user_id: str,
    task_type: str,
    db: AsyncSession,
) -> list[dict]:
    """Load all framework performance rows for a user/task pair."""
    from sqlalchemy import select as sa_select

    from app.models.framework_performance import FrameworkPerformance
    from app.utils.json_fields import parse_json_column

    stmt = (
        sa_select(FrameworkPerformance)
        .where(FrameworkPerformance.user_id == user_id)
        .where(FrameworkPerformance.task_type == task_type)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {
            "framework": r.framework,
            "avg_scores": (
                parse_json_column(r.avg_scores) if r.avg_scores else None
            ),
            "user_rating_avg": r.user_rating_avg,
            "sample_count": r.sample_count,
        }
        for r in rows
    ]


async def _upsert_framework_perf(
    user_id: str,
    task_type: str,
    framework: str,
    scores: dict[str, float],
    elasticity_snapshot: dict[str, float] | None,
    db: AsyncSession,
) -> None:
    """Upsert framework performance after pipeline validation."""
    from sqlalchemy import select as sa_select

    from app.models.framework_performance import FrameworkPerformance

    stmt = (
        sa_select(FrameworkPerformance)
        .where(FrameworkPerformance.user_id == user_id)
        .where(FrameworkPerformance.task_type == task_type)
        .where(FrameworkPerformance.framework == framework)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    from datetime import datetime, timezone

    from app.utils.json_fields import parse_json_column

    now = datetime.now(timezone.utc)

    if row:
        # Incremental average update
        old_count = row.sample_count or 0
        new_count = old_count + 1
        old_avg = parse_json_column(row.avg_scores) if row.avg_scores else {}

        merged_avg: dict[str, float] = {}
        for dim, score in scores.items():
            if dim == "overall_score":
                continue
            old_val = old_avg.get(dim, score)
            merged_avg[dim] = (old_val * old_count + score) / new_count

        row.avg_scores = json.dumps(merged_avg)
        row.sample_count = new_count
        row.last_updated = now
        if elasticity_snapshot:
            row.elasticity_snapshot = json.dumps(elasticity_snapshot)
    else:
        dim_scores = {
            k: v for k, v in scores.items() if k != "overall_score"
        }
        row = FrameworkPerformance(
            user_id=user_id,
            task_type=task_type,
            framework=framework,
            avg_scores=json.dumps(dim_scores),
            sample_count=1,
            last_updated=now,
            elasticity_snapshot=(
                json.dumps(elasticity_snapshot) if elasticity_snapshot else None
            ),
        )
        db.add(row)

    await db.flush()


async def _run_optimize_validate(
    provider: LLMProvider,
    raw_prompt: str,
    analysis: dict,
    strategy_result: dict,
    codebase_context: dict | None,
    file_contexts: list[dict] | None,
    url_fetched_contexts: list[dict] | None,
    instructions: list[str] | None,
    model_optimize: str | None,
    model_validate: str | None,
    stream_optimize: bool,
    retry_constraints: dict | None = None,
    user_weights: dict[str, float] | None = None,
    adaptation_hints: str = "",
    extra_validation_context: str | None = None,
) -> AsyncGenerator[tuple, None]:
    """Run optimize + validate stages and yield all events.

    After yielding all stage events, yields a final sentinel:
        ("_ov_result", {
            "optimization_result": dict | None,
            "validation": dict | None,
            "tokens": int,
            "opt_failed": bool,
        })

    The sentinel has an underscore prefix and is stripped by the caller
    before yielding to the external consumer.
    """
    total_tokens = 0

    # ---- Optimize ----
    yield ("stage", {"stage": "optimize", "status": "started", "streaming": stream_optimize})
    start = time.time()

    optimization_result = None
    async for event_type, event_data in run_optimize(
        provider=provider,
        raw_prompt=raw_prompt,
        analysis=analysis,
        strategy=strategy_result,
        codebase_context=codebase_context,
        file_contexts=file_contexts,
        url_fetched_contexts=url_fetched_contexts,
        instructions=instructions,
        retry_constraints=retry_constraints,
        model=model_optimize,
        streaming=stream_optimize,
        adaptation_hints=adaptation_hints,
    ):
        if event_type == "optimization":
            optimization_result = event_data
            optimization_result["model"] = model_optimize or MODEL_ROUTING["optimize"]
        yield (event_type, event_data)

    opt_text = optimization_result.get("optimized_prompt", "") if optimization_result else ""
    usage = provider.get_last_usage()
    stage_tokens = usage.total_tokens if usage else _estimate_tokens(opt_text)
    if not usage and not retry_constraints:
        stage_tokens = (
            _estimate_tokens(raw_prompt) + _estimate_tokens(json.dumps(analysis))
            + _estimate_tokens(json.dumps(strategy_result)) + _estimate_tokens(opt_text)
        )
    total_tokens += stage_tokens

    yield ("stage", {
        "stage": "optimize",
        "status": "complete",
        "duration_ms": int((time.time() - start) * 1000),
        "token_count": stage_tokens,
        "usage": usage.to_dict() if usage else None,
    })

    opt_failed = bool((optimization_result or {}).get("optimization_failed", False))
    if opt_failed:
        yield ("_ov_result", {
            "optimization_result": optimization_result,
            "validation": None,
            "tokens": total_tokens,
            "opt_failed": True,
        })
        return

    # ---- Validate ----
    yield ("stage", {"stage": "validate", "status": "started"})
    start = time.time()

    optimized_prompt = optimization_result.get("optimized_prompt", "") if optimization_result else ""
    changes_made = optimization_result.get("changes_made", []) if optimization_result else []

    validation = None
    async for event_type, event_data in run_validate(
        provider=provider,
        original_prompt=raw_prompt,
        optimized_prompt=optimized_prompt,
        changes_made=changes_made,
        codebase_context=codebase_context,
        instructions=instructions,
        model=model_validate,
        user_weights=user_weights,
        extra_validation_context=extra_validation_context,
    ):
        if event_type == "validation":
            validation = event_data
        else:
            yield (event_type, event_data)

    validation = validation or {}
    validation["model"] = model_validate or MODEL_ROUTING["validate"]

    usage = provider.get_last_usage()
    stage_tokens = usage.total_tokens if usage else (
        _estimate_tokens(raw_prompt) + _estimate_tokens(optimized_prompt)
        + _estimate_tokens(json.dumps(validation))
    )
    total_tokens += stage_tokens

    yield ("validation", validation)
    yield ("stage", {
        "stage": "validate",
        "status": "complete",
        "duration_ms": int((time.time() - start) * 1000),
        "token_count": stage_tokens,
        "usage": usage.to_dict() if usage else None,
    })

    yield ("_ov_result", {
        "optimization_result": optimization_result,
        "validation": validation,
        "tokens": total_tokens,
        "opt_failed": False,
    })


async def run_pipeline(
    provider: LLMProvider,
    raw_prompt: str,
    optimization_id: str,
    strategy_override: str | None = None,
    repo_full_name: str | None = None,
    repo_branch: str | None = None,
    session_id: str | None = None,
    github_token: str | None = None,
    file_contexts: list[dict] | None = None,
    url_fetched_contexts: list[dict] | None = None,
    instructions: list[str] | None = None,
    user_id: str | None = None,
) -> AsyncGenerator[tuple, None]:
    """Run the full optimization pipeline, yielding (event_type, event_data) tuples.

    Stages:
      0. Explore (only if repo linked)
      1. Analyze
      2. Strategy
      3. Optimize (streaming)
      4. Validate

    Stage failure handling:
      - Stage 0 (Explore) failures are recoverable; pipeline continues without codebase context
      - Stages 1-4 failures mark subsequent stages as 'skipped' and emit an error event
    """
    # ---- Load user settings once for the entire pipeline run ----
    app_settings = load_settings()

    # Compute model override: "auto" means use MODEL_ROUTING defaults
    _user_model = app_settings.get("default_model", "auto")
    model_override: str | None = _user_model if _user_model != "auto" else None

    # Effective max retries (user setting with config.py default as fallback;
    # the settings router validates 0-5 at the API layer)
    effective_max_retries = app_settings.get("max_retries", settings.MAX_PIPELINE_RETRIES)

    # Effective default strategy
    effective_strategy = strategy_override or app_settings.get("default_strategy")

    # Streaming preference
    stream_optimize = app_settings.get("stream_optimize", True)

    codebase_context = None

    total_tokens = 0

    # Load user adaptation (if authenticated)
    adaptation = None
    if user_id:
        from app.database import get_session_context
        async with get_session_context() as db:
            adaptation = await load_adaptation(user_id, db)

    # Initialize oracle (replaces LOW_SCORE_THRESHOLD)
    oracle_threshold = adaptation.get("retry_threshold", 5.0) if adaptation else 5.0
    oracle_weights = adaptation.get("dimension_weights") if adaptation else None
    oracle = RetryOracle(
        max_retries=effective_max_retries,
        threshold=oracle_threshold,
        user_weights=oracle_weights,
    )

    # ---- Context truncation ----
    # Enforce injection caps BEFORE stages run. Slices happen here so every
    # downstream stage receives already-truncated lists — no silent over-injection.
    # Cap constants live in context_builders (single source of truth).
    _orig_files = len(file_contexts or [])
    _orig_urls  = len(url_fetched_contexts or [])
    _orig_instr = len(instructions or [])

    file_contexts        = list(file_contexts or [])[:MAX_FILE_CONTEXTS]
    url_fetched_contexts = list(url_fetched_contexts or [])[:MAX_URL_CONTEXTS]
    instructions         = list(instructions or [])[:MAX_INSTRUCTIONS]

    _dropped_files = max(0, _orig_files - MAX_FILE_CONTEXTS)
    _dropped_urls  = max(0, _orig_urls  - MAX_URL_CONTEXTS)
    _dropped_instr = max(0, _orig_instr - MAX_INSTRUCTIONS)
    if _dropped_files or _dropped_urls or _dropped_instr:
        yield ("context_warning", {
            "dropped_files": _dropped_files,
            "dropped_urls": _dropped_urls,
            "dropped_instructions": _dropped_instr,
            "total_files_received": _orig_files,
            "total_urls_received": _orig_urls,
            "total_instructions_received": _orig_instr,
        })

    # ---- Stage 0 + Stage 1: Parallel Explore + Analyze (H1) ----
    # Explore and Analyze run concurrently. Analyze does NOT require
    # codebase_context (it's optional navigational aid). Running concurrently
    # saves ~min(explore, analyze) seconds of wall-clock time.
    #
    # Events are buffered into lists and yielded in deterministic order
    # (Explore first, then Analyze) to maintain frontend SSE expectations.
    should_explore = bool(repo_full_name and (session_id or github_token))

    # Emit diagnostic when Explore is skipped so callers know WHY
    if not should_explore:
        skip_reasons = []
        if not repo_full_name:
            skip_reasons.append("no repository linked")
        elif not session_id and not github_token:
            skip_reasons.append("no GitHub credentials (no session or token)")
        yield ("stage", {
            "stage": "explore",
            "status": "skipped",
            "reason": "; ".join(skip_reasons) or "explore gate not met",
        })

    explore_events: list[tuple[str, dict]] = []
    analyze_events: list[tuple[str, dict]] = []
    analysis = None

    async def _collect_explore() -> None:
        nonlocal codebase_context
        explore_events.append(("stage", {
            "stage": "explore",
            "status": "started",
            "repo": repo_full_name,
        }))
        start = time.time()
        try:
            async for event_type, event_data in run_explore(
                provider=provider,
                raw_prompt=raw_prompt,
                repo_full_name=repo_full_name,
                repo_branch=repo_branch or "main",
                session_id=session_id,
                github_token=github_token,
                model=model_override,
            ):
                if event_type == "explore_result":
                    codebase_context = event_data
                else:
                    explore_events.append((event_type, event_data))

            duration_ms = int((time.time() - start) * 1000)
            explore_failed = bool(codebase_context and codebase_context.get("explore_failed"))
            if explore_failed:
                exp_error = (codebase_context or {}).get("explore_error", "Exploration failed")
                codebase_context = {
                    k: v for k, v in codebase_context.items()
                    if k not in ("explore_failed", "explore_error")
                } if codebase_context else None
                if codebase_context:
                    codebase_context["duration_ms"] = duration_ms
                    codebase_context["model"] = model_override or MODEL_ROUTING["explore"]
                    explore_events.append(("codebase_context", codebase_context))
                explore_events.append(("stage", {
                    "stage": "explore",
                    "status": "failed",
                    "error": exp_error,
                    "duration_ms": duration_ms,
                }))
            else:
                if codebase_context:
                    codebase_context["duration_ms"] = duration_ms
                    codebase_context["model"] = model_override or MODEL_ROUTING["explore"]
                    explore_events.append(("codebase_context", codebase_context))
                explore_events.append(("stage", {
                    "stage": "explore",
                    "status": "complete",
                    "files_read": codebase_context.get("files_read_count", 0) if codebase_context else 0,
                    "duration_ms": duration_ms,
                }))
        except Exception as e:
            logger.warning("Stage 0 (Explore) failed: %s. Continuing without codebase context.", e)
            explore_events.append(("error", {
                "stage": "explore",
                "error": str(e),
                "recoverable": True,
            }))

    async def _collect_analyze() -> None:
        nonlocal analysis
        analyze_events.append(("stage", {"stage": "analyze", "status": "started"}))
        start = time.time()

        async for event_type, event_data in run_analyze(
            provider=provider,
            raw_prompt=raw_prompt,
            codebase_context=None,   # No codebase context in parallel mode
            file_contexts=file_contexts,
            url_fetched_contexts=url_fetched_contexts,
            instructions=instructions,
            model=model_override,
        ):
            if event_type == "analysis":
                analysis = event_data
            else:
                analyze_events.append((event_type, event_data))

        analysis = analysis or {}
        if not analysis.get("task_type") or not isinstance(analysis.get("task_type"), str):
            logger.warning("Stage 1 produced no task_type; defaulting to 'general'")
            analysis["task_type"] = "general"
        if not analysis.get("complexity") or not isinstance(analysis.get("complexity"), str):
            logger.warning("Stage 1 produced no complexity; defaulting to 'moderate'")
            analysis["complexity"] = "moderate"
        analysis["model"] = model_override or MODEL_ROUTING["analyze"]

        usage = provider.get_last_usage()
        est = _estimate_tokens(raw_prompt) + _estimate_tokens(json.dumps(analysis))
        stage_tokens = usage.total_tokens if usage else est

        analyze_events.append(("analysis", analysis))
        analyze_events.append(("stage", {
            "stage": "analyze",
            "status": "complete",
            "duration_ms": int((time.time() - start) * 1000),
            "token_count": stage_tokens,
            "usage": usage.to_dict() if usage else None,
        }))

    # Run concurrently — asyncio.wait (not TaskGroup) so one failure doesn't cancel the other
    tasks: list[asyncio.Task] = [asyncio.create_task(_collect_analyze())]
    if should_explore:
        tasks.insert(0, asyncio.create_task(_collect_explore()))

    _max_timeout = max(settings.EXPLORE_TIMEOUT_SECONDS, settings.ANALYZE_TIMEOUT_SECONDS) + 10
    done, pending = await asyncio.wait(tasks, timeout=_max_timeout)
    for t in pending:
        t.cancel()
        try:
            await t
        except (asyncio.CancelledError, Exception):
            pass

    # Check for Analyze failure (fatal — Explore failure is recoverable)
    analyze_task = tasks[-1]  # Analyze is always the last task
    if analyze_task.done() and analyze_task.exception():
        analyze_exc = analyze_task.exception()
        logger.error("Stage 1 (Analyze) failed fatally: %s", analyze_exc)
        # Yield explore events first (if any), then the error
        for et, ed in explore_events:
            yield (et, ed)
        yield ("error", {
            "stage": "analyze",
            "error": str(analyze_exc),
            "recoverable": False,
        })
        for item in _skip_remaining(["strategy", "optimize", "validate"]):
            yield item
        return

    # Yield in deterministic order: Explore first, then Analyze
    for et, ed in explore_events:
        yield (et, ed)
    for et, ed in analyze_events:
        # Accumulate token counts from the analyze stage events
        if et == "stage" and ed.get("status") == "complete":
            total_tokens += ed.get("token_count", 0)
        yield (et, ed)

    # ---- Dynamic model routing (H4) ----
    # After Analyze produces complexity, downstream stages may use cheaper models.
    complexity = analysis.get("complexity", "moderate")
    model_strategy = select_model("strategy", complexity, model_override)
    model_optimize = select_model("optimize", complexity, model_override)
    model_validate = select_model("validate", complexity, model_override)

    # Emit model_selection event for observability
    _routing_info: dict[str, dict] = {}
    for _stage_name, _stage_model, _default in [
        ("strategy", model_strategy, MODEL_ROUTING["strategy"]),
        ("optimize", model_optimize, MODEL_ROUTING["optimize"]),
        ("validate", model_validate, MODEL_ROUTING["validate"]),
    ]:
        _reason = "user override" if model_override else (
            f"downgraded ({complexity})" if _stage_model != _default else "default"
        )
        _routing_info[_stage_name] = {"model": _stage_model, "reason": _reason}

    yield ("model_selection", {
        "complexity": complexity,
        **_routing_info,
    })

    # ---- Stage 2: Strategy ----
    # Pre-load framework performance rows for strategy (non-fatal)
    _strategy_perf_rows: list[dict] | None = None
    if user_id:
        try:
            from app.database import get_session_context as _gs_sp
            async with _gs_sp() as _db_sp:
                _strategy_perf_rows = await _load_all_framework_perfs(
                    user_id, analysis.get("task_type", "general"), _db_sp,
                )
        except Exception as e:
            logger.warning("Failed to load perf rows for strategy: %s", e)

    try:
        yield ("stage", {"stage": "strategy", "status": "started"})
        start = time.time()

        if effective_strategy:
            from app.services.strategy_selector import build_override_approach_notes
            strategy_result = {
                "primary_framework": effective_strategy,
                "secondary_frameworks": [],
                "rationale": f"User-specified strategy override: {effective_strategy}",
                "approach_notes": build_override_approach_notes(effective_strategy, analysis),
                "strategy_source": "override",
                "model": model_strategy,
            }
        else:
            strategy_result = None
            strategy_affinities = adaptation.get("strategy_affinities") if adaptation else None
            async for event_type, event_data in run_strategy(
                provider=provider,
                raw_prompt=raw_prompt,
                analysis=analysis,
                codebase_context=codebase_context,
                file_contexts=file_contexts,
                url_fetched_contexts=url_fetched_contexts,
                instructions=instructions,
                model=model_strategy,
                strategy_affinities=strategy_affinities,
                framework_perf_rows=_strategy_perf_rows,
            ):
                if event_type == "strategy":
                    strategy_result = event_data
                else:
                    yield (event_type, event_data)

            strategy_result = strategy_result or {}
            strategy_result["model"] = model_strategy

        usage = provider.get_last_usage()
        est = _estimate_tokens(raw_prompt) + _estimate_tokens(json.dumps(strategy_result))
        stage_tokens = usage.total_tokens if usage else est
        total_tokens += stage_tokens

        yield ("strategy", strategy_result)
        yield ("stage", {
            "stage": "strategy",
            "status": "complete",
            "duration_ms": int((time.time() - start) * 1000),
            "token_count": stage_tokens,
            "usage": usage.to_dict() if usage else None,
        })
    except Exception as e:
        logger.error("Stage 2 (Strategy) failed fatally: %s", e)
        yield ("error", {
            "stage": "strategy",
            "error": str(e),
            "recoverable": False,
        })
        for item in _skip_remaining(["optimize", "validate"]):
            yield item
        return

    # ---- Adaptation wiring (between strategy and optimize) ----
    primary_framework = (strategy_result or {}).get("primary_framework", "")
    fw_profile = get_profile(primary_framework) if primary_framework else None

    # Build issue guardrails from feedback-reported issue frequency
    issue_frequency = adaptation.get("issue_frequency") if adaptation else None
    # Load framework-specific issue frequency for guardrail merging
    framework_performance: dict | None = None
    framework_issue_freq: dict[str, int] | None = None
    if user_id and primary_framework:
        try:
            from app.database import get_session_context as _gs2
            async with _gs2() as _db2:
                framework_performance = await _load_framework_perf(
                    user_id, analysis.get("task_type", "general"),
                    primary_framework, _db2,
                )
                if framework_performance:
                    framework_issue_freq = framework_performance.get(
                        "issue_frequency",
                    )
        except Exception as e:
            logger.warning("Failed to load framework performance: %s", e)

    active_guardrails_text = ""
    if issue_frequency:
        active_guardrails_text = build_issue_guardrails(
            issue_frequency, framework_issue_freq,
        )

    # Build issue verification prompt for the validator
    issue_verification_context: str | None = None
    if issue_frequency:
        issue_verification_context = build_issue_verification_prompt(
            issue_frequency,
        )

    # Build adaptation hints for the optimizer (pass guardrails text as list)
    # build_adaptation_hints expects a list of guardrail strings
    _guardrail_list: list[str] = []
    if active_guardrails_text:
        # Extract individual guardrails from the formatted text
        _guardrail_list = [
            line.lstrip("- ").strip()
            for line in active_guardrails_text.splitlines()
            if line.strip().startswith("- ")
        ]
    _adaptation_hints = build_adaptation_hints(
        fw_profile, oracle_weights, _guardrail_list,
    )

    # Compute effective weights for the validator (user weights + framework profile)
    effective_weights = compute_effective_weights(oracle_weights, fw_profile)

    # Update oracle with framework context (not known at init time)
    if primary_framework:
        oracle._framework = primary_framework

    # Emit adaptation_injected event for observability
    if adaptation:
        yield ("adaptation_injected", {
            "framework": primary_framework,
            "has_user_weights": oracle_weights is not None,
            "guardrail_count": len(_guardrail_list),
            "has_verification_prompt": issue_verification_context is not None,
            "feedback_count": adaptation.get("feedback_count", 0),
        })

    # ---- Stage 3: Optimize (streaming) ----
    _opt_failed = False  # M1: initialize before try so except path never leaves it unbound
    try:
        yield ("stage", {"stage": "optimize", "status": "started", "streaming": stream_optimize})
        start = time.time()

        optimization_result = None
        async for event_type, event_data in run_optimize(
            provider=provider,
            raw_prompt=raw_prompt,
            analysis=analysis,
            strategy=strategy_result,
            codebase_context=codebase_context,
            file_contexts=file_contexts,
            url_fetched_contexts=url_fetched_contexts,
            instructions=instructions,
            model=model_optimize,
            streaming=stream_optimize,
            adaptation_hints=_adaptation_hints,
        ):
            if event_type == "optimization":
                optimization_result = event_data
                optimization_result["model"] = model_optimize
            yield (event_type, event_data)

        opt_text = optimization_result.get("optimized_prompt", "") if optimization_result else ""
        usage = provider.get_last_usage()
        stage_tokens = usage.total_tokens if usage else (
            _estimate_tokens(raw_prompt) + _estimate_tokens(json.dumps(analysis))
            + _estimate_tokens(json.dumps(strategy_result)) + _estimate_tokens(opt_text)
        )
        total_tokens += stage_tokens

        yield ("stage", {
            "stage": "optimize",
            "status": "complete",
            "duration_ms": int((time.time() - start) * 1000),
            "token_count": stage_tokens,
            "usage": usage.to_dict() if usage else None,
        })
        _opt_failed = bool((optimization_result or {}).get("optimization_failed", False))
    except Exception as e:
        logger.error("Stage 3 (Optimize) failed fatally: %s", e)
        yield ("error", {
            "stage": "optimize",
            "error": str(e),
            "recoverable": False,
        })
        for item in _skip_remaining(["validate"]):
            yield item
        return

    if _opt_failed:
        logger.error("Skipping Stage 4: optimizer signalled failure")
        yield ("stage", {"stage": "validate", "status": "skipped"})
        yield ("error", {"stage": "optimize",
                         "error": "All optimizer provider calls failed; no prompt to validate.",
                         "recoverable": False})
        return

    # ---- Stage 4: Validate ----
    # Check auto_validate setting — skip when disabled by user
    if not app_settings.get("auto_validate", True):
        logger.info("Stage 4 (Validate) skipped — auto_validate is disabled in settings")
        yield ("stage", {"stage": "validate", "status": "skipped"})
        return

    try:
        yield ("stage", {"stage": "validate", "status": "started"})
        start = time.time()

        optimized_prompt = optimization_result.get("optimized_prompt", "") if optimization_result else ""
        changes_made = optimization_result.get("changes_made", []) if optimization_result else []

        validation = None
        async for event_type, event_data in run_validate(
            provider=provider,
            original_prompt=raw_prompt,
            optimized_prompt=optimized_prompt,
            changes_made=changes_made,
            codebase_context=codebase_context,
            instructions=instructions,
            model=model_validate,
            user_weights=effective_weights,
            extra_validation_context=issue_verification_context,
        ):
            if event_type == "validation":
                validation = event_data
            else:
                yield (event_type, event_data)

        validation = validation or {}
        validation["model"] = model_validate

        usage = provider.get_last_usage()
        stage_tokens = usage.total_tokens if usage else (
            _estimate_tokens(raw_prompt) + _estimate_tokens(optimized_prompt)
            + _estimate_tokens(json.dumps(validation))
        )
        total_tokens += stage_tokens

        yield ("validation", validation)
        yield ("stage", {
            "stage": "validate",
            "status": "complete",
            "duration_ms": int((time.time() - start) * 1000),
            "token_count": stage_tokens,
            "usage": usage.to_dict() if usage else None,
        })
    except Exception as e:
        logger.error("Stage 4 (Validate) failed fatally: %s", e)
        yield ("error", {
            "stage": "validate",
            "error": str(e),
            "recoverable": False,
        })
        return

    # ---- Oracle-driven retry loop ----
    # Record first attempt
    validation_scores = validation.get("scores", {})
    optimized_prompt = (optimization_result or {}).get("optimized_prompt", "")
    oracle.record_attempt(validation_scores, optimized_prompt, [])
    yield ("retry_diagnostics", oracle.get_diagnostics())

    # Store all attempts for best-of-N selection
    all_attempts = [{
        "optimization_result": optimization_result,
        "validation": validation,
    }]

    while True:
        decision = oracle.should_retry()
        if decision.action in ("accept", "accept_best"):
            # Best-of-N: if accept_best, swap to the highest-scoring attempt
            if decision.action == "accept_best" and decision.best_attempt is not None:
                best = all_attempts[decision.best_attempt]
                optimization_result = best["optimization_result"]
                validation = best["validation"]
                yield ("retry_best_selected", {
                    "best_attempt_index": decision.best_attempt,
                    "best_score": oracle._attempts[decision.best_attempt].overall_score,
                    "selected_attempt": decision.best_attempt + 1,
                    "total_attempts": len(all_attempts),
                    "reason": decision.reason,
                })
            break

        # Retry: build diagnostic message for the optimizer
        yield ("stage", {
            "stage": "optimize",
            "status": "retrying",
            "attempt": oracle.attempt_count + 1,
        })
        diagnostic_msg = oracle.build_diagnostic_message(decision.focus_areas)
        yield ("rate_limit_warning", {
            "message": diagnostic_msg,
            "stage": "validate",
        })

        try:
            ov_result = None
            async for event_type, event_data in _run_optimize_validate(
                provider, raw_prompt, analysis, strategy_result, codebase_context,
                file_contexts, url_fetched_contexts, instructions,
                model_optimize, model_validate, stream_optimize,
                retry_constraints={
                    "focus_areas": decision.focus_areas,
                    "min_score_target": oracle.threshold + 2,
                    "previous_score": oracle._attempts[-1].overall_score,
                    "retry_attempt": oracle.attempt_count,
                },
                user_weights=effective_weights,
                adaptation_hints=_adaptation_hints,
                extra_validation_context=issue_verification_context,
            ):
                if event_type == "_ov_result":
                    ov_result = event_data
                else:
                    yield (event_type, event_data)

            assert ov_result is not None

            if ov_result["opt_failed"]:
                logger.error("Retry optimizer failed; aborting retry loop")
                yield ("stage", {"stage": "validate", "status": "skipped"})
                yield ("error", {"stage": "optimize",
                                 "error": "Retry optimizer failed",
                                 "recoverable": False})
                return

            # Record new attempt
            new_opt = ov_result["optimization_result"]
            new_val = ov_result["validation"] or {}
            new_scores = new_val.get("scores", {})
            new_prompt = (new_opt or {}).get("optimized_prompt", "")
            oracle.record_attempt(new_scores, new_prompt, decision.focus_areas)
            yield ("retry_diagnostics", oracle.get_diagnostics())

            all_attempts.append({
                "optimization_result": new_opt,
                "validation": new_val,
            })

            # Update running references
            if new_opt:
                optimization_result = new_opt
            validation = new_val

        except Exception as e:
            logger.warning("Retry %d failed: %s", oracle.attempt_count, e)
            yield ("error", {
                "stage": "optimize",
                "error": f"Retry failed: {e}",
                "recoverable": True,
            })
            break

    # ---- Post-validation intelligence (non-fatal, guarded) ----
    final_scores = (validation or {}).get("scores", {})
    final_overall = (validation or {}).get("overall_score", 0.0)
    final_task_type = analysis.get("task_type", "general")

    # Pre-initialize elasticity_snap so it's available across try blocks
    elasticity_snap: dict[str, float] | None = None
    if primary_framework and oracle._elasticity_matrix.get(primary_framework):
        elasticity_snap = dict(oracle._elasticity_matrix[primary_framework])

    # Issue 3: Suggest likely issues
    try:
        suggestions = suggest_likely_issues(
            scores=final_scores,
            framework=primary_framework,
            framework_issue_freq=framework_issue_freq,
            user_issue_freq=issue_frequency,
        )
        if suggestions:
            yield ("issue_suggestions", {
                "suggestions": [
                    {
                        "issue_id": s.issue_id,
                        "reason": s.reason,
                        "confidence": round(s.confidence, 2),
                    }
                    for s in suggestions
                ],
            })
    except Exception as e:
        logger.warning("Issue suggestion failed: %s (non-fatal)", e)

    # Issue 4: Result assessment
    try:
        oracle_diag = oracle.get_diagnostics()
        gate_triggered = oracle_diag.get("gate")

        # Build attempt score dicts for trade-off detection
        attempt_score_dicts = []
        for att in oracle._attempts:
            att_dict = dict(att.scores)
            att_dict["overall_score"] = att.overall_score
            attempt_score_dicts.append(att_dict)

        # Get previous scores (from first attempt if retried, else None)
        prev_scores: dict[str, float] | None = None
        if len(oracle._attempts) >= 2:
            prev_scores = dict(oracle._attempts[0].scores)

        assessment = compute_result_assessment(
            overall_score=final_overall,
            scores=final_scores,
            threshold=oracle.threshold,
            framework=primary_framework,
            task_type=final_task_type,
            user_weights=oracle_weights,
            framework_perf=framework_performance,
            all_framework_perfs=None,  # loaded below if user_id available
            elasticity=elasticity_snap,
            previous_scores=prev_scores,
            attempts=attempt_score_dicts,
            oracle_diagnostics=[oracle_diag],
            gate_triggered=gate_triggered,
            active_guardrails=_guardrail_list or None,
        )
        # Load all framework perfs for fit comparison if user authenticated
        if user_id:
            try:
                from app.database import get_session_context as _gs_fit
                async with _gs_fit() as _db_fit:
                    all_perfs = await _load_all_framework_perfs(
                        user_id, final_task_type, _db_fit,
                    )
                    if all_perfs:
                        from app.services.result_intelligence import (
                            compute_framework_fit,
                        )
                        assessment.framework_fit = compute_framework_fit(
                            framework=primary_framework,
                            task_type=final_task_type,
                            overall_score=final_overall,
                            framework_perf=framework_performance,
                            all_perfs=all_perfs,
                        )
            except Exception as e:
                logger.warning("Framework fit computation failed: %s", e)

        yield ("result_assessment", assessment.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Result assessment failed: %s (non-fatal)", e)

    # Issue 5: Adaptation impact report
    try:
        from app.schemas.result_assessment import AdaptationImpactReport
        impact_data = AdaptationImpactReport(
            weights_applied=effective_weights,
            guardrails_active=_guardrail_list,
            threshold_used=oracle.threshold,
        )
        # Estimate impact by comparing with default behavior
        if adaptation and oracle_weights:
            # If adapted weights exist and scores are good, adaptation likely helped
            if final_overall >= oracle.threshold:
                impact_data.estimated_impact = "positive"
            elif final_overall >= oracle.threshold - 1.0:
                impact_data.estimated_impact = "neutral"
            else:
                impact_data.estimated_impact = "negative"
        yield ("adaptation_impact", impact_data.model_dump(mode="json"))
    except Exception as e:
        logger.warning("Adaptation impact report failed: %s (non-fatal)", e)

    # Issue 6: Upsert framework_performance table
    if user_id and primary_framework and final_scores:
        try:
            from app.database import get_session_context as _gs_fp
            async with _gs_fp() as _db_fp:
                await _upsert_framework_perf(
                    user_id=user_id,
                    task_type=final_task_type,
                    framework=primary_framework,
                    scores=final_scores,
                    elasticity_snapshot=elasticity_snap,
                    db=_db_fp,
                )
                await _db_fp.commit()
        except Exception as e:
            logger.warning(
                "Framework performance upsert failed: %s (non-fatal)", e,
            )

    # Create trunk branch for refinement support
    try:
        from app.database import get_session_context as _gs
        async with _gs() as db:
            final_prompt = (optimization_result or {}).get("optimized_prompt", "")
            if final_prompt:
                trunk = await create_trunk_branch(
                    optimization_id=optimization_id,
                    prompt=final_prompt,
                    scores=final_scores,
                    db=db,
                )
                await db.commit()
                yield ("branch_created", {"branch": trunk})
    except Exception as e:
        logger.warning("Trunk branch creation failed: %s (non-fatal)", e)

    # Store adaptation snapshot for transparency
    if adaptation:
        yield ("adaptation_snapshot", adaptation)
