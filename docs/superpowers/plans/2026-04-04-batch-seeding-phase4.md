# Batch Seeding Phase 4 — Frontend UI

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Use the **frontend-design** skill and **brand-guidelines** skill for the SeedModal component.

**Goal:** Add a "Seed" button to the topology view that opens a brand-compliant modal for batch seeding, with real-time progress via SSE.

**Architecture:** SeedModal.svelte handles user input (project description or prompt list), cost estimation, and progress display. Communicates with `POST /api/seed` endpoint. Progress tracked via `seed_batch_progress` SSE events in `+page.svelte`. Activity panel already shows seed events from Phase 3.

**Tech Stack:** SvelteKit 2, Svelte 5 runes, Tailwind CSS 4, TypeScript

**Spec:** `docs/superpowers/specs/2026-04-04-explore-driven-batch-seeding-design.md`

**Depends on:** Phase 3 (REST endpoint + MCP tool)

**IMPORTANT:** Use `frontend-design` and `brand-guidelines` skills when implementing SeedModal.svelte. Industrial cyberpunk theme: dark backgrounds, 1px neon contours, no rounded corners, no shadows, no gradients, monospace font hierarchy.

---

### Task 1: API Client

**Files:**
- Create: `frontend/src/lib/api/seed.ts`

- [ ] **Step 1: Create seed API client**

```typescript
// frontend/src/lib/api/seed.ts
import { apiFetch } from './client';

export interface SeedRequest {
  project_description: string;
  workspace_path?: string | null;
  repo_full_name?: string | null;
  prompt_count?: number;
  agents?: string[] | null;
  prompts?: string[] | null;
}

export interface SeedOutput {
  status: 'completed' | 'partial' | 'failed';
  batch_id: string;
  tier: string;
  prompts_generated: number;
  prompts_optimized: number;
  prompts_failed: number;
  estimated_cost_usd: number | null;
  actual_cost_usd: number | null;
  domains_touched: string[];
  clusters_created: number;
  summary: string;
  duration_ms: number;
}

export interface SeedAgent {
  name: string;
  description: string;
  task_types: string[];
  prompts_per_run: number;
  enabled: boolean;
}

export async function seedTaxonomy(req: SeedRequest): Promise<SeedOutput> {
  return apiFetch<SeedOutput>('/seed', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function listSeedAgents(): Promise<SeedAgent[]> {
  return apiFetch<SeedAgent[]>('/seed/agents');
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/api/seed.ts
git commit -m "feat: seed API client types and functions"
```

---

### Task 2: Seed Agents List Endpoint

**Files:**
- Modify: `backend/app/routers/seed.py`

- [ ] **Step 1: Add agent list endpoint**

```python
@router.get("/api/seed/agents")
async def list_seed_agents() -> list[dict]:
    """List available seed agents with metadata."""
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

- [ ] **Step 2: Commit**

```bash
git add backend/app/routers/seed.py
git commit -m "feat: GET /api/seed/agents endpoint"
```

---

### Task 3: SeedModal Component

**Files:**
- Create: `frontend/src/lib/components/taxonomy/SeedModal.svelte`

- [ ] **Step 1: Create the modal component**

Use the **frontend-design** and **brand-guidelines** skills. The modal should follow the industrial cyberpunk theme exactly:
- Dark background (`var(--color-bg-secondary)`)
- 1px neon contours (`var(--color-border-subtle)`, accent with `var(--color-neon-cyan)`)
- No rounded corners, no shadows, no gradients
- Monospace font (`var(--font-mono)`)
- Consistent with existing modals in the codebase

**Component structure:**

```svelte
<script lang="ts">
  import { seedTaxonomy, listSeedAgents, type SeedOutput, type SeedAgent } from '$lib/api/seed';
  import { clustersStore } from '$lib/stores/clusters.svelte';

  interface Props {
    open: boolean;
    onClose: () => void;
  }

  let { open = $bindable(), onClose }: Props = $props();

  // State
  let mode = $state<'generate' | 'provide'>('generate');
  let projectDescription = $state('');
  let promptsText = $state('');
  let promptCount = $state(30);
  let agents = $state<SeedAgent[]>([]);
  let selectedAgents = $state<Set<string>>(new Set());
  let seeding = $state(false);
  let result = $state<SeedOutput | null>(null);
  let error = $state<string | null>(null);
  let progress = $state({ completed: 0, total: 0, current: '' });

  // Load agents on mount
  $effect(() => {
    if (open) {
      listSeedAgents().then(a => {
        agents = a;
        selectedAgents = new Set(a.map(ag => ag.name));
      }).catch(() => {});
    }
  });

  // SSE progress listener
  $effect(() => {
    if (!seeding) return;
    const handler = (e: Event) => {
      const data = (e as CustomEvent).detail;
      if (data?.phase === 'optimize') {
        progress = {
          completed: data.completed ?? progress.completed,
          total: data.total ?? progress.total,
          current: data.current_prompt ?? progress.current,
        };
      }
    };
    window.addEventListener('seed-batch-progress', handler);
    return () => window.removeEventListener('seed-batch-progress', handler);
  });

  async function handleSeed() {
    seeding = true;
    error = null;
    result = null;
    progress = { completed: 0, total: promptCount, current: '' };

    try {
      const req = mode === 'generate'
        ? {
            project_description: projectDescription,
            prompt_count: promptCount,
            agents: [...selectedAgents],
          }
        : {
            project_description: 'User-provided prompts',
            prompts: promptsText.split('\n').map(s => s.trim()).filter(Boolean),
          };

      result = await seedTaxonomy(req);
      clustersStore.invalidateClusters();
    } catch (err) {
      error = err instanceof Error ? err.message : 'Seed failed';
    } finally {
      seeding = false;
    }
  }
