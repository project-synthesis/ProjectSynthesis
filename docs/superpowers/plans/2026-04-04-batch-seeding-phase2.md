# Batch Seeding Phase 2 — In-Memory Batch Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the optimization pipeline for N prompts in parallel, accumulate results in memory, and persist everything in a single DB transaction — followed by batched taxonomy integration.

**Architecture:** `PendingOptimization` dataclass accumulates pipeline results in memory. Parallel `asyncio.gather` runs up to 10 prompts through analyze → optimize → score → embed simultaneously with zero DB writes. Bulk INSERT commits all rows atomically. Batched `assign_cluster()` assigns taxonomy in a second transaction. Pattern extraction deferred to warm path.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy async, Pydantic

**Spec:** `docs/superpowers/specs/2026-04-04-explore-driven-batch-seeding-design.md`

**Depends on:** Phase 1 (SeedOrchestrator produces the prompt list)

**MLOps mindset:**
- **Reproducibility:** `batch_id` is threaded through every function — every event, every persisted row, and the final report reference it. Passed in from Phase 3's `handle_seed()`.
- **Lineage:** `context_sources` on each `PendingOptimization` tracks batch origin: `{"source": "batch_seed", "batch_id": "...", "agent": "coding-implementation"}`. Set in `run_single_prompt()`.
- **Monitoring:** `seed_batch_progress` SSE events enable live progress in the frontend. `seed_persist_complete` event includes throughput data for observability dashboards.
- **Idempotency:** `bulk_persist()` checks existing optimization IDs before inserting. If a batch is interrupted and retried, prompts already persisted (same `batch_id`) are skipped.

---

### Task 1: PendingOptimization Dataclass

**Files:**
- Create: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Define the in-memory accumulation dataclass**

```python
# backend/app/services/batch_pipeline.py
"""In-memory batch optimization pipeline.

Runs N prompts through analyze → optimize → score → embed in parallel
with zero DB writes. Results accumulate as PendingOptimization objects.
Bulk persist writes everything in a single transaction.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.config import settings
from app.providers.base import LLMProvider
from app.services.embedding_service import EmbeddingService
from app.services.prompt_loader import PromptLoader

logger = logging.getLogger(__name__)


@dataclass
class PendingOptimization:
    """In-memory optimization result awaiting bulk persist."""

    id: str
    trace_id: str
    raw_prompt: str
    batch_id: str = ""  # Lineage: which batch produced this row
    optimized_prompt: str | None = None
    task_type: str | None = None
    strategy_used: str | None = None
    changes_summary: str | None = None
    score_clarity: float | None = None
    score_specificity: float | None = None
    score_structure: float | None = None
    score_faithfulness: float | None = None
    score_conciseness: float | None = None
    overall_score: float | None = None
    improvement_score: float | None = None
    scoring_mode: str | None = None
    intent_label: str | None = None
    domain: str | None = None
    domain_raw: str | None = None
    embedding: bytes | None = None
    optimized_embedding: bytes | None = None
    transformation_embedding: bytes | None = None
    models_by_phase: dict | None = None
    original_scores: dict | None = None
    score_deltas: dict | None = None
    duration_ms: int | None = None
    status: str = "completed"
    provider: str | None = None
    model_used: str | None = None
    routing_tier: str | None = None
    heuristic_flags: list | None = None
    suggestions: list | None = None
    context_sources: dict | None = None
    error: str | None = None  # Non-None if this prompt failed
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: PendingOptimization dataclass for in-memory batch accumulation"
```

---

### Task 2: Single-Prompt Pipeline Execution (No DB)

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

**IMPORTANT: This function does NOT use PipelineOrchestrator.** The real constructor is `PipelineOrchestrator(prompts_dir: Path)` — it takes a path, not a provider. Additionally, `PipelineOrchestrator.run()` requires a real `AsyncSession` for DB persistence and various DB-dependent features (adaptation, pattern injection, few-shot retrieval). Using it here with `db=None` would raise immediately.

