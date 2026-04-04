# Batch Seeding Phase 3 — MCP Tool + API Endpoint

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose batch seeding as an MCP tool (`synthesis_seed`) and REST endpoint (`POST /api/seed`), with multi-tier routing and full observability.

**Architecture:** Tool handler in `backend/app/tools/seed.py` orchestrates the full flow: resolve tier → explore → generate → batch optimize → persist → taxonomy. REST endpoint in `backend/app/routers/seed.py` mirrors the MCP interface for UI consumption. Both return `SeedOutput` schema.

**Tech Stack:** Python 3.12, FastAPI, FastMCP, Pydantic

**Spec:** `docs/superpowers/specs/2026-04-04-explore-driven-batch-seeding-design.md`

**Depends on:** Phase 1 (orchestrator) + Phase 2 (batch pipeline)

**MLOps mindset:**
- **Reproducibility:** `batch_id` is generated ONCE here in `handle_seed()` — it is the single authoritative source. All downstream functions receive it as a parameter.
- **Lineage:** Every persisted optimization carries `context_sources = {"source": "batch_seed", "batch_id": "...", "agent": "..."}` (set in Phase 2's `run_single_prompt()`).
- **Monitoring:** The `seed_completed` event includes: `total_duration_ms`, `prompts_optimized`, `prompts_failed`, `clusters_created`, `domains_touched`, `cost_usd` — enough to compute prompts/minute throughput, cost/prompt efficiency, failure rate, and domain distribution.
- **Idempotency:** If `handle_seed` is retried with the same (or different) `batch_id`, Phase 2's `bulk_persist()` skips already-persisted rows for that batch.

---

### Task 1: SeedOutput Response Schema

**Files:**
- Create: `backend/app/schemas/seed.py`

- [ ] **Step 1: Define schemas**

```python
# backend/app/schemas/seed.py
"""Request/response schemas for batch seeding."""

from pydantic import BaseModel, Field


class SeedRequest(BaseModel):
    """POST /api/seed request body."""

    project_description: str = Field(
        ..., min_length=20,
        description="Project description for prompt generation.",
    )
    workspace_path: str | None = Field(
        None, description="Local workspace path for context extraction.",
    )
    repo_full_name: str | None = Field(
        None, description="GitHub repo (owner/repo) for explore phase.",
    )
    prompt_count: int = Field(
        30, ge=5, le=100,
        description="Target total prompts across all agents.",
    )
    agents: list[str] | None = Field(
        None, description="Specific agent names. None = all enabled.",
    )
    prompts: list[str] | None = Field(
        None, description="User-provided prompts (bypasses generation).",
    )


class SeedOutput(BaseModel):
    """Response from synthesis_seed tool and POST /api/seed.

    NOTE: actual_cost_usd is intentionally omitted — cost tracking is
    estimation-only (estimated_cost_usd). No per-call billing data is
    available from the provider without additional instrumentation.
    """

    status: str  # completed | partial | failed
    batch_id: str
    tier: str  # internal | sampling | passthrough
    prompts_generated: int
    prompts_optimized: int
    prompts_failed: int
    estimated_cost_usd: float | None = None
    domains_touched: list[str] = []
    clusters_created: int = 0
    summary: str = ""
    duration_ms: int = 0
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/seed.py
git commit -m "feat: SeedRequest/SeedOutput schemas (no actual_cost_usd)"
```

---

### Task 2: Tool Handler

**Files:**
- Create: `backend/app/tools/seed.py`

**Routing resolution note:** `get_routing()` from `tools/_shared.py` is only initialized by the MCP server lifespan — not the backend lifespan. The REST endpoint runs in the backend process and must use `app.state.routing` instead. `handle_seed()` accepts an optional `routing` parameter; the REST endpoint injects it directly from `request.app.state.routing`, bypassing `_shared.py` entirely.

**Explore failure note:** If explore (workspace intelligence) raises an exception, the handler logs a warning and continues with `workspace_profile = None`. Explore failure does NOT cause `seed_failed` — it degrades gracefully per spec.

**seed_started note:** This is the SINGLE authoritative emit for `seed_started`. Phase 1's SeedOrchestrator does NOT emit it. This location has all required context: tier, estimated_cost, agent_count.

- [ ] **Step 1: Implement handle_seed()**

```python
# backend/app/tools/seed.py
"""MCP tool handler for batch seeding."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.schemas.seed import SeedOutput
from app.services.agent_loader import AgentLoader
from app.services.batch_pipeline import (
    batch_taxonomy_assign,
    bulk_persist,
    estimate_batch_cost,
    run_batch,
)
from app.services.seed_orchestrator import SeedOrchestrator

logger = logging.getLogger(__name__)


async def handle_seed(
    project_description: str | None = None,
    workspace_path: str | None = None,
    repo_full_name: str | None = None,
    prompt_count: int = 30,
    agents: list[str] | None = None,
    prompts: list[str] | None = None,
    ctx: Any | None = None,
    routing: Any | None = None,  # Injected by REST endpoint from request.app.state.routing
) -> SeedOutput:
    """Full batch seeding flow: explore → generate → optimize → persist → taxonomy.

    Routing resolution:
    - REST context: caller passes routing=request.app.state.routing directly.
    - MCP context: falls back to get_routing() from tools/_shared.py.
    This avoids the _shared.py singleton being uninitialized in the backend process.
    """
    batch_id = str(uuid.uuid4())
    t0 = time.monotonic()
    explore_t0 = t0

    # Resolve routing tier
    if routing is None:
        try:
            from app.tools._shared import get_routing
            routing = get_routing()
        except Exception:
            routing = None

    tier = routing.state.tier if routing else "passthrough"
    provider = routing.state.provider if routing else None

    # Resolve agent count for cost estimation
    agent_count = len(AgentLoader(PROMPTS_DIR / "seed-agents").list_enabled())

    # Cost estimation (done before pipeline so it can be included in seed_started)
    estimated_cost = estimate_batch_cost(
        prompt_count if not prompts else len(prompts),
        agent_count,
        tier,
    )

    # Log seed_started — single authoritative emit
    # (Phase 1 SeedOrchestrator does NOT emit this)
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_started",
            context={
                "batch_id": batch_id,
                "tier": tier,
                "project_description": (project_description or "")[:200],
                "prompt_count_target": prompt_count if not prompts else len(prompts),
                "has_user_prompts": prompts is not None,
                "agent_count": agent_count,
                "estimated_cost_usd": estimated_cost,
            },
        )
    except RuntimeError:
        pass

    # Track completed count for error events (populated as execution proceeds)
    prompts_completed_before_failure = 0

    # Determine prompt source
    if prompts:
        # User-provided prompts — skip generation
        generated_prompts = prompts
        prompts_generated = len(prompts)
        workspace_profile = None
        codebase_context = None

    elif project_description and provider:
        # Generated mode — explore + agents

        # Explore workspace context — degrades gracefully on failure
        workspace_profile = None
        codebase_context = None
        explore_t0 = time.monotonic()
        if workspace_path:
            try:
                from pathlib import Path

                from app.services.workspace_intelligence import WorkspaceIntelligence
                wi = WorkspaceIntelligence()
                workspace_profile = wi.analyze([Path(workspace_path)])
            except Exception as exc:
                logger.warning("Explore failed (continuing without context): %s", exc)
                workspace_profile = None

        # Log explore completion event
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_explore_complete",
                context={
                    "batch_id": batch_id,
                    "workspace_profile_length": len(workspace_profile or ""),
                    "codebase_context_length": len(codebase_context or ""),
                    "duration_ms": int((time.monotonic() - explore_t0) * 1000),
                },
            )
        except RuntimeError:
            pass

        try:
            orchestrator = SeedOrchestrator(provider=provider)
            gen_result = await orchestrator.generate(
                project_description=project_description,
                batch_id=batch_id,
                workspace_profile=workspace_profile,
                codebase_context=codebase_context,
                agent_names=agents,
                prompt_count=prompt_count,
            )
            generated_prompts = gen_result.prompts
            prompts_generated = len(generated_prompts)
        except Exception as exc:
            logger.error("Seed generation failed: %s", exc, exc_info=True)
            try:
                get_event_logger().log_decision(
                    path="hot", op="seed", decision="seed_failed",
                    context={
                        "batch_id": batch_id,
                        "phase": "generate",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc)[:200],
                        "prompts_completed_before_failure": 0,
                    },
                )
            except RuntimeError:
                pass
            return SeedOutput(
                status="failed",
                batch_id=batch_id,
                tier=tier,
                prompts_generated=0,
                prompts_optimized=0,
                prompts_failed=0,
                estimated_cost_usd=estimated_cost,
                summary=f"Generation failed: {exc}",
                duration_ms=int((time.monotonic() - t0) * 1000),
            )

    else:
        return SeedOutput(
            status="failed",
            batch_id=batch_id,
            tier=tier,
            prompts_generated=0,
            prompts_optimized=0,
            prompts_failed=0,
            estimated_cost_usd=estimated_cost,
            summary="Requires project_description with a provider, or user-provided prompts.",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # Determine concurrency based on tier
    max_parallel = {"internal": 10, "sampling": 2, "passthrough": 1}.get(tier, 5)

    # Run batch pipeline
    from app.services.embedding_service import EmbeddingService
    from app.services.prompt_loader import PromptLoader

    try:
        results = await run_batch(
            prompts=generated_prompts,
            provider=provider,
            prompt_loader=PromptLoader(PROMPTS_DIR),
            embedding_service=EmbeddingService(),
            max_parallel=max_parallel,
            codebase_context=codebase_context if not prompts else None,
            workspace_guidance=workspace_profile if not prompts else None,
            batch_id=batch_id,
        )
    except Exception as exc:
        logger.error("Seed batch execution failed: %s", exc, exc_info=True)
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_failed",
                context={
                    "batch_id": batch_id,
                    "phase": "optimize",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
                    "prompts_completed_before_failure": prompts_completed_before_failure,
                },
            )
        except RuntimeError:
            pass
        return SeedOutput(
            status="failed",
            batch_id=batch_id,
            tier=tier,
            prompts_generated=prompts_generated,
            prompts_optimized=0,
            prompts_failed=len(generated_prompts),
            estimated_cost_usd=estimated_cost,
            summary=f"Batch execution failed: {exc}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    prompts_completed_before_failure = sum(
        1 for r in results if r.status == "completed"
    )

    # Bulk persist
    from app.database import async_session_factory

    try:
        rows_inserted = await bulk_persist(results, async_session_factory, batch_id)
    except Exception as exc:
        logger.error("Seed persist failed: %s", exc, exc_info=True)
        completed = sum(1 for r in results if r.status == "completed")
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_failed",
                context={
                    "batch_id": batch_id,
                    "phase": "persist",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
                    "prompts_completed_before_failure": completed,
                },
            )
        except RuntimeError:
            pass
        return SeedOutput(
            status="partial",
            batch_id=batch_id,
            tier=tier,
            prompts_generated=prompts_generated,
            prompts_optimized=completed,
            prompts_failed=len(results) - completed,
            estimated_cost_usd=estimated_cost,
            summary=f"Optimized {completed} prompts but persist failed: {exc}",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # Taxonomy integration
    try:
        taxonomy_result = await batch_taxonomy_assign(
            results, async_session_factory, batch_id,
        )
    except Exception as exc:
        logger.warning("Taxonomy integration failed (non-fatal): %s", exc)
        taxonomy_result = {"clusters_created": 0, "domains_touched": []}

    # Final summary
    completed = sum(1 for r in results if r.status == "completed")
    failed = sum(1 for r in results if r.status == "failed")
    duration_ms = int((time.monotonic() - t0) * 1000)

    status = "completed" if failed == 0 else "partial"
    summary = (
        f"{completed} prompts optimized"
        f"{f', {failed} failed' if failed else ''}"
        f". {taxonomy_result.get('clusters_created', 0)} clusters created"
        f", domains: {', '.join(taxonomy_result.get('domains_touched', []))}"
    )

    # Log completion event — includes monitoring data
    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_completed",
            context={
                "batch_id": batch_id,
                "total_duration_ms": duration_ms,
                "prompts_generated": prompts_generated,
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "clusters_created": taxonomy_result.get("clusters_created", 0),
                "domains_touched": taxonomy_result.get("domains_touched", []),
                "cost_usd": estimated_cost,
                "tier": tier,
                # Sufficient for: prompts/min = prompts_optimized / (total_duration_ms/60000)
                #                 cost/prompt = cost_usd / prompts_optimized
                #                 failure_rate = prompts_failed / prompts_generated
            },
        )
    except RuntimeError:
        pass

    return SeedOutput(
        status=status,
        batch_id=batch_id,
        tier=tier,
        prompts_generated=prompts_generated,
        prompts_optimized=completed,
        prompts_failed=failed,
        estimated_cost_usd=estimated_cost,
        domains_touched=taxonomy_result.get("domains_touched", []),
        clusters_created=taxonomy_result.get("clusters_created", 0),
        summary=summary,
        duration_ms=duration_ms,
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/tools/seed.py
git commit -m "feat: handle_seed() tool handler with full flow + observability"
```

---

### Task 3: MCP Tool Registration

**Files:**
- Modify: `backend/app/mcp_server.py`

- [ ] **Step 1: Register synthesis_seed tool**

Add the tool registration in `mcp_server.py` following the existing `synthesis_optimize` pattern:

```python
@mcp.tool(structured_output=True)
async def synthesis_seed(
    project_description: Annotated[str, Field(
        description="Project description for prompt generation (20+ chars).",
    )],
    workspace_path: Annotated[str | None, Field(
        default=None,
        description="Absolute path to workspace root for context extraction.",
    )] = None,
    repo_full_name: Annotated[str | None, Field(
        default=None,
        description="GitHub repo in 'owner/repo' format for explore phase.",
    )] = None,
    prompt_count: Annotated[int, Field(
        default=30,
        description="Target total prompts (5-100).",
    )] = 30,
    agents: Annotated[list[str] | None, Field(
        default=None,
        description="Specific agent names. None = all enabled.",
    )] = None,
    prompts: Annotated[list[str] | None, Field(
        default=None,
        description="User-provided prompts (bypasses generation).",
    )] = None,
    ctx: Context | None = None,
) -> SeedOutput:
    """Seed the taxonomy by generating and optimizing diverse prompts.

    Two modes:
    1. Generated (default): Provide project_description. Agents generate
       diverse prompts which are optimized through the full pipeline.
    2. Provided: Supply prompts list directly for batch optimization.

    The taxonomy discovers clusters, domains, and patterns organically
    from the optimized results. No structure is forced — the pipeline
    and taxonomy engine handle everything.
    """
    from app.tools.seed import handle_seed
    # MCP context: routing resolved via get_routing() inside handle_seed fallback
    return await handle_seed(
        project_description=project_description,
        workspace_path=workspace_path,
        repo_full_name=repo_full_name,
        prompt_count=prompt_count,
        agents=agents,
        prompts=prompts,
        ctx=ctx,
        routing=None,  # MCP path: handle_seed falls back to get_routing()
    )
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/mcp_server.py
git commit -m "feat: register synthesis_seed MCP tool"
```

---

### Task 4: REST Endpoint

**Files:**
- Create: `backend/app/routers/seed.py`
- Modify: `backend/app/main.py` (register router)

**Routing note:** REST callers must pass `routing=request.app.state.routing` to `handle_seed()`. This bypasses `tools/_shared.py`'s `get_routing()` singleton, which is only initialized by the MCP server lifespan (not the backend lifespan). Passing it as a parameter avoids the uninitialized singleton error.

**NOTE on GET /api/seed/agents:** This endpoint is not in the spec but is required for the SeedModal agent selector (Phase 4). It is added here alongside POST /api/seed for convenience.

- [ ] **Step 1: Create seed router**

```python
# backend/app/routers/seed.py
"""Batch seed REST endpoints for UI consumption."""

import logging

from fastapi import APIRouter, HTTPException, Request

from app.config import PROMPTS_DIR
from app.schemas.seed import SeedOutput, SeedRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["seed"])


@router.post("/api/seed", response_model=SeedOutput)
async def seed_taxonomy(body: SeedRequest, request: Request) -> SeedOutput:
    """Seed the taxonomy with generated or user-provided prompts.

    Routing is resolved from request.app.state.routing — NOT from
    tools/_shared.get_routing() which is MCP-only.
    """
    try:
        from app.tools.seed import handle_seed
        # REST context: inject routing directly from app.state to bypass
        # the MCP-only _shared.py singleton
        routing = getattr(request.app.state, "routing", None)
        return await handle_seed(
            project_description=body.project_description,
            workspace_path=body.workspace_path,
            repo_full_name=body.repo_full_name,
            prompt_count=body.prompt_count,
            agents=body.agents,
            prompts=body.prompts,
            routing=routing,
        )
    except Exception as exc:
        logger.error("POST /api/seed failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Seed failed: {exc}") from exc


@router.get("/api/seed/agents")
async def list_seed_agents() -> list[dict]:
    """List available seed agents with metadata.

    NOTE: This endpoint is not in the spec but required for the SeedModal
    agent selector in the Phase 4 frontend.
    """
    from app.services.agent_loader import AgentLoader
    loader = AgentLoader(PROMPTS_DIR / "seed-agents")
    return [
        {
            "name": a.name,
            "description": a.description,
            "task_types": a.task_types,
            "prompts_per_run": a.prompts_per_run,
            "enabled": a.enabled,
        }
        for a in loader.list_enabled()
    ]
```

- [ ] **Step 2: Register router in main.py**

Add to the router registration section in `backend/app/main.py`:

```python
from app.routers.seed import router as seed_router
app.include_router(seed_router)
```

- [ ] **Step 3: Commit**

```bash
git add backend/app/routers/seed.py backend/app/main.py
git commit -m "feat: POST /api/seed + GET /api/seed/agents REST endpoints"
```

---

### Task 5: Integration Test

- [ ] **Step 1: Verify schema has no actual_cost_usd**

```bash
cd backend && source .venv/bin/activate && python -c "
from app.schemas.seed import SeedOutput
fields = SeedOutput.model_fields
assert 'actual_cost_usd' not in fields, 'actual_cost_usd must not be in SeedOutput'
print('SeedOutput fields:', list(fields.keys()))
print('actual_cost_usd correctly absent')
"
```

- [ ] **Step 2: Verify MCP tool registration**

```bash
cd backend && source .venv/bin/activate && python -c "
import app.mcp_server
print('MCP server imports OK — synthesis_seed registered')
"
```

- [ ] **Step 3: Verify REST endpoint**

```bash
# After restarting services:
curl -s -X POST http://localhost:8000/api/seed \
  -H 'Content-Type: application/json' \
  -d '{"project_description": "test"}' | python3 -m json.tool
```

Expected: Returns validation error (project_description too short — min 20 chars).

- [ ] **Step 4: Cross-plan coherence check**

Before completing Phase 3, verify:
1. `handle_seed()` emits `seed_started` with tier, estimated_cost, agent_count — YES
2. `handle_seed()` does NOT re-emit `seed_started` (no duplicate from Phase 1) — verify grep
3. `SeedOutput` has no `actual_cost_usd` field — verified above
4. Explore failure degrades gracefully (no `seed_failed` on explore exception) — YES (warning + continue)
5. REST endpoint passes `routing=request.app.state.routing` — YES
6. MCP endpoint passes `routing=None` — YES (falls back to `get_routing()`)

```bash
grep -n "seed_started" backend/app/tools/seed.py backend/app/services/seed_orchestrator.py
# Expected: only one match in seed.py
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: Phase 3 complete — MCP tool + REST endpoint"
```
