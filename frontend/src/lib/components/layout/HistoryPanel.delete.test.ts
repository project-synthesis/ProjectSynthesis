import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import HistoryPanel from './HistoryPanel.svelte';

// Mock getHistory so the test can control what rows HistoryPanel renders.
// Fields must match the real HistoryItem shape from $lib/api/client:
//   id, trace_id, raw_prompt, cluster_id, status, overall_score,
//   strategy_used, created_at, task_type, intent_label, domain,
//   project_id, feedback_rating
vi.mock('$lib/api/client', () => ({
  getHistory: vi.fn().mockResolvedValue({
    total: 2,
    count: 2,
    offset: 0,
    has_more: false,
    next_offset: null,
    items: [
      {
        id: 'opt-1',
        trace_id: 'trace-1',
        raw_prompt: 'row A',
        cluster_id: 'c1',
        cluster_label: null,        // not a real field; HistoryPanel derives from clusterLabelMap
        overall_score: 8.2,
        status: 'completed',
        strategy_used: 'chain-of-thought',
        created_at: '2026-04-23T00:00:00Z',
        task_type: 'coding',
        intent_label: 'row A',
        domain: 'backend',
        project_id: null,
        feedback_rating: null,
        duration_ms: 1000,
        provider: 'claude-cli',
        routing_tier: null,
        optimized_prompt: null,
      },
      {
        id: 'opt-2',
        trace_id: 'trace-2',
        raw_prompt: 'row B',
        cluster_id: null,
        cluster_label: null,
        overall_score: 7.1,
        status: 'completed',
        strategy_used: 'structured-output',
        created_at: '2026-04-22T23:00:00Z',
        task_type: 'writing',
        intent_label: 'row B',
        domain: null,
        project_id: null,
        feedback_rating: null,
        duration_ms: 1000,
        provider: 'claude-cli',
        routing_tier: null,
        optimized_prompt: null,
      },
    ],
  }),
  getOptimization: vi.fn(),
  updateOptimization: vi.fn(),
  // Stores invoked by reconcileAfterDelete (clustersStore.invalidateClusters,
  // domainStore.invalidate, readinessStore.invalidate) call apiFetch under the
  // hood. Stub it here so the store-side async fetches don't throw missing-
  // export errors inside our component tests.
  apiFetch: vi.fn().mockResolvedValue({}),
  tryFetch: vi.fn().mockResolvedValue(null),
  ApiError: class ApiError extends Error {
    status = 0;
    constructor(status: number, message?: string) { super(message); this.status = status; }
  },
}));

vi.mock('$lib/api/optimizations', () => ({
  deleteOptimization: vi.fn().mockResolvedValue({
    deleted: 1,
    requested: 1,
    affected_cluster_ids: ['c1'],
    affected_project_ids: [],
  }),
  deleteOptimizations: vi.fn(),
  ApiError: class ApiError extends Error {
    status = 0;
    constructor(status: number, message?: string) { super(message); this.status = status; }
  },
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
}));

import { deleteOptimization } from '$lib/api/optimizations';
import { toastsStore } from '$lib/stores/toasts.svelte';

describe('HistoryPanel — delete flow', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (deleteOptimization as ReturnType<typeof vi.fn>).mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('× button appears on row hover', async () => {
    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const rowA = screen.getByText('row A').closest('button')!;
    await fireEvent.mouseEnter(rowA);
    const xBtn = rowA.querySelector('[data-testid="row-delete-btn"]');
    expect(xBtn).not.toBeNull();
  });

  it('clicking × opens an UndoToast and defers API call until timer expires', async () => {
    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const rowA = screen.getByText('row A').closest('button')!;
    await fireEvent.mouseEnter(rowA);
    const xBtn = rowA.querySelector('[data-testid="row-delete-btn"]') as HTMLButtonElement;
    await fireEvent.click(xBtn);

    // toast present, API NOT called during grace window
    expect(screen.getByText(/deleting optimization/i)).toBeInTheDocument();
    expect(deleteOptimization).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(5000);
    expect(deleteOptimization).toHaveBeenCalledWith('opt-1');
  });

  it('clicking Undo cancels the commit — no API call fires', async () => {
    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const rowA = screen.getByText('row A').closest('button')!;
    await fireEvent.mouseEnter(rowA);
    const xBtn = rowA.querySelector('[data-testid="row-delete-btn"]') as HTMLButtonElement;
    await fireEvent.click(xBtn);

    const undoBtn = screen.getByRole('button', { name: /undo/i });
    await fireEvent.click(undoBtn);

    await vi.advanceTimersByTimeAsync(10000);
    expect(deleteOptimization).not.toHaveBeenCalled();
  });

  it('SSE optimization-deleted event surgically removes the matching row', async () => {
    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    window.dispatchEvent(new CustomEvent('optimization-deleted', {
      detail: { id: 'opt-2', cluster_id: null },
    }));

    await waitFor(() => {
      expect(screen.queryByText('row B')).toBeNull();
    });
    expect(screen.getByText('row A')).toBeInTheDocument();
  });
});

