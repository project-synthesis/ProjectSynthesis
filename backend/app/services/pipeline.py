"""Pipeline orchestrator.

Runs the full optimization pipeline (up to 5 stages) and yields SSE events.
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

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
from app.services.optimizer import run_optimize
from app.services.refinement_service import create_trunk_branch
from app.services.retry_oracle import RetryOracle
from app.services.settings_service import load_settings
from app.services.strategy import run_strategy
from app.services.validator import run_validate

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
    try:
        yield ("stage", {"stage": "strategy", "status": "started"})
        start = time.time()

        if effective_strategy:
            strategy_result = {
                "primary_framework": effective_strategy,
                "secondary_frameworks": [],
                "rationale": f"User-specified strategy override: {effective_strategy}",
                "approach_notes": f"Apply {effective_strategy} framework as requested.",
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
            user_weights=oracle_weights,
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
                user_weights=oracle_weights,
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

    # Create trunk branch for refinement support
    try:
        from app.database import get_session_context as _gs
        async with _gs() as db:
            final_prompt = (optimization_result or {}).get("optimized_prompt", "")
            final_scores = validation.get("scores", {})
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