Instead, `run_single_prompt()` makes **direct provider calls** following the same phase logic but without DB dependencies. Read `backend/app/services/pipeline.py` lines 280-900 to understand the exact call patterns before implementing:

- **Analyze phase** (~line 285): `call_provider_with_retry(provider, model=analyzer_model, system_prompt=..., user_message=analyze_msg, output_format=AnalysisResult, max_tokens=ANALYZE_MAX_TOKENS)`
- **Optimize phase** (~line 553): `call_provider_with_retry(provider, model=optimizer_model, ..., output_format=OptimizationResult, streaming=True)`
- **Score phase** (~line 639): `call_provider_with_retry(provider, model=scorer_model, ..., output_format=ScoreResult, max_tokens=SCORE_MAX_TOKENS)` — scorer uses a static system prompt (`scoring.md`), not agent-guidance
- **Embedding**: `embedding_service.aembed_single(text)` for raw, optimized, and transformation vectors

This is a lightweight extraction — 4 LLM calls + up to 3 embedding calls per prompt. No DB, no session, no orchestrator.

- [ ] **Step 1: Implement run_single_prompt()**

```python
async def run_single_prompt(
    raw_prompt: str,
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    embedding_service: EmbeddingService,
    *,
    codebase_context: str | None = None,
    workspace_guidance: str | None = None,
    batch_id: str = "",
    agent_name: str = "",
    prompt_index: int = 0,
    total_prompts: int = 1,
) -> PendingOptimization:
    """Run one prompt through analyze → optimize → score → embed in memory.

    IMPORTANT: This function does NOT use PipelineOrchestrator. It makes
    direct provider calls following the same phase logic but without DB
    dependencies. Read pipeline.py lines 280-900 to understand the exact
    call patterns for analyze/optimize/score phases before implementing.

    Returns a PendingOptimization with all fields populated.
    On any phase failure, returns a PendingOptimization with error set
    and status="failed". Never raises — errors are captured in the result.
    """
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    t0 = time.monotonic()

    try:
        from app.config import DATA_DIR
        from app.providers.base import call_provider_with_retry
        from app.schemas.pipeline_contracts import (
            AnalysisResult,
            DimensionScores,
            OptimizationResult,
            ScoreResult,
        )
        from app.services.heuristic_scorer import HeuristicScorer
        from app.services.pipeline_constants import (
            ANALYZE_MAX_TOKENS,
            SCORE_MAX_TOKENS,
            VALID_TASK_TYPES,
            compute_optimize_max_tokens,
            resolve_effective_strategy,
            semantic_upgrade_general,
        )
        from app.services.preferences import PreferencesService
        from app.services.score_blender import blend_scores
        from app.services.strategy_loader import StrategyLoader
        from app.utils.text_cleanup import sanitize_optimization_result, title_case_label

        prefs = PreferencesService(DATA_DIR)
        prefs_snapshot = prefs.load()
        analyzer_model = prefs.resolve_model("analyzer", prefs_snapshot)
        optimizer_model = prefs.resolve_model("optimizer", prefs_snapshot)
        scorer_model = prefs.resolve_model("scorer", prefs_snapshot)

        system_prompt = prompt_loader.load("agent-guidance.md")
        strategy_loader = StrategyLoader(prompt_loader._prompts_dir / "strategies")
        available_strategies = strategy_loader.format_available()

        # --- Phase 1: Analyze ---
        analyze_msg = prompt_loader.render("analyze.md", {
            "raw_prompt": raw_prompt,
            "available_strategies": available_strategies,
            "known_domains": "backend, frontend, database, data, devops, security, fullstack, general",
        })
        analysis: AnalysisResult = await call_provider_with_retry(
            provider,
            model=analyzer_model,
            system_prompt=system_prompt,
            user_message=analyze_msg,
            output_format=AnalysisResult,
            max_tokens=ANALYZE_MAX_TOKENS,
            effort=prefs.get("pipeline.analyzer_effort", prefs_snapshot) or "low",
        )

        # Semantic upgrade gate (matches pipeline.py)
        effective_task_type = semantic_upgrade_general(analysis.task_type, raw_prompt)
        if effective_task_type != analysis.task_type:
            analysis.task_type = effective_task_type

        effective_strategy = resolve_effective_strategy(
            selected_strategy=analysis.selected_strategy,
            available=strategy_loader.list_strategies(),
            blocked_strategies=set(),
            confidence=analysis.confidence,
            strategy_override=None,
            trace_id=trace_id,
            data_recommendation=None,
        )
        strategy_instructions = strategy_loader.load(effective_strategy)
        analysis_summary = (
            f"Task type: {analysis.task_type}\n"
            f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
            f"Strengths: {', '.join(analysis.strengths)}\n"
            f"Strategy: {effective_strategy}\n"
            f"Rationale: {analysis.strategy_rationale}"
        )

        # --- Phase 2: Optimize ---
        optimize_msg = prompt_loader.render("optimize.md", {
            "raw_prompt": raw_prompt,
            "analysis_summary": analysis_summary,
            "strategy_instructions": strategy_instructions,
            "codebase_guidance": workspace_guidance,
            "codebase_context": codebase_context,
            "adaptation_state": None,
            "applied_patterns": None,
            "few_shot_examples": None,
        })
        dynamic_max_tokens = compute_optimize_max_tokens(len(raw_prompt))
        optimization: OptimizationResult = await call_provider_with_retry(
            provider,
            model=optimizer_model,
            system_prompt=system_prompt,
            user_message=optimize_msg,
            output_format=OptimizationResult,
            max_tokens=dynamic_max_tokens,
            effort=prefs.get("pipeline.optimizer_effort", prefs_snapshot) or "high",
            streaming=True,
        )
        _clean_prompt, _clean_changes = sanitize_optimization_result(
            optimization.optimized_prompt, optimization.changes_summary,
        )
        optimization = OptimizationResult(
            optimized_prompt=_clean_prompt,
            changes_summary=_clean_changes,
            strategy_used=optimization.strategy_used,
        )

        # --- Phase 3: Score ---
        original_scores = None
        optimized_scores = None
        deltas = None
        scoring_mode = "skipped"
        if prefs.get("pipeline.enable_scoring", prefs_snapshot):
            import random
            original_first = random.choice([True, False])
            prompt_a = raw_prompt if original_first else optimization.optimized_prompt
            prompt_b = optimization.optimized_prompt if original_first else raw_prompt

            scoring_system = prompt_loader.load("scoring.md")
            scorer_msg = (
                f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
                f"<prompt-b>\n{prompt_b}\n</prompt-b>"
            )
            scores: ScoreResult = await call_provider_with_retry(
                provider,
                model=scorer_model,
                system_prompt=scoring_system,
                user_message=scorer_msg,
                output_format=ScoreResult,
                max_tokens=SCORE_MAX_TOKENS,
                effort=prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low",
            )
            llm_original = scores.prompt_a_scores if original_first else scores.prompt_b_scores
            llm_optimized = scores.prompt_b_scores if original_first else scores.prompt_a_scores

            heur_original = HeuristicScorer.score_prompt(raw_prompt)
            heur_optimized = HeuristicScorer.score_prompt(
                optimization.optimized_prompt, original=raw_prompt,
            )
            blended_original = blend_scores(llm_original, heur_original, None)
            blended_optimized = blend_scores(llm_optimized, heur_optimized, None)

            original_scores = blended_original.to_dimension_scores()
            optimized_scores = blended_optimized.to_dimension_scores()
            deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)
            scoring_mode = "hybrid"

        # Improvement score (matches pipeline.py weights)
        improvement_score: float | None = None
        if deltas:
            _imp = (
                deltas.get("clarity", 0) * 0.25
                + deltas.get("specificity", 0) * 0.25
                + deltas.get("structure", 0) * 0.20
                + deltas.get("faithfulness", 0) * 0.20
                + deltas.get("conciseness", 0) * 0.10
            )
            improvement_score = round(max(0.0, min(10.0, _imp)), 2)

        # --- Phase 4: Embed ---
        raw_embedding: bytes | None = None
        opt_embedding: bytes | None = None
        xfm_embedding: bytes | None = None
        try:
            raw_vec = await embedding_service.aembed_single(raw_prompt)
            raw_embedding = raw_vec.astype("float32").tobytes()
        except Exception as exc:
            logger.warning("Raw embedding failed for prompt %d: %s", prompt_index, exc)
        try:
            opt_vec = await embedding_service.aembed_single(optimization.optimized_prompt)
            opt_embedding = opt_vec.astype("float32").tobytes()
        except Exception:
            pass
        try:
            diff_text = f"{raw_prompt} → {optimization.optimized_prompt}"
            xfm_vec = await embedding_service.aembed_single(diff_text)
            xfm_embedding = xfm_vec.astype("float32").tobytes()
        except Exception:
            pass

        duration_ms = int((time.monotonic() - t0) * 1000)
        task_type = (
            analysis.task_type if analysis.task_type in VALID_TASK_TYPES else "general"
        )

        return PendingOptimization(
            id=opt_id,
            trace_id=trace_id,
            batch_id=batch_id,
            raw_prompt=raw_prompt,
            optimized_prompt=optimization.optimized_prompt,
            task_type=task_type,
            strategy_used=effective_strategy,
            changes_summary=optimization.changes_summary,
            score_clarity=optimized_scores.clarity if optimized_scores else None,
            score_specificity=optimized_scores.specificity if optimized_scores else None,
            score_structure=optimized_scores.structure if optimized_scores else None,
            score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
            score_conciseness=optimized_scores.conciseness if optimized_scores else None,
            overall_score=optimized_scores.overall if optimized_scores else None,
            improvement_score=improvement_score,
            scoring_mode=scoring_mode,
            intent_label=title_case_label(analysis.intent_label or "general"),
            domain=analysis.domain or "general",
            domain_raw=(analysis.domain or "general"),
            embedding=raw_embedding,
            optimized_embedding=opt_embedding,
            transformation_embedding=xfm_embedding,
            models_by_phase={"analyze": analyzer_model, "optimize": optimizer_model, "score": scorer_model},
            original_scores=original_scores.model_dump() if original_scores else None,
            score_deltas=deltas,
            duration_ms=duration_ms,
            status="completed",
            provider=provider.name,
            model_used=optimizer_model,
            routing_tier="internal",
            context_sources={
                "source": "batch_seed",
                "batch_id": batch_id,
                "agent": agent_name,
            },
        )

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            "Batch prompt %d/%d failed: %s", prompt_index + 1, total_prompts, exc
        )
        return PendingOptimization(
            id=opt_id,
            trace_id=trace_id,
            batch_id=batch_id,
            raw_prompt=raw_prompt,
            status="failed",
            error=str(exc)[:500],
            duration_ms=duration_ms,
        )
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: run_single_prompt() — standalone in-memory pipeline execution"
```

