import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/svelte';
import { mockFetch } from '$lib/test-utils';
import HistoryPanel from './HistoryPanel.svelte';

describe('HistoryPanel — smoke', () => {
  beforeEach(() => {
    mockFetch([
      { match: '/api/history', response: { items: [], total: 0, count: 0, offset: 0, has_more: false, next_offset: null } },
    ]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('mounts without throwing when inactive (no data fetched)', () => {
    const { container } = render(HistoryPanel, { props: { active: false } });
    expect(container).toBeTruthy();
  });

  it('mounts and shows the empty-state or loading indicator when active', async () => {
    render(HistoryPanel, { props: { active: true } });
    // Empty list → empty-state message; accept either "empty" language OR no items rendered.
    await waitFor(() => {
      const txt = document.body.textContent || '';
      expect(txt.length).toBeGreaterThan(0);
    });
  });

  it('renders history items when fetch returns data', async () => {
    mockFetch([
      {
        match: '/api/history',
        response: {
          items: [
            {
              id: 'opt-1',
              trace_id: 'trace-1',
              raw_prompt: 'Raw prompt text',
              intent_label: 'Sample intent',
              task_type: 'coding',
              strategy_used: 'chain-of-thought',
              overall_score: 7.5,
              status: 'completed',
              created_at: '2026-04-15T10:00:00Z',
              domain: 'backend',
              project_id: null,
            },
          ],
          total: 1,
          count: 1,
          offset: 0,
          has_more: false,
          next_offset: null,
        },
      },
    ]);
    render(HistoryPanel, { props: { active: true } });

    await waitFor(() => {
      expect(screen.getByText('Sample intent')).toBeInTheDocument();
    });
  });
});
