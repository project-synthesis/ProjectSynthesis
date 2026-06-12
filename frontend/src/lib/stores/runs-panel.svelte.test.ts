// frontend/src/lib/stores/runs-panel.svelte.test.ts
//
// v0.4.37 D-link: minimal run-selection API. SuiteDetailView's RUN link
// calls requestSelect(runId), which stashes the pending id and dispatches
// the existing `switch-activity` DOM CustomEvent with detail 'runs' so
// +layout.svelte activates the Runs panel (RunsPanel consumes the pending
// id on mount and expands the row).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { runsPanelStore } from './runs-panel.svelte';

describe('runsPanelStore', () => {
  beforeEach(() => {
    runsPanelStore._reset();
  });

  it('requestSelect stashes the id and dispatches switch-activity:runs', () => {
    const handler = vi.fn();
    window.addEventListener('switch-activity', handler);
    runsPanelStore.requestSelect('run-42');
    expect(runsPanelStore.pendingSelectRunId).toBe('run-42');
    expect(handler).toHaveBeenCalledTimes(1);
    expect((handler.mock.calls[0][0] as CustomEvent).detail).toBe('runs');
    window.removeEventListener('switch-activity', handler);
  });

  it('clearPending clears the stash', () => {
    runsPanelStore.requestSelect('run-42');
    runsPanelStore.clearPending();
    expect(runsPanelStore.pendingSelectRunId).toBeNull();
  });
});
