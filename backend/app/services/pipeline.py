"""Pipeline orchestrator.

Runs the full optimization pipeline (up to 5 stages) and yields SSE events.
"""

import json
import logging
import time
from typing import AsyncGenerator, Optional

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

    # ---- Stage 1: Analyze ----
    try:
        yield ("stage", {"stage": "analyze", "status": "started"})
        start = time.time()

        analysis = await run_analyze(
            provider=provider,
            raw_prompt=raw_prompt,
            codebase_context=codebase_context,
        )
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
                "model": MODEL_ROUTING["strategy"],
            }
        else:
            strategy_result = await run_strategy(
                provider=provider,
                raw_prompt=raw_prompt,
                analysis=analysis,
                codebase_context=codebase_context,
            )
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

    # ---- Stage 4: Validate ----
    try:
        yield ("stage", {"stage": "validate", "status": "started"})
        start = time.time()

        optimized_prompt = optimization_result.get("optimized_prompt", "") if optimization_result else ""
        changes_made = optimization_result.get("changes_made", []) if optimization_result else []

        validation = await run_validate(
            provider=provider,
            original_prompt=raw_prompt,
            optimized_prompt=optimized_prompt,
            changes_made=changes_made,
            codebase_context=codebase_context,
        )
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
    overall_score = validation.get("scores", validation).get("overall_score", 10)
    is_improvement = validation.get("is_improvement", True)
    if overall_score is not None and overall_score < LOW_SCORE_THRESHOLD and not is_improvement:
        logger.info(
            f"Overall score {overall_score} < {LOW_SCORE_THRESHOLD}: "
            "retrying optimize+validate with adjusted constraints"
        )
        yield ("rate_limit_warning", {
            "message": f"Score {overall_score}/10 below threshold — retrying with stricter constraints",
            "stage": "validate",
        })

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
                retry_constraints={
                    "min_score_target": LOW_SCORE_THRESHOLD + 2,
                    "previous_score": overall_score,
                    "focus_areas": validation.get("issues", []),
                },
            ):
                if event_type == "optimization":
                    retry_optimization_result = event_data
                    retry_optimization_result["model"] = MODEL_ROUTING["optimize"]
                yield (event_type, event_data)

            if retry_optimization_result:
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

            # Re-run validate
            yield ("stage", {"stage": "validate", "status": "started"})
            start = time.time()

            optimized_prompt = optimization_result.get("optimized_prompt", "") if optimization_result else ""
            changes_made = optimization_result.get("changes_made", []) if optimization_result else []

            validation = await run_validate(
                provider=provider,
                original_prompt=raw_prompt,
                optimized_prompt=optimized_prompt,
                changes_made=changes_made,
                codebase_context=codebase_context,
            )
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
            logger.warning(f"Retry failed: {e}. Using original validation result.")
