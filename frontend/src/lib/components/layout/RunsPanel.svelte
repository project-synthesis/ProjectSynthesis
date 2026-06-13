<!-- frontend/src/lib/components/layout/RunsPanel.svelte -->
<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { listRuns, bulkDeleteRuns, bulkExportRuns, type RunSummary } from '$lib/api/runs';
  import { projectStore } from '$lib/stores/project.svelte';
  import { runsPanelStore } from '$lib/stores/runs-panel.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import RunRowItem from './RunRowItem.svelte';
  import BulkActionBar from './BulkActionBar.svelte';
  import AnimatedDialog from '$lib/components/shared/AnimatedDialog.svelte';
  import DestructiveConfirmModal from '$lib/components/shared/DestructiveConfirmModal.svelte';

  interface Props {
    active?: boolean;
  }
  let { active = true }: Props = $props();

  let modeFilter = $state<'all' | 'topic_probe' | 'seed_agent' | 'replay_run'>('all');
  let statusFilter = $state<'all' | 'running' | 'completed' | 'partial' | 'failed'>('all');

  let runs = $state<RunSummary[]>([]);
  let runsTotal = $state(0);
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

  // R-23 — Select-all tri-state indeterminate binding via $effect.
  let selectAllEl: HTMLInputElement | undefined = $state();
  const allSelected = $derived(runs.length > 0 && runs.every(r => selectedIds.has(r.id)));
  const someSelected = $derived(selectedIds.size > 0 && !allSelected);
  $effect(() => {
    if (selectAllEl) {
      selectAllEl.indeterminate = someSelected;
    }
  });

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
      throw err instanceof Error ? err : new Error(String(err));
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
      runsTotal = resp.total;
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

  function loadMore(): void {
    if (!hasMore || loadingMore || nextOffset === null) return;
    void fetchRuns(nextOffset, true);
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

  // v0.4.37 D-link: consume a pending run-selection request dispatched by
  // SuiteDetailView's RUN link. Declared AFTER the fetch effect above so
  // this effect's `expandedId` write lands after that effect's
  // `expandedId = null` reset within the same flush (Svelte 5 runs
  // effects in declaration order). Clearing the pending id re-triggers
  // this effect once; the early return makes the second pass a no-op.
  $effect(() => {
    if (!active) return;
    const pending = runsPanelStore.pendingSelectRunId;
    if (!pending) return;
    expandedId = pending;
    untrack(() => runsPanelStore.clearPending());
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

<div class="panel runs-panel">
  <header class="panel-header">
    <!-- R-24 — section heading is an <h2> (not <span>) so document outline
         and SR rotor expose it. R-25 — count badge surfaces when runs.length > 0. -->
    <h2 class="section-heading">Runs</h2>
    {#if runs.length > 0}
      <span class="panel-count font-mono">{runs.length}</span>
    {/if}
    <button
      type="button"
      class="select-toggle"
      aria-label="Toggle select mode"
      aria-pressed={selectMode}
      onclick={toggleSelectMode}
      use:tooltip={selectMode
        ? 'Exit select mode (Esc)'
        : 'Select — toggle rows for bulk delete / export'}
    >
      {selectMode ? 'Cancel' : 'Select'}
    </button>
  </header>

  <div class="filter-region">
    <div class="filter-row" role="toolbar" aria-label="Run filters" aria-controls="runs-list-region">
      <div class="filter-group" role="group" aria-label="Mode filter">
        <button
          type="button"
          class="chip chip-rect"
          class:active={modeFilter === 'all'}
          aria-pressed={modeFilter === 'all'}
          onclick={() => modeFilter = 'all'}
        >All</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={modeFilter === 'topic_probe'}
          aria-pressed={modeFilter === 'topic_probe'}
          onclick={() => modeFilter = 'topic_probe'}
        >Probe</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={modeFilter === 'seed_agent'}
          aria-pressed={modeFilter === 'seed_agent'}
          onclick={() => modeFilter = 'seed_agent'}
        >Seed</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={modeFilter === 'replay_run'}
          aria-pressed={modeFilter === 'replay_run'}
          onclick={() => modeFilter = 'replay_run'}
        >Replay</button>
      </div>
      <div class="filter-group" role="group" aria-label="Status filter">
        <button
          type="button"
          class="chip chip-rect"
          class:active={statusFilter === 'all'}
          aria-pressed={statusFilter === 'all'}
          onclick={() => statusFilter = 'all'}
        >All</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={statusFilter === 'running'}
          aria-pressed={statusFilter === 'running'}
          onclick={() => statusFilter = 'running'}
        >Running</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={statusFilter === 'completed'}
          aria-pressed={statusFilter === 'completed'}
          onclick={() => statusFilter = 'completed'}
        >Completed</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={statusFilter === 'partial'}
          aria-pressed={statusFilter === 'partial'}
          onclick={() => statusFilter = 'partial'}
        >Partial</button>
        <button
          type="button"
          class="chip chip-rect"
          class:active={statusFilter === 'failed'}
          aria-pressed={statusFilter === 'failed'}
          onclick={() => statusFilter = 'failed'}
        >Failed</button>
      </div>
    </div>
    {#if selectMode}
      <label class="select-all-wrap">
        <input
          bind:this={selectAllEl}
          type="checkbox"
          class="select-all-checkbox"
          aria-label="Select all"
          checked={allSelected}
          onchange={selectAll}
        />
        <span class="select-all-label">Select all</span>
      </label>
    {/if}
    <BulkActionBar
      count={selectedIds.size}
      onDelete={() => { confirmDeleteIds = Array.from(selectedIds); }}
      onExport={executeBulkExport}
      onClear={() => { selectedIds = new Set(); }}
      inFlight={bulkActionInFlight}
    />
  </div>

  <div class="panel-body" id="runs-list-region">
    {#if runsError}
      <p class="empty-note panel-error">Failed to load runs: {runsError}</p>
      <button type="button" class="btn-outline-secondary" onclick={() => fetchRuns(0, false)}>Retry</button>
    {:else if !runsLoaded}
      <!-- R-12 — skeleton loading rows (4 placeholders). -->
      {#each { length: 4 } as _}
        <div class="skeleton-row">
          <div class="skeleton-bar"></div>
        </div>
      {/each}
    {:else if runs.length === 0}
      <p class="empty-note">No runs match the current filters.</p>
      <button type="button" class="btn-outline-secondary" onclick={resetFilters}>Reset filters</button>
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
          <!-- RU-057 — sentinel exposes a keyboard-reachable "Load more"
               affordance. Auto-load fires via IntersectionObserver for
               pointer users; keyboard / SR users get the explicit button. -->
          <div bind:this={sentinelEl} class="runs-sentinel">
            <span class="sentinel-status font-mono">
              Showing {runs.length} of {runsTotal}
            </span>
            <button
              type="button"
              class="btn-outline-secondary load-more-btn"
              onclick={loadMore}
              disabled={loadingMore}
            >
              {loadingMore ? 'Loading…' : 'Load more'}
            </button>
          </div>
        {/if}
      </div>
    {/if}
  </div>
</div>

<!-- R-14 — AnimatedDialog wraps DestructiveConfirmModal so the modal
     inherits the canonical scrim+dialog transitions, token-driven z-indices,
     ESC + click-outside dismiss, and body-scroll lock. Parity with
     HistoryPanel's bulk-delete pattern — the typed DELETE literal gate
     prevents accidental destructive actions. -->
<AnimatedDialog
  open={confirmDeleteIds !== null}
  onClose={() => { if (!bulkActionInFlight) confirmDeleteIds = null; }}
  dismissible={!bulkActionInFlight}
  ariaLabel="Confirm delete"
>
  <DestructiveConfirmModal
    open={confirmDeleteIds !== null}
    title={`DELETE ${confirmDeleteIds?.length ?? 0} RUN${confirmDeleteIds?.length === 1 ? '' : 'S'}?`}
    sideEffectHint="This cannot be undone."
    confirmLabel={`Delete ${confirmDeleteIds?.length ?? 0}`}
    onConfirm={executeBulkDelete}
    onCancel={() => { confirmDeleteIds = null; }}
  />
</AnimatedDialog>

<style>
  /* Canonical panel chrome inherited from app.css .panel/.panel-header/.panel-body. */
  .panel-body { padding: 4px 6px 6px; }

  /* RU-007 — compress filter region to a single row of padding (was 6px
     uniform). 4px vertical / 6px horizontal mirrors HistoryPanel toolbar
     density. */
  .filter-region {
    flex-shrink: 0;
    padding: 4px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .filter-row { display: flex; flex-direction: column; gap: 6px; }
  .filter-group { display: flex; gap: 4px; flex-wrap: wrap; }

  /* R-07 — chip-active recipe lives in app.css under `.chip[aria-pressed="true"]`.
     The local `.active` modifier is preserved for back-compat with the
     existing class-based styling pattern; the aria-pressed selector is
     the canonical / a11y-aware path. */
  .chip.chip-rect.active {
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .runs-list { display: flex; flex-direction: column; gap: 2px; }
  .runs-sentinel {
    padding: 6px 0;
    min-height: 24px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .sentinel-status {
    font-size: 10px;
    color: var(--color-text-dim);
  }
  .load-more-btn {
    margin-left: auto;
  }

  .select-all-wrap {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    cursor: pointer;
    min-height: 22px;
  }
  /* R-23 — custom checkbox + indeterminate state. The `:indeterminate`
     selector picks up `el.indeterminate = true` set in the $effect above. */
  .select-all-checkbox {
    appearance: none;
    -webkit-appearance: none;
    width: 14px;
    height: 14px;
    margin: 0;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    border-radius: 0;
    cursor: pointer;
    position: relative;
    transition: border-color var(--duration-hover) var(--ease-spring),
                background-color var(--duration-hover) var(--ease-spring);
  }
  .select-all-checkbox:hover {
    border-color: var(--color-border-accent);
  }
  .select-all-checkbox:checked {
    background: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }
  .select-all-checkbox:checked::after {
    content: '';
    position: absolute;
    left: 4px;
    top: 1px;
    width: 4px;
    height: 8px;
    border: solid var(--color-bg-primary);
    border-width: 0 1.5px 1.5px 0;
    transform: rotate(45deg);
  }
  .select-all-checkbox:indeterminate {
    background: color-mix(in srgb, var(--color-neon-cyan) 50%, transparent);
    border-color: var(--color-neon-cyan);
  }
  .select-all-checkbox:indeterminate::after {
    content: '';
    position: absolute;
    left: 2px;
    top: 5px;
    width: 8px;
    height: 2px;
    background: var(--color-bg-primary);
  }
  .select-all-checkbox:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
  }
  .select-all-label {
    font-size: 10px;
    color: var(--color-text-secondary);
  }

  /* Ghost-style select toggle mirroring HistoryPanel's .select-toggle.
     Lives in .panel-header alongside the section heading. Tokenized
     transitions (R-04) + :active state (R-06) added v0.4.39. */
  .select-toggle {
    height: 20px;
    padding: 0 8px;
    line-height: 18px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    border-radius: 0;
    cursor: pointer;
    transition: background-color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring),
                color var(--duration-hover) var(--ease-spring);
  }
  .select-toggle:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-subtle);
    color: var(--color-text-primary);
  }
  .select-toggle:active {
    border-color: transparent;
  }
  .select-toggle:focus-visible {
    outline: 1px solid var(--color-focus-ring);
    outline-offset: var(--focus-offset-inset);
  }

  /* R-19 — reduced-motion scoped override. */
  @media (prefers-reduced-motion: reduce) {
    .select-toggle,
    .chip.chip-rect,
    .select-all-checkbox {
      transition: none !important;
      animation: none !important;
    }
  }
</style>
