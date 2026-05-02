"""Parallel batch orchestration for seed optimization.

Extracted from ``batch_pipeline`` in Phase 3E of the code-quality sweep.

``run_batch`` fans out ``run_single_prompt`` across N prompts with a shared
``asyncio.Semaphore`` (concurrency limit: 10 internal / 5 API / 2 sampling).
The orchestrator also:

* pre-fetches the score distribution once per batch (N+1-avoidance for
  z-score normalization),
* resolves the project_id once at batch head (B1/B7 invariant — every seed
  prompt in a batch belongs to the same project scope),
* publishes ``seed_prompt_scored`` / ``seed_prompt_failed`` decision events
  per prompt, ``seed_batch_progress`` SSE events for the UI, and invokes
  the caller's ``on_progress`` callback after each completion,
* includes a one-shot 429 rate-limit backoff: the first prompt to see a
  rate-limit error halves effective parallelism (acquires an extra slot),
  sleeps 5s, then retries.

Per-prompt execution stays in ``run_single_prompt`` (which this module
imports lazily inside ``run_batch`` to avoid circular imports — the
``batch_pipeline`` module re-exports both entry points).

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.event_bus import event_bus
from app.services.optimization_service import OptimizationService
from app.services.project_service import resolve_repo_project
from app.services.prompt_loader import PromptLoader
from app.services.taxonomy.event_logger import get_event_logger

if TYPE_CHECKING:
    from app.services.batch_pipeline import (
        PendingOptimization,
        ScoreDistribution,
        SessionFactory,
    )

logger = logging.getLogger(__name__)


BATCH_CONCURRENCY_BY_TIER: dict[str, int] = {
    "internal": 10,
    "api": 5,
    "sampling": 2,
}


async def run_batch(
    prompts: list[str],
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    embedding_service: EmbeddingService,
    *,
    max_parallel: int = 10,
    codebase_context: str | None = None,
    repo_full_name: str | None = None,
    batch_id: str | None = None,
    on_progress: Any | None = None,
    session_factory: SessionFactory | None = None,
    taxonomy_engine: Any | None = None,
    domain_resolver: Any | None = None,
    tier: str = "internal",
    context_service: Any | None = None,
) -> list[PendingOptimization]:
    """Run N prompts through the pipeline in parallel.

    Args:
        prompts: Raw prompt strings to optimize.
        provider: LLM provider for all phases.
        max_parallel: Concurrency limit (10 internal, 5 API, 2 sampling).
        on_progress: Callback fired after each prompt completes.
        session_factory: Async DB session factory for enrichment queries
            (pattern injection, few-shot retrieval, adaptation state, score stats).
        taxonomy_engine: TaxonomyEngine singleton for pattern injection.
        domain_resolver: DomainResolver singleton for domain resolution.

    Returns:
        List of PendingOptimization results (some may have status="failed").
    """
    # Local import avoids the batch_pipeline → batch_orchestrator → batch_pipeline
    # circular at module load. Resolved once on first call.
    from app.services.batch_pipeline import run_single_prompt

    batch_id = batch_id or str(uuid.uuid4())
    semaphore = asyncio.Semaphore(max_parallel)
    results: list[PendingOptimization] = [None] * len(prompts)  # type: ignore

    # Rate-limit short-circuit: when one prompt hits a provider rate limit,
    # every other in-flight prompt would hit the same limit. Mark a flag,
    # let in-flight prompts finish naturally (they'll fail fast on the same
    # 429), and abort any prompts that haven't started yet so the caller
    # can surface ``reset_at`` to the user without burning the rest of
    # the budget on certain-to-fail attempts.
    _rate_limited_flag = {"hit": False, "reset_at_iso": None, "provider": None}

    # Shared event for cooperative cancellation: when set, in-flight
    # prompts that haven't started their next LLM call yet can bail
    # early to passthrough-fallback instead of burning a guaranteed-429
    # call. Set by the first prompt to detect a rate limit.
    _rate_limit_event = asyncio.Event()

    # Pre-fetch the score distribution once per batch so every prompt shares
    # a single DB round-trip instead of N redundant aggregate queries
    # (previously run inside run_single_prompt per prompt — N+1 pattern).
    # Matches pipeline.py where z-score stats are resolved once at setup.
    shared_stats: ScoreDistribution | None = None
    if session_factory is not None:
        try:
            async with session_factory() as _stats_db:
                svc = OptimizationService(_stats_db)
                shared_stats = await svc.get_score_distribution(
                    exclude_scoring_modes=["heuristic"],
                )
        except Exception as _hs_exc:
            logger.debug("Batch-level historical stats fetch failed: %s", _hs_exc)

    # B1/B7: freeze project_id once per batch so enrichment + final stamping
    # share a single resolved value. Every seed prompt in this batch belongs
    # to the same project scope.
    _batch_project_id: str | None = None
    try:
        _, _batch_project_id = await resolve_repo_project(repo_full_name)
    except Exception as _pid_exc:
        logger.debug("Batch project_id resolution failed: %s", _pid_exc)

    async def _run_with_semaphore(index: int, prompt: str) -> None:
        async def _attempt() -> PendingOptimization:
            return await run_single_prompt(
                raw_prompt=prompt,
                provider=provider,
                prompt_loader=prompt_loader,
                embedding_service=embedding_service,
                codebase_context=codebase_context,
                repo_full_name=repo_full_name,
                batch_id=batch_id,
                prompt_index=index,
                total_prompts=len(prompts),
                session_factory=session_factory,
                taxonomy_engine=taxonomy_engine,
                domain_resolver=domain_resolver,
                tier=tier,
                context_service=context_service,
                historical_stats=shared_stats,
                project_id=_batch_project_id,
                rate_limit_event=_rate_limit_event,
            )

        async with semaphore:
            # Short-circuit: if a previous prompt already hit a rate limit,
            # don't issue this one -- it would just fail with the same
            # error and waste budget. Mark the slot as a synthetic
            # rate_limited row so the caller sees a coherent batch.
            if _rate_limited_flag["hit"]:
                from uuid import uuid4 as _u
                results[index] = PendingOptimization(
                    id=str(_u()),
                    trace_id=str(_u()),
                    batch_id=batch_id,
                    raw_prompt=prompt,
                    status="failed",
                    error=(
                        f"rate_limited: aborted after sibling prompt hit "
                        f"{_rate_limited_flag['provider']} rate limit "
                        f"(reset_at={_rate_limited_flag['reset_at_iso']})"
                    )[:500],
                    routing_tier=tier,
                    rate_limit_meta={
                        "rate_limited": True,
                        "rate_limit_aborted_by_sibling": True,
                        "provider": _rate_limited_flag["provider"],
                        "reset_at_iso": _rate_limited_flag["reset_at_iso"],
                    },
                )
                if on_progress:
                    try:
                        on_progress(index, len(prompts), results[index])
                    except Exception:
                        pass
                return

            result = await _attempt()
            results[index] = result

            # Detect rate-limit flag on the result and trip the short-circuit
            # for any prompts that haven't acquired the semaphore yet.
            # rate_limit_meta is the canonical rate-limit channel
            # (separate from heuristic_flags which is a list of
            # blender divergence flags -- different shape, would crash
            # ``.get()`` here).
            flags = getattr(result, "rate_limit_meta", None) or {}
            if flags.get("rate_limited") and not _rate_limited_flag["hit"]:
                _rate_limited_flag["hit"] = True
                _rate_limited_flag["reset_at_iso"] = flags.get("reset_at_iso")
                _rate_limited_flag["provider"] = flags.get("provider")
                # Signal in-flight prompts to bail at the next phase gate.
                _rate_limit_event.set()
                logger.warning(
                    "Batch hit rate limit on prompt %d (provider=%s, "
                    "reset_at=%s) -- aborting %d remaining prompts",
                    index, flags.get("provider"), flags.get("reset_at_iso"),
                    sum(1 for r in results if r is None),
                )

            # Log per-prompt event
            try:
                decision = "seed_prompt_scored" if result.status == "completed" else "seed_prompt_failed"
                ctx: dict[str, Any] = {
                    "batch_id": batch_id,
                    "prompt_index": index,
                    "total": len(prompts),
                    "overall_score": result.overall_score,
                    "improvement_score": result.improvement_score,
                    "task_type": result.task_type,
                    "strategy_used": result.strategy_used,
                    "intent_label": result.intent_label,
                    "duration_ms": result.duration_ms,
                    "error": result.error,
                }
                if result.status == "failed":
                    ctx["recovery"] = "skipped"
                get_event_logger().log_decision(
                    path="hot", op="seed", decision=decision,
                    optimization_id=result.trace_id,
                    context=ctx,
                )
            except RuntimeError:
                pass

            # Publish seed_batch_progress to event bus for SSE frontend
            try:
                event_bus.publish("seed_batch_progress", {
                    "batch_id": batch_id,
                    "phase": "optimize",
                    "completed": sum(1 for r in results if r is not None),
                    "total": len(prompts),
                    "current_prompt": (
                        result.intent_label or result.raw_prompt[:60]
                        if result.status == "completed"
                        else result.raw_prompt[:60]
                    ),
                    "failed": sum(
                        1 for r in results if r is not None and r.status == "failed"
                    ),
                })
            except Exception as _bus_exc:
                logger.debug("seed_batch_progress publish failed: %s", _bus_exc)

            if on_progress:
                on_progress(index, len(prompts), result)

    await asyncio.gather(
        *[_run_with_semaphore(i, p) for i, p in enumerate(prompts)],
        return_exceptions=True,
    )

    # Stamp project_id on all completed results (resolved once at batch head).
    if _batch_project_id:
        for r in results:
            if r is not None and r.status == "completed":
                r.project_id = _batch_project_id

    return [r for r in results if r is not None]


__all__ = ["run_batch"]
