import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/svelte';
import DrillIntoClusterModal from './DrillIntoClusterModal.svelte';

const sampleCluster = {
  id: 'c-abc',
  label: 'react testing',
  domain: 'frontend',
  task_type: 'coding',
};

describe('DrillIntoClusterModal', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('pre-fills topic from cluster.label and disables Launch when topic < 3 chars', async () => {
    const { getByRole } = render(DrillIntoClusterModal, {
      cluster: sampleCluster,
      onClose: vi.fn(),
      onDrilled: vi.fn(),
    });
    const input = getByRole('textbox') as HTMLInputElement;
    expect(input.value).toBe('react testing');

    const launchBtn = getByRole('button', { name: /launch/i }) as HTMLButtonElement;
    expect(launchBtn.disabled).toBe(false);

    await fireEvent.input(input, { target: { value: 'ab' } });
    expect(launchBtn.disabled).toBe(true);
  });

  it('submits to /api/clusters/{id}/drill on 202 and calls onDrilled with run_id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 202,
      json: () => Promise.resolve({
        run_id: 'rr-new',
        poll_url: '/api/runs/rr-new',
        source_cluster_id: 'c-abc',
        started_at: '2026-05-19T18:42:00+00:00',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const onDrilled = vi.fn();
    const { getByRole } = render(DrillIntoClusterModal, {
      cluster: sampleCluster,
      onClose: vi.fn(),
      onDrilled,
    });

    await fireEvent.click(getByRole('button', { name: /launch/i }));
    await new Promise((r) => setTimeout(r, 10));

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/clusters/c-abc/drill',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ topic: 'react testing' }),
      }),
    );
    expect(onDrilled).toHaveBeenCalledWith('rr-new');
  });

  it('displays inline error on 4xx response; modal stays open', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json: () => Promise.resolve({ detail: 'cluster_not_found' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const onDrilled = vi.fn();
    const onClose = vi.fn();
    const { getByRole, findByText } = render(DrillIntoClusterModal, {
      cluster: sampleCluster,
      onClose,
      onDrilled,
    });

    await fireEvent.click(getByRole('button', { name: /launch/i }));
    const err = await findByText(/cluster_not_found/);
    expect(err).toBeTruthy();
    expect(onDrilled).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it('displays "network_error" on fetch rejection', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error('boom'));
    vi.stubGlobal('fetch', fetchMock);

    const { getByRole, findByText } = render(DrillIntoClusterModal, {
      cluster: sampleCluster,
      onClose: vi.fn(),
      onDrilled: vi.fn(),
    });

    await fireEvent.click(getByRole('button', { name: /launch/i }));
    const err = await findByText(/network_error/);
    expect(err).toBeTruthy();
  });
});
