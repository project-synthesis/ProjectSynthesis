# Batch Seeding Phase 2 — In-Memory Batch Pipeline

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the optimization pipeline for N prompts in parallel, accumulate results in memory, and persist everything in a single DB transaction — followed by batched taxonomy integration.

**Architecture:** `PendingOptimization` dataclass accumulates pipeline results in memory. Parallel `asyncio.gather` runs up to 10 prompts through analyze → optimize → score → embed simultaneously with zero DB writes. Bulk INSERT commits all rows atomically. Batched `assign_cluster()` assigns taxonomy in a second transaction. Pattern extraction deferred to warm path.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy async, Pydantic

**Spec:** `docs/superpowers/specs/2026-04-04-explore-driven-batch-seeding-design.md`

**Depends on:** Phase 1 (SeedOrchestrator produces the prompt list)

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

- [ ] **Step 1: Implement run_single_prompt()**

This function runs one prompt through the full pipeline in memory — no DB writes. It reuses the existing pipeline's analyze, optimize, and score logic but extracts only the computation, not the persistence.

```python
async def run_single_prompt(
    raw_prompt: str,
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    embedding_service: EmbeddingService,
    *,
    codebase_context: str | None = None,
    workspace_guidance: str | None = None,
    batch_id: str | None = None,
    prompt_index: int = 0,
    total_prompts: int = 1,
) -> PendingOptimization:
    """Run one prompt through analyze → optimize → score → embed in memory.

    Returns a PendingOptimization with all fields populated.
    On any phase failure, returns a PendingOptimization with error set
    and status="failed". Never raises — errors are captured in the result.
    """
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())
    t0 = time.monotonic()

    try:
        # Import pipeline internals for phase execution
        from app.services.pipeline import PipelineOrchestrator, PipelineEvent

        # Create orchestrator but DON'T pass db — we collect events only
        orchestrator = PipelineOrchestrator(provider, prompt_loader)

        # Run the pipeline as a generator, collecting the final result
        result_data: dict[str, Any] = {}
        async for event in orchestrator.run(
            raw_prompt=raw_prompt,
            provider=provider,
            db=None,  # NO DB — in-memory only
            codebase_guidance=workspace_guidance,
            codebase_context=codebase_context,
        ):
            if event.event == "optimization_complete":
                result_data = event.data
            elif event.event == "error":
                raise RuntimeError(event.data.get("message", "Pipeline error"))

        # ... build PendingOptimization from result_data ...

    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.warning(
            "Batch prompt %d/%d failed: %s", prompt_index + 1, total_prompts, exc
        )
        return PendingOptimization(
            id=opt_id,
            trace_id=trace_id,
            raw_prompt=raw_prompt,
            status="failed",
            error=str(exc)[:500],
            duration_ms=duration_ms,
        )
```

**IMPORTANT:** The existing `PipelineOrchestrator.run()` currently requires a `db: AsyncSession` parameter for persistence. Phase 2 implementation must either:
- (a) Make `db` optional in the orchestrator and skip persistence when None
- (b) Create a lightweight wrapper that runs only the LLM phases without the DB phases
- (c) Extract the LLM phase logic into standalone functions callable without a session

Option (b) is recommended — create `run_pipeline_in_memory()` that calls the same LLM provider methods but returns results as dicts instead of persisting. Read the existing pipeline.py to determine the exact extraction points.

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: run_single_prompt() — in-memory pipeline execution"
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
    on_progress: Callable[[int, int, PendingOptimization], None] | None = None,
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
        async with semaphore:
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
            results[index] = result

            # Log per-prompt event
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                decision = "seed_prompt_scored" if result.status == "completed" else "seed_prompt_failed"
                get_event_logger().log_decision(
                    path="hot", op="seed", decision=decision,
                    optimization_id=result.trace_id,
                    context={
                        "batch_id": batch_id,
                        "prompt_index": index,
                        "total": len(prompts),
                        "overall_score": result.overall_score,
                        "improvement_score": result.improvement_score,
                        "task_type": result.task_type,
                        "strategy_used": result.strategy_used,
                        "duration_ms": result.duration_ms,
                        "error": result.error,
                    },
                )
            except RuntimeError:
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
git commit -m "feat: parallel batch execution with semaphore concurrency control"
```

---

### Task 4: Bulk Persist

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Implement bulk_persist()**

```python
async def bulk_persist(
    results: list[PendingOptimization],
    session_factory: Callable,
    batch_id: str,
) -> int:
    """Persist all completed optimizations in a single transaction.

    Returns count of rows inserted. Skips failed optimizations.
    """
    t0 = time.monotonic()
    completed = [r for r in results if r.status == "completed"]

    if not completed:
        return 0

    async with session_factory() as db:
        from app.models import Optimization

        for pending in completed:
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

        await db.commit()

    duration_ms = int((time.monotonic() - t0) * 1000)

    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_persist_complete",
            context={
                "batch_id": batch_id,
                "rows_inserted": len(completed),
                "transaction_ms": duration_ms,
            },
        )
    except RuntimeError:
        pass

    logger.info("Bulk persist: %d rows in %dms", len(completed), duration_ms)
    return len(completed)
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/services/batch_pipeline.py
git commit -m "feat: bulk_persist() — single-transaction INSERT for batch results"
```

---

### Task 5: Batched Taxonomy Integration

**Files:**
- Modify: `backend/app/services/batch_pipeline.py`

- [ ] **Step 1: Implement batch_taxonomy_assign()**

```python
async def batch_taxonomy_assign(
    results: list[PendingOptimization],
    session_factory: Callable,
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
                    embedding_index=engine._embedding_index,
                )

                # Track what was created
                if cluster.member_count == 1:
                    clusters_created += 1
                domains_touched.add(pending.domain or "general")

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
git commit -m "feat: batched taxonomy assignment with deferred pattern extraction"
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

# PendingOptimization construction
p = PendingOptimization(
    id='test', trace_id='test', raw_prompt='Hello',
    overall_score=8.5, status='completed',
)
print(f'PendingOptimization: status={p.status} score={p.overall_score}')
print('Phase 2 foundation OK')
"
```

- [ ] **Step 2: Commit**

```bash
git add -A
git commit -m "feat: Phase 2 complete — in-memory batch pipeline"
```