describe('HistoryPanel — delete error branches', () => {
  let pushSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    (deleteOptimization as ReturnType<typeof vi.fn>).mockReset();
    pushSpy = vi.spyOn(toastsStore, 'push');
  });
  afterEach(() => {
    pushSpy.mockRestore();
    vi.useRealTimers();
  });

  it('404 response reconciles locally and surfaces "Already deleted elsewhere." info toast', async () => {
    // 404 on a single delete means the row exists in the UI but not in the
    // DB (stale state from another-client delete, MCP tool, or corrupted
    // reference). Treat as soft success — remove the row locally, info toast
    // (not error). reconcileAfterDelete fires in the background: store
    // invalidations + historyLoaded reset so the next panel activation
    // verifies with backend truth.
    const { ApiError } = await import('$lib/api/optimizations');
    (deleteOptimization as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError(404, 'not found'),
    );

    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const rowA = screen.getByText('row A').closest('button')!;
    await fireEvent.mouseEnter(rowA);
    const xBtn = rowA.querySelector('[data-testid="row-delete-btn"]') as HTMLElement;
    await fireEvent.click(xBtn);

    // Let the grace window expire so commit fires
    await vi.advanceTimersByTimeAsync(5100);
    await vi.runAllTimersAsync();

    await waitFor(() => {
      expect(pushSpy).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'info', message: 'Already deleted elsewhere.' }),
      );
    });
  }, 15000);

  it('generic 500 response surfaces "Delete failed." toast', async () => {
    const { ApiError } = await import('$lib/api/optimizations');
    (deleteOptimization as ReturnType<typeof vi.fn>).mockRejectedValue(
      new ApiError(500, 'server error'),
    );

    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const rowA = screen.getByText('row A').closest('button')!;
    await fireEvent.mouseEnter(rowA);
    const xBtn = rowA.querySelector('[data-testid="row-delete-btn"]') as HTMLElement;
    await fireEvent.click(xBtn);

    await vi.advanceTimersByTimeAsync(5100);
    await vi.runAllTimersAsync();

    await waitFor(() => {
      expect(pushSpy).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'error', message: 'Delete failed.' }),
      );
    });
  }, 15000);
});

describe('HistoryPanel — re-entry guard', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (deleteOptimization as ReturnType<typeof vi.fn>).mockReset();
    (deleteOptimization as ReturnType<typeof vi.fn>).mockResolvedValue({
      deleted: 1,
      requested: 1,
      affected_cluster_ids: ['c1'],
      affected_project_ids: [],
    });
  });
  afterEach(() => vi.useRealTimers());

  it('rapid × clicks only fire the API once (re-entry guard)', async () => {
    render(HistoryPanel, { props: { active: true } });
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const rowA = screen.getByText('row A').closest('button')!;
    await fireEvent.mouseEnter(rowA);
    const xBtn = rowA.querySelector('[data-testid="row-delete-btn"]') as HTMLElement;

    // Rapid double-click
    await fireEvent.click(xBtn);
    await fireEvent.click(xBtn);

    await vi.advanceTimersByTimeAsync(5100);
    await vi.runAllTimersAsync();

    expect(deleteOptimization).toHaveBeenCalledTimes(1);
  }, 15000);
});

import { deleteOptimizations as bulkDeleteMock } from '$lib/api/optimizations';

