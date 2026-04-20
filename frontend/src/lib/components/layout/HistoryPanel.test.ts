import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';
import { mockHistoryItem } from '$lib/test-utils';

vi.mock('$lib/api/client', () => ({
  getHistory: vi.fn(),
  getOptimization: vi.fn(),
  updateOptimization: vi.fn(),
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
}));

import HistoryPanel from './HistoryPanel.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { projectStore } from '$lib/stores/project.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { addToast } from '$lib/stores/toast.svelte';
import * as apiClient from '$lib/api/client';

function historyResp(items: ReturnType<typeof mockHistoryItem>[], has_more = false, next_offset: number | null = null) {
  return {
    items,
    total: items.length,
    count: items.length,
    offset: 0,
    has_more,
    next_offset,
  };
}

describe('HistoryPanel', () => {
  beforeEach(() => {
    forgeStore._reset();
    editorStore._reset?.();
    projectStore._reset?.();
    clustersStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  describe('lazy load', () => {
    it('does not fetch history when active=false', () => {
      render(HistoryPanel, { props: { active: false } });
      expect(apiClient.getHistory).not.toHaveBeenCalled();
    });

    it('fetches history once when active=true', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(historyResp([]) as never);
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(apiClient.getHistory).toHaveBeenCalledTimes(1));
      expect(apiClient.getHistory).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 50, sort_by: 'created_at', sort_order: 'desc' }),
      );
    });

    it('renders items after successful fetch', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([
          mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'First prompt', trace_id: 'tr-1' }),
          mockHistoryItem({ id: 'h-2', status: 'completed', intent_label: 'Second prompt', trace_id: 'tr-2' }),
        ]) as never,
      );
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => {
        expect(screen.getByText('First prompt')).toBeInTheDocument();
        expect(screen.getByText('Second prompt')).toBeInTheDocument();
      });
    });

    it('shows empty-state when no completed items', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(historyResp([]) as never);
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => {
        expect(screen.getByText(/No optimizations yet/i)).toBeInTheDocument();
      });
    });

    it('shows error message when fetch fails', async () => {
      vi.mocked(apiClient.getHistory).mockRejectedValueOnce(new Error('boom'));
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => {
        expect(screen.getByText('boom')).toBeInTheDocument();
      });
    });
  });

  describe('pagination', () => {
    it('renders Load more button when has_more=true', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([mockHistoryItem({ status: 'completed' })], true, 50) as never,
      );
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /Load more/i })).toBeInTheDocument();
      });
    });

    it('Load more fetches additional page and appends', async () => {
      vi.mocked(apiClient.getHistory)
        .mockResolvedValueOnce(
          historyResp(
            [mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'Page 1 item' })],
            true,
            50,
          ) as never,
        )
        .mockResolvedValueOnce(
          historyResp(
            [mockHistoryItem({ id: 'h-2', status: 'completed', intent_label: 'Page 2 item' })],
            false,
            null,
          ) as never,
        );
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('Page 1 item')).toBeInTheDocument());

      await fireEvent.click(screen.getByRole('button', { name: /Load more/i }));

      await waitFor(() => {
        expect(apiClient.getHistory).toHaveBeenCalledTimes(2);
        expect(screen.getByText('Page 2 item')).toBeInTheDocument();
      });
    });
  });

  describe('rename flow', () => {
    it('double-click opens inline rename form', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'Original' })]) as never,
      );
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('Original')).toBeInTheDocument());

      await fireEvent.dblClick(screen.getByText('Original'));
      await waitFor(() => expect(screen.getByLabelText('Rename optimization')).toBeInTheDocument());
    });

    it('submit rename calls updateOptimization and updates list', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'Original' })]) as never,
      );
      vi.mocked(apiClient.updateOptimization).mockResolvedValueOnce(undefined as never);
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('Original')).toBeInTheDocument());
      await fireEvent.dblClick(screen.getByText('Original'));

      const input = await screen.findByLabelText('Rename optimization');
      await fireEvent.input(input, { target: { value: 'Renamed' } });
      await fireEvent.submit(input.closest('form') as HTMLFormElement);

      await waitFor(() => {
        expect(apiClient.updateOptimization).toHaveBeenCalledWith('h-1', { intent_label: 'Renamed' });
        expect(screen.getByText('Renamed')).toBeInTheDocument();
      });
    });

    it('toasts on rename failure', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'Original' })]) as never,
      );
      vi.mocked(apiClient.updateOptimization).mockRejectedValueOnce(new Error('nope'));
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('Original')).toBeInTheDocument());
      await fireEvent.dblClick(screen.getByText('Original'));
      const input = await screen.findByLabelText('Rename optimization');
      await fireEvent.input(input, { target: { value: 'X' } });
      await fireEvent.submit(input.closest('form') as HTMLFormElement);
      await waitFor(() => {
        expect(addToast).toHaveBeenCalledWith('deleted', 'Rename failed');
      });
    });

    it('Cancel button closes rename without saving', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'Original' })]) as never,
      );
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('Original')).toBeInTheDocument());
      await fireEvent.dblClick(screen.getByText('Original'));
      await screen.findByLabelText('Rename optimization');
      await fireEvent.click(screen.getByLabelText('Cancel'));
      await waitFor(() => expect(screen.queryByLabelText('Rename optimization')).toBeNull());
      expect(apiClient.updateOptimization).not.toHaveBeenCalled();
    });
  });

  describe('project filter', () => {
    it('filters items by projectStore.currentProjectId when set', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([
          mockHistoryItem({ id: 'h-1', status: 'completed', intent_label: 'Scoped', project_id: 'proj-A' }),
          mockHistoryItem({ id: 'h-2', status: 'completed', intent_label: 'Other', project_id: 'proj-B' }),
        ]) as never,
      );
      projectStore.setCurrent?.('proj-A');
      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('Scoped')).toBeInTheDocument());
      expect(screen.queryByText('Other')).toBeNull();
    });
  });

  describe('cross-tab navigation', () => {
    it('cluster-link click dispatches switch-activity event and selects cluster', async () => {
      vi.mocked(apiClient.getHistory).mockResolvedValueOnce(
        historyResp([
          mockHistoryItem({
            id: 'h-1',
            status: 'completed',
            intent_label: 'With cluster',
            cluster_id: 'c-1',
          }),
        ]) as never,
      );
      clustersStore.taxonomyTree = [
        {
          id: 'c-1',
          parent_id: null,
          label: 'API patterns',
          state: 'active',
          domain: 'backend',
          task_type: 'coding',
          persistence: null,
          coherence: null,
          separation: null,
          stability: null,
          member_count: 3,
          usage_count: 5,
          avg_score: 7.0,
          color_hex: null,
          umap_x: null,
          umap_y: null,
          umap_z: null,
          preferred_strategy: null,
          template_count: 0,
          created_at: '2026-03-15T10:00:00Z',
        },
      ] as never;

      const listener = vi.fn();
      window.addEventListener('switch-activity', listener);

      render(HistoryPanel, { props: { active: true } });
      await waitFor(() => expect(screen.getByText('With cluster')).toBeInTheDocument());

      // Cluster label is rendered with truncation — match by regex so the
      // test doesn't break if the truncation cutoff changes.
      const clusterLink = screen.getByText(/API patt/);
      await fireEvent.click(clusterLink);

      await waitFor(() => {
        expect(listener).toHaveBeenCalledTimes(1);
        const evt = listener.mock.calls[0][0] as CustomEvent;
        expect(evt.detail).toBe('clusters');
      });

      window.removeEventListener('switch-activity', listener);
    });
  });
});