---

### Task 3: Parallel Batch Execution

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Implement run_batch()**

```python
async def run_batch(
    prompts: list[str],
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    embedding_service: EmbeddingService,
    *,
    max_parallel: int = 10,
    codebase_context: str | None = None,
    workspace_guidance: str | None = None,
    batch_id: str | None = None,
    on_progress: Any | None = None,
) -> list[PendingOptimization]:
    """Run N prompts through the pipeline in parallel.

    Args:
        prompts: Raw prompt strings to optimize.
        provider: LLM provider for all phases.
        max_parallel: Concurrency limit (10 internal, 5 API, 2 sampling).
        on_progress: Callback fired after each prompt completes.

    Returns:
        List of PendingOptimization results (some may have status="failed").
    """
    batch_id = batch_id or str(uuid.uuid4())
    semaphore = asyncio.Semaphore(max_parallel)
    results: list[PendingOptimization] = [None] * len(prompts)  # type: ignore

    async def _run_with_semaphore(index: int, prompt: str) -> None:
        nonlocal semaphore

        # Rate limit (429) backoff: reduce semaphore by half on first 429, retry once
        _rate_limited = False

        async def _attempt() -> PendingOptimization:
            nonlocal _rate_limited
            result = await run_single_prompt(
                raw_prompt=prompt,
                provider=provider,
                prompt_loader=prompt_loader,
                embedding_service=embedding_service,
                codebase_context=codebase_context,
                workspace_guidance=workspace_guidance,
                batch_id=batch_id,
                prompt_index=index,
                total_prompts=len(prompts),
            )
            # Check for rate limit error in result
            if (
                result.status == "failed"
                and result.error
                and ("429" in result.error or "rate_limit" in result.error.lower())
                and not _rate_limited
            ):
                _rate_limited = True
                logger.warning(
                    "Rate limit hit on prompt %d — reducing concurrency and retrying", index
                )
                # Reduce effective parallelism by acquiring an extra slot
                await semaphore.acquire()
                await asyncio.sleep(5)
                retry = await run_single_prompt(
                    raw_prompt=prompt,
                    provider=provider,
                    prompt_loader=prompt_loader,
                    embedding_service=embedding_service,
                    codebase_context=codebase_context,
                    workspace_guidance=workspace_guidance,
                    batch_id=batch_id,
                    prompt_index=index,
                    total_prompts=len(prompts),
                )
                semaphore.release()
                return retry
            return result

        async with semaphore:
            result = await _attempt()
            results[index] = result

            # Log per-prompt event
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                decision = "seed_prompt_scored" if result.status == "completed" else "seed_prompt_failed"
                ctx: dict[str, Any] = {
                    "batch_id": batch_id,
                    "prompt_index": index,
                    "total": len(prompts),
                    "overall_score": result.overall_score,
                    "improvement_score": result.improvement_score,
                    "task_type": result.task_type,
                    "strategy_used": result.strategy_used,
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
                from app.services.event_bus import event_bus
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
            except Exception:
                pass

            if on_progress:
                on_progress(index, len(prompts), result)

    await asyncio.gather(
        *[_run_with_semaphore(i, p) for i, p in enumerate(prompts)],
        return_exceptions=True,
    )

    return [r for r in results if r is not None]
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: parallel batch execution with semaphore concurrency + 429 backoff"
```

