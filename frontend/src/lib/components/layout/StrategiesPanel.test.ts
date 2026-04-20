import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api/client', () => ({
  getStrategy: vi.fn(),
  updateStrategy: vi.fn(),
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
}));

import StrategiesPanel from './StrategiesPanel.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { addToast } from '$lib/stores/toast.svelte';
import * as apiClient from '$lib/api/client';

function mockStrategy(overrides: Record<string, unknown> = {}) {
  return {
    name: 'chain-of-thought',
    description: 'Decompose reasoning into explicit steps.',
    tagline: 'CoT',
    ...overrides,
  };
}

describe('StrategiesPanel', () => {
  beforeEach(() => {
    forgeStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  describe('rendering', () => {
    it('renders a row per strategy', () => {
      render(StrategiesPanel, {
        props: {
          strategies: [mockStrategy({ name: 'cot' }), mockStrategy({ name: 'tot' })],
          onSaved: vi.fn(),
        },
      });
      expect(screen.getByText('cot')).toBeInTheDocument();
      expect(screen.getByText('tot')).toBeInTheDocument();
    });

    it('shows empty-state when no strategies', () => {
      render(StrategiesPanel, {
        props: { strategies: [], onSaved: vi.fn() },
      });
      expect(screen.getByText(/No strategy files found/i)).toBeInTheDocument();
    });
  });

  describe('strategy selection', () => {
    it('selecting a strategy sets forgeStore.strategy', async () => {
      render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByText('cot'));
      expect(forgeStore.strategy).toBe('cot');
    });

    it('re-clicking an active strategy deselects it', async () => {
      forgeStore.strategy = 'cot';
      render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByText('cot'));
      expect(forgeStore.strategy).toBeNull();
    });

    it('marks the active strategy row with --active class', () => {
      forgeStore.strategy = 'cot';
      const { container } = render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      expect(container.querySelector('.strat-row--active')).not.toBeNull();
    });

    it('Enter key on row selects strategy', async () => {
      render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      const row = screen.getByText('cot').closest('[role="button"]') as HTMLElement;
      await fireEvent.keyDown(row, { key: 'Enter' });
      expect(forgeStore.strategy).toBe('cot');
    });
  });

  describe('strategy editor', () => {
    it('opens the inline editor and fetches content on edit-button click', async () => {
      vi.mocked(apiClient.getStrategy).mockResolvedValueOnce({
        name: 'cot',
        description: 'CoT',
        content: '# Chain of Thought\n',
      } as never);
      render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByLabelText('Edit template'));
      await waitFor(() => {
        expect(apiClient.getStrategy).toHaveBeenCalledWith('cot');
        expect(screen.getByText('prompts/strategies/cot.md')).toBeInTheDocument();
      });
    });

    it('clicking edit-button a second time closes editor (toggle)', async () => {
      vi.mocked(apiClient.getStrategy).mockResolvedValue({
        name: 'cot',
        description: 'CoT',
        content: '# x',
      } as never);
      const { container } = render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      const editBtn = screen.getByLabelText('Edit template');
      await fireEvent.click(editBtn);
      await waitFor(() => expect(container.querySelector('.strategy-editor')).not.toBeNull());
      await fireEvent.click(editBtn);
      await waitFor(() => expect(container.querySelector('.strategy-editor')).toBeNull());
    });

    it('shows toast when getStrategy fails', async () => {
      vi.mocked(apiClient.getStrategy).mockRejectedValueOnce(new Error('fetch failed'));
      render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByLabelText('Edit template'));
      await waitFor(() => {
        expect(addToast).toHaveBeenCalledWith('deleted', expect.stringContaining('load'));
      });
    });

    it('SAVE calls updateStrategy + onSaved and clears dirty', async () => {
      vi.mocked(apiClient.getStrategy).mockResolvedValueOnce({
        name: 'cot',
        description: 'CoT',
        content: 'original',
      } as never);
      vi.mocked(apiClient.updateStrategy).mockResolvedValueOnce(undefined as never);
      const onSaved = vi.fn();
      const { container } = render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved },
      });
      await fireEvent.click(screen.getByLabelText('Edit template'));
      await waitFor(() => expect(container.querySelector('.strategy-textarea')).not.toBeNull());

      const textarea = container.querySelector('.strategy-textarea') as HTMLTextAreaElement;
      await fireEvent.input(textarea, { target: { value: 'edited' } });

      await fireEvent.click(screen.getByRole('button', { name: /^SAVE$/ }));

      await waitFor(() => {
        expect(apiClient.updateStrategy).toHaveBeenCalledWith('cot', 'edited');
        expect(onSaved).toHaveBeenCalledWith('cot');
      });
    });

    it('SAVE shows toast when updateStrategy fails', async () => {
      vi.mocked(apiClient.getStrategy).mockResolvedValueOnce({
        name: 'cot',
        description: 'CoT',
        content: 'original',
      } as never);
      vi.mocked(apiClient.updateStrategy).mockRejectedValueOnce(new Error('save failed'));
      const { container } = render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByLabelText('Edit template'));
      await waitFor(() => expect(container.querySelector('.strategy-textarea')).not.toBeNull());

      const textarea = container.querySelector('.strategy-textarea') as HTMLTextAreaElement;
      await fireEvent.input(textarea, { target: { value: 'new' } });
      await fireEvent.click(screen.getByRole('button', { name: /^SAVE$/ }));

      await waitFor(() => {
        expect(addToast).toHaveBeenCalledWith('deleted', expect.stringContaining('save'));
      });
    });

    it('SAVE is disabled when not dirty', async () => {
      vi.mocked(apiClient.getStrategy).mockResolvedValueOnce({
        name: 'cot',
        description: 'CoT',
        content: 'original',
      } as never);
      const { container } = render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByLabelText('Edit template'));
      await waitFor(() => expect(container.querySelector('.strategy-textarea')).not.toBeNull());
      const saveBtn = screen.getByRole('button', { name: /^SAVE$/ });
      expect(saveBtn).toBeDisabled();
    });

    it('DISCARD closes editor without persisting', async () => {
      vi.mocked(apiClient.getStrategy).mockResolvedValueOnce({
        name: 'cot',
        description: 'CoT',
        content: 'original',
      } as never);
      const { container } = render(StrategiesPanel, {
        props: { strategies: [mockStrategy({ name: 'cot' })], onSaved: vi.fn() },
      });
      await fireEvent.click(screen.getByLabelText('Edit template'));
      await waitFor(() => expect(container.querySelector('.strategy-textarea')).not.toBeNull());
      await fireEvent.click(screen.getByRole('button', { name: /DISCARD/i }));
      await waitFor(() => expect(container.querySelector('.strategy-editor')).toBeNull());
      expect(apiClient.updateStrategy).not.toHaveBeenCalled();
    });
  });
});