</script>
```

**Template:** Modal overlay with two tabs (Generate / Provide), agent checkboxes, prompt count slider, cost estimate display, progress bar during execution, and result summary.

The exact template and styling should be built using the frontend-design skill to match the brand precisely. Key elements:
- Tab switcher between Generate and Provide modes
- Project description textarea (Generate mode)
- Prompt list textarea (Provide mode)
- Agent checkboxes with descriptions
- Prompt count slider (5-100)
- "Estimated cost: $X.XX" display
- Start/Cancel buttons
- Progress bar during seeding
- Result card on completion

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/components/taxonomy/SeedModal.svelte
git commit -m "feat: SeedModal component with brand-compliant UI"
```

---

### Task 4: Wire Into Topology

**Files:**
- Modify: `frontend/src/lib/components/taxonomy/TopologyControls.svelte`
- Modify: `frontend/src/lib/components/taxonomy/SemanticTopology.svelte`

- [ ] **Step 1: Add Seed button to TopologyControls**

Next to the existing "Recluster" button (around line 96-102), add:

```svelte
<button
  class="tc-recluster"
  onclick={onSeed}
  use:tooltip={'Seed taxonomy with generated prompts'}
>
  Seed
</button>
```

Add `onSeed` to the Props interface.

- [ ] **Step 2: Wire SeedModal in SemanticTopology**

Import SeedModal and add state:

```svelte
import SeedModal from './SeedModal.svelte';

let seedModalOpen = $state(false);
```

Pass to TopologyControls:

```svelte
<TopologyControls
  {lodTier}
  showActivity={clustersStore.activityOpen}
  onSearch={handleSearch}
  onRecluster={handleRecluster}
  onToggleActivity={() => clustersStore.toggleActivity()}
  onSeed={() => { seedModalOpen = true; }}
/>

{#if seedModalOpen}
  <SeedModal bind:open={seedModalOpen} onClose={() => { seedModalOpen = false; }} />
{/if}
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/taxonomy/TopologyControls.svelte frontend/src/lib/components/taxonomy/SemanticTopology.svelte
git commit -m "feat: Seed button in topology controls + modal wiring"
```

---

### Task 5: SSE Handler for Seed Progress

**Files:**
- Modify: `frontend/src/routes/app/+page.svelte`

- [ ] **Step 1: Add seed_batch_progress SSE handler**

In the SSE event handler (where `taxonomy_activity` and `taxonomy_changed` are handled), add:

```typescript
if (type === 'seed_batch_progress') {
  window.dispatchEvent(new CustomEvent('seed-batch-progress', { detail: data }));
}
```

- [ ] **Step 2: Add seed events to Activity panel color mapping**

In `ActivityPanel.svelte`, add to the `decisionColor` function:

- `seed_started` → cyan (informational)
- `seed_agents_complete` → cyan (informational)
- `seed_prompt_scored` → secondary (informational)
- `seed_completed` → green (success)
- `seed_failed` → red (error)

- [ ] **Step 3: Verify frontend compiles**

```bash
cd frontend && npx svelte-check --threshold error
```

- [ ] **Step 4: Commit**

```bash
git add frontend/src/routes/app/+page.svelte frontend/src/lib/components/taxonomy/ActivityPanel.svelte
git commit -m "feat: SSE handler for seed_batch_progress + Activity panel colors"
```

---

### Task 6: End-to-End Test

- [ ] **Step 1: Restart services**

```bash
./init.sh restart
```

- [ ] **Step 2: Verify via REST API**

```bash
curl -s -X POST http://localhost:8000/api/seed \
  -H 'Content-Type: application/json' \
  -d '{"project_description": "A fintech payment processing API built with Python and FastAPI, handling Stripe integration, webhook processing, and transaction reconciliation", "prompt_count": 10}' \
  | python3 -m json.tool
```

Expected: SeedOutput with status="completed", prompts_optimized=~10, clusters_created>0, domains_touched non-empty.

- [ ] **Step 3: Verify UI**

Open browser, navigate to Pattern Graph, click "Seed" button. Verify:
- Modal opens with agent checkboxes
- Enter project description, click Start
- Progress bar updates
- Activity panel shows seed events
- On completion, topology refreshes with new clusters

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: Phase 4 complete — frontend seed UI"
```
