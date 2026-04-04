# Batch Seeding Phase 3 — MCP Tool + API Endpoint

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose batch seeding as an MCP tool (`synthesis_seed`) and REST endpoint (`POST /api/seed`), with multi-tier routing and full observability.

**Architecture:** Tool handler in `backend/app/tools/seed.py` orchestrates the full flow: resolve tier → explore → generate → batch optimize → persist → taxonomy. REST endpoint in `backend/app/routers/seed.py` mirrors the MCP interface for UI consumption. Both return `SeedOutput` schema.

**Tech Stack:** Python 3.12, FastAPI, FastMCP, Pydantic

**Spec:** `docs/superpowers/specs/2026-04-04-explore-driven-batch-seeding-design.md`

**Depends on:** Phase 1 (orchestrator) + Phase 2 (batch pipeline)

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
    """Response from synthesis_seed tool and POST /api/seed."""

    status: str  # completed | partial | failed
    batch_id: str
    tier: str  # internal | sampling | passthrough
    prompts_generated: int
    prompts_optimized: int
    prompts_failed: int
    estimated_cost_usd: float | None = None
    actual_cost_usd: float | None = None
    domains_touched: list[str] = []
    clusters_created: int = 0
    summary: str = ""
    duration_ms: int = 0
```

- [ ] **Step 2: Commit**

```bash
git add backend/app/schemas/seed.py
git commit -m "feat: SeedRequest/SeedOutput schemas"
```

---

### Task 2: Tool Handler

**Files:**
- Create: `backend/app/tools/seed.py`

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
from app.tools._shared import get_routing

logger = logging.getLogger(__name__)


async def handle_seed(
    project_description: str | None = None,
    workspace_path: str | None = None,
    repo_full_name: str | None = None,
    prompt_count: int = 30,
    agents: list[str] | None = None,
    prompts: list[str] | None = None,
    ctx: Any | None = None,
) -> SeedOutput:
    """Full batch seeding flow: explore → generate → optimize → persist → taxonomy."""
    batch_id = str(uuid.uuid4())
    t0 = time.monotonic()

    # Resolve routing tier
    routing = get_routing()
    tier = routing.state.tier if routing else "passthrough"
    provider = routing.state.provider if routing else None

    # Log start event
    try:
        from app.services.taxonomy.event_logger import get_event_logger
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_started",
            context={
                "batch_id": batch_id,
                "tier": tier,
                "project_description": (project_description or "")[:200],
                "prompt_count_target": prompt_count,
                "has_user_prompts": prompts is not None,
            },
        )
    except RuntimeError:
        pass

    # Determine prompt source
    if prompts:
        # User-provided prompts — skip generation
        generated_prompts = prompts
        prompts_generated = len(prompts)
    elif project_description and provider:
        # Generated mode — explore + agents
        try:
            # Explore workspace context
            workspace_profile = None
            codebase_context = None
            if workspace_path:
                from app.services.workspace_intelligence import WorkspaceIntelligence
                from pathlib import Path
                wi = WorkspaceIntelligence()
                workspace_profile = wi.analyze([Path(workspace_path)])

            orchestrator = SeedOrchestrator(provider=provider)
            gen_result = await orchestrator.generate(
                project_description=project_description,
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
            summary="Requires project_description with a provider, or user-provided prompts.",
            duration_ms=int((time.monotonic() - t0) * 1000),
        )

    # Cost estimation
    agent_count = len(AgentLoader(PROMPTS_DIR / "seed-agents").list_enabled())
    estimated_cost = estimate_batch_cost(len(generated_prompts), agent_count, tier)

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

    # Bulk persist
    from app.database import async_session_factory

    try:
        rows_inserted = await bulk_persist(results, async_session_factory, batch_id)
    except Exception as exc:
        logger.error("Seed persist failed: %s", exc, exc_info=True)
        try:
            get_event_logger().log_decision(
                path="hot", op="seed", decision="seed_failed",
                context={
                    "batch_id": batch_id,
                    "phase": "persist",
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:200],
                },
            )
        except RuntimeError:
            pass
        completed = sum(1 for r in results if r.status == "completed")
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

    # Log completion event
    try:
        get_event_logger().log_decision(
            path="hot", op="seed", decision="seed_completed",
            context={
                "batch_id": batch_id,
                "total_duration_ms": duration_ms,
                "prompts_optimized": completed,
                "prompts_failed": failed,
                "clusters_created": taxonomy_result.get("clusters_created", 0),
                "domains_touched": taxonomy_result.get("domains_touched", []),
                "cost_usd": estimated_cost,
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
    return await handle_seed(
        project_description=project_description,
        workspace_path=workspace_path,
        repo_full_name=repo_full_name,
        prompt_count=prompt_count,
        agents=agents,
        prompts=prompts,
        ctx=ctx,
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

- [ ] **Step 1: Create seed router**

```python
# backend/app/routers/seed.py
"""Batch seed REST endpoint for UI consumption."""

import logging

from fastapi import APIRouter, HTTPException

from app.schemas.seed import SeedOutput, SeedRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["seed"])


@router.post("/api/seed", response_model=SeedOutput)
async def seed_taxonomy(body: SeedRequest) -> SeedOutput:
    """Seed the taxonomy with generated or user-provided prompts."""
    try:
        from app.tools.seed import handle_seed
        return await handle_seed(
            project_description=body.project_description,
            workspace_path=body.workspace_path,
            repo_full_name=body.repo_full_name,
            prompt_count=body.prompt_count,
            agents=body.agents,
            prompts=body.prompts,
        )
    except Exception as exc:
        logger.error("POST /api/seed failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"Seed failed: {exc}") from exc
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
git commit -m "feat: POST /api/seed REST endpoint"
```

---

### Task 5: Integration Test

- [ ] **Step 1: Verify MCP tool registration**

```bash
cd backend && source .venv/bin/activate && python -c "
import app.mcp_server
print('MCP server imports OK — synthesis_seed registered')
"
```

- [ ] **Step 2: Verify REST endpoint**

```bash
# After restarting services:
curl -s -X POST http://localhost:8000/api/seed \
  -H 'Content-Type: application/json' \
  -d '{\"project_description\": \"test\"}' | python3 -m json.tool
```

Expected: Returns SeedOutput with `status: "failed"` (project_description too short — min 20 chars).

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "feat: Phase 3 complete — MCP tool + REST endpoint"
```
