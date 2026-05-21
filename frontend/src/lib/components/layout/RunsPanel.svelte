<!-- frontend/src/lib/components/layout/RunsPanel.svelte -->
<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { listRuns, bulkDeleteRuns, bulkExportRuns, type RunSummary } from '$lib/api/runs';
  import { projectStore } from '$lib/stores/project.svelte';
  import RunRowItem from './RunRowItem.svelte';
  import BulkActionBar from './BulkActionBar.svelte';

  interface Props {
    active?: boolean;
  }
  let { active = true }: Props = $props();

  let modeFilter = $state<'all' | 'topic_probe' | 'seed_agent' | 'replay_run'>('all');
  let statusFilter = $state<'all' | 'running' | 'completed' | 'partial' | 'failed'>('all');

  let runs = $state<RunSummary[]>([]);
  let runsError = $state<string | null>(null);
  let runsLoaded = $state(false);
  let hasMore = $state(false);
  let nextOffset = $state<number | null>(null);
  let loadingMore = $state(false);
  let expandedId = $state<string | null>(null);
  let requestId = 0;

  // v0.4.32 — selection / rename / bulk-action state
  let selectMode = $state(false);
  let selectedIds = $state(new Set<string>());
  let confirmDeleteIds = $state<string[] | null>(null);
  let bulkActionInFlight = $state(false);

  function toggleSelectMode(): void {
    selectMode = !selectMode;
    if (!selectMode) {
      selectedIds = new Set();
    }
  }

  function toggleRowSelection(id: string): void {
    if (selectedIds.has(id)) {
      selectedIds.delete(id);
    } else {
      selectedIds.add(id);
    }
    selectedIds = new Set(selectedIds);  // trigger reactivity
  }

  function selectAll(): void {
    if (runs.every(r => selectedIds.has(r.id))) {
      // All selected → clear
      selectedIds = new Set();
    } else {
      selectedIds = new Set(runs.map(r => r.id));
    }
  }

  async function executeBulkDelete(): Promise<void> {
    if (confirmDeleteIds === null || confirmDeleteIds.length === 0) return;
    bulkActionInFlight = true;
    try {
      const result = await bulkDeleteRuns(confirmDeleteIds);
      // Remove deleted from runs[]
      const deletedSet = new Set(result.deleted);
      runs = runs.filter(r => !deletedSet.has(r.id));
      if (result.not_found.length > 0) {
        runsError = `${result.not_found.length} runs were not found (already deleted).`;
      }
      selectedIds = new Set();
      selectMode = false;
      confirmDeleteIds = null;
    } catch (err) {
      runsError = err instanceof Error ? err.message : 'Bulk delete failed';
    } finally {
      bulkActionInFlight = false;
    }
  }

  async function executeBulkExport(): Promise<void> {
    if (selectedIds.size === 0) return;
    bulkActionInFlight = true;
    try {
      const ids = Array.from(selectedIds);
      const data = await bulkExportRuns(ids);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `runs-export-${formatExportTimestamp()}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      runsError = err instanceof Error ? err.message : 'Bulk export failed';
    } finally {
      bulkActionInFlight = false;
    }
  }

  function formatExportTimestamp(): string {
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
  }

  async function fetchRuns(offset: number, append: boolean): Promise<void> {
    const myRequest = ++requestId;
    if (offset === 0) {
      runsLoaded = false;
      runsError = null;
    } else {
      loadingMore = true;
    }
    try {
      const resp = await listRuns({
        mode: modeFilter !== 'all' ? modeFilter : undefined,
        status: statusFilter !== 'all' ? statusFilter : undefined,
        project_id: projectStore.currentProjectId ?? undefined,
        limit: 20,
        offset,
      });
      if (myRequest !== requestId) return;  // stale response — discard
      runs = append ? [...runs, ...resp.items] : resp.items;
      hasMore = resp.has_more;  // authoritative; runs.ts:80
      nextOffset = resp.next_offset;
      runsLoaded = true;
      runsError = null;
    } catch (err) {
      if (myRequest !== requestId) return;
      runsError = err instanceof Error ? err.message : 'Failed to load runs';
      runsLoaded = true;
    } finally {
      if (myRequest === requestId && offset > 0) loadingMore = false;
    }
  }

  function resetFilters(): void {
    modeFilter = 'all';
    statusFilter = 'all';
  }

  function handleRowClick(runId: string): void {
    expandedId = expandedId === runId ? null : runId;
  }

  // Bound via `bind:this`; declared with `$state(...)` so the IntersectionObserver
  // wiring inside `onMount` reads the post-mount node value rather than the
  // initial `undefined` (silences `non_reactive_update`).
  let sentinelEl = $state<HTMLDivElement | undefined>(undefined);

  // Initial load + reactive re-fetch on filter / project change.
  $effect(() => {
    if (!active) return;
    // Touch all three reactive sources so $effect tracks them
    void modeFilter;
    void statusFilter;
    void projectStore.currentProjectId;
    expandedId = null;  // collapse open row on filter/project change
    untrack(() => fetchRuns(0, false));
  });

  // Scroll-load sentinel observer.
  onMount(() => {
    if (typeof IntersectionObserver === 'undefined') return;
    const obs = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasMore && !loadingMore && nextOffset !== null) {
        void fetchRuns(nextOffset, true);
      }
    }, { rootMargin: '100px' });
    if (sentinelEl) obs.observe(sentinelEl);
    return () => obs.disconnect();
  });
</script>

<div class="runs-panel">
  <div class="filter-row" role="toolbar" aria-label="Run filters">
    <div class="filter-group" role="group" aria-label="Mode filter">
      <button class="chip chip-rect" class:active={modeFilter === 'all'} onclick={() => modeFilter = 'all'}>All</button>
      <button class="chip chip-rect" class:active={modeFilter === 'topic_probe'} onclick={() => modeFilter = 'topic_probe'}>Probe</button>
      <button class="chip chip-rect" class:active={modeFilter === 'seed_agent'} onclick={() => modeFilter = 'seed_agent'}>Seed</button>
      <button class="chip chip-rect" class:active={modeFilter === 'replay_run'} onclick={() => modeFilter = 'replay_run'}>Replay</button>
    </div>
    <div class="filter-group" role="group" aria-label="Status filter">
      <button class="chip chip-rect" class:active={statusFilter === 'all'} onclick={() => statusFilter = 'all'}>All</button>
      <button class="chip chip-rect" class:active={statusFilter === 'running'} onclick={() => statusFilter = 'running'}>Running</button>
      <button class="chip chip-rect" class:active={statusFilter === 'completed'} onclick={() => statusFilter = 'completed'}>Completed</button>
      <button class="chip chip-rect" class:active={statusFilter === 'partial'} onclick={() => statusFilter = 'partial'}>Partial</button>
      <button class="chip chip-rect" class:active={statusFilter === 'failed'} onclick={() => statusFilter = 'failed'}>Failed</button>
    </div>
  </div>

  <div class="select-mode-row">
    <button
      class="btn-outline-secondary"
      aria-label="Toggle select mode"
      aria-pressed={selectMode}
      onclick={toggleSelectMode}
    >
      {selectMode ? 'Cancel select' : 'Select'}
    </button>
    {#if selectMode}
      <label class="select-all-wrap">
        <input
          type="checkbox"
          aria-label="Select all"
          checked={runs.length > 0 && runs.every(r => selectedIds.has(r.id))}
          onchange={selectAll}
        />
        <span class="text-[10px]">Select all</span>
      </label>
    {/if}
  </div>

  <BulkActionBar
    count={selectedIds.size}
    onDelete={() => { confirmDeleteIds = Array.from(selectedIds); }}
    onExport={executeBulkExport}
    onClear={() => { selectedIds = new Set(); }}
    inFlight={bulkActionInFlight}
  />

  {#if runsError}
    <div class="runs-error">
      <p class="text-[10px] text-neon-red">Failed to load runs: {runsError}</p>
      <button class="btn-outline-secondary" onclick={() => fetchRuns(0, false)}>Retry</button>
    </div>
  {:else if !runsLoaded}
    <p class="text-[10px] text-text-dim">Loading runs…</p>
  {:else if runs.length === 0}
    <div class="runs-empty">
      <p class="text-[10px] text-text-dim">No runs match the current filters.</p>
      <button class="btn-outline-secondary" onclick={resetFilters}>Reset filters</button>
    </div>
  {:else}
    <div class="runs-list" role="list">
      {#each runs as run (run.id)}
        <RunRowItem
          {run}
          expanded={expandedId === run.id}
          onClick={() => handleRowClick(run.id)}
          {selectMode}
          selected={selectedIds.has(run.id)}
          onToggleSelect={() => toggleRowSelection(run.id)}
          onDeleteConfirm={(id) => { confirmDeleteIds = [id]; }}
        />
      {/each}
      {#if hasMore}
        <div bind:this={sentinelEl} class="runs-sentinel" aria-hidden="true">
          {#if loadingMore}<span class="text-[10px] text-text-dim">Loading more…</span>{/if}
        </div>
      {/if}
    </div>
  {/if}
</div>

{#if confirmDeleteIds !== null}
  <div
    class="confirm-modal-scrim"
    role="presentation"
    onclick={() => { confirmDeleteIds = null; }}
    onkeydown={(e) => { if (e.key === 'Escape') confirmDeleteIds = null; }}
  >
    <div
      class="confirm-modal"
      role="dialog"
      tabindex={-1}
      aria-modal="true"
      aria-labelledby="confirm-title"
      onclick={(e) => e.stopPropagation()}
      onkeydown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); confirmDeleteIds = null; } }}
    >
      <h3 id="confirm-title" class="text-[11px]" style="text-transform: uppercase; letter-spacing: 0.1em; font-family: var(--font-display);">
        Confirm delete
      </h3>
      <p class="text-[11px]">
        Delete {confirmDeleteIds.length} run{confirmDeleteIds.length === 1 ? '' : 's'}?
        This cannot be undone.
      </p>
      <div class="confirm-actions">
        <button class="btn-outline-secondary" onclick={() => { confirmDeleteIds = null; }}>
          Cancel
        </button>
        <button
          class="btn-outline-danger"
          onclick={executeBulkDelete}
          disabled={bulkActionInFlight}
        >
          Confirm
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .runs-panel { padding: 6px; }
  .filter-row { display: flex; flex-direction: column; gap: 6px; padding: 4px 0 6px; }
  .filter-group { display: flex; gap: 4px; flex-wrap: wrap; }
  .chip.chip-rect.active {
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }
  .runs-error, .runs-empty {
    display: flex; flex-direction: column; gap: 6px; align-items: flex-start; padding: 6px 0;
  }
  .runs-list { display: flex; flex-direction: column; gap: 2px; }
  .runs-sentinel { padding: 6px 0; min-height: 24px; }
  .select-mode-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 6px;
  }
  .select-all-wrap {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    cursor: pointer;
  }
  .confirm-modal-scrim {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    animation: scrim-in 200ms ease-out;
  }
  .confirm-modal {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    padding: 16px;
    min-width: 280px;
    animation: dialog-in 300ms cubic-bezier(0.16, 1, 0.3, 1);
  }
  .confirm-actions {
    display: flex;
    gap: 8px;
    justify-content: flex-end;
    margin-top: 12px;
  }
</style>
