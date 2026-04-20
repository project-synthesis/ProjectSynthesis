import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/svelte';

vi.mock('$lib/api/client', () => ({
  getSettings: vi.fn(),
  getProviders: vi.fn(),
  getApiKey: vi.fn(),
  setApiKey: vi.fn(),
  deleteApiKey: vi.fn(),
  getPreferences: vi.fn(),
  patchPreferences: vi.fn(),
  githubMe: vi.fn(),
  githubLinked: vi.fn(),
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
  toastStore: { add: vi.fn(), toasts: [] },
}));

import SettingsPanel from './SettingsPanel.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { githubStore } from '$lib/stores/github.svelte';
import { addToast } from '$lib/stores/toast.svelte';
import * as apiClient from '$lib/api/client';

function mockSettingsResponse(overrides: Record<string, unknown> = {}) {
  return {
    models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
    pipeline: {
      enable_explore: true,
      enable_scoring: true,
      enable_strategy_intelligence: true,
      force_sampling: false,
      force_passthrough: false,
      optimizer_effort: 'high',
      analyzer_effort: 'low',
      scorer_effort: 'low',
    },
    model_catalog: [
      {
        tier: 'sonnet',
        label: 'Sonnet',
        model_id: 'claude-sonnet-4-6',
        supported_efforts: ['low', 'medium', 'high'],
      },
      {
        tier: 'opus',
        label: 'Opus',
        model_id: 'claude-opus-4-6',
        supported_efforts: ['low', 'medium', 'high'],
      },
      {
        tier: 'haiku',
        label: 'Haiku',
        model_id: 'claude-haiku-4-5',
        supported_efforts: ['low'],
      },
    ],
    ...overrides,
  };
}

function mockProvidersResponse(overrides: Record<string, unknown> = {}) {
  return {
    provider: 'claude-cli',
    active_tier: 'internal',
    routing_tiers: ['internal', 'passthrough'],
    ...overrides,
  };
}