---

### Task 4: Bulk Persist

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Implement bulk_persist()**

```python
async def bulk_persist(
    results: list[PendingOptimization],
    session_factory: Any,
    batch_id: str,
) -> int:
    """Persist all completed optimizations in a single transaction.

    Returns count of rows inserted. Skips failed optimizations.
    Idempotent: skips prompts already persisted for this batch_id.
    Includes retry logic — one retry after 5s on transient failures.
    """
    t0 = time.monotonic()
    completed = [r for r in results if r.status == "completed"]

    if not completed:
        return 0

    for attempt in range(2):
        try:
            async with session_factory() as db:
                from sqlalchemy import select as sa_select

                from app.models import Optimization

                # Idempotency check: find already-persisted IDs for this batch
                existing_ids_result = await db.execute(
                    sa_select(Optimization.id).where(
                        Optimization.context_sources.op("->>")(
                            "batch_id"
                        ) == batch_id
                    )
                )
                existing_ids: set[str] = {row[0] for row in existing_ids_result}

                inserted = 0
                for pending in completed:
                    if pending.id in existing_ids:
                        logger.debug(
                            "Skipping already-persisted optimization %s (batch_id=%s)",
                            pending.id[:8], batch_id,
                        )
                        continue

                    db_opt = Optimization(
                        id=pending.id,
                        trace_id=pending.trace_id,
                        raw_prompt=pending.raw_prompt,
                        optimized_prompt=pending.optimized_prompt,
                        task_type=pending.task_type,
                        strategy_used=pending.strategy_used,
                        changes_summary=pending.changes_summary,
                        score_clarity=pending.score_clarity,
                        score_specificity=pending.score_specificity,
                        score_structure=pending.score_structure,
                        score_faithfulness=pending.score_faithfulness,
                        score_conciseness=pending.score_conciseness,
                        overall_score=pending.overall_score,
                        improvement_score=pending.improvement_score,
                        scoring_mode=pending.scoring_mode,
                        intent_label=pending.intent_label,
                        domain=pending.domain,
                        domain_raw=pending.domain_raw,
                        embedding=pending.embedding,
                        optimized_embedding=pending.optimized_embedding,
                        transformation_embedding=pending.transformation_embedding,
                        models_by_phase=pending.models_by_phase,
                        original_scores=pending.original_scores,
                        score_deltas=pending.score_deltas,
                        duration_ms=pending.duration_ms,
                        status=pending.status,
                        provider=pending.provider,
                        model_used=pending.model_used,
                        routing_tier=pending.routing_tier,
                        heuristic_flags=pending.heuristic_flags,
                        suggestions=pending.suggestions,
                        context_sources=pending.context_sources,
                    )
                    db.add(db_opt)
                    inserted += 1

                await db.commit()
            break  # success
        except Exception as exc:
            if attempt == 0:
                logger.warning("Bulk persist failed, retrying in 5s: %s", exc)
                await asyncio.sleep(5)
            else:
                raise

    duration_ms = int((time.monotonic() - t0) * 1000)

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_persist_complete",
            context={
                "batch_id": batch_id,
                "rows_inserted": inserted,
                "rows_skipped_idempotent": len(completed) - inserted,
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    logger.info("Bulk persist: %d rows in %dms", inserted, duration_ms)
    return inserted
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: bulk_persist() — single-transaction INSERT with retry + idempotency"
```

