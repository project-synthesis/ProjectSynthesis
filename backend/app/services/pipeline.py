"""Pipeline orchestrator.

Runs the full optimization pipeline (up to 5 stages) and yields SSE events.
"""

import json
import logging
import time
from typing import AsyncGenerator, Optional

from app.config import settings
from app.providers.base import MODEL_ROUTING, LLMProvider
from app.services.analyzer import run_analyze
from app.services.codebase_explorer import run_explore
from app.services.optimizer import run_optimize
from app.services.strategy import run_strategy
from app.services.validator import run_validate

logger = logging.getLogger(__name__)

# Retry threshold: if overall_score < this, retry optimize+validate once
LOW_SCORE_THRESHOLD = 5


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text (~4 chars per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _skip_remaining(remaining_stages: list[str]):
    """Yield skipped events for stages that won't run due to earlier failure."""
    for stage_name in remaining_stages:
        yield ("stage", {"stage": stage_name, "status": "skipped"})


async def run_pipeline(
    provider: LLMProvider,
    raw_prompt: str,
    optimization_id: str,
    strategy_override: Optional[str] = None,
    repo_full_name: Optional[str] = None,
    repo_branch: Optional[str] = None,
    session_id: Optional[str] = None,
    github_token: Optional[str] = None,
    file_contexts: Optional[list] = None,
    url_fetched_contexts: Optional[list] = None,
    instructions: Optional[list] = None,
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
    codebase_context = None

    # ---- Stage 0: Explore (conditional) ----
    # Runs whenever a repo is linked AND a token is available (session_id or
    # explicit github_token). Skipped silently when neither is provided.
    if repo_full_name and (session_id or github_token):
        try:
            yield ("stage", {
                "stage": "explore",
                "status": "started",
                "repo": repo_full_name,
            })
            start = time.time()

            async for event_type, event_data in run_explore(
                provider=provider,
                raw_prompt=raw_prompt,
                repo_full_name=repo_full_name,
                repo_branch=repo_branch or "main",
                session_id=session_id,
                github_token=github_token,
            ):
                if event_type == "explore_result":
                    codebase_context = event_data
                    # Buffer — duration_ms and model not yet known; yield after loop
                else:
                    yield (event_type, event_data)

            duration_ms = int((time.time() - start) * 1000)
            explore_failed = bool(codebase_context and codebase_context.get("explore_failed"))
            if explore_failed:
                explore_error = (codebase_context or {}).get("explore_error", "Exploration failed")
                # Strip internal flags so downstream stages get clean context
                codebase_context = {
                    k: v for k, v in codebase_context.items()
                    if k not in ("explore_failed", "explore_error")
                } if codebase_context else None
                if codebase_context:
                    codebase_context["duration_ms"] = duration_ms
                    codebase_context["model"] = MODEL_ROUTING["explore"]
                    yield ("codebase_context", codebase_context)
                yield ("stage", {
                    "stage": "explore",
                    "status": "failed",
                    "error": explore_error,
                    "duration_ms": duration_ms,
                })
            else:
                if codebase_context:
                    codebase_context["duration_ms"] = duration_ms
                    codebase_context["model"] = MODEL_ROUTING["explore"]
                    yield ("codebase_context", codebase_context)
                yield ("stage", {
                    "stage": "explore",
                    "status": "complete",
                    "files_read": codebase_context.get("files_read_count", 0) if codebase_context else 0,
                    "duration_ms": duration_ms,
                })
        except Exception as e:
            logger.warning(f"Stage 0 (Explore) failed: {e}. Continuing without codebase context.")
            yield ("error", {
                "stage": "explore",
                "error": str(e),
                "recoverable": True,
            })

    total_tokens = 0

    # ---- Context truncation ----
    # Enforce injection caps BEFORE stages run. Slices happen here so every
    # downstream stage receives already-truncated lists — no silent over-injection.
    _orig_files = len(file_contexts or [])
    _orig_urls  = len(url_fetched_contexts or [])
    _orig_instr = len(instructions or [])

    file_contexts        = list(file_contexts or [])[:5]
    url_fetched_contexts = list(url_fetched_contexts or [])[:3]
    instructions         = list(instructions or [])[:10]

    _dropped_files = max(0, _orig_files - 5)
    _dropped_urls  = max(0, _orig_urls  - 3)
    _dropped_instr = max(0, _orig_instr - 10)
    if _dropped_files or _dropped_urls or _dropped_instr:
        yield ("context_warning", {
            "dropped_files": _dropped_files,
            "dropped_urls": _dropped_urls,
            "dropped_instructions": _dropped_instr,
            "total_files_received": _orig_files,
            "total_urls_received": _orig_urls,
            "total_instructions_received": _orig_instr,
        })

    # ---- Stage 1: Analyze ----
    try:
        yield ("stage", {"stage": "analyze", "status": "started"})
        start = time.time()

        analysis = None
        async for event_type, event_data in run_analyze(
            provider=provider,
            raw_prompt=raw_prompt,
            codebase_context=codebase_context,
            file_contexts=file_contexts,
            url_fetched_contexts=url_fetched_contexts,
            instructions=instructions,
        ):
            if event_type == "analysis":
                analysis = event_data
            else:
                yield (event_type, event_data)

        analysis = analysis or {}
        if not analysis.get("task_type") or not isinstance(analysis.get("task_type"), str):
            logger.warning("Stage 1 produced no task_type; defaulting to 'general'")
            analysis["task_type"] = "general"
        if not analysis.get("complexity") or not isinstance(analysis.get("complexity"), str):
            logger.warning("Stage 1 produced no complexity; defaulting to 'moderate'")
            analysis["complexity"] = "moderate"
        analysis["model"] = MODEL_ROUTING["analyze"]

        stage_tokens = _estimate_tokens(raw_prompt) + _estimate_tokens(json.dumps(analysis))
        total_tokens += stage_tokens

        yield ("analysis", analysis)
        yield ("stage", {
            "stage": "analyze",
            "status": "complete",
            "duration_ms": int((time.time() - start) * 1000),
            "token_count": stage_tokens,
        })
    except Exception as e:
        logger.error(f"Stage 1 (Analyze) failed fatally: {e}")
        yield ("error", {
            "stage": "analyze",
            "error": str(e),
            "recoverable": False,
        })
        for item in _skip_remaining(["strategy", "optimize", "validate"]):
            yield item
        return

    # ---- Stage 2: Strategy ----
    try:
        yield ("stage", {"stage": "strategy", "status": "started"})
        start = time.time()

        if strategy_override:
            strategy_result = {
                "primary_framework": strategy_override,
                "secondary_frameworks": [],
                "rationale": f"User-specified strategy override: {strategy_override}",
                "approach_notes": f"Apply {strategy_override} framework as requested.",
                "strategy_source": "override",
                "model": MODEL_ROUTING["strategy"],
            }
        else:
            strategy_result = None
            async for event_type, event_data in run_strategy(
                provider=provider,
                raw_prompt=raw_prompt,
                analysis=analysis,
                codebase_context=codebase_context,
            ):
                if event_type == "strategy":
                    strategy_result = event_data
                else:
                    yield (event_type, event_data)

            strategy_result = strategy_result or {}
            strategy_result["model"] = MODEL_ROUTING["strategy"]

        stage_tokens = _estimate_tokens(raw_prompt) + _estimate_tokens(json.dumps(strategy_result))
        total_tokens += stage_tokens

        yield ("strategy", strategy_result)
        yield ("stage", {
            "stage": "strategy",
            "status": "complete",
            "duration_ms": int((time.time() - start) * 1000),
            "token_count": stage_tokens,
        })
    except Exception as e:
        logger.error(f"Stage 2 (Strategy) failed fatally: {e}")
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
        yield ("stage", {"stage": "optimize", "status": "started"})
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
        ):
            if event_type == "optimization":
                optimization_result = event_data
                optimization_result["model"] = MODEL_ROUTING["optimize"]
            yield (event_type, event_data)

        opt_text = optimization_result.get("optimized_prompt", "") if optimization_result else ""
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
        })
        _opt_failed = bool((optimization_result or {}).get("optimization_failed", False))
    except Exception as e:
        logger.error(f"Stage 3 (Optimize) failed fatally: {e}")
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
        ):
            if event_type == "validation":
                validation = event_data
            else:
                yield (event_type, event_data)

        validation = validation or {}
        validation["model"] = MODEL_ROUTING["validate"]

        stage_tokens = (
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
        })
    except Exception as e:
        logger.error(f"Stage 4 (Validate) failed fatally: {e}")
        yield ("error", {
            "stage": "validate",
            "error": str(e),
            "recoverable": False,
        })
        return

    # ---- Retry on low score ----
    # Retries up to settings.MAX_PIPELINE_RETRIES times when overall_score is
    # below LOW_SCORE_THRESHOLD and the result is not an improvement.
    # On the second retry (retry_count >= 1), focus_areas is narrowed to the
    # single lowest-scoring dimension instead of all failing areas.
    retry_count = 0
    overall_score = validation.get("scores", validation).get("overall_score", 10)
    is_improvement = validation.get("is_improvement", False)
    while (
        retry_count < settings.MAX_PIPELINE_RETRIES
        and overall_score is not None
        and overall_score < LOW_SCORE_THRESHOLD
        and not is_improvement
    ):
        logger.info(
            f"Overall score {overall_score} < {LOW_SCORE_THRESHOLD} "
            f"(retry {retry_count + 1}/{settings.MAX_PIPELINE_RETRIES}): "
            "retrying optimize+validate with adjusted constraints"
        )
        yield ("rate_limit_warning", {
            "message": (
                f"Score {overall_score}/10 below threshold — "
                f"retrying with stricter constraints "
                f"(attempt {retry_count + 1}/{settings.MAX_PIPELINE_RETRIES})"
            ),
            "stage": "validate",
        })

        # Determine focus_areas for this retry attempt.
        # First retry: all failing areas from validation issues.
        # Second+ retry: only the single lowest-scoring dimension.
        if retry_count == 0:
            focus_areas = validation.get("issues", [])
        else:
            scores = validation.get("scores", {})
            score_dims = {
                "clarity": scores.get("clarity_score", 10),
                "specificity": scores.get("specificity_score", 10),
                "structure": scores.get("structure_score", 10),
                "faithfulness": scores.get("faithfulness_score", 10),
                "conciseness": scores.get("conciseness_score", 10),
            }
            lowest_dim = min(score_dims, key=score_dims.get)
            focus_areas = [lowest_dim]
            logger.info(f"Second retry: narrowing focus to lowest dimension '{lowest_dim}'")

        try:
            # Re-run optimize with adjusted constraints
            yield ("stage", {"stage": "optimize", "status": "started"})
            start = time.time()

            retry_optimization_result = None
            async for event_type, event_data in run_optimize(
                provider=provider,
                raw_prompt=raw_prompt,
                analysis=analysis,
                strategy=strategy_result,
                codebase_context=codebase_context,
                file_contexts=file_contexts,
                url_fetched_contexts=url_fetched_contexts,
                instructions=instructions,
                retry_constraints={
                    "min_score_target": LOW_SCORE_THRESHOLD + 2,
                    "previous_score": overall_score,
                    "focus_areas": focus_areas,
                    "retry_attempt": retry_count + 1,
                },
            ):
                if event_type == "optimization":
                    retry_optimization_result = event_data
                    retry_optimization_result["model"] = MODEL_ROUTING["optimize"]
                yield (event_type, event_data)

            # Gate: if retry optimizer also failed totally, skip validate and stop retrying
            _retry_opt_failed = bool(
                (retry_optimization_result or {}).get("optimization_failed", False)
            )
            if not _retry_opt_failed and retry_optimization_result:
                optimization_result = retry_optimization_result

            opt_text = optimization_result.get("optimized_prompt", "") if optimization_result else ""
            stage_tokens = _estimate_tokens(opt_text)
            total_tokens += stage_tokens

            yield ("stage", {
                "stage": "optimize",
                "status": "complete",
                "duration_ms": int((time.time() - start) * 1000),
                "token_count": stage_tokens,
            })

            if _retry_opt_failed:
                logger.error("Retry optimizer signalled failure; skipping validate and aborting retry loop")
                yield ("stage", {"stage": "validate", "status": "skipped"})
                yield ("error", {"stage": "optimize",
                                 "error": "Retry optimizer failed; no prompt to validate.",
                                 "recoverable": False})
                return

            # Re-run validate
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
            ):
                if event_type == "validation":
                    validation = event_data
                else:
                    yield (event_type, event_data)

            validation = validation or {}
            validation["model"] = MODEL_ROUTING["validate"]

            stage_tokens = (
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
            })

            # Update loop conditions for next iteration
            overall_score = validation.get("scores", validation).get("overall_score", 10)
            is_improvement = validation.get("is_improvement", False)

        except Exception as e:
            logger.warning(f"Retry {retry_count + 1} failed: {e}. Using previous validation result.")
            break

        retry_count += 1
