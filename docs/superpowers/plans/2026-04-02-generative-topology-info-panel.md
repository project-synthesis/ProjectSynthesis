# Generative Topology Info Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static TopologyControls metrics section with a context-aware panel that adapts based on selection state (system/cluster/domain), surfacing output coherence, silhouette, and blend weight data with intelligent tooltips.

**Architecture:** Backend adds output_coherence and blend weight fields to `_node_to_dict` + schemas. Frontend extracts the health section from TopologyControls into a new TopologyInfoPanel component with 5-row grid layout that switches content based on `clustersStore.selectedClusterId`. Tooltips use existing `use:tooltip` action + centralized definitions in `metric-tooltips.ts`.

**Tech Stack:** Python/Pydantic (backend schemas), SvelteKit 2 / Svelte 5 runes, TypeScript, Tailwind CSS 4

---

### Task 1: Backend — Add New Fields to Schema and Engine

**Files:**
- Modify: `backend/app/schemas/clusters.py`
- Modify: `backend/app/services/taxonomy/engine.py` (`_node_to_dict` at line 2136)

- [ ] **Step 1: Add fields to ClusterDetail and ClusterNode schemas**

In `backend/app/schemas/clusters.py`, add to `ClusterNode` class (after `preferred_strategy` line 27):

```python
    output_coherence: float | None = None
    blend_w_raw: float | None = None
    blend_w_optimized: float | None = None
    blend_w_transform: float | None = None
    split_failures: int = 0
```

Add the same fields to `ClusterDetail` class (after `separation` line 63):

```python
    output_coherence: float | None = None
    blend_w_raw: float | None = None
    blend_w_optimized: float | None = None
    blend_w_transform: float | None = None
    split_failures: int = 0
```

- [ ] **Step 2: Populate fields in _node_to_dict**

In `backend/app/services/taxonomy/engine.py`, update `_node_to_dict` (line 2136). Add after `"created_at"` line:

```python
    @staticmethod
    def _node_to_dict(node: PromptCluster) -> dict:
        from app.services.taxonomy._constants import (
            CLUSTERING_BLEND_W_OPTIMIZED,
            CLUSTERING_BLEND_W_RAW,
            CLUSTERING_BLEND_W_TRANSFORM,
        )
        from app.services.taxonomy.cluster_meta import read_meta

        meta = read_meta(node.cluster_metadata)
        out_coh = meta.get("output_coherence")

        # Compute effective blend weights for this cluster
        w_opt = CLUSTERING_BLEND_W_OPTIMIZED
        if out_coh is not None and out_coh < 0.5:
            w_opt = CLUSTERING_BLEND_W_OPTIMIZED * max(0.25, out_coh / 0.5)
        w_raw = 1.0 - w_opt - CLUSTERING_BLEND_W_TRANSFORM

        return {
            "id": node.id,
            "label": node.label,
            "parent_id": node.parent_id,
            "state": node.state,
            "domain": node.domain,
            "task_type": node.task_type,
            "member_count": node.member_count or 0,
            "coherence": node.coherence,
            "separation": node.separation,
            "stability": node.stability,
            "persistence": node.persistence,
            "color_hex": node.color_hex,
            "umap_x": node.umap_x,
            "umap_y": node.umap_y,
            "umap_z": node.umap_z,
            "usage_count": node.usage_count or 0,
            "avg_score": node.avg_score,
            "preferred_strategy": node.preferred_strategy,
            "promoted_at": node.promoted_at.isoformat() if node.promoted_at else None,
            "created_at": node.created_at.isoformat() if node.created_at else None,
            "output_coherence": out_coh,
            "blend_w_raw": round(w_raw, 4),
            "blend_w_optimized": round(w_opt, 4),
            "blend_w_transform": CLUSTERING_BLEND_W_TRANSFORM,
            "split_failures": meta.get("split_failures", 0),
        }
```

- [ ] **Step 3: Run backend tests**

Run: `cd backend && source .venv/bin/activate && pytest --tb=short -q 2>&1 | tail -5`

