import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { mockFetch, mockHealthResponse, mockOptimizationResult } from '$lib/test-utils';

import StatusBar from './StatusBar.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { patternsStore } from '$lib/stores/patterns.svelte';

describe('StatusBar', () => {
  beforeEach(() => {
    forgeStore._reset();
    patternsStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the status bar element', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    expect(screen.getByRole('status', { name: 'Status bar' })).toBeInTheDocument();
  });

  it('shows Ctrl+K keyboard shortcut hint', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    expect(screen.getByLabelText('Open command palette with Ctrl+K')).toBeInTheDocument();
    expect(screen.getByText('Ctrl+K')).toBeInTheDocument();
  });

  it('shows provider badge (PASSTHROUGH when no provider loaded yet)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    // ProviderBadge renders PASSTHROUGH when provider is null (initial state)
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
  });

  it('shows pattern count when taxonomyStats has confirmed nodes > 0', async () => {
    // Set taxonomy stats with confirmed nodes
    patternsStore.taxonomyStats = {
      q_system: 0.8,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      nodes: { confirmed: 7, candidate: 2, retired: 0, max_depth: 3, leaf_count: 5 },
      last_warm_path: null,
      last_cold_path: null,
      q_history: [],
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.queryByText('7 patterns')).toBeInTheDocument();
    });
  });

  it('does not show pattern count when taxonomyStats is null', async () => {
    patternsStore.taxonomyStats = null;
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByText(/patterns/)).not.toBeInTheDocument();
  });

  it('shows "disconnected" label when MCP is disconnected', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.mcpDisconnected = true;
    render(StatusBar);
    expect(screen.getByText('disconnected')).toBeInTheDocument();
  });

  it('does not show "disconnected" when MCP is connected', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.mcpDisconnected = false;
    render(StatusBar);
    expect(screen.queryByText('disconnected')).not.toBeInTheDocument();
  });

  it('shows phase label while forge is analyzing', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'analyzing';
    render(StatusBar);
    expect(screen.getByText(/analyzing\.\.\./i)).toBeInTheDocument();
  });

  it('shows phase label while forge is optimizing', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'optimizing';
    render(StatusBar);
    expect(screen.getByText(/optimizing\.\.\./i)).toBeInTheDocument();
  });

  it('shows version from health API after load', async () => {
    mockFetch([{
      match: '/api/health',
      response: mockHealthResponse({ version: '1.2.3' }),
    }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.queryByText('v1.2.3')).toBeInTheDocument();
    });
  });

  it('shows last score after forge is complete', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.result = mockOptimizationResult({ overall_score: 8.5 }) as any;
    render(StatusBar);
    expect(screen.getByText(/8\.5/)).toBeInTheDocument();
  });

  it('shows strategy used after forge is complete', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.result = mockOptimizationResult({ overall_score: 8.0, strategy_used: 'chain-of-thought' }) as any;
    render(StatusBar);
    expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
  });

  it('shows breadcrumb with domain and intent_label after forge completes', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.result = mockOptimizationResult({
      overall_score: 7.5,
      domain: 'backend',
      intent_label: 'API endpoint design',
    }) as any;
    render(StatusBar);
    expect(screen.getByText('backend')).toBeInTheDocument();
    expect(screen.getByText('API endpoint design')).toBeInTheDocument();
  });

  it('shows breadcrumb without domain when domain is null', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.result = mockOptimizationResult({
      overall_score: 7.5,
      domain: null,
      intent_label: 'Code review helper',
    }) as any;
    render(StatusBar);
    expect(screen.getByText('Code review helper')).toBeInTheDocument();
  });

  it('shows passthrough status phase with yellow color class', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'passthrough';
    render(StatusBar);
    // passthrough phase label
    expect(screen.getByText(/passthrough\.\.\./i)).toBeInTheDocument();
  });

  it('pattern count updates when taxonomy stats change', async () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);

    // Initially no pattern count
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByText(/patterns/)).not.toBeInTheDocument();

    // Set taxonomy stats
    patternsStore.taxonomyStats = {
      q_system: 0.8,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      nodes: { confirmed: 5, candidate: 1, retired: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      q_history: [],
    };

    await vi.waitFor(() => {
      expect(screen.queryByText('5 patterns')).toBeInTheDocument();
    });
  });
});