---

### Task 5: Batched Taxonomy Integration

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Implement batch_taxonomy_assign()**

```python
async def batch_taxonomy_assign(
    results: list[PendingOptimization],
    session_factory: Any,
    batch_id: str,
) -> dict[str, Any]:
    """Assign clusters for all persisted optimizations in one transaction.

    Pattern extraction is deferred (pattern_stale=True) — the warm path
    handles it after the batch completes.

    Returns summary dict with clusters_assigned, clusters_created, domains_touched.
    """
    t0 = time.monotonic()
    completed = [r for r in results if r.status == "completed" and r.embedding]
    clusters_created = 0
    domains_touched: set[str] = set()

    if not completed:
        return {"clusters_assigned": 0, "clusters_created": 0, "domains_touched": []}

    from app.services.taxonomy import get_engine
    from app.services.taxonomy.family_ops import assign_cluster

    engine = get_engine()

    async with session_factory() as db:
        for pending in completed:
            try:
                embedding = np.frombuffer(pending.embedding, dtype=np.float32)
                cluster = await assign_cluster(
                    db=db,
                    embedding=embedding,
                    label=pending.intent_label or "general",
                    domain=pending.domain or "general",
                    task_type=pending.task_type or "general",
                    overall_score=pending.overall_score,
                    embedding_index=engine._embedding_index,  # Access via property — fragile but matches existing assign_cluster pattern
                )

                # Track what was created
                if cluster.member_count == 1:
                    clusters_created += 1
                domains_touched.add(pending.domain or "general")

                # Defer pattern extraction to warm path
                from app.services.taxonomy.cluster_meta import write_meta
                cluster.cluster_metadata = write_meta(
                    cluster.cluster_metadata, pattern_stale=True,
                )

            except Exception as exc:
                logger.warning(
                    "Taxonomy assign failed for %s: %s",
                    pending.id[:8], exc,
                )

        await db.commit()

    duration_ms = int((time.monotonic() - t0) * 1000)
    domains_list = sorted(domains_touched)

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_taxonomy_complete",
            context={
                "batch_id": batch_id,
                "clusters_assigned": len(completed),
                "clusters_created": clusters_created,
                "domains_touched": domains_list,
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    # Trigger warm path (single event — debounce handles the rest)
    try:
        from app.services.event_bus import event_bus
        event_bus.publish("taxonomy_changed", {
            "trigger": "batch_seed",
            "batch_id": batch_id,
            "clusters_created": clusters_created,
        })
    except Exception:
        pass

    logger.info(
        "Taxonomy assign: %d clusters (%d new), domains=%s (%dms)",
        len(completed), clusters_created, domains_list, duration_ms,
    )

    return {
        "clusters_assigned": len(completed),
        "clusters_created": clusters_created,
        "domains_touched": domains_list,
    }
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: batched taxonomy assignment with deferred pattern extraction + pattern_stale"
```

