/**
 * v0.4.15 P0 RED — HistoryPanel pagination correctness via server-pushdown.
 *
 * Pins spec § 11 rows 5, 6, 7 + § 8 row 9 (defensive client-filter retention).
 */
import { render, cleanup } from '@testing-library/svelte';
import { afterEach, describe, it, expect, vi, beforeEach } from 'vitest';
import HistoryPanel from './HistoryPanel.svelte';

// Mock the API client + projectStore
vi.mock('$lib/api/client', () => ({
  getHistory: vi.fn().mockResolvedValue({
    total: 0, count: 0, offset: 0, items: [], has_more: false, next_offset: null,
  }),
  getOptimization: vi.fn(),
  deleteOptimization: vi.fn(),
  bulkDeleteOptimizations: vi.fn(),
  updateOptimization: vi.fn(),
}));

import { getHistory } from '$lib/api/client';
import { projectStore } from '$lib/stores/project.svelte';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('HistoryPanel passes currentProjectId on initial fetch (v0.4.15)', () => {
  beforeEach(() => {
    projectStore.setCurrent('p1');
  });

  it('calls getHistory with project_id and status=completed on initial fetch', async () => {
    render(HistoryPanel, { props: { active: true } });
    await new Promise((r) => setTimeout(r, 50));
    const mock = getHistory as unknown as ReturnType<typeof vi.fn>;
    expect(mock).toHaveBeenCalled();
    const args = mock.mock.calls[0][0];
    expect(args).toMatchObject({
      project_id: 'p1',
      status: 'completed',
      limit: 50,
      sort_by: 'created_at',
      sort_order: 'desc',
    });
  });
});

describe('HistoryPanel re-fetches on project switch (v0.4.15)', () => {
  beforeEach(() => {
    projectStore.setCurrent('p1');
  });

  it('re-issues getHistory with new project_id when projectStore.currentProjectId changes', async () => {
    render(HistoryPanel, { props: { active: true } });
    await new Promise((r) => setTimeout(r, 50));
    const mock = getHistory as unknown as ReturnType<typeof vi.fn>;
    const callsBeforeSwitch = mock.mock.calls.length;

    projectStore.setCurrent('p2');
    await new Promise((r) => setTimeout(r, 100));

    const callsAfterSwitch = mock.mock.calls.length;
    expect(callsAfterSwitch).toBeGreaterThan(callsBeforeSwitch);
    const lastArgs = mock.mock.calls[callsAfterSwitch - 1][0];
    expect(lastArgs).toMatchObject({ project_id: 'p2', status: 'completed' });
  });
});

describe('HistoryPanel filters non-completed row injected into state (v0.4.15)', () => {
  beforeEach(() => {
    projectStore.setCurrent('p1');
  });

  it('does not render a row whose status is not completed even if present in historyItems', async () => {
    // Mock returns one completed + one failed row from the server (defensive — backend
    // SHOULD filter these, but the client-side derivations are belt-and-suspenders).
    (getHistory as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      total: 2,
      count: 2,
      offset: 0,
      items: [
        { id: '1', trace_id: 't1', status: 'completed', project_id: 'p1', raw_prompt: 'ok', created_at: new Date().toISOString() },
        { id: '2', trace_id: 't2', status: 'failed', project_id: 'p1', raw_prompt: 'broken', created_at: new Date().toISOString() },
      ],
      has_more: false,
      next_offset: null,
    });

    const { queryByText } = render(HistoryPanel, { props: { active: true } });
    await new Promise((r) => setTimeout(r, 100));
    expect(queryByText(/ok/)).toBeTruthy();
    expect(queryByText(/broken/)).toBeNull();
  });
});
