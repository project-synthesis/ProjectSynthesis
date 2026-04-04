# Explore-Driven Batch Seeding

**Date:** 2026-04-04
**Status:** Design
**Scope:** Agent-driven prompt generation + in-memory batch optimization + taxonomy seeding

## Problem

The taxonomy engine needs organic data to form clusters, domains, and sub-domains. Currently data only enters through individual optimizations — one prompt at a time. There's no way to bootstrap a domain or pre-populate a taxonomy for a new project. Users starting a new project face a cold-start problem: the system has no patterns, no clusters, no learned weights.

Additionally, the quality-driven split trigger redesign requires 200+ optimizations to validate hypotheses about output coherence and pattern relevance correlation. Generating that data manually is impractical.

## Solution

An explore-driven batch seeding system that generates diverse prompts from a project description, optimizes them through the existing pipeline in parallel, and persists everything in a single transaction. The taxonomy discovers structure organically — no forced cluster creation, domain assignment, or node placement.

### Architecture Overview

```
Phase 0: EXPLORE (1 LLM call)
  → workspace profile + codebase context (reuse existing explore infrastructure)

Phase 1: GENERATE (N parallel Haiku calls, 1 per agent)
  → agents read from prompts/seed-agents/*.md (hot-reloaded, user-extensible)
  → each agent produces prompts for its specialization
  → deduplicated via embedding cosine similarity (> 0.90 threshold)

Phase 2: BATCH OPTIMIZE (parallel in-memory, zero DB writes)
  → up to 10 prompts in parallel (internal), 2 (sampling)
  → each prompt: analyze → optimize → score → embed
  → all results accumulated as list[PendingOptimization]

Phase 3: BULK PERSIST (1 DB transaction)
  → INSERT all Optimization rows + embeddings in one commit

Phase 4: TAXONOMY INTEGRATION (1 DB transaction)  
  → assign_cluster() for each optimization
  → pattern extraction deferred (pattern_stale=True)
  → single taxonomy_changed event triggers warm path via 30s debounce
```

## Agent Definition System

### File Location

`prompts/seed-agents/` — follows the `prompts/strategies/` pattern.

### Agent File Format

Each agent is a `.md` file with YAML frontmatter:

```yaml
---
name: coding-implementation
description: Generates implementation and coding task prompts
task_types: [coding, system]
phase_context: [build, maintain]
prompts_per_run: 8
enabled: true
---

You are a prompt generation agent specialized in coding and implementation tasks.

Given a project description and workspace context, generate diverse prompts
that a developer would bring to an AI assistant when working on this project.
Each prompt should represent a real task, at the natural level of detail the
developer would have. Cover different aspects of {{task_types}} work in the
{{phase_context}} phase.
```

### Frontmatter Fields

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier |
| `description` | string | yes | Shown in UI, used by orchestrator |
| `task_types` | list[string] | yes | Taxonomy task types this agent covers |
| `phase_context` | list[string] | yes | SDLC phases informing prompt generation |
| `prompts_per_run` | int | no | Prompts to generate per run (default 6) |
| `enabled` | bool | no | Toggle without deleting (default true) |

### Default Agents

1. **`coding-implementation.md`** — implementation tasks, bug fixes, feature code, refactoring
2. **`architecture-design.md`** — system design, API design, data modeling, infrastructure decisions
3. **`analysis-debugging.md`** — performance analysis, debugging, trade-off evaluation, code review
4. **`testing-quality.md`** — test writing, CI/CD setup, quality assurance, monitoring
5. **`documentation-communication.md`** — READMEs, API docs, team communication, changelogs

### Hot-Reload

File watcher (same `watchdog` pattern as strategy watcher) detects add/modify/delete in `prompts/seed-agents/`. Agent registry updates immediately. Users add custom agents by dropping a `.md` file.

## Orchestrator

**New service:** `backend/app/services/seed_orchestrator.py`

### Explore Phase

Runs ONCE per batch. Reuses existing `WorkspaceIntelligence.analyze()` for local workspace context and `CodebaseExplorer.explore()` for GitHub repo context. Output shared across all agents.

### Agent Dispatch