Expected: All pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/clusters.py backend/app/services/taxonomy/engine.py
git commit -m "feat: add output_coherence and blend weights to cluster API"
```

---

### Task 2: Frontend — Update TypeScript Interfaces

**Files:**
- Modify: `frontend/src/lib/api/clusters.ts`

- [ ] **Step 1: Add fields to ClusterNode interface**

In `frontend/src/lib/api/clusters.ts`, add to `ClusterNode` (after `preferred_strategy` line 29):

```typescript
  output_coherence: number | null;
  blend_w_raw: number | null;
  blend_w_optimized: number | null;
  blend_w_transform: number | null;
  split_failures: number;
```

- [ ] **Step 2: Add fields to ClusterDetail interface**

In the same file, add to `ClusterDetail` (after `separation` line 63):

```typescript
  output_coherence: number | null;
  blend_w_raw: number | null;
  blend_w_optimized: number | null;
  blend_w_transform: number | null;
  split_failures: number;
```

- [ ] **Step 3: Type check**

Run: `cd frontend && npx svelte-check 2>&1 | tail -5`

Expected: 0 errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/clusters.ts
git commit -m "feat: add output_coherence and blend weight fields to cluster TS types"
```

---

### Task 3: Add Tooltip Definitions

**Files:**
- Modify: `frontend/src/lib/utils/metric-tooltips.ts`

- [ ] **Step 1: Add TOPOLOGY_PANEL_TOOLTIPS**

In `frontend/src/lib/utils/metric-tooltips.ts`, add after `TAXONOMY_TOOLTIPS` (after line 22):

```typescript
// ---------------------------------------------------------------------------
// Topology info panel (context-aware metrics overlay)
// ---------------------------------------------------------------------------

export const TOPOLOGY_PANEL_TOOLTIPS = {
  silhouette:
    'How well-defined the clusters are overall — higher means each cluster is internally tight and clearly separate from neighbors',
  output_coherence:
    'Do similar prompts in this group produce similar optimizations? Low means the group mixes different optimization styles',
  blend_raw:
    'How much the raw topic signal influences this cluster — increases when output coherence is low',
  blend_optimized:
    'How much the optimization output signal influences this cluster — reduced when outputs diverge',
  blend_transform:
    'How much the improvement-technique signal influences this cluster',
  coverage:
    'What fraction of all optimizations belong to an active cluster. 1.0 = everything is organized',
  avg_score_domain:
    'Average quality score across all prompts in this domain',
  members_domain:
    'Total prompt count across all clusters in this domain',
  split_failures:
    'How many times the system tried and failed to split this cluster. Resets on growth or recluster',
};
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/utils/metric-tooltips.ts
git commit -m "feat: add topology panel tooltip definitions"
```

---

### Task 4: Add Insight Generator

**Files:**
- Modify: `frontend/src/lib/utils/taxonomy-health.ts`

- [ ] **Step 1: Add generatePanelInsight function**

In `frontend/src/lib/utils/taxonomy-health.ts`, add after the `capitalize` function (after line 195):

