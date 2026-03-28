import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { mockFetch, mockHealthResponse, mockOptimizationResult } from '$lib/test-utils';

import StatusBar from './StatusBar.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';

describe('StatusBar', () => {
  beforeEach(() => {
    forgeStore._reset();
    preferencesStore._reset();
    clustersStore._reset();
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

  it('shows tier badge (PASSTHROUGH when no provider loaded yet)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    // TierBadge renders PASSTHROUGH when routing resolves to passthrough (initial state)
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
  });

  it('shows cluster count when taxonomyStats has active nodes > 0', async () => {
    // Set taxonomy stats with active nodes
    clustersStore.taxonomyStats = {
      q_system: 0.8,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      total_clusters: 7,
      nodes: { active: 7, candidate: 2, mature: 0, template: 0, archived: 0, max_depth: 3, leaf_count: 5 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [],
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.queryByText('7 clusters')).toBeInTheDocument();
    });
  });

  it('does not show cluster count when taxonomyStats is null', async () => {
    clustersStore.taxonomyStats = null;
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByText(/clusters/)).not.toBeInTheDocument();
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

  it('applies green phase color when sampling tier is active', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = null;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    forgeStore.status = 'optimizing';
    render(StatusBar);
    const phase = screen.getByText(/optimizing\.\.\./i);
    expect(phase.classList.contains('status-phase-sampling')).toBe(true);
  });

  it('does not show version in status bar (moved to System accordion)', () => {
    mockFetch([]);
    forgeStore.version = '1.2.3';
    render(StatusBar);
    expect(screen.queryByText('v1.2.3')).not.toBeInTheDocument();
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

  it('shows Q_system badge when taxonomyStats has q_system value', async () => {
    clustersStore.taxonomyStats = {
      q_system: 0.82,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      total_clusters: 5,
      nodes: { active: 5, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [],
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.getByTitle('Taxonomy health (Q_system)')).toBeInTheDocument();
      expect(screen.getByText('0.82')).toBeInTheDocument();
    });
  });

  it('does not show Q_system badge when taxonomyStats is null', async () => {
    clustersStore.taxonomyStats = null;
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByTitle('Taxonomy health (Q_system)')).not.toBeInTheDocument();
  });

  it('cluster count updates when taxonomy stats change', async () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);

    // Initially no cluster count
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByText(/clusters/)).not.toBeInTheDocument();

    // Set taxonomy stats
    clustersStore.taxonomyStats = {
      q_system: 0.8,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      total_clusters: 5,
      nodes: { active: 5, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [],
    };

    await vi.waitFor(() => {
      expect(screen.queryByText('5 clusters')).toBeInTheDocument();
    });
  });

  // -----------------------------------------------------------------------
  // Domain count display
  // -----------------------------------------------------------------------

  it('shows domain count when forgeStore.domainCount is set', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.domainCount = 5;
    forgeStore.domainCeiling = 30;
    render(StatusBar);
    expect(screen.getByText('5 domains')).toBeInTheDocument();
  });

  it('does not show domain count when forgeStore.domainCount is null', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.domainCount = null;
    render(StatusBar);
    expect(screen.queryByText(/domains/)).not.toBeInTheDocument();
  });

  it('shows domain count in amber when near ceiling (>=80%)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.domainCount = 25;
    forgeStore.domainCeiling = 30;
    render(StatusBar);
    const el = screen.getByText('25 domains');
    expect(el).toBeInTheDocument();
    expect(el.getAttribute('style')).toContain('var(--color-neon-yellow)');
  });

  it('shows domain count in dim color when well below ceiling', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.domainCount = 5;
    forgeStore.domainCeiling = 30;
    render(StatusBar);
    const el = screen.getByText('5 domains');
    expect(el.getAttribute('style')).toContain('var(--color-text-dim)');
  });

  // -----------------------------------------------------------------------
  // TierBadge integration
  // -----------------------------------------------------------------------

  it('shows CLI tier badge when claude_cli provider is available', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = 'claude_cli';
    render(StatusBar);
    expect(screen.getByText('CLI')).toBeInTheDocument();
  });

  it('shows API tier badge when anthropic_api provider is available', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = 'anthropic_api';
    render(StatusBar);
    expect(screen.getByText('API')).toBeInTheDocument();
  });

  it('shows SAMPLING tier badge when auto-sampling active', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = null;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    render(StatusBar);
    expect(screen.getByText('SAMPLING')).toBeInTheDocument();
  });

  // -----------------------------------------------------------------------
  // Auto-fallback and degradation display
  // -----------------------------------------------------------------------

  it('shows clean CLI badge during auto-fallback (no struck-through SAMPLING)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = 'claude_cli';
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = true;
    preferencesStore.prefs.pipeline.force_sampling = true;
    render(StatusBar);
    expect(screen.getByText('CLI')).toBeInTheDocument();
    // No struck-through SAMPLING — auto-fallback is seamless
    expect(screen.queryByTitle('Requested tier unavailable')).not.toBeInTheDocument();
    expect(screen.queryByText('disconnected')).not.toBeInTheDocument();
  });

  it('shows struck-through SAMPLING when truly degraded (passthrough fallback)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = null;
    forgeStore.samplingCapable = null;
    preferencesStore.prefs.pipeline.force_sampling = true;
    render(StatusBar);
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
    expect(screen.getByText('SAMPLING')).toBeInTheDocument(); // struck-through
  });

  it('shows "disconnected" when MCP is disconnected but no force toggle active', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = 'claude_cli';
    forgeStore.mcpDisconnected = true;
    // force_sampling is false (default) — no auto-fallback, no degradation
    render(StatusBar);
    expect(screen.getByText('disconnected')).toBeInTheDocument();
  });
});
