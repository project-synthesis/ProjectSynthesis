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