describe('SettingsPanel', () => {
  beforeEach(() => {
    forgeStore._reset();
    preferencesStore._reset?.();
    githubStore._reset();
    githubStore.uiTab = 'info';
    vi.clearAllMocks();

    // Sensible defaults so initial render doesn't throw
    vi.mocked(apiClient.getSettings).mockResolvedValue(mockSettingsResponse() as never);
    vi.mocked(apiClient.getProviders).mockResolvedValue(mockProvidersResponse() as never);
    vi.mocked(apiClient.getApiKey).mockResolvedValue({ configured: false, masked_key: null } as never);
    vi.mocked(apiClient.patchPreferences).mockImplementation(async (patch) => {
      // Merge patch into current prefs so the store updates reflect
      const merged = structuredClone(preferencesStore.prefs);
      if (patch.pipeline) Object.assign(merged.pipeline, patch.pipeline);
      if (patch.models) Object.assign(merged.models, patch.models);
      return merged as never;
    });
  });

  afterEach(() => {
    cleanup();
  });

  describe('tier branching — internal', () => {
    it('renders Models dropdowns when tier is internal', async () => {
      // No force flags, forgeStore.provider set → internal
      forgeStore.provider = 'claude-cli';
      render(SettingsPanel, { props: { active: true, strategies: [] } });
      await waitFor(() => {
        expect(screen.getByLabelText('Analyzer model')).toBeInTheDocument();
        expect(screen.getByLabelText('Optimizer model')).toBeInTheDocument();
        expect(screen.getByLabelText('Scorer model')).toBeInTheDocument();
      });
    });

    it('populates model dropdown options from model_catalog', async () => {
      forgeStore.provider = 'claude-cli';
      render(SettingsPanel, { props: { active: true, strategies: [] } });
      await waitFor(() => {
        const analyzer = screen.getByLabelText('Analyzer model') as HTMLSelectElement;
        expect(analyzer.querySelectorAll('option').length).toBe(3);
      });
    });
  });

  describe('tier branching — passthrough', () => {
    it('renders Context sub-heading instead of Models when force_passthrough', async () => {
      preferencesStore.prefs.pipeline.force_passthrough = true;
      render(SettingsPanel, { props: { active: true, strategies: [] } });
      await waitFor(() => {
        expect(screen.getByText('Context')).toBeInTheDocument();
      });
    });
  });

  describe('effort degradation', () => {
    it('auto-degrades optimizer_effort when selected model does not support current effort', async () => {
      forgeStore.provider = 'claude-cli';
      preferencesStore.prefs.models.optimizer = 'haiku';
      preferencesStore.prefs.pipeline.optimizer_effort = 'high';

      render(SettingsPanel, { props: { active: true, strategies: [] } });

      await waitFor(() => {
        expect(apiClient.patchPreferences).toHaveBeenCalledWith(
          expect.objectContaining({
            pipeline: expect.objectContaining({ optimizer_effort: 'low' }),
          }),
        );
      });
    });

    it('does NOT degrade effort when model supports current effort', async () => {
      forgeStore.provider = 'claude-cli';
      preferencesStore.prefs.models.optimizer = 'opus';
      preferencesStore.prefs.pipeline.optimizer_effort = 'high';

      render(SettingsPanel, { props: { active: true, strategies: [] } });

      // Allow the $effect a tick to run
      await new Promise((r) => setTimeout(r, 20));

      const calls = vi.mocked(apiClient.patchPreferences).mock.calls;
      const effortCalls = calls.filter((c) =>
        (c[0] as Record<string, unknown>)?.pipeline &&
        'optimizer_effort' in ((c[0] as { pipeline: Record<string, unknown> }).pipeline),
      );
      expect(effortCalls.length).toBe(0);
    });
  });

  describe('API key lifecycle', () => {
    it('SET calls setApiKey with input value and surfaces success toast', async () => {
      forgeStore.provider = 'claude-cli';
      vi.mocked(apiClient.setApiKey).mockResolvedValueOnce({
        configured: true,
        masked_key: 'sk-...abc',
      } as never);
      render(SettingsPanel, { props: { active: true, strategies: [] } });

      // Expand the Provider accordion header
      await waitFor(() => expect(screen.getByText(/Provider/)).toBeInTheDocument());
      await fireEvent.click(screen.getByText(/Provider/));

      const input = await screen.findByLabelText('Anthropic API key');
      await fireEvent.input(input, { target: { value: 'sk-test-123' } });
      await fireEvent.click(screen.getByRole('button', { name: /^SET$/ }));

      await waitFor(() => {
        expect(apiClient.setApiKey).toHaveBeenCalledWith('sk-test-123');
        expect(addToast).toHaveBeenCalledWith('created', 'API key saved');
      });
    });

    it('DEL requires two clicks (confirmation) before deleteApiKey fires', async () => {
      forgeStore.provider = 'claude-cli';
      vi.mocked(apiClient.getApiKey).mockResolvedValue({
        configured: true,
        masked_key: 'sk-...abc',
      } as never);
      vi.mocked(apiClient.deleteApiKey).mockResolvedValueOnce({
        configured: false,
        masked_key: null,
      } as never);

      render(SettingsPanel, { props: { active: true, strategies: [] } });
      await waitFor(() => expect(screen.getByText(/Provider/)).toBeInTheDocument());
      await fireEvent.click(screen.getByText(/Provider/));

      const delBtn = await screen.findByRole('button', { name: /^DEL$/ });
      await fireEvent.click(delBtn);
      // First click arms confirmation — no API call yet
      expect(apiClient.deleteApiKey).not.toHaveBeenCalled();

      const confirmBtn = await screen.findByRole('button', { name: /OK\?/ });
      await fireEvent.click(confirmBtn);

      await waitFor(() => {
        expect(apiClient.deleteApiKey).toHaveBeenCalled();
        expect(addToast).toHaveBeenCalledWith('deleted', 'API key removed');
      });
    });

    it('SET surfaces error when setApiKey rejects', async () => {
      forgeStore.provider = 'claude-cli';
      vi.mocked(apiClient.setApiKey).mockRejectedValueOnce(new Error('bad key'));
      render(SettingsPanel, { props: { active: true, strategies: [] } });
      await waitFor(() => expect(screen.getByText(/Provider/)).toBeInTheDocument());
      await fireEvent.click(screen.getByText(/Provider/));

      const input = await screen.findByLabelText('Anthropic API key');
      await fireEvent.input(input, { target: { value: 'sk-bad' } });
      await fireEvent.click(screen.getByRole('button', { name: /^SET$/ }));

      await waitFor(() => {
        expect(screen.getByText(/bad key/)).toBeInTheDocument();
      });
    });
  });
});
