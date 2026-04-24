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
          // Success path: backend will fire `optimization_deleted` +
          // `taxonomy_changed` SSE events. Those drive the surgical row
          // removal (via our listener) and invalidate clustersStore /
          // domainStore / readinessStore (via +page.svelte dispatcher).
          // 2s fallback timer covers SSE stream gaps. No direct reconcile
          // needed — the reactive pipeline handles everything.
          scheduleFallbackRemoval(item.id, 2000);
        } catch (e) {
          const status = (e as ApiError).status;
          if (status === 404) {
            // 404 = row is no longer in the DB. Could be expected (another
            // client already deleted) or a deployment mismatch (endpoint
            // missing). Either way, reconcile locally — filter the row
            // out + info toast. Taxonomy-adjacent stores refresh via the
            // SSE `taxonomy_changed` handler in +page.svelte when the
            // backend later confirms anything changed; if the endpoint
            // is truly missing, no SSE fires, and stale cluster counts
            // persist until a full-page reload.
            historyItems = historyItems.filter(i => i.id !== item.id);
            setRowState(item.id, 'idle');
            toastsStore.push({
              kind: 'info',
              message: 'Already deleted elsewhere.',
              durationMs: 4000,
            });
          } else {
            setRowState(item.id, 'idle');
            toastsStore.push({
              kind: 'error',
              message: 'Delete failed.',
              durationMs: 4000,
            });
          }
        }
      },
    });
  }

  function onOptimizationDeleted(e: Event) {
    const detail = (e as CustomEvent<{ id: string }>).detail;
    historyItems = historyItems.filter(i => i.id !== detail.id);
    setRowState(detail.id, 'idle');
    cancelFallbackRemoval(detail.id);
    // Taxonomy-adjacent store invalidation is handled centrally by the SSE
    // dispatcher in +page.svelte on `taxonomy_changed` (which the backend
    // emits alongside `optimization_deleted` for any delete that cascades).
    // Duplicating the invalidation here would double-fetch clusters /
    // domains / readiness for every deleted row.
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
  // Anchor for shift-click range selection. Tracks the index of the last
  // row the user clicked (with or without ctrl/cmd); shift+click picks the
  // contiguous range from anchor to current into selectedIds.
  let rangeAnchorIdx = $state<number | null>(null);

  function toggleSelectMode() {
    selectMode = !selectMode;
    if (!selectMode) {
      selectedIds.clear();
      rangeAnchorIdx = null;
    }
  }

  function toggleSelected(id: string, checked: boolean) {
    if (checked) selectedIds.add(id);
    else selectedIds.delete(id);
  }

  function openBulkModal() {
    if (selectedIds.size === 0) return;
    bulkModalOpen = true;
  }

  // ── Modifier-aware row click router ──────────────────────────────
  //
  // Routes a row click based on modifier keys + current select-mode:
  //   - ctrl/cmd+click:  toggle this row's selection; auto-enter select
  //                      mode if idle; set this row as the range anchor.
  //   - shift+click:     extend selection from rangeAnchorIdx to clicked
  //                      row. If no anchor, treat as ctrl+click.
  //   - plain click:     the default row behaviour — load the optimization
  //                      into the editor (existing loadHistoryItem path).
  //
  // The row wrapper <button> native click semantics still drive keyboard
  // (Enter/Space) activation; that routes through this handler too, so
  // keyboard-Enter loads the optimization without modifier handling.
  function onRowClick(e: MouseEvent, item: HistoryItem, idx: number) {
    const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform);
    const toggleKey = isMac ? e.metaKey : e.ctrlKey;

    if (toggleKey) {
      e.preventDefault();
      e.stopPropagation();
      if (!selectMode) selectMode = true;
      if (selectedIds.has(item.id)) selectedIds.delete(item.id);
      else selectedIds.add(item.id);
      rangeAnchorIdx = idx;
      return;
    }

    if (e.shiftKey) {
      e.preventDefault();
      e.stopPropagation();
      if (!selectMode) selectMode = true;
      if (rangeAnchorIdx === null) {
        // No anchor yet — treat shift+click as a single toggle and
        // remember this row as the anchor for the next shift+click.
        selectedIds.add(item.id);
        rangeAnchorIdx = idx;
        return;
      }
      const [from, to] = [
        Math.min(rangeAnchorIdx, idx),
        Math.max(rangeAnchorIdx, idx),
      ];
      for (let i = from; i <= to; i++) {
        const row = filteredCompletedItems[i];
        if (row) selectedIds.add(row.id);
      }
      return;
    }

    // Plain click in select mode → toggle selection (matches file-manager
    // conventions: clicking a row in select mode selects it, not loads it).
    if (selectMode) {
      e.preventDefault();
      e.stopPropagation();
      if (selectedIds.has(item.id)) selectedIds.delete(item.id);
      else selectedIds.add(item.id);
      rangeAnchorIdx = idx;
      return;
    }

    // Plain click when not in select mode — the original behaviour:
    // load this optimization into the editor.
    loadHistoryItem(item);
  }

  // ── Panel-level keyboard shortcuts ───────────────────────────────
  //
  // Listens on the panel root (not window) so shortcuts don't bleed
  // into other surfaces. Row-focused shortcuts (Delete, arrows) rely
  // on `document.activeElement` being inside the panel.
  function onPanelKeyDown(e: KeyboardEvent) {
    const isMac = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform);
    const toggleKey = isMac ? e.metaKey : e.ctrlKey;
    const target = e.target as HTMLElement | null;
    const inInput =
      target?.tagName === 'INPUT' ||
      target?.tagName === 'TEXTAREA' ||
      target?.isContentEditable;
    // Don't intercept shortcuts typed inside the rename input or any
    // future input inside the panel.
    if (inInput) return;

    // Esc — exit select mode (no-op if idle). Never blocks other Esc
    // handlers (e.g. modal close); the modal's own listener fires first.
    if (e.key === 'Escape') {
      if (bulkModalOpen) return; // let the modal own Esc while open
      if (selectMode) {
        e.preventDefault();
        selectMode = false;
        selectedIds.clear();
        rangeAnchorIdx = null;
      }
      return;
    }

    // Ctrl/Cmd+A — select all visible rows (only when already in select mode).
    if (toggleKey && (e.key === 'a' || e.key === 'A')) {
      if (!selectMode) return; // don't hijack the browser's select-all elsewhere
      e.preventDefault();
      for (const item of filteredCompletedItems) selectedIds.add(item.id);
      return;
    }

    // Delete / Backspace on a focused row → trigger that row's delete.
    if (e.key === 'Delete' || e.key === 'Backspace') {
      const row = findFocusedRow();
      if (!row) return;
      const item = historyItems.find(i => i.id === row.dataset.rowId);
      if (!item) return;
      if (selectMode) {
        // In select mode with selection — open bulk confirm.
        if (selectedIds.size > 0) {
          e.preventDefault();
          openBulkModal();
        }
        return;
      }
      // Single-row delete (same code path as clicking ×).
      if (rowStateOf(item.id) !== 'idle') return;
      e.preventDefault();
      onRowDelete(item, e);
      return;
    }

    // Arrow keys — move focus between rows.
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp' || e.key === 'Home' || e.key === 'End') {
      const rows = rowButtonElements();
      if (rows.length === 0) return;
      const currentIdx = rows.findIndex(r => r === document.activeElement);
      let nextIdx: number;
      if (e.key === 'Home') nextIdx = 0;
      else if (e.key === 'End') nextIdx = rows.length - 1;
      else if (e.key === 'ArrowDown') nextIdx = currentIdx < 0 ? 0 : Math.min(rows.length - 1, currentIdx + 1);
      else nextIdx = currentIdx <= 0 ? 0 : currentIdx - 1;
      if (rows[nextIdx]) {
        e.preventDefault();
        rows[nextIdx].focus();
      }
    }
  }

  function rowButtonElements(): HTMLButtonElement[] {
    if (typeof document === 'undefined') return [];
    return Array.from(
      document.querySelectorAll<HTMLButtonElement>(
        '.history-panel .history-row[data-row-id]',
      ),
    );
  }

  function findFocusedRow(): HTMLButtonElement | null {
    const el = typeof document !== 'undefined' ? document.activeElement : null;
    if (!el || !(el instanceof HTMLElement)) return null;
    // The focused element could be the row button itself or a descendant
    // (e.g. the × affordance). Walk up to the nearest .history-row.
    const row = el.closest<HTMLButtonElement>('.history-row[data-row-id]');
    return row;
  }

  /**
   * Delete N ids via per-row DELETE /api/optimizations/{id} calls in parallel.
   * Used as a fallback when the bulk POST endpoint is unreachable (older
   * backend that predates v0.4.3) and directly for final reconciliation when
   * the bulk succeeded partially. Returns a breakdown so the caller can
   * surface an accurate info/error toast.
   */
  async function deleteIdsOneByOne(ids: string[]): Promise<{
    deleted: string[];
    alreadyGone: string[];
    failed: Array<{ id: string; status: number | undefined }>;
  }> {
    const results = await Promise.allSettled(ids.map(id => deleteOptimization(id)));
    const deleted: string[] = [];
    const alreadyGone: string[] = [];
    const failed: Array<{ id: string; status: number | undefined }> = [];
    results.forEach((r, i) => {
      const id = ids[i];
      if (r.status === 'fulfilled') {
        deleted.push(id);
      } else {
        const err = r.reason as ApiError;
        if (err?.status === 404) alreadyGone.push(id);
        else failed.push({ id, status: err?.status });
      }
    });
    return { deleted, alreadyGone, failed };
  }

  async function confirmBulk() {
    const ids = [...selectedIds];
    try {
      const res = await deleteOptimizations(ids);

      // All-gone: every selected row was already deleted elsewhere (another
      // client, MCP tool, or a corrupted-state cleanup). Treat as successful
      // reconciliation — close the modal and surface a non-alarming info toast.
      if (res.deleted === 0 && res.requested > 0) {
        historyItems = historyItems.filter(i => !ids.includes(i.id));
        bulkModalOpen = false;
        selectMode = false;
        selectedIds.clear();
        toastsStore.push({
          kind: 'info',
          message:
            res.requested === 1
              ? 'Already deleted elsewhere.'
              : `All ${res.requested} were already deleted elsewhere.`,
          durationMs: 4000,
        });
        return;
      }

      // Success path (may be partial when some ids are stale). Backend fires
      // per-row `optimization_deleted` SSE + one aggregated `taxonomy_changed`
      // — those drive surgical row removal + store invalidation via the SSE
      // dispatcher. No inline reconcile needed on the definite-success branch.
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
      const err = e as { status?: number; message?: string };
      const status = err.status;

      // Bulk endpoint unreachable (e.g. older backend that predates v0.4.3):
      // fall back to per-id DELETE /api/optimizations/{id}, which has shipped
      // since v0.4.2. This keeps the UX working on mismatched deployments and
      // fully handles corrupted/stale rows because per-id 404 is reconciled
      // locally rather than surfaced as an error.
      if (status === 404) {
        try {
          const { deleted, alreadyGone, failed } = await deleteIdsOneByOne(ids);
          if (failed.length > 0) {
            const anyNonBenign = failed.some(f => f.status !== undefined && f.status !== 404);
            throw new Error(
              anyNonBenign
                ? `Deleted ${deleted.length}. ${failed.length} failed. Retry.`
                : 'Delete failed. Retry.',
            );
          }
          // All succeeded or were already-gone — reconcile locally.
          historyItems = historyItems.filter(i => !ids.includes(i.id));
          bulkModalOpen = false;
          selectMode = false;
          selectedIds.clear();
          if (deleted.length === 0 && alreadyGone.length > 0) {
            toastsStore.push({
              kind: 'info',
              message:
                alreadyGone.length === 1
                  ? 'Already deleted elsewhere.'
                  : `All ${alreadyGone.length} were already deleted elsewhere.`,
              durationMs: 4000,
            });
          } else if (alreadyGone.length > 0) {
            toastsStore.push({
              kind: 'info',
              message: `Deleted ${deleted.length}. ${alreadyGone.length} were already gone.`,
              durationMs: 4000,
            });
          }
          // Verify with backend: if the rows are STILL there (meaning per-id
          // 404 was a deployment/auth issue rather than actual-deletion), the
          // reconcile step will surface an error toast.
          return;
        } catch (fallbackErr) {
          // If the fallback itself threw, propagate the friendlier message.
          throw fallbackErr;
        }
      }

      // Translate remaining HTTP statuses into actionable copy. Raw
      // "Not Found" / "Internal Server Error" strings aren't user-facing —
      // they're protocol artefacts. Modal state (open, selectMode,
      // selectedIds) is preserved so the user can retry without re-selecting.
      let friendly: string;
      if (status === 429) {
        friendly = 'Too many deletes. Wait a moment, then retry.';
      } else if (status !== undefined && status >= 500) {
        friendly = 'Server error. Retry.';
      } else if (
        err.message &&
        err.message !== 'Not Found' &&
        err.message !== 'Internal Server Error' &&
        !err.message.startsWith('HTTP ')
      ) {
        friendly = err.message;
      } else {
        friendly = 'Delete failed.';
      }
      throw new Error(friendly);
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

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="panel history-panel" onkeydown={onPanelKeyDown}>
  <header class="panel-header">
    <span class="section-heading">History</span>
    <button
      type="button"
      class="select-toggle"
      onclick={toggleSelectMode}
      use:tooltip={selectMode
        ? 'Exit select mode (Esc)'
        : 'Select — then Ctrl+click to toggle, Shift+click for range, Ctrl+A for all'}
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
      {#each filteredCompletedItems as item, idx (item.id)}
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
            class:select-mode={selectMode}
            class:selected={selectedIds.has(item.id)}
            data-row-id={item.id}
            style="--accent: {taxonomyColor(item.domain)};"
            onclick={(e) => onRowClick(e, item, idx)}
          >
            {#if selectMode}
              <!-- Absolute-positioned at vertical center, left-most edge of
                   the card. .history-row is column-flex, so leaving the
                   checkbox in the flow puts it above the content (wrong —
                   it should sit centered beside the two-line card). The row
                   gets select-mode class which shifts its padding-left to
                   reserve the 14px checkbox + 8px gap. -->
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
              {#if !selectMode}
                <!-- × lives inside history-meta as the last flex child so it
                     never overlaps row-time (on the line above). Hidden by
                     default via opacity; reveals on row hover / focus-within.
                     Hidden entirely in selectMode - the selection toolbar is
                     the destructive entry point there, not a per-row ×. -->
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
                  use:tooltip={'Delete'}
                >×</span>
              {/if}
            </div>
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
      {@const truncated = preview.length > 56 ? preview.slice(0, 56) + '…' : preview}
      <li>
        <!-- Each preview row pairs prompt text with a mono timestamp so
             duplicate prompts (common when the same user re-runs the same
             raw prompt) stay distinguishable in the confirm dialog. -->
        <span class="bulk-preview-text">{truncated}</span>
        <span class="bulk-preview-time font-mono">{formatRelativeTime(opt.created_at)}</span>
      </li>
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

  /* Selected row in select mode — cyan tint to reinforce the checkbox
     state. Subordinate to --active; when both apply the active state
     (domain-accent) wins on border/text, the selected state only
     contributes the surface tint. */
  .history-row.selected {
    background: color-mix(in srgb, var(--color-neon-cyan) 6%, transparent);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 20%, transparent);
  }
  .history-row.selected:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 10%, transparent);
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
    /* Last flex child of .history-meta. `margin-left: auto` pushes the ×
       to the far-right edge of the meta line, so it sits DIRECTLY BELOW
       the timestamp on the prompt line above (both pinned to the right
       edge of the card). Width reserved at 14px so hover-reveal doesn't
       cause layout jitter. */
    margin-left: auto;
    width: 14px;
    height: 14px;
    flex-shrink: 0;
    border: 1px solid transparent;
    background: transparent;
    color: var(--color-neon-red);
    font-size: 12px;
    line-height: 1;
    opacity: 0;
    pointer-events: none;
    cursor: pointer;
    /* Flat edges — brand default. */
    border-radius: 0;
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
    border-color: color-mix(in srgb, var(--color-neon-red) 40%, transparent);
  }
  .row-delete-btn:active {
    /* Brand active state — border contracts toward subtle. */
    border-color: var(--color-border-subtle);
  }

  .row-delete-btn:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  /* Pre-commit grace window — user can still Undo. Soft opacity cue +
     strike-through on the primary prompt text only; we avoid striking
     colored badges (score, feedback arrow, cluster id) since struck
     text overlaps badge color fills and reads as noise. */
  .row-item.pending-delete {
    opacity: 0.4;
    cursor: default;
  }
  .row-item.pending-delete :global(.row-prompt) {
    text-decoration: line-through;
    text-decoration-color: var(--color-text-dim);
  }

  /* Post-commit, pre-SSE-reconcile — API call in flight, undo no
     longer possible. Cyan left-edge accent signals "action in motion"
     (brand primary chroma) + the row stays at reduced opacity. */
  .row-item.deleting {
    opacity: 0.3;
    cursor: wait;
    pointer-events: none;
    box-shadow: inset 2px 0 0 0 var(--color-neon-cyan);
  }
  .row-item.deleting :global(.row-prompt) {
    text-decoration: line-through;
    text-decoration-color: var(--color-text-dim);
  }

  .toast-stack {
    position: fixed;
    bottom: 24px;
    right: 24px;
    display: flex;
    flex-direction: column-reverse;
    gap: 6px;
    /* Brand z-index tier 100 (Popover) — above modals (tier 50), below
       the emergency tier (9999) reserved for skip links / CommandPalette.
       The pre-commit grace-window toast stays reachable even if a
       DestructiveConfirmModal is open over it. */
    z-index: 100;
  }

  /* Reduced-motion is enforced globally in app.css. */

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
    line-height: 18px;
    background: transparent;
    border: 1px solid transparent;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    /* Flat edges — brand default. */
    border-radius: 0;
    cursor: pointer;
    transition: background 200ms var(--ease-spring), border-color 200ms var(--ease-spring);
  }
  .select-toggle:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-subtle);
  }
  .select-toggle:active {
    border-color: transparent;
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
    line-height: 18px;
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 500;
    /* Flat edges — brand default. */
    border-radius: 0;
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
  .btn-cancel-toolbar:active {
    border-color: transparent;
  }
  .btn-delete-toolbar {
    background: transparent;
    border: 1px solid var(--color-neon-red);
    color: var(--color-neon-red);
  }
  .btn-delete-toolbar:hover {
    background: color-mix(in srgb, var(--color-neon-red) 12%, transparent);
  }
  .btn-delete-toolbar:active {
    border-color: color-mix(in srgb, var(--color-neon-red) 40%, transparent);
  }
  .btn-cancel-toolbar:focus-visible,
  .btn-delete-toolbar:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  /* ── Row checkbox ──────────────────────────────────────────────── */

  /* ── Select-mode row padding shift ─────────────────────────────── */

  /* Reserve 22px of left padding (14px checkbox + 8px gap) when the row
     is in select mode. Keeps content right-aligned to the checkbox. */
  .history-row.select-mode {
    padding-left: 30px;
  }

  .row-checkbox {
    width: 14px;
    height: 14px;
    appearance: none;
    border: 1px solid var(--color-border-subtle);
    /* Flat edges — brand default. */
    border-radius: 0;
    background: transparent;
    cursor: pointer;
    /* Float at center-middle-left of the card, outside the column flow so
       it doesn't stack on top of the prompt line. */
    position: absolute;
    left: 8px;
    top: 50%;
    transform: translateY(-50%);
    flex-shrink: 0;
    transition: border-color 200ms var(--ease-spring), background 200ms var(--ease-spring);
  }
  .row-checkbox:hover {
    border-color: color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
  }
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
    padding: 4px 0;
    color: var(--color-text-primary);
    display: flex;
    align-items: baseline;
    gap: 6px;
  }
  /* Prompt text grows to fill and ellipses if the browser chose to wrap. */
  .bulk-preview-text {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* Mono timestamp anchored to the right — disambiguates rows that share
     the same raw prompt text. */
  .bulk-preview-time {
    flex-shrink: 0;
    font-size: 9px;
    color: var(--color-text-dim);
  }
  /* Hairline separator between preview rows — matches the brand's
     "1 px contour" language for ambient separation without adding
     weight. First row has no top border; last row has no bottom. */
  .bulk-preview-list li + li {
    border-top: 1px solid var(--color-border-subtle);
  }
  .bulk-preview-list li.more {
    color: var(--color-text-dim);
    font-size: 10px;
  }
</style>