describe('HistoryPanel — multi-select + bulk', () => {
  let pushSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers();
    (bulkDeleteMock as ReturnType<typeof vi.fn>).mockClear();
    (deleteOptimization as ReturnType<typeof vi.fn>).mockClear();
    (bulkDeleteMock as ReturnType<typeof vi.fn>).mockResolvedValue({
      deleted: 2,
      requested: 2,
      affected_cluster_ids: ['c1'],
      affected_project_ids: [],
    });
    pushSpy = vi.spyOn(toastsStore, 'push');
  });
  afterEach(() => {
    pushSpy.mockRestore();
    vi.useRealTimers();
  });

  it('Select mode toggles checkboxes on every row', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());

    const selectBtn = screen.getByRole('button', { name: /^select$/i });
    await fireEvent.click(selectBtn);
    expect(screen.getAllByRole('checkbox')).toHaveLength(2);
  });

  it('Selection toolbar appears when >= 1 row is checked', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());
    await fireEvent.click(screen.getByRole('button', { name: /^select$/i }));

    await fireEvent.click(screen.getAllByRole('checkbox')[0]);
    expect(screen.getByText(/\bselected\b/i)).toBeInTheDocument();
  });

  it('Bulk delete opens the confirm modal and fires bulk API on confirm', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());
    await fireEvent.click(screen.getByRole('button', { name: /^select$/i }));

    await fireEvent.click(screen.getAllByRole('checkbox')[0]);
    await fireEvent.click(screen.getAllByRole('checkbox')[1]);
    await fireEvent.click(screen.getByRole('button', { name: /delete 2/i }));

    await waitFor(() =>
      expect(screen.getByText(/delete 2 optimizations/i)).toBeInTheDocument(),
    );

    await fireEvent.input(screen.getByRole('textbox'), {
      target: { value: 'DELETE' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete 2' }));

    await waitFor(() => {
      expect(bulkDeleteMock).toHaveBeenCalledWith(['opt-1', 'opt-2']);
    });
  });

  it('Bulk 404 falls back to per-id DELETE and reconciles already-gone rows', async () => {
    // Simulates a deployment where the bulk endpoint isn't available yet
    // (e.g. older backend that predates v0.4.3). The UI should fall back to
    // the per-row DELETE /api/optimizations/{id} endpoint (shipped in v0.4.2)
    // and treat 404s on those as "already gone" — soft success, info toast,
    // modal closes, rows removed locally.
    const { ApiError } = await import('$lib/api/optimizations');
    (bulkDeleteMock as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new ApiError(404, 'not found'),
    );
    // Per-id delete fallback: one succeeds, one is already gone (404).
    (deleteOptimization as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        deleted: 1, requested: 1, affected_cluster_ids: [], affected_project_ids: [],
      })
      .mockRejectedValueOnce(new ApiError(404, 'not found'));

    render(HistoryPanel);
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());
    await fireEvent.click(screen.getByRole('button', { name: /^select$/i }));
    await fireEvent.click(screen.getAllByRole('checkbox')[0]);
    await fireEvent.click(screen.getAllByRole('checkbox')[1]);
    await fireEvent.click(screen.getByRole('button', { name: /delete 2/i }));

    await waitFor(() =>
      expect(screen.getByText(/delete 2 optimizations/i)).toBeInTheDocument(),
    );
    await fireEvent.input(screen.getByRole('textbox'), {
      target: { value: 'DELETE' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete 2' }));

    // Fallback fires per-id DELETE twice (once per selected id).
    await waitFor(() => {
      expect(deleteOptimization).toHaveBeenCalledTimes(2);
    });
    // Info toast reports the mixed result ("Deleted 1. 1 were already gone.").
    await waitFor(() => {
      expect(pushSpy).toHaveBeenCalledWith(
        expect.objectContaining({ kind: 'info', message: expect.stringMatching(/already gone/i) }),
      );
    });
  }, 15000);

  it('Bulk 404 with all-gone falls back to singles and shows "Already deleted elsewhere"', async () => {
    const { ApiError } = await import('$lib/api/optimizations');
    (bulkDeleteMock as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new ApiError(404, 'not found'),
    );
    (deleteOptimization as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new ApiError(404, 'not found'))
      .mockRejectedValueOnce(new ApiError(404, 'not found'));

    render(HistoryPanel);
    await vi.runAllTimersAsync();
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());
    await fireEvent.click(screen.getByRole('button', { name: /^select$/i }));
    await fireEvent.click(screen.getAllByRole('checkbox')[0]);
    await fireEvent.click(screen.getAllByRole('checkbox')[1]);
    await fireEvent.click(screen.getByRole('button', { name: /delete 2/i }));
    await waitFor(() =>
      expect(screen.getByText(/delete 2 optimizations/i)).toBeInTheDocument(),
    );
    await fireEvent.input(screen.getByRole('textbox'), { target: { value: 'DELETE' } });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete 2' }));

    await waitFor(() => {
      expect(pushSpy).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: 'info',
          message: expect.stringMatching(/All 2 were already deleted elsewhere/i),
        }),
      );
    });
  }, 15000);
});

// ── Keyboard shortcuts ──────────────────────────────────────────

