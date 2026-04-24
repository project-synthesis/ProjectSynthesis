<script lang="ts">
  /**
   * HistoryPanel — sidebar History tab.
   *
   * Owns its own list loading, pagination, rename flow, and project filter.
   * Cross-tab cluster navigation dispatches `switch-activity` on window so the
   * parent workbench can swing to the Clusters panel.
   *
   * Extracted from Navigator.svelte; keeps every visible interaction identical.
   */
  import type { HistoryItem } from '$lib/api/client';
  import { getHistory, getOptimization, updateOptimization } from '$lib/api/client';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { projectStore } from '$lib/stores/project.svelte';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { toastsStore } from '$lib/stores/toasts.svelte';
  import { SvelteSet } from 'svelte/reactivity';
  import { deleteOptimization, deleteOptimizations, ApiError } from '$lib/api/optimizations';
  import UndoToast from '$lib/components/shared/UndoToast.svelte';
  import DestructiveConfirmModal from '$lib/components/shared/DestructiveConfirmModal.svelte';
  import { scoreColor, taxonomyColor } from '$lib/utils/colors';
  import { formatScore, formatRelativeTime } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import { STRATEGY_TOOLTIPS } from '$lib/utils/ui-tooltips';

  interface Props {
    active?: boolean;
  }

  let { active = true }: Props = $props();

  const TASK_TYPE_ABBREV: Record<string, string> = {
    coding: 'COD', writing: 'WRT', analysis: 'ANL',
    creative: 'CRE', data: 'DAT', system: 'SYS',
  };

  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);
  const activeTraceId = $derived(activeResult?.trace_id ?? forgeStore.traceId ?? null);

  let historyItems = $state<HistoryItem[]>([]);
  let historyError = $state<string | null>(null);
  let historyLoaded = $state(false);
  let historyHasMore = $state(false);
  let historyNextOffset = $state<number | null>(null);
  let historyLoadingMore = $state(false);

  let renamingOptId = $state<string | null>(null);
  let renameOptValue = $state('');
  let renameOptSaving = $state(false);

  const completedItems = $derived(historyItems.filter((i) => i.status === 'completed'));
  const filteredCompletedItems = $derived(
    projectStore.currentProjectId
      ? completedItems.filter((i) => i.project_id === projectStore.currentProjectId)
      : completedItems,
  );

  const projectLabelMap = $derived<Record<string, string>>(
    Object.fromEntries(projectStore.projects.map((p) => [p.id, p.label])),
  );

  // Cluster label map — excludes structural nodes (domain/project)
  const clusterLabelMap = $derived<Record<string, string>>(
    Object.fromEntries(
      clustersStore.taxonomyTree
        .filter((n) => n.state !== 'domain' && n.state !== 'project' && n.label)
        .map((n) => [n.id, n.label!]),
    ),
  );

  // Lazy load when panel becomes active
  $effect(() => {
    if (active && !historyLoaded) {
      getHistory({ limit: 50, sort_by: 'created_at', sort_order: 'desc' })
        .then((resp) => {
          historyItems = resp.items;
          historyHasMore = resp.has_more;
          historyNextOffset = resp.next_offset ?? null;
          historyError = null;
          historyLoaded = true;
        })
        .catch((err: unknown) => {
          historyError = err instanceof Error ? err.message : 'Failed to load history';
          historyLoaded = true;
        });
    }
  });

  // Reset loaded state on new optimizations (drives re-fetch on next activation)
  $effect(() => {
    if (forgeStore.status === 'complete') {
      historyLoaded = false;
      historyHasMore = false;
      historyNextOffset = null;
    }
  });

  // React to external optimization events — reset loaded flag
  $effect(() => {
    const handler = () => {
      historyLoaded = false;
      historyHasMore = false;
      historyNextOffset = null;
    };
    window.addEventListener('optimization-event', handler);
    return () => window.removeEventListener('optimization-event', handler);
  });

  // Inline feedback rating update — avoid full re-fetch
  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (!detail?.optimization_id || !detail?.rating) return;
      historyItems = historyItems.map((h) =>
        h.id === detail.optimization_id ? { ...h, feedback_rating: detail.rating } : h,
      );
    };
    window.addEventListener('feedback-event', handler);
    return () => window.removeEventListener('feedback-event', handler);
  });

  function startOptRename(e: MouseEvent, item: HistoryItem): void {
    e.stopPropagation();
    renamingOptId = item.id;
    renameOptValue = item.intent_label || '';
  }

  function cancelOptRename(): void {
    renamingOptId = null;
    renameOptValue = '';
  }

  async function submitOptRename(): Promise<void> {
    const id = renamingOptId;
    const trimmed = renameOptValue.trim();
    if (!id || !trimmed || renameOptSaving) return;
    renameOptSaving = true;
    try {
      await updateOptimization(id, { intent_label: trimmed });
      historyItems = historyItems.map((h) => (h.id === id ? { ...h, intent_label: trimmed } : h));
      editorStore.updateTabTitle(id, trimmed);
      renamingOptId = null;
      renameOptValue = '';
    } catch {
      addToast('deleted', 'Rename failed');
    }
    renameOptSaving = false;
  }

  async function loadHistoryItem(item: HistoryItem): Promise<void> {
    if (forgeStore.status !== 'idle' && forgeStore.status !== 'complete' && forgeStore.status !== 'error') {
      forgeStore.cancel();
    }
    try {
      const opt = await getOptimization(item.trace_id);
      forgeStore.loadFromRecord(opt);
      editorStore.openResult(opt.id);
    } catch {
      addToast('deleted', 'Failed to load optimization');
      forgeStore.prompt = item.raw_prompt;
      forgeStore.status = 'idle';
    }
  }

  async function loadMoreHistory(): Promise<void> {
    if (!historyHasMore || historyNextOffset == null || historyLoadingMore) return;
    historyLoadingMore = true;
    try {
      const resp = await getHistory({
        limit: 50,
        offset: historyNextOffset,
        sort_by: 'created_at',
        sort_order: 'desc',
      });
      historyItems = [...historyItems, ...resp.items];
      historyHasMore = resp.has_more;
      historyNextOffset = resp.next_offset ?? null;
    } catch {
      // Keep current list; user can retry.
    }
    historyLoadingMore = false;
  }

  function navigateToCluster(e: Event, clusterId: string): void {
    e.stopPropagation();
    clustersStore.selectCluster(clusterId);
    window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'clusters' }));
  }

  // ── Row delete state machine ─────────────────────────────────────

  type RowState = 'idle' | 'pending-delete' | 'deleting';
  const rowStates: Map<string, RowState> = $state(new Map());
  const fallbackTimers = new Map<string, number>();

  function rowStateOf(id: string): RowState {
    return rowStates.get(id) ?? 'idle';
  }

  function setRowState(id: string, state: RowState) {
    if (state === 'idle') {
      rowStates.delete(id);
    } else {
      rowStates.set(id, state);
    }
    // Force Svelte to detect the Map mutation by reassigning via spread
    rowStates.forEach(() => {});
  }

  function scheduleFallbackRemoval(id: string, timeoutMs: number) {
    const handle = window.setTimeout(() => {
      historyItems = historyItems.filter(i => i.id !== id);
      setRowState(id, 'idle');
      fallbackTimers.delete(id);
    }, timeoutMs);
    fallbackTimers.set(id, handle);
  }

  function cancelFallbackRemoval(id: string) {
    const handle = fallbackTimers.get(id);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      fallbackTimers.delete(id);
    }
  }

  function onRowDelete(item: HistoryItem, ev: MouseEvent | KeyboardEvent) {
    ev.stopPropagation();
    if (rowStateOf(item.id) !== 'idle') return;     // re-entry guard: prevents duplicate undo-toasts on rapid clicks
    setRowState(item.id, 'pending-delete');
    // Single-row delete: cluster_id is a singular FK, so at most one cluster
    // is affected — the literal "1 cluster" is correct by construction. The
    // bulk path (confirmBulk → bulkSideEffectHint) instead computes the
    // distinct count across all selected rows; the asymmetry is intentional.
    const meta = item.cluster_id ? '1 cluster will rebalance.' : undefined;
    toastsStore.push({
      kind: 'undo',
      message: 'Deleting optimization.',
      meta,
      durationMs: 5000,
      undo: () => setRowState(item.id, 'idle'),
      commit: async () => {
        setRowState(item.id, 'deleting');
        try {
          await deleteOptimization(item.id);
          // SSE surgically removes the row; fallback 2s timeout covers SSE gaps
          scheduleFallbackRemoval(item.id, 2000);
        } catch (e) {
          setRowState(item.id, 'idle');
          const status = (e as ApiError).status;
          const msg = status === 404 ? 'Already deleted elsewhere.' : 'Delete failed.';
          toastsStore.push({ kind: 'error', message: msg, durationMs: 4000 });
        }
      },
    });
  }

  function onOptimizationDeleted(e: Event) {
    const detail = (e as CustomEvent<{ id: string }>).detail;
    historyItems = historyItems.filter(i => i.id !== detail.id);
    setRowState(detail.id, 'idle');
    cancelFallbackRemoval(detail.id);
  }

  $effect(() => {
    window.addEventListener('optimization-deleted', onOptimizationDeleted);
    return () => {
      window.removeEventListener('optimization-deleted', onOptimizationDeleted);
      fallbackTimers.forEach(h => window.clearTimeout(h));
      fallbackTimers.clear();
    };
  });

  // ── Multi-select + bulk delete ───────────────────────────────────

  let selectMode = $state(false);
  const selectedIds: SvelteSet<string> = $state(new SvelteSet<string>());
  let bulkModalOpen = $state(false);

  function toggleSelectMode() {
    selectMode = !selectMode;
    if (!selectMode) selectedIds.clear();
  }

  function toggleSelected(id: string, checked: boolean) {
    if (checked) selectedIds.add(id);
    else selectedIds.delete(id);
  }

  function openBulkModal() {
    if (selectedIds.size === 0) return;
    bulkModalOpen = true;
  }

  async function confirmBulk() {
    const ids = [...selectedIds];
    try {
      const res = await deleteOptimizations(ids);
      // Success: exit selection mode + close modal + clear selection
      bulkModalOpen = false;
      selectMode = false;
      selectedIds.clear();
      if (res.deleted < res.requested) {
        const missing = res.requested - res.deleted;
        toastsStore.push({
          kind: 'info',
          message: `Deleted ${res.deleted} of ${res.requested}. ${missing} were already gone.`,
          durationMs: 4000,
        });
      }
    } catch (e) {
      // Modal stays open and renders its own error banner via DestructiveConfirmModal's
      // try/catch on onConfirm. State (bulkModalOpen, selectMode, selectedIds) intentionally
      // preserved so the user can retry without re-selecting. Re-throw to let the modal
      // surface the error message.
      throw e;
    }
  }

  const selectedClusterCount = $derived.by(() => {
    const ids = new Set<string>();
    for (const opt of historyItems) {
      if (selectedIds.has(opt.id) && opt.cluster_id) ids.add(opt.cluster_id);
    }
    return ids.size;
  });
  const bulkSideEffectHint = $derived(
    selectedClusterCount > 0
      ? `${selectedClusterCount} cluster${selectedClusterCount === 1 ? '' : 's'} will rebalance.`
      : undefined,
  );