```typescript
// ---------------------------------------------------------------------------
// Context-aware insight text for the topology info panel
// ---------------------------------------------------------------------------

export type PanelMode = 'system' | 'cluster' | 'domain';

export interface PanelInsightInput {
  mode: PanelMode;
  stats: ClusterStats | null;
  detail: {
    coherence: number | null;
    separation: number | null;
    output_coherence: number | null;
    blend_w_optimized: number | null;
    member_count: number;
    split_failures: number;
    label: string;
    state: string;
  } | null;
  domainChildCount?: number;
  domainBelowFloor?: number;
  topPattern?: string;
  topPatternCount?: number;
}

export function generatePanelInsight(input: PanelInsightInput): string {
  const { mode, stats, detail } = input;

  if (mode === 'system') {
    const parts: string[] = [];
    const active = stats?.nodes?.active ?? 0;
    const domains = stats?.total_clusters ?? 0;
    if (active > 0) parts.push(`${active} active clusters`);

    const dbcv = stats?.q_dbcv ?? 0;
    if (dbcv > 0) {
      parts.push(`silhouette ${dbcv.toFixed(2)}`);
    } else {
      parts.push('silhouette pending (run recluster)');
    }

    const lastCold = stats?.last_cold_path;
    if (lastCold) {
      const ago = formatTimeAgo(lastCold);
      parts.push(`last recluster ${ago}`);
    }
    return capitalize(parts.join('. ')) + '.';
  }

  if (mode === 'cluster' && detail) {
    const parts: string[] = [];
    const outCoh = detail.output_coherence;
    const coh = detail.coherence ?? 1.0;
    const blendOpt = detail.blend_w_optimized;

    if (coh >= 0.7 && (outCoh == null || outCoh >= 0.5)) {
      parts.push('Well-focused group');
      if (blendOpt != null && blendOpt >= 0.15) {
        parts.push('all embedding signals contribute');
      }
    } else if (outCoh != null && outCoh < 0.25) {
      parts.push('Members produce divergent outputs');
      if (blendOpt != null) {
        parts.push(`optimized signal reduced to ${Math.round(blendOpt * 100)}%`);
      }
    } else if (coh < 0.5) {
      parts.push('Low coherence — prompts in this group are quite different');
      if (detail.split_failures >= 3) {
        parts.push('split attempts exhausted');
      }
    } else {
      parts.push(`${detail.member_count} members`);
      if (outCoh != null && outCoh < 0.5) {
        parts.push('moderate output diversity');
      }
    }
    return capitalize(parts.join('. ')) + '.';
  }

  if (mode === 'domain') {
    const parts: string[] = [];
    const childCount = input.domainChildCount ?? 0;
    if (childCount > 0) parts.push(`${childCount} clusters`);
    const belowFloor = input.domainBelowFloor ?? 0;
    if (belowFloor > 0) {
      parts.push(`${belowFloor} below coherence floor`);
    }
    if (input.topPattern && input.topPatternCount) {
      const shortPattern = input.topPattern.length > 40
        ? input.topPattern.slice(0, 37) + '...'
        : input.topPattern;
      parts.push(`top pattern: ${shortPattern} (x${input.topPatternCount})`);
    }
    return capitalize(parts.join('. ')) + '.';
  }

  return '';
}

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}
```

Note: The `capitalize` function already exists at line 193 — reuse it.

- [ ] **Step 2: Type check**