Each enabled agent receives the project context and generates prompts via a single LLM call (Haiku for cost efficiency). Agents run in parallel via `asyncio.gather`. Each returns a structured list of prompt strings.

**Prompt template:** `prompts/seed.md` — renders with project_description, workspace_profile, codebase_context, task_types, phase_context, prompts_per_run.

### Deduplication

Before pipeline execution, all generated prompts are embedded (local model) and checked for pairwise cosine similarity. Prompts with similarity > 0.90 are deduplicated (keep the first occurrence).

## In-Memory Batch Pipeline

### Zero-DB Execution

Phases analyze, optimize, score, and embed run entirely in memory. No database reads or writes until bulk persist. This eliminates SQLite contention completely during the LLM-heavy portion of the batch.

### Parallel Execution

Multiple prompts run through the pipeline simultaneously. Concurrency limit depends on routing tier:

| Tier | Max Parallel | Reason |
|---|---|---|
| Internal (CLI) | 10 | CLI subprocess pool capacity |
| Internal (API) | 5 | API rate limits |
| Sampling | 2 | IDE LLM shared with user's work |
| Passthrough | N/A | User provides pre-optimized prompts |

Each prompt's three phases are sequential (analyze → optimize → score) but multiple prompts execute in parallel.

### Accumulation

Results accumulate as `list[PendingOptimization]` — a dataclass holding all fields needed for the Optimization DB record plus computed embeddings. No ORM objects until persist phase.

## Bulk Persist

Single database transaction:

```python
async with session_factory() as db:
    for pending in accumulated_results:
        db_opt = Optimization(**pending.to_dict())
        db.add(db_opt)
    await db.commit()
```

One SQLite write lock, milliseconds. All optimizations written atomically.

## Taxonomy Integration

After bulk persist, a second transaction runs the hot path for each optimization:

```python
async with session_factory() as db:
    for opt in persisted_optimizations:
        cluster = await assign_cluster(db, embedding, label, domain, task_type, score)
        # Pattern extraction deferred — set pattern_stale=True
    await db.commit()
```

Cluster assignment runs sequentially (shared embedding index). Pattern extraction is deferred to the warm path's Phase 4 (Refresh) via `pattern_stale=True` — the same pattern used by split children.

After commit, a single `taxonomy_changed` event triggers the warm path (with 30s debounce). The warm path runs ONE cycle to reconcile member counts, refresh labels/patterns, and discover domains/sub-domains.

## Multi-Tier Routing

### Internal Tier

Full pipeline with internal provider. Parallel execution (up to 10). Cost estimated from model pricing. This is the primary path.

### Sampling Tier

Full pipeline via IDE's LLM (`SamplingLLMAdapter`). Lower parallelism (2). Cost = $0 (IDE subscription). The MCP `ctx.session.create_message()` provides the LLM.

### Passthrough Tier

No LLM available for generation. User provides `prompts` list directly (bypassing explore and generate phases). Each prompt gets assembled with context templates and returned for external processing via `synthesis_save_result`. Batch tracking still applies.

## MCP Tool Interface

**Tool:** `synthesis_seed`

**Parameters:**
- `project_description: str` — what the project is about (required for generated mode)
- `workspace_path: str | None` — local workspace for context extraction
- `repo_full_name: str | None` — GitHub repo for explore phase
- `prompt_count: int = 30` — target total prompts across all agents
- `agents: list[str] | None` — specific agent names (None = all enabled)
- `prompts: list[str] | None` — user-provided prompts (passthrough/override mode)

**Response:**

```python
class SeedOutput(BaseModel):
    status: str                      # completed | partial | failed
    batch_id: str
    tier: str                        # internal | sampling | passthrough
    prompts_generated: int
    prompts_optimized: int
    prompts_failed: int
    estimated_cost_usd: float | None # None for sampling
    domains_touched: list[str]
    clusters_created: int
    summary: str                     # Human-readable result
```

If both `project_description` and `prompts` are provided, `prompts` takes precedence.

## Frontend UI

### Entry Point

"Seed" button in `TopologyControls.svelte` (next to "Recluster"). Opens a modal.

### Seed Modal