---

### Task 6: Cost Estimation

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Implement estimate_batch_cost()**

```python
def estimate_batch_cost(
    prompt_count: int,
    agent_count: int,
    tier: str,
) -> float | None:
    """Estimate USD cost for a batch seed run.

    Returns None for sampling tier (IDE subscription covers it).
    """
    if tier == "sampling":
        return None
    if tier == "passthrough":
        return 0.0

    # Agent generation: N agents × 1 Haiku call
    # ~500 input tokens + ~500 output tokens per agent
    haiku_cost_per_call = 0.001 * 0.5 + 0.005 * 0.5  # $1/$5 per 1M tokens
    agent_cost = agent_count * haiku_cost_per_call

    # Per optimization: analyze (Sonnet) + optimize (Opus) + score (Sonnet)
    # Rough estimates: ~2K tokens in + ~2K out per phase
    sonnet_cost = 0.003 * 2 + 0.015 * 2  # $3/$15 per 1M tokens
    opus_cost = 0.005 * 2 + 0.025 * 2    # $5/$25 per 1M tokens
    per_opt = sonnet_cost + opus_cost + sonnet_cost  # analyze + optimize + score

    return round(agent_cost + prompt_count * per_opt, 2)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: batch cost estimation by routing tier"
```

---

### Task 7: Integration Test

