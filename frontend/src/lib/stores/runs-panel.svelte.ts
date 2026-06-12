// frontend/src/lib/stores/runs-panel.svelte.ts
//
// v0.4.37 D-link: minimal cross-panel run-selection API.
//
// SuiteDetailView's RUN link calls `requestSelect(runId)`. The store
// stashes the id and dispatches the existing `switch-activity` DOM
// CustomEvent (handled in routes/app/+layout.svelte) with detail 'runs'
// so the ActivityBar switches to the Runs panel. RunsPanel remounts
// (Navigator conditionally renders panels), reads `pendingSelectRunId`
// in an $effect, expands the row, and calls `clearPending()`.
//
// RunsPanel had no selection API before this release — `expandedId` was
// component-local state. This store is deliberately minimal: a single
// pending id, no history, no multi-select.

class RunsPanelStore {
  pendingSelectRunId = $state<string | null>(null);

  /** Stash a run id and activate the Runs panel. */
  requestSelect(runId: string): void {
    this.pendingSelectRunId = runId;
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'runs' }));
    }
  }

  clearPending(): void {
    this.pendingSelectRunId = null;
  }

  /** @internal Test-only: restore initial state. */
  _reset(): void {
    this.pendingSelectRunId = null;
  }
}

export const runsPanelStore = new RunsPanelStore();