describe('HistoryPanel — keyboard shortcuts', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    (deleteOptimization as ReturnType<typeof vi.fn>).mockClear();
  });
  afterEach(() => vi.useRealTimers());

  async function getRowButtons(): Promise<HTMLButtonElement[]> {
    await waitFor(() => expect(screen.queryByText('row A')).not.toBeNull());
    return Array.from(
      document.querySelectorAll<HTMLButtonElement>('.history-row[data-row-id]'),
    );
  }

  it('Ctrl+click toggles a row into selection and auto-enters select mode', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();
    expect(rows.length).toBeGreaterThanOrEqual(2);

    // Idle: no Select toggle fired. Ctrl+click should flip into select mode
    // and select the clicked row.
    await fireEvent.click(rows[0], { ctrlKey: true });
    expect(rows[0].classList.contains('selected')).toBe(true);
    // Selection toolbar now visible with count = 1.
    expect(screen.getByText(/\bselected\b/i)).toBeInTheDocument();

    // Ctrl+click again on same row → toggles off.
    await fireEvent.click(rows[0], { ctrlKey: true });
    expect(rows[0].classList.contains('selected')).toBe(false);
  });

  it('Shift+click extends selection from anchor to target (range select)', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();
    expect(rows.length).toBeGreaterThanOrEqual(2);

    // Establish anchor via Ctrl+click on the first row.
    await fireEvent.click(rows[0], { ctrlKey: true });
    expect(rows[0].classList.contains('selected')).toBe(true);

    // Shift+click the second row → both selected.
    await fireEvent.click(rows[1], { shiftKey: true });
    expect(rows[0].classList.contains('selected')).toBe(true);
    expect(rows[1].classList.contains('selected')).toBe(true);
  });

  it('Esc exits select mode and clears selection', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();
    await fireEvent.click(rows[0], { ctrlKey: true });
    expect(screen.getByText(/\bselected\b/i)).toBeInTheDocument();

    // Panel-level keydown — fire on the panel root.
    const panel = document.querySelector('.history-panel') as HTMLElement;
    await fireEvent.keyDown(panel, { key: 'Escape' });

    await waitFor(() => {
      expect(screen.queryByText(/\bselected\b/i)).toBeNull();
    });
    // `Select` header button should be back (not `Cancel`).
    expect(screen.getByRole('button', { name: /^select$/i })).toBeInTheDocument();
  });

  it('Ctrl+A in select mode selects all filtered rows', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();

    // Enter select mode (via header toggle).
    await fireEvent.click(screen.getByRole('button', { name: /^select$/i }));
    const panel = document.querySelector('.history-panel') as HTMLElement;
    await fireEvent.keyDown(panel, { key: 'a', ctrlKey: true });

    // Both rows should now be selected.
    expect(rows[0].classList.contains('selected')).toBe(true);
    expect(rows[1].classList.contains('selected')).toBe(true);
  });

  it('Ctrl+A outside select mode is a no-op (browser default preserved)', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();

    const panel = document.querySelector('.history-panel') as HTMLElement;
    await fireEvent.keyDown(panel, { key: 'a', ctrlKey: true });

    // Not in select mode → nothing selected.
    expect(rows[0].classList.contains('selected')).toBe(false);
    expect(rows[1].classList.contains('selected')).toBe(false);
  });

  it('Delete key on focused row triggers single-row delete grace window', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();

    rows[0].focus();
    await fireEvent.keyDown(rows[0], { key: 'Delete' });

    // UndoToast message appears; API call is deferred by the grace window.
    expect(screen.getByText(/deleting optimization/i)).toBeInTheDocument();
    expect(deleteOptimization).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(5000);
    expect(deleteOptimization).toHaveBeenCalledWith('opt-1');
  }, 15000);

  it('ArrowDown / ArrowUp move keyboard focus between rows', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();

    rows[0].focus();
    expect(document.activeElement).toBe(rows[0]);

    const panel = document.querySelector('.history-panel') as HTMLElement;
    await fireEvent.keyDown(panel, { key: 'ArrowDown' });
    expect(document.activeElement).toBe(rows[1]);

    await fireEvent.keyDown(panel, { key: 'ArrowUp' });
    expect(document.activeElement).toBe(rows[0]);
  });

  it('Home / End jump focus to first / last row', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();

    rows[0].focus();
    const panel = document.querySelector('.history-panel') as HTMLElement;
    await fireEvent.keyDown(panel, { key: 'End' });
    expect(document.activeElement).toBe(rows[rows.length - 1]);

    await fireEvent.keyDown(panel, { key: 'Home' });
    expect(document.activeElement).toBe(rows[0]);
  });

  it('Plain click in select mode toggles that row (does not load)', async () => {
    render(HistoryPanel);
    await vi.runAllTimersAsync();
    const rows = await getRowButtons();

    // Enter select mode; plain click should TOGGLE not LOAD.
    await fireEvent.click(screen.getByRole('button', { name: /^select$/i }));
    await fireEvent.click(rows[0]); // plain click in select mode
    expect(rows[0].classList.contains('selected')).toBe(true);

    await fireEvent.click(rows[0]); // toggle off
    expect(rows[0].classList.contains('selected')).toBe(false);
  });
});