Run: `cd frontend && npx svelte-check 2>&1 | tail -5`

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/utils/taxonomy-health.ts
git commit -m "feat: add generatePanelInsight() for context-aware topology text"
```

---

### Task 5: Create TopologyInfoPanel Component

**Files:**
- Create: `frontend/src/lib/components/taxonomy/TopologyInfoPanel.svelte`

- [ ] **Step 1: Create the component**

Create `frontend/src/lib/components/taxonomy/TopologyInfoPanel.svelte`. This is the core deliverable — a 5-row adaptive panel. Full code:

```svelte
<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { qHealthColor } from '$lib/utils/colors';
  import { assessTaxonomyHealth, generatePanelInsight } from '$lib/utils/taxonomy-health';
  import type { PanelMode } from '$lib/utils/taxonomy-health';
  import { TAXONOMY_TOOLTIPS, TOPOLOGY_PANEL_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { tooltip } from '$lib/actions/tooltip';
  import ScoreSparkline from '$lib/components/refinement/ScoreSparkline.svelte';

  const stats = $derived(clustersStore.taxonomyStats);
  const detail = $derived(clustersStore.clusterDetail);
  const selectedId = $derived(clustersStore.selectedClusterId);

  // Determine panel mode
  const mode: PanelMode = $derived.by(() => {
    if (!selectedId || !detail) return 'system';
    if (detail.state === 'domain') return 'domain';
    return 'cluster';
  });

  // System mode data
  const qSystem = $derived(stats?.q_system ?? null);
  const qColor = $derived(qHealthColor(qSystem));
  const health = $derived(stats ? assessTaxonomyHealth(stats) : null);
  const sparkline = $derived(stats?.q_sparkline ?? []);
  const hasSparkline = $derived(sparkline.length >= 2);
  const silhouette = $derived(stats?.q_dbcv ?? null);
  const coverage = $derived(stats?.q_coverage ?? null);
  const coherence = $derived(stats?.q_coherence ?? null);
  const separation = $derived(stats?.q_separation ?? null);

  // Cluster mode data
  const clusterCoh = $derived(detail?.coherence ?? null);
  const clusterSep = $derived(detail?.separation ?? null);
  const outCoh = $derived(detail?.output_coherence ?? null);
  const avgScore = $derived(detail?.avg_score ?? null);
  const blendRaw = $derived(detail?.blend_w_raw ?? null);
  const blendOpt = $derived(detail?.blend_w_optimized ?? null);
  const blendTrans = $derived(detail?.blend_w_transform ?? null);

  // Domain mode: aggregate from children
  const domainChildren = $derived(detail?.children ?? []);
  const domainChildCount = $derived(domainChildren.length);
  const domainAvgCoh = $derived.by(() => {
    const vals = domainChildren.filter(c => c.coherence != null).map(c => c.coherence!);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const domainAvgSep = $derived.by(() => {
    const vals = domainChildren.filter(c => c.separation != null).map(c => c.separation!);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const domainAvgScore = $derived.by(() => {
    const vals = domainChildren.filter(c => c.avg_score != null).map(c => c.avg_score!);
    return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  });
  const domainTotalMembers = $derived(
    domainChildren.reduce((sum, c) => sum + (c.member_count || 0), 0)
  );
  const domainBelowFloor = $derived(
    domainChildren.filter(c => c.coherence != null && c.coherence < 0.5).length
  );

  // Insight text
  const insight = $derived(generatePanelInsight({
    mode,
    stats,
    detail: detail ? {
      coherence: detail.coherence,
      separation: detail.separation,
      output_coherence: detail.output_coherence ?? null,
      blend_w_optimized: detail.blend_w_optimized ?? null,
      member_count: detail.member_count,
      split_failures: detail.split_failures ?? 0,
      label: detail.label,
      state: detail.state,
    } : null,
    domainChildCount,
    domainBelowFloor,
  }));

  function fmt(v: number | null): string {
    if (v == null) return '--';
    return v.toFixed(2);
  }

  function pct(v: number | null): string {
    if (v == null) return '--';
    return Math.round(v * 100) + '%';
  }
</script>

<div class="ip-panel">
  <!-- ROW 1: Identity -->
  <div class="ip-row ip-identity">
    {#if mode === 'system'}
      <div class="ip-identity-row">
        <span class="ip-q" use:tooltip={TAXONOMY_TOOLTIPS.q_system}>
          <span class="ip-q-label">Q</span>
          <span class="ip-q-value" style="color: {qColor}">{qSystem != null ? qSystem.toFixed(3) : '--'}</span>
        </span>
        {#if hasSparkline}
          <span class="ip-sparkline"><ScoreSparkline scores={sparkline} width={64} height={14} minRange={0.2} /></span>
        {/if}
        {#if health}
          <span class="ip-severity" style="background: {health.color}"></span>
        {/if}
      </div>
      {#if health}
        <div class="ip-headline" style="color: {health.color}" use:tooltip={health.detail}>{health.headline}</div>
      {/if}
    {:else if mode === 'cluster' && detail}
      <div class="ip-identity-row">
        <span class="ip-name" title={detail.label}>{detail.label}</span>
        <span class="ip-member-count">{detail.member_count}m</span>
      </div>
      <div class="ip-badges">
        <span class="ip-badge ip-badge-domain">{detail.domain.toUpperCase()}</span>
        <span class="ip-badge ip-badge-state">{detail.state.toUpperCase()}</span>
        {#if avgScore != null}
          <span class="ip-badge-score">{avgScore.toFixed(1)}</span>
        {/if}
      </div>
    {:else if mode === 'domain' && detail}
      <div class="ip-identity-row">
        <span class="ip-domain-name">{detail.label.toUpperCase()}</span>
        <span class="ip-member-count">{domainChildCount} clusters</span>
      </div>
    {/if}
  </div>

  <!-- ROW 2: 2x2 Metric Grid -->
  <div class="ip-row ip-grid">
    {#if mode === 'system'}
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
        <span class="ip-cell-label">COH</span>
        <span class="ip-cell-value">{fmt(coherence)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
        <span class="ip-cell-label">SEP</span>
        <span class="ip-cell-value">{fmt(separation)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.silhouette}>
        <span class="ip-cell-label ip-cell-label-accent">SIL</span>
        <span class="ip-cell-value">{fmt(silhouette)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.coverage}>
        <span class="ip-cell-label">COV</span>
        <span class="ip-cell-value">{fmt(coverage)}</span>
      </div>
    {:else if mode === 'cluster'}
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
        <span class="ip-cell-label">COH</span>
        <span class="ip-cell-value" class:ip-warn={clusterCoh != null && clusterCoh < 0.5}>{fmt(clusterCoh)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
        <span class="ip-cell-label">SEP</span>
        <span class="ip-cell-value">{fmt(clusterSep)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.output_coherence}>
        <span class="ip-cell-label ip-cell-label-accent">OUT</span>
        <span class="ip-cell-value" class:ip-warn={outCoh != null && outCoh < 0.25} class:ip-caution={outCoh != null && outCoh >= 0.25 && outCoh < 0.5}>{fmt(outCoh)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.q_system}>
        <span class="ip-cell-label">SCORE</span>
        <span class="ip-cell-value ip-cell-value-green">{avgScore != null ? avgScore.toFixed(1) : '--'}</span>
      </div>
    {:else if mode === 'domain'}
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.coherence}>
        <span class="ip-cell-label">AVG COH</span>
        <span class="ip-cell-value">{fmt(domainAvgCoh)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TAXONOMY_TOOLTIPS.separation}>
        <span class="ip-cell-label">AVG SEP</span>
        <span class="ip-cell-value">{fmt(domainAvgSep)}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.avg_score_domain}>
        <span class="ip-cell-label">AVG SCORE</span>
        <span class="ip-cell-value ip-cell-value-green">{domainAvgScore != null ? domainAvgScore.toFixed(1) : '--'}</span>
      </div>
      <div class="ip-cell" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.members_domain}>
        <span class="ip-cell-label">MEMBERS</span>
        <span class="ip-cell-value">{domainTotalMembers}</span>
      </div>
    {/if}
  </div>

  <!-- ROW 3: Visual Bar -->
  <div class="ip-row ip-bar">
    {#if mode === 'system' && hasSparkline}
      <!-- System: no extra bar needed, sparkline is in Row 1 -->
      <div class="ip-bar-empty"></div>
    {:else if mode === 'cluster' && blendRaw != null}
      <div class="ip-bar-label">BLEND</div>
      <div class="ip-blend-bar" use:tooltip={`Raw ${pct(blendRaw)} / Optimized ${pct(blendOpt)} / Transform ${pct(blendTrans)}`}>
        <div class="ip-blend-seg ip-blend-raw" style="flex: {(blendRaw ?? 0.65) * 100}" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.blend_raw}>
          {#if (blendRaw ?? 0) > 0.3}<span>RAW {pct(blendRaw)}</span>{/if}
        </div>
        <div class="ip-blend-seg ip-blend-opt" style="flex: {(blendOpt ?? 0.20) * 100}" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.blend_optimized}>
          {#if (blendOpt ?? 0) > 0.1}<span>O {pct(blendOpt)}</span>{/if}
        </div>
        <div class="ip-blend-seg ip-blend-trans" style="flex: {(blendTrans ?? 0.15) * 100}" use:tooltip={TOPOLOGY_PANEL_TOOLTIPS.blend_transform}>
          {#if (blendTrans ?? 0) > 0.1}<span>T {pct(blendTrans)}</span>{/if}
        </div>
      </div>
    {:else if mode === 'domain' && domainChildren.length > 0}
      <div class="ip-bar-label">TASKS</div>
      {@const taskCounts = (() => {
        const counts: Record<string, number> = {};
        for (const c of domainChildren) {
          const t = c.task_type || 'general';
          counts[t] = (counts[t] || 0) + (c.member_count || 0);
        }
        return Object.entries(counts).sort((a, b) => b[1] - a[1]);
      })()}
      <div class="ip-task-bar">
        {#each taskCounts.slice(0, 4) as [type, count]}
          <div class="ip-task-seg" style="flex: {count}" use:tooltip={`${type}: ${count} members`}>
            {#if count > 2}<span>{type.slice(0, 3)}</span>{/if}
          </div>
        {/each}
      </div>
    {:else}
      <div class="ip-bar-empty"></div>
    {/if}
  </div>

  <!-- ROW 4: Insight -->
  <div class="ip-row ip-insight">
    <p class="ip-insight-text">{insight}</p>
  </div>
</div>

<style>
  .ip-panel {
    display: flex;
    flex-direction: column;
  }

  .ip-row {
    padding: 4px 6px;
  }

  .ip-row + .ip-row {
    border-top: 1px solid var(--color-border-subtle);
  }

  /* -- Row 1: Identity -- */

  .ip-identity {
    padding: 6px;
  }

  .ip-identity-row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    width: 100%;
  }

  .ip-q {
    display: flex;
    align-items: center;
    gap: 3px;
    font-family: var(--font-mono);
    font-size: 11px;
  }

  .ip-q-label {
    color: var(--color-text-dim);
    font-weight: 500;
  }

  .ip-q-value {
    font-weight: 700;
  }

  .ip-sparkline {
    flex: 1;
    min-width: 0;
    display: flex;
    justify-content: flex-end;
  }

  .ip-severity {
    width: 5px;
    height: 5px;
    flex-shrink: 0;
    margin-left: 4px;
  }

  .ip-headline {
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    margin-top: 3px;
    width: 100%;
  }

  .ip-name {
    font-family: var(--font-mono);
    font-size: 11px;
    font-weight: 700;
    color: var(--color-neon-cyan);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 130px;
  }

  .ip-member-count {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .ip-badges {
    display: flex;
    gap: 4px;
    align-items: center;
    margin-top: 3px;
    width: 100%;
  }

  .ip-badge {
    font-family: var(--font-mono);
    font-size: 8px;
    padding: 0 4px;
    border: 1px solid;
    letter-spacing: 0.03em;
  }

  .ip-badge-domain {
    color: var(--color-neon-purple);
    border-color: color-mix(in srgb, var(--color-neon-purple) 40%, transparent);
  }

  .ip-badge-state {
    color: var(--color-neon-green);
    border-color: color-mix(in srgb, var(--color-neon-green) 40%, transparent);
  }

  .ip-badge-score {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    margin-left: auto;
  }

  .ip-domain-name {
    font-family: var(--font-display);
    font-size: 13px;
    font-weight: 700;
    color: var(--color-neon-purple);
    letter-spacing: 0.08em;
  }

  /* -- Row 2: 2x2 Grid -- */

  .ip-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 1px;
    padding: 0;
    background: var(--color-border-subtle);
  }

  .ip-cell {
    background: var(--color-bg-secondary);
    padding: 3px 6px;
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    cursor: default;
  }

  .ip-cell-label {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .ip-cell-label-accent {
    color: var(--color-neon-cyan);
  }

  .ip-cell-value {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text-secondary);
  }

  .ip-cell-value-green {
    color: var(--color-neon-green);
  }

  .ip-warn {
    color: var(--color-neon-orange);
  }

  .ip-caution {
    color: var(--color-neon-yellow);
  }

  /* -- Row 3: Visual bar -- */

  .ip-bar {
    padding: 3px 6px 4px;
  }

  .ip-bar-label {
    font-family: var(--font-mono);
    font-size: 8px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 2px;
  }

  .ip-bar-empty {
    height: 0;
  }

  .ip-blend-bar,
  .ip-task-bar {
    display: flex;
    gap: 1px;
    height: 14px;
    width: 100%;
  }

  .ip-blend-seg,
  .ip-task-seg {
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--font-mono);
    font-size: 7px;
    overflow: hidden;
    white-space: nowrap;
  }

  .ip-blend-raw {
    background: color-mix(in srgb, var(--color-neon-cyan) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-cyan) 25%, transparent);
    color: var(--color-neon-cyan);
  }

  .ip-blend-opt {
    background: color-mix(in srgb, var(--color-neon-pink) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-pink) 25%, transparent);
    color: var(--color-neon-pink);
  }

  .ip-blend-trans {
    background: color-mix(in srgb, var(--color-neon-indigo) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-indigo) 25%, transparent);
    color: var(--color-neon-indigo);
  }

  .ip-task-seg {
    background: color-mix(in srgb, var(--color-neon-cyan) 8%, transparent);
    border: 1px solid color-mix(in srgb, var(--color-neon-cyan) 20%, transparent);
    color: var(--color-text-dim);
  }

  .ip-task-seg:nth-child(2) {
    border-color: color-mix(in srgb, var(--color-neon-pink) 20%, transparent);
    background: color-mix(in srgb, var(--color-neon-pink) 8%, transparent);
  }

  .ip-task-seg:nth-child(3) {
    border-color: color-mix(in srgb, var(--color-neon-green) 20%, transparent);
    background: color-mix(in srgb, var(--color-neon-green) 8%, transparent);
  }

  /* -- Row 4: Insight -- */

  .ip-insight {
    padding: 4px 6px 5px;
  }

  .ip-insight-text {
    font-family: var(--font-sans);
    font-size: 10px;
    color: var(--color-text-dim);
    line-height: 1.5;
    text-align: justify;
    width: 100%;
    margin: 0;
  }
</style>
```

- [ ] **Step 2: Type check**

Run: `cd frontend && npx svelte-check 2>&1 | tail -5`

Expected: 0 errors (or only pre-existing warnings).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/taxonomy/TopologyInfoPanel.svelte
git commit -m "feat: create TopologyInfoPanel with 5-row adaptive grid"
```

---

### Task 6: Wire TopologyInfoPanel into TopologyControls

**Files:**
- Modify: `frontend/src/lib/components/taxonomy/TopologyControls.svelte`

- [ ] **Step 1: Replace health section with TopologyInfoPanel**

In `frontend/src/lib/components/taxonomy/TopologyControls.svelte`:

1. Add import at top of `<script>` (after line 8):
```typescript
  import TopologyInfoPanel from './TopologyInfoPanel.svelte';
```

2. Remove the following imports that are no longer needed directly (they're now in TopologyInfoPanel):
   - Remove `import { qHealthColor }` (line 3) — only if not used elsewhere in this file
   - Remove `import { assessTaxonomyHealth }` (line 4) — only if not used elsewhere
   - Remove `import { TAXONOMY_TOOLTIPS }` (line 5) — check if still needed for footer counts
   - Remove `import ScoreSparkline` (line 8) — only if not used elsewhere

   **Keep** `TAXONOMY_TOOLTIPS` if the footer counts section (lines 176-184) still uses it.

3. Remove derived values that moved to TopologyInfoPanel (lines 23-34):
   - Remove `stats`, `qSystem`, `qColor`, `health`, `coherence`, `separation`, `sparkline`, `hasSparkline`
   
   **Keep** `filteredCounts` (line 37) — still used by footer.

4. Remove `formatMetric` function (line 69-72) — moved to TopologyInfoPanel.

5. Replace the health section markup (lines 78-116):

From:
```svelte
  <!-- Health section -->
  <div class="tc-section tc-health">
    {#if qSystem != null}
      ...entire health section...
    {:else}
      <div class="tc-empty">No data</div>
    {/if}
  </div>
```

To:
```svelte
  <!-- Adaptive info panel -->
  <div class="tc-section tc-info">
    <TopologyInfoPanel />
  </div>
```

6. Remove the CSS for the old health section (lines 230-310 approximately): `.tc-health`, `.tc-health-row`, `.tc-q-group`, `.tc-q-label`, `.tc-q-value`, `.tc-sparkline`, `.tc-severity-dot`, `.tc-headline`, `.tc-metrics`, `.tc-metric`, `.tc-metric-label`, `.tc-metric-value`, `.tc-empty`.

Add minimal CSS for the new section:
```css
  .tc-info {
    padding: 0;
  }
```

- [ ] **Step 2: Type check**

Run: `cd frontend && npx svelte-check 2>&1 | tail -5`

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/taxonomy/TopologyControls.svelte
git commit -m "refactor: replace static health section with TopologyInfoPanel"
```

---

### Task 7: Full Suite + Lint + Push

- [ ] **Step 1: Backend tests**

Run: `cd backend && source .venv/bin/activate && pytest --tb=short -q 2>&1 | tail -5`

Expected: All pass.

- [ ] **Step 2: Backend lint**

Run: `cd backend && ruff check app/ tests/`

Expected: All checks passed.

- [ ] **Step 3: Frontend type check**

Run: `cd frontend && npx svelte-check 2>&1 | tail -5`

Expected: 0 errors.

- [ ] **Step 4: Push**

```bash
git push origin main
```
