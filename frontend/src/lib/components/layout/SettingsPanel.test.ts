import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/svelte';
import { mockFetch, mockHealthResponse } from '$lib/test-utils';
import SettingsPanel from './SettingsPanel.svelte';

const strategies = [
  { name: 'chain-of-thought', tagline: 'Step-by-step', description: 'desc' },
  { name: 'few-shot', tagline: 'Examples', description: 'desc' },
];

describe('SettingsPanel — smoke', () => {
  beforeEach(() => {
    mockFetch([
      {
        match: '/api/settings',
        response: {
          provider: 'claude-cli',
          models: { analyze: 'claude-sonnet-4-6', optimize: 'claude-sonnet-4-6', score: 'claude-haiku-4-5' },
          enable_scoring: true,
          enable_suggestions: true,
          enable_pattern_injection: true,
          enable_strategy_intelligence: true,
          default_strategy: null,
          effort_by_phase: { analyze: 'medium', optimize: 'medium', score: 'medium' },
          force_passthrough: false,
          force_sampling: false,
          domain_readiness_notifications: true,
        },
      },
      { match: '/api/providers', response: mockHealthResponse() },
      { match: '/api/provider/api-key', response: { set: false } },
    ]);
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it('mounts without throwing when inactive (lazy-load; no fetch)', () => {
    const { container } = render(SettingsPanel, { props: { active: false, strategies } });
    expect(container).toBeTruthy();
  });

  it('mounts without throwing when active and loads settings', async () => {
    const { container } = render(SettingsPanel, { props: { active: true, strategies } });
    expect(container).toBeTruthy();
    // Give the component a tick to issue its lazy fetches; just ensure it didn't crash.
    await waitFor(() => {
      expect(container.textContent?.length ?? 0).toBeGreaterThan(0);
    });
  });
});
