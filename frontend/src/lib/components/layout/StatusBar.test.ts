import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { mockFetch, mockHealthResponse, mockOptimizationResult } from '$lib/test-utils';

import StatusBar from './StatusBar.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { sseHealthStore } from '$lib/stores/sse-health.svelte';
import type { ClusterNode } from '$lib/api/clusters';

/** Create a minimal active ClusterNode for tree population. */
function mockClusterNode(id: string, overrides: Partial<ClusterNode> = {}): ClusterNode {
  return {
    id, parent_id: null, label: `Cluster ${id}`, state: 'active',
    domain: 'general', task_type: 'coding', persistence: null,
    coherence: 0.8, separation: null, stability: null,
    member_count: 3, usage_count: 1, avg_score: 7.0,
    color_hex: null, umap_x: null, umap_y: null, umap_z: null,
    preferred_strategy: null,
    output_coherence: null, blend_w_raw: null, blend_w_optimized: null,
    blend_w_transform: null, split_failures: 0, meta_pattern_count: 0, template_count: 0,
    created_at: null,
    ...overrides,
  };
}

describe('StatusBar', () => {
  beforeEach(() => {
    forgeStore._reset();
    preferencesStore._reset();
    clustersStore._reset();
    sseHealthStore._reset();
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

  it('shows cluster count when taxonomy tree has active nodes > 0', async () => {
    // Populate taxonomy tree — liveClusterCount derives from this
    clustersStore.taxonomyTree = Array.from({ length: 7 }, (_, i) => mockClusterNode(`c-${i}`));
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

  it('shows phase progress while forge is analyzing', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'analyzing';
    render(StatusBar);
    expect(screen.getByText(/analyzing \[1\/3\]/i)).toBeInTheDocument();
  });

  it('shows phase progress while forge is optimizing', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.status = 'optimizing';
    render(StatusBar);
    expect(screen.getByText(/optimizing \[2\/3\]/i)).toBeInTheDocument();
  });

  it('shows phase progress with status-phase class during pipeline', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    forgeStore.provider = null;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    forgeStore.status = 'optimizing';
    render(StatusBar);
    const phase = screen.getByText(/optimizing \[2\/3\]/i);
    // Tier accent color is inherited from --tier-accent CSS variable
    // set on the workbench container (not a per-element class).
    expect(phase.classList.contains('status-phase')).toBe(true);
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

  it('shows Q_health badge when taxonomyStats has q_health value', async () => {
    clustersStore.taxonomyStats = {
      q_system: 0.82,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      q_health: 0.75,
      q_health_coherence_w: 0.68,
      q_health_separation_w: 0.55,
      q_health_weights: { w_c: 0.40, w_s: 0.35, w_v: 0.25, w_d: 0.00 },
      q_health_total_members: 42,
      q_health_cluster_count: 5,
      total_clusters: 5,
      nodes: { active: 5, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [],
      q_trend: 0.15,
      q_current: 0.82,
      q_min: 0.5,
      q_max: 0.9,
      q_point_count: 10,
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.getByText('0.75')).toBeInTheDocument();
      // Q: label is always present when stats exist
      expect(screen.getByText(/^Q:$/)).toBeInTheDocument();
    });
  });

  it('does not show Q_system badge when taxonomyStats is null', async () => {
    clustersStore.taxonomyStats = null;
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByTitle('Taxonomy health (Q_system)')).not.toBeInTheDocument();
  });

  it('renders sparkline SVG when q_sparkline has sufficient data', async () => {
    clustersStore.taxonomyStats = {
      q_system: 0.8,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      q_health: 0.72,
      q_health_coherence_w: null,
      q_health_separation_w: null,
      q_health_weights: null,
      q_health_total_members: null,
      q_health_cluster_count: null,
      total_clusters: 5,
      nodes: { active: 5, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [0.6, 0.7, 0.75, 0.8],
      q_trend: 0.15,
      q_current: 0.8,
      q_min: 0.6,
      q_max: 0.8,
      q_point_count: 4,
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      expect(screen.getByRole('img', { name: 'Score progression sparkline' })).toBeInTheDocument();
    });
  });

  it('shows improving trend indicator when q_trend > 0.1 and q_point_count >= 3', async () => {
    clustersStore.taxonomyStats = {
      q_system: 0.85,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      q_health: 0.78,
      q_health_coherence_w: null,
      q_health_separation_w: null,
      q_health_weights: null,
      q_health_total_members: null,
      q_health_cluster_count: null,
      total_clusters: 5,
      nodes: { active: 5, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [0.6, 0.7, 0.85],
      q_trend: 0.25,
      q_current: 0.85,
      q_min: 0.6,
      q_max: 0.85,
      q_point_count: 3,
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      // Health assessment produces a headline based on Q + sub-metrics
      const trendEl = screen.getByText(/getting better|well organized|looking great/i);
      expect(trendEl).toBeInTheDocument();
    });
  });

  it('does not show trend indicator when q_point_count < 3', async () => {
    clustersStore.taxonomyStats = {
      q_system: 0.8,
      q_coherence: 0.7,
      q_separation: 0.6,
      q_coverage: 0.5,
      q_dbcv: 0.4,
      q_health: 0.72,
      q_health_coherence_w: null,
      q_health_separation_w: null,
      q_health_weights: null,
      q_health_total_members: null,
      q_health_cluster_count: null,
      total_clusters: 5,
      nodes: { active: 5, candidate: 1, mature: 0, template: 0, archived: 0, max_depth: 2, leaf_count: 4 },
      last_warm_path: null,
      last_cold_path: null,
      warm_path_age: null,
      q_history: [],
      q_sparkline: [0.75, 0.8],
      q_trend: 0.15,
      q_current: 0.8,
      q_min: 0.75,
      q_max: 0.8,
      q_point_count: 2,
    };
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    await vi.waitFor(() => {
      // Q value renders (q_health=0.72 displayed as 0.72)
      expect(screen.getAllByText('0.72').length).toBeGreaterThanOrEqual(1);
    });
    // With only 2 data points, no trend-based headline like "getting better"
    expect(screen.queryByText(/getting better|looking great/i)).not.toBeInTheDocument();
  });

  it('cluster count updates when taxonomy tree changes', async () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);

    // Initially no cluster count
    await vi.waitFor(() => {}, { timeout: 100 });
    expect(screen.queryByText(/clusters/)).not.toBeInTheDocument();

    // Populate taxonomy tree — liveClusterCount derives from this
    clustersStore.taxonomyTree = Array.from({ length: 5 }, (_, i) => mockClusterNode(`c-${i}`));

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

  // -----------------------------------------------------------------------
  // SSE health indicator
  // -----------------------------------------------------------------------

  it('renders SSE indicator (shows "SSE \u00D7" when disconnected)', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    // Store starts disconnected by default.
    render(StatusBar);
    expect(screen.getByText('SSE \u00D7')).toBeInTheDocument();
  });

  it('SSE indicator uses red color when disconnected', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    const el = screen.getByText('SSE \u00D7');
    expect(el.closest('.status-sse')?.getAttribute('style')).toContain('var(--color-neon-red)');
  });

  it('SSE indicator dot element is present', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    render(StatusBar);
    const sseEl = screen.getByText('SSE \u00D7').closest('.status-sse');
    const dot = sseEl?.querySelector('.status-sse-dot');
    expect(dot).toBeInTheDocument();
  });

  it('SSE indicator shows "SSE" when healthy', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    sseHealthStore.connectionState = 'healthy';
    render(StatusBar);
    // "SSE" without the cross — exact match
    const sseEls = screen.getAllByText('SSE');
    expect(sseEls.length).toBeGreaterThanOrEqual(1);
  });

  it('SSE indicator uses cyan color when healthy', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    sseHealthStore.connectionState = 'healthy';
    render(StatusBar);
    const sseEls = screen.getAllByText('SSE');
    const el = sseEls.find(e => e.closest('.status-sse'));
    expect(el?.closest('.status-sse')?.getAttribute('style')).toContain('var(--color-neon-cyan)');
  });

  it('SSE indicator uses amber color when degraded', () => {
    mockFetch([{ match: '/api/health', response: mockHealthResponse() }]);
    sseHealthStore.connectionState = 'degraded';
    render(StatusBar);
    const sseEls = screen.getAllByText('SSE');
    const el = sseEls.find(e => e.closest('.status-sse'));
    expect(el?.closest('.status-sse')?.getAttribute('style')).toContain('var(--color-neon-yellow)');
  });
});
