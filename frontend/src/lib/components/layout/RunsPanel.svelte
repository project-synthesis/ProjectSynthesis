<!-- frontend/src/lib/components/layout/RunsPanel.svelte -->
<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { listRuns, type RunSummary } from '$lib/api/runs';
  import { projectStore } from '$lib/stores/project.svelte';
  import RunRowItem from './RunRowItem.svelte';

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

  let sentinelEl: HTMLDivElement;

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
</style>