</script>

<div class="panel">
  <header class="panel-header">
    <span class="section-heading">History</span>
    <button
      type="button"
      class="select-toggle"
      onclick={toggleSelectMode}
    >{selectMode ? 'Cancel' : 'Select'}</button>
  </header>
  {#if selectMode && selectedIds.size > 0 && !bulkModalOpen}
    <div class="selection-toolbar" role="toolbar" aria-label="Selected rows">
      <span class="selection-count">
        <span class="count-num">{selectedIds.size}</span> selected
      </span>
      <div class="toolbar-actions">
        <button class="btn-cancel-toolbar" onclick={toggleSelectMode}>Cancel</button>
        <button
          class="btn-delete-toolbar"
          onclick={openBulkModal}
        >Delete {selectedIds.size}</button>
      </div>
    </div>
  {/if}
  <div class="panel-body">
    {#if historyError}
      <p class="empty-note">{historyError}</p>
    {:else if !historyLoaded}
      {#each { length: 4 } as _}
        <div class="skeleton-row">
          <div class="skeleton-bar skeleton-wide"></div>
          <div class="skeleton-bar skeleton-narrow"></div>
        </div>
      {/each}
    {:else if historyItems.length === 0}
      <p class="empty-note">No optimizations yet.</p>
    {:else}
      {#each filteredCompletedItems as item (item.id)}
        {#if renamingOptId === item.id}
          <div class="row-item history-row" style="--accent: {taxonomyColor(item.domain)};">
            <span class="row-prompt-line">
              {#if item.task_type && item.task_type !== 'general' && TASK_TYPE_ABBREV[item.task_type]}
                <span class="row-type">{TASK_TYPE_ABBREV[item.task_type]}</span>
              {/if}
              <!-- svelte-ignore a11y_autofocus -->
              <form class="rename-form-inline" onsubmit={(e) => { e.preventDefault(); submitOptRename(); }}>
                <input
                  class="rename-input-inline"
                  type="text"
                  bind:value={renameOptValue}
                  onkeydown={(e) => { if (e.key === 'Escape') cancelOptRename(); }}
                  autofocus
                  aria-label="Rename optimization"
                />
                <button
                  class="rename-btn-inline save"
                  type="submit"
                  disabled={renameOptSaving || !renameOptValue.trim()}
                  use:tooltip={'Save'}
                  aria-label="Save"
                >&#x2713;</button>
                <button
                  class="rename-btn-inline cancel"
                  type="button"
                  onclick={() => cancelOptRename()}
                  use:tooltip={'Cancel'}
                  aria-label="Cancel"
                >×</button>
              </form>
              <span class="row-time">{formatRelativeTime(item.created_at)}</span>
            </span>
          </div>
        {:else}
          <button
            class="row-item history-row"
            class:history-row--active={activeTraceId === item.trace_id}
            class:pending-delete={rowStateOf(item.id) === 'pending-delete'}
            class:deleting={rowStateOf(item.id) === 'deleting'}
            style="--accent: {taxonomyColor(item.domain)};"
            onclick={() => loadHistoryItem(item)}
          >
            {#if selectMode}
              <input
                type="checkbox"
                class="row-checkbox"
                checked={selectedIds.has(item.id)}
                onclick={(e) => e.stopPropagation()}
                onchange={(e) => toggleSelected(item.id, (e.target as HTMLInputElement).checked)}
                aria-label="Select {item.intent_label || item.raw_prompt?.slice(0, 40) || 'optimization'}"
              />
            {/if}
            <span class="row-prompt-line">
              {#if item.task_type && item.task_type !== 'general' && TASK_TYPE_ABBREV[item.task_type]}
                <span class="row-type">{TASK_TYPE_ABBREV[item.task_type]}</span>
              {/if}
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <span
                class="row-prompt"
                tabindex="-1"
                ondblclick={(e) => startOptRename(e, item)}
                use:tooltip={'Double-click to rename'}
              >{item.intent_label || (item.raw_prompt ? item.raw_prompt.slice(0, 60) + (item.raw_prompt.length > 60 ? '..' : '') : 'Untitled')}</span>
              <span class="row-time">{formatRelativeTime(item.created_at)}</span>
            </span>
            <div class="history-meta">
              {#if item.project_id && projectLabelMap[item.project_id]}
                <span class="row-project font-mono" use:tooltip={`Project: ${projectLabelMap[item.project_id]}`}>
                  {projectLabelMap[item.project_id].slice(0, 2).toUpperCase()}
                </span>
              {/if}
              {#if item.cluster_id && clusterLabelMap[item.cluster_id]}
                <!-- svelte-ignore a11y_no_static_element_interactions -->
                <span
                  class="row-cluster font-mono"
                  role="link"
                  tabindex="-1"
                  use:tooltip={`Cluster: ${clusterLabelMap[item.cluster_id]}`}
                  onclick={(e) => navigateToCluster(e, item.cluster_id!)}
                  onkeydown={(e) => { if (e.key === 'Enter') navigateToCluster(e, item.cluster_id!); }}
                >{clusterLabelMap[item.cluster_id].slice(0, 8)}</span>
              {/if}
              <span class="row-badge font-mono">{item.strategy_used || 'auto'}</span>
              <span
                class="row-score font-mono"
                style="color: {scoreColor(item.overall_score)};"
              >
                {item.overall_score != null ? formatScore(item.overall_score) : '—'}
              </span>
              {#if item.feedback_rating}
                <span
                  class="row-feedback font-mono"
                  style="color: {item.feedback_rating === 'thumbs_up' ? 'var(--color-neon-cyan)' : 'var(--color-neon-red)'};"
                  use:tooltip={item.feedback_rating === 'thumbs_up' ? STRATEGY_TOOLTIPS.feedback_positive : STRATEGY_TOOLTIPS.feedback_negative}
                >{item.feedback_rating === 'thumbs_up' ? '\u2191' : '\u2193'}</span>
              {/if}
            </div>
            <span
              role="button"
              class="row-delete-btn"
              data-testid="row-delete-btn"
              onclick={(e) => onRowDelete(item, e)}
              onkeydown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onRowDelete(item, e);
                }
              }}
              aria-label="Delete optimization"
              tabindex="0"
            >×</span>
          </button>
        {/if}
      {/each}
      {#if filteredCompletedItems.length === 0}
        <p class="empty-note">{projectStore.currentProjectId ? 'No optimizations for this project.' : 'No completed optimizations yet.'}</p>
      {/if}
      {#if historyHasMore}
        <button
          class="load-more-btn"
          onclick={loadMoreHistory}
          disabled={historyLoadingMore}
        >
          {historyLoadingMore ? 'Loading...' : 'Load more'}
        </button>
      {/if}
    {/if}
  </div>
</div>

<div class="toast-stack" aria-live="polite">
  {#each toastsStore.toasts.filter(t => t.kind === 'undo') as t (t.id)}
    <UndoToast toast={t} />
  {/each}
</div>

{#snippet bulkBody()}
  <ul class="bulk-preview-list">
    {#each historyItems.filter(i => selectedIds.has(i.id)).slice(0, 3) as opt}
      {@const preview = opt.raw_prompt || opt.intent_label || 'Untitled'}
      <li>{preview.slice(0, 60)}{preview.length > 60 ? '…' : ''}</li>
    {/each}
    {#if selectedIds.size > 3}
      <li class="more">…and {selectedIds.size - 3} more</li>
    {/if}
  </ul>
{/snippet}

<DestructiveConfirmModal
  open={bulkModalOpen}
  title={`DELETE ${selectedIds.size} OPTIMIZATION${selectedIds.size === 1 ? '' : 'S'}?`}
  body={bulkBody}
  sideEffectHint={bulkSideEffectHint}
  confirmLabel={`Delete ${selectedIds.size}`}
  onConfirm={confirmBulk}
  onCancel={() => (bulkModalOpen = false)}
/>

<style>
  .row-item {
    position: relative;
    display: flex;
    align-items: center;
    height: 20px;
    padding: 0 6px;
    border: none;
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    width: 100%;
    text-align: left;
    gap: 6px;
    transition:
      color var(--duration-hover) var(--ease-spring),
      background var(--duration-hover) var(--ease-spring);
  }

  .row-item:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: transparent;
  }

  .row-item:active {
    transform: none;
  }

  .row-badge {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .history-row {
    height: auto;
    min-height: 20px;
    padding: 2px 6px 2px 8px;
    flex-direction: column;
    align-items: stretch;
    gap: 1px;
    border: 1px solid transparent;
    border-left: 1px solid var(--accent, transparent);
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .history-row:hover {
    border-color: var(--color-border-accent);
    border-left-color: var(--accent, transparent);
  }

  .history-row:active {
    background: var(--color-bg-hover);
  }

  .history-row--active {
    border-color: color-mix(in srgb, var(--accent, var(--color-neon-cyan)) 40%, transparent);
    border-left-color: var(--accent, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--accent, var(--color-neon-cyan)) 4%, transparent);
  }

  .history-row--active .row-prompt {
    color: var(--accent, var(--color-neon-cyan));
  }

  .load-more-btn {
    display: block;
    width: 100%;
    padding: 6px 0;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    background: transparent;
    border: none;
    border-top: 1px solid var(--color-border-subtle);
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    transition: color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .load-more-btn:hover:not(:disabled) {
    color: var(--tier-accent, var(--color-neon-cyan));
    background: var(--color-bg-hover);
  }

  .load-more-btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .row-prompt-line {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
  }

  .row-type {
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }

  .row-time {
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    margin-left: auto;
    flex-shrink: 0;
  }

  .row-prompt {
    font-size: 10px;
    font-weight: 400;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: left;
    flex: 1;
    min-width: 0;
  }

  .rename-form-inline {
    display: flex;
    align-items: center;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .rename-input-inline {
    flex: 1;
    min-width: 0;
    height: 18px;
    font-size: 10px;
    font-family: inherit;
    background: var(--color-bg-secondary);
    border: 1px solid var(--accent, var(--color-border-subtle));
    color: var(--color-text-primary);
    padding: 0 4px;
    outline: none;
  }

  .rename-btn-inline {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 18px;
    font-size: 10px;
    border: none;
    background: transparent;
    cursor: pointer;
    padding: 0;
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .rename-btn-inline.save {
    color: var(--color-neon-green);
  }

  .rename-btn-inline.save:hover {
    color: var(--accent, var(--color-neon-cyan));
  }

  .rename-btn-inline.save:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .rename-btn-inline.cancel {
    color: var(--color-text-dim);
  }

  .rename-btn-inline.cancel:hover {
    color: var(--color-text-primary);
  }

  .history-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
  }

  .row-project {
    font-size: 9px;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    padding: 0 3px;
    white-space: nowrap;
  }

  .row-cluster {
    font-size: 8px;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    padding: 0 3px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 64px;
    cursor: pointer;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring);
  }

  .row-cluster:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }

  .row-score {
    font-size: 10px;
    flex-shrink: 0;
  }

  .row-feedback {
    font-size: 10px;
    flex-shrink: 0;
  }

  .skeleton-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 6px 4px 10px;
    border-left: 1px solid var(--color-border-subtle);
  }

  .skeleton-bar {
    height: 8px;
    background: var(--color-bg-card);
    animation: skeleton-pulse var(--duration-skeleton) ease-in-out infinite;
  }

  .skeleton-wide { width: 85%; }
  .skeleton-narrow { width: 55%; }

  @keyframes skeleton-pulse {
    0%, 100% { opacity: 0.4; }
    50% { opacity: 1; }
  }

  /* ── Row delete affordance ─────────────────────────────────────── */

  .row-delete-btn {
    position: absolute;
    top: 50%;
    right: 6px;
    transform: translateY(-50%);
    width: 16px;
    height: 16px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--color-neon-red);
    font-size: 12px;
    line-height: 1;
    opacity: 0;
    pointer-events: none;
    cursor: pointer;
    border-radius: 2px;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: opacity 200ms var(--ease-spring),
                background 200ms var(--ease-spring),
                border-color 200ms var(--ease-spring);
  }

  .row-item:hover .row-delete-btn,
  .row-item:focus-within .row-delete-btn {
    opacity: 0.6;
    pointer-events: auto;
  }

  .row-delete-btn:hover {
    opacity: 1;
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
    border-color: rgba(255, 51, 102, 0.4);
  }

  .row-delete-btn:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .row-item.pending-delete {
    opacity: 0.4;
  }

  .row-item.pending-delete :global(*) {
    text-decoration: line-through;
  }

  .toast-stack {
    position: fixed;
    bottom: 24px;
    right: 24px;
    display: flex;
    flex-direction: column-reverse;
    gap: 6px;
    /* Above modals (900/901) so a pre-commit grace-window toast stays
       reachable even when a DestructiveConfirmModal is open. Below the
       top-level CommandPalette tier (9999). */
    z-index: 1100;
  }

  @media (prefers-reduced-motion: reduce) {
    .row-delete-btn { transition-duration: 0.01ms; }
  }

  /* ── Panel header layout ───────────────────────────────────────── */

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  /* ── Select toggle ─────────────────────────────────────────────── */

  .select-toggle {
    height: 20px;
    padding: 0 8px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    border-radius: 4px;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring);
  }
  .select-toggle:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-subtle);
  }
  .select-toggle:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  /* ── Selection toolbar ─────────────────────────────────────────── */

  .selection-toolbar {
    height: 28px;
    padding: 0 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 6px;
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .selection-count {
    font-family: var(--font-sans);
    font-size: 11px;
  }
  .count-num { font-family: var(--font-mono); }
  .toolbar-actions { display: flex; gap: 6px; }
  .btn-cancel-toolbar,
  .btn-delete-toolbar {
    height: 20px;
    padding: 0 8px;
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    border-radius: 4px;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring);
  }
  .btn-cancel-toolbar {
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-secondary);
  }
  .btn-cancel-toolbar:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-subtle);
  }
  .btn-delete-toolbar {
    background: transparent;
    border: 1px solid var(--color-neon-red);
    color: var(--color-neon-red);
  }
  .btn-delete-toolbar:hover {
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
  }
  .btn-cancel-toolbar:focus-visible,
  .btn-delete-toolbar:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  /* ── Row checkbox ──────────────────────────────────────────────── */

  .row-checkbox {
    width: 14px;
    height: 14px;
    margin-right: 6px;
    appearance: none;
    border: 1px solid var(--color-border-subtle);
    border-radius: 2px;
    background: transparent;
    cursor: pointer;
    position: relative;
    flex-shrink: 0;
  }
  .row-checkbox:hover { border-color: rgba(0, 229, 255, 0.3); }
  .row-checkbox:checked {
    background: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }
  .row-checkbox:checked::after {
    content: '';
    position: absolute;
    left: 3px;
    top: 1px;
    width: 4px;
    height: 8px;
    border: solid var(--color-bg-primary);
    border-width: 0 1px 1px 0;
    transform: rotate(45deg);
  }
  .row-checkbox:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  /* ── Bulk preview list ─────────────────────────────────────────── */

  .bulk-preview-list {
    margin: 0;
    padding: 0;
    list-style: none;
    font-size: 12px;
  }
  .bulk-preview-list li {
    padding: 2px 0;
  }
  .bulk-preview-list li.more {
    color: var(--color-text-dim);
    font-size: 10px;
  }
</style>