Follows brand guidelines (industrial cyberpunk: dark bg, 1px neon contours, no rounded corners, monospace). Built using frontend-design skill.

**Content:**
- Text area: project description (generated mode)
- Or: paste prompt list (provided mode)
- Agent selector: checkboxes for active agents, all enabled by default
- Prompt count slider: 10-50 (default 30)
- Cost estimate: shown before starting, based on resolved tier
- Start button

**Progress:**
- Progress bar: "Optimizing 12/30..."
- Activity panel shows real-time events (score, assign, extract)
- Uses `seed_batch_progress` SSE events from orchestrator

**Completion:**
- Summary: "30 prompts optimized. 5 clusters created. 3 domains touched."
- Topology refreshes via taxonomy_changed SSE
- Close modal to see populated taxonomy

### REST Endpoint

`POST /api/seed` — mirrors MCP tool interface for direct UI consumption.

## SSE Events

New event type: `seed_batch_progress`

```json
{
  "batch_id": "...",
  "phase": "generate" | "optimize" | "persist" | "taxonomy",
  "completed": 12,
  "total": 30,
  "current_prompt": "How do I...",
  "failed": 0
}
```

Published to existing event bus. Frontend handles in `+page.svelte` SSE handler.

## Phase Decomposition

### Phase 1: Agent Definition System + Orchestrator Core
- `prompts/seed-agents/` with 5 default agent files
- Agent loader with frontmatter parsing
- File watcher for hot-reload
- `SeedOrchestrator` service: explore → generate → deduplicate
- `prompts/seed.md` generation template
- Unit tests for agent loading and prompt generation

### Phase 2: In-Memory Batch Pipeline
- `PendingOptimization` dataclass for accumulation
- Parallel pipeline execution (analyze → optimize → score → embed)
- Bulk persist in single transaction
- Batched taxonomy hot path
- Cost estimation
- `seed_batch_progress` SSE events

### Phase 3: MCP Tool + API Endpoint
- `synthesis_seed` MCP tool in `mcp_server.py`
- `handle_seed()` in `backend/app/tools/seed.py`
- `POST /api/seed` REST endpoint
- `SeedOutput` response schema
- Multi-tier routing (internal/sampling/passthrough)

### Phase 4: Frontend UI
- Seed button in TopologyControls
- Seed modal (brand guidelines, frontend-design skill)
- Agent selector + prompt count + cost estimate
- Progress indicator via SSE
- Completion summary

## Files Created/Modified

| File | Phase | Change |
|---|---|---|
| `prompts/seed-agents/*.md` (5 files) | 1 | Default agent definitions |
| `prompts/seed.md` | 1 | Generation template |
| `backend/app/services/seed_orchestrator.py` | 1 | Orchestrator service |
| `backend/app/services/agent_loader.py` | 1 | Agent file parser + watcher |
| `backend/app/services/batch_pipeline.py` | 2 | In-memory batch execution |
| `backend/app/tools/seed.py` | 3 | MCP tool handler |
| `backend/app/routers/seed.py` | 3 | REST endpoint |
| `backend/app/schemas/seed.py` | 3 | Request/response schemas |
| `backend/app/mcp_server.py` | 3 | Tool registration |
| `frontend/src/lib/components/taxonomy/SeedModal.svelte` | 4 | Seed UI |
| `frontend/src/lib/components/taxonomy/TopologyControls.svelte` | 4 | Seed button |
| `frontend/src/lib/api/seed.ts` | 4 | API client |
| `frontend/src/routes/app/+page.svelte` | 4 | SSE handler for seed_batch_progress |

## Verification

Per phase:
1. Generate prompts from a project description, verify diversity and deduplication
2. Batch optimize 10 prompts, verify single DB transaction, verify taxonomy assignment
3. Call MCP tool end-to-end, verify response schema, test all 3 tiers
4. Click Seed button, verify modal flow, verify Activity panel shows events

Cross-phase:
- Seed a "fintech API" project with 30 prompts, verify clusters form organically in expected domains
- Check sub-domain discovery triggers if cluster-count threshold is met
- Verify warm path runs once after batch (not 30 times)
- Verify pattern extraction happens on subsequent warm cycle