- [ ] **Step 1: Test the full Phase 2 flow**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.services.batch_pipeline import estimate_batch_cost, PendingOptimization

# Cost estimation
print('Internal cost (30 prompts, 5 agents):', estimate_batch_cost(30, 5, 'internal'))
print('Sampling cost:', estimate_batch_cost(30, 5, 'sampling'))
print('Passthrough cost:', estimate_batch_cost(30, 5, 'passthrough'))

# PendingOptimization construction with batch_id
p = PendingOptimization(
    id='test', trace_id='test', raw_prompt='Hello',
    batch_id='batch-123',
    overall_score=8.5, status='completed',
)
print(f'PendingOptimization: status={p.status} score={p.overall_score} batch_id={p.batch_id}')
print('Phase 2 foundation OK')
"
```

- [ ] **Step 2: Cross-plan coherence check**

Verify before completing Phase 2:
1. `PendingOptimization` has `batch_id` field — YES (added in Task 1)
2. `run_single_prompt()` does NOT import or use `PipelineOrchestrator` — verify with grep
3. `run_batch()` publishes `seed_batch_progress` to `event_bus` — YES (added in Task 3)
4. `bulk_persist()` has retry loop — YES (added in Task 4)
5. `batch_taxonomy_assign()` sets `pattern_stale=True` — YES (added in Task 5)

```bash
grep -n "PipelineOrchestrator" backend/app/services/batch_pipeline.py
# Expected: zero matches
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: Phase 2 complete — in-memory batch pipeline"
```
