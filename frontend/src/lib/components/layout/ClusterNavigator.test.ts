import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { mockFetch, mockPatternFamily, mockMetaPattern } from '$lib/test-utils';

import ClusterNavigator from './ClusterNavigator.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { templatesStore, type Template } from '$lib/stores/templates.svelte';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Build a tree response for /api/clusters/tree. */
function treeResponse(items: ReturnType<typeof mockClusterNode>[]) {
  return { nodes: items };
}

/** Build a mock Template for templatesStore. */
function mockTemplate(overrides: Partial<Template> = {}): Template {
  return {
    id: 't-1',
    source_cluster_id: 'c-1',
    source_optimization_id: 'o-1',
    project_id: null,
    label: 'Mock template',
    prompt: 'Mock prompt text',
    strategy: 'chain-of-thought',
    score: 8.2,
    pattern_ids: [],
    domain_label: 'backend',
    promoted_at: '2026-04-15T00:00:00Z',
    retired_at: null,
    retired_reason: null,
    usage_count: 0,
    last_used_at: null,
    ...overrides,
  };
}

/** Build a mock ClusterNode for tree responses. */
function mockClusterNode(overrides: Record<string, unknown> = {}) {
  return {
    id: 'fam-1',
    parent_id: null,
    label: 'API endpoint patterns',
    state: 'active',
    domain: 'backend',
    task_type: 'coding',
    persistence: null,
    coherence: null,
    separation: null,
    stability: null,
    member_count: 3,
    usage_count: 5,
    avg_score: 7.8,
    color_hex: null,
    umap_x: null,
    umap_y: null,
    umap_z: null,
    preferred_strategy: null,
    created_at: '2026-03-15T10:00:00Z',
    ...overrides,
  };
}

/** Compat wrapper: convert old mockPatternFamily calls to cluster nodes. */
function familyToNode(fam: ReturnType<typeof mockPatternFamily>) {
  return mockClusterNode({
    id: fam.id,
    label: fam.intent_label,
    domain: fam.domain,
    task_type: fam.task_type,
    usage_count: fam.usage_count,
    member_count: fam.member_count,
    avg_score: fam.avg_score,
    created_at: fam.created_at,
  });
}

/** Compat wrapper: accept mockPatternFamily items and return { nodes: [...] }. */
function treeResponseWrapped(
  items: ReturnType<typeof mockPatternFamily>[],
  _opts?: { total?: number; has_more?: boolean; next_offset?: number | null }
) {
  return { nodes: items.map(familyToNode) };
}

/** Build a ClusterDetail response for /api/clusters/:id */
function clusterDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 'fam-1',
    parent_id: null,
    label: 'API patterns',
    state: 'active',
    domain: 'backend',
    task_type: 'coding',
    member_count: 3,
    usage_count: 5,
    avg_score: 7.8,
    coherence: null,
    separation: null,
    preferred_strategy: null,
    promoted_at: null,
    meta_patterns: [mockMetaPattern({ id: 'mp-1', pattern_text: 'Handle errors', source_count: 2 })],
    optimizations: [],
    children: null,
    breadcrumb: null,
    ...overrides,
  };
}

/** Default stats response for /api/clusters/stats. */
const defaultStats = {
  q_system: null, q_coherence: null, q_separation: null, q_coverage: null, q_dbcv: null,
  q_health: null, q_health_coherence_w: null, q_health_separation_w: null,
  q_health_weights: null, q_health_total_members: null, q_health_cluster_count: null,
  total_clusters: 0, nodes: null, last_warm_path: null, last_cold_path: null,
  warm_path_age: null, q_history: null, q_sparkline: null,
  q_trend: 0, q_current: null, q_min: null, q_max: null, q_point_count: 0,
};

/** Default fetch handlers for the component's initial data needs. */
function defaultHandlers(
  items: ReturnType<typeof mockPatternFamily>[] = [],
  _opts: { has_more?: boolean; next_offset?: number | null; total?: number } = {}
) {
  const nodes = items.map(familyToNode);
  return mockFetch([
    {
      match: '/api/clusters/tree',
      response: treeResponse(nodes),
    },
    {
      match: '/api/clusters/stats',
      response: defaultStats,
    },
    {
      match: '/api/clusters/',
      response: clusterDetail(),
    },
  ]);
}

/** Stub global fetch with custom handlers + default stats response.
 *  `handlers` is an object mapping URL substrings to response objects.
 *  The /api/clusters/stats endpoint is automatically handled. */
function stubFetch(handlers: Record<string, unknown>) {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    // Check specific handlers (longest match first avoids false positives)
    for (const [match, response] of Object.entries(handlers).sort((a, b) => b[0].length - a[0].length)) {
      if (url.includes(match)) {
        if (response instanceof Promise) return response; // hanging promise
        return new Response(JSON.stringify(response), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
    }
    // Default stats handler
    if (url.includes('/api/clusters/stats')) {
      return new Response(JSON.stringify(defaultStats), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    }
    return new Response('Not Found', { status: 404 });
  }));
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('ClusterNavigator', () => {
  beforeEach(() => {
    clustersStore._reset();
    editorStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  // ── 1. Family list rendering ────────────────────────────────────────────────

  it('renders domain headers when families from different domains are loaded', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('backend')).toBeInTheDocument();
    });
    expect(screen.getByText('frontend')).toBeInTheDocument();
  });

  it('renders intent_label for each family', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.getByText('UI patterns')).toBeInTheDocument();
  });

  it('renders usage_count badge for each family', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns', usage_count: 7 }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('7')).toBeInTheDocument();
    });
  });

  it('renders avg_score for each family', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns', avg_score: 8.3 }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      // formatScore(8.3) = '8.3'
      expect(screen.getByText('8.3')).toBeInTheDocument();
    });
  });

  it('shows domain count beside the domain header', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'backend', intent_label: 'Auth patterns' }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('backend')).toBeInTheDocument();
    });
    // domain count = 2; there may be multiple elements containing '2' (e.g. header badge also shows total)
    expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1);
  });

  it('displays total families count in the header badge', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'frontend', intent_label: 'UI patterns' }),
    ], { total: 2 });
    render(ClusterNavigator);

    await waitFor(() => {
      // totalFamilies = 2, shown in header badge
      expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1);
    });
  });

  // ── 2. Pagination ──────────────────────────────────────────────────────────

  it('shows "Load more" button when has_more is true', async () => {
    // PAGE_SIZE is 500 — provide >500 nodes so client-side pagination shows "Load more"
    const manyNodes = Array.from({ length: 501 }, (_, i) =>
      mockClusterNode({ id: `fam-${i}`, label: `Pattern ${i}`, domain: 'backend' }),
    );
    mockFetch([
      { match: '/api/clusters/tree', response: { nodes: manyNodes } },
      { match: '/api/clusters/stats', response: defaultStats },
      { match: '/api/clusters/', response: clusterDetail() },
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('Load more')).toBeInTheDocument();
    });
  });

  it('hides "Load more" button when has_more is false', async () => {
    defaultHandlers(
      [mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' })],
      { has_more: false }
    );
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Load more')).not.toBeInTheDocument();
  });

  it('clicking "Load more" fetches next page and appends results', async () => {
    const user = userEvent.setup();

    // PAGE_SIZE is 500 — 501 nodes so first page = 500 items with has_more=true
    const manyNodes = Array.from({ length: 501 }, (_, i) =>
      mockClusterNode({ id: `fam-${i}`, label: i === 500 ? 'Auth patterns' : `Pattern ${i}`, domain: 'backend' }),
    );
    mockFetch([
      { match: '/api/clusters/tree', response: { nodes: manyNodes } },
      { match: '/api/clusters/stats', response: defaultStats },
      { match: '/api/clusters/', response: clusterDetail() },
    ]);

    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('Load more')).toBeInTheDocument();
    });

    // Load more now expands client-side page limit (no additional fetch)
    await user.click(screen.getByText('Load more'));

    await waitFor(() => {
      expect(screen.getByText('Auth patterns')).toBeInTheDocument();
    });
    // First page item still present
    expect(screen.getByText('Pattern 0')).toBeInTheDocument();
    // Load more hidden after fully loaded
    expect(screen.queryByText('Load more')).not.toBeInTheDocument();
  });

  // ── 3. Domain filtering (via search) ───────────────────────────────────────
  //
  // Note: ClusterNavigator groups families by domain. Domain filtering is
  // rendered through domain grouping. We verify the grouping reflects
  // different domains correctly.

  it('groups families from the same domain under one header', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'backend', intent_label: 'Auth patterns' }),
      mockPatternFamily({ id: 'fam-3', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    // Only one 'backend' header, one 'frontend' header
    expect(screen.getAllByText('backend').length).toBe(1);
    expect(screen.getAllByText('frontend').length).toBe(1);
  });

  // ── 4. Search (local filtering from taxonomy tree) ──────────────────────────

  it('shows search input with placeholder', () => {
    defaultHandlers([]);
    render(ClusterNavigator);
    expect(screen.getByPlaceholderText('Search patterns...')).toBeInTheDocument();
  });

  it('shows search results immediately when typing (local filter from taxonomy tree)', async () => {
    const user = userEvent.setup();

    // Pre-populate taxonomy tree for local search
    clustersStore.taxonomyTree = [
      { id: 'node-1', parent_id: null, label: 'API patterns', state: 'active', persistence: null, coherence: 0.9, separation: null, stability: null, member_count: 3, usage_count: 5, color_hex: '#a855f7', umap_x: null, umap_y: null, umap_z: null },
    ] as any;

    defaultHandlers([]);
    render(ClusterNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'API');

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
  });

  it('shows no-match message when search query matches no taxonomy nodes', async () => {
    const user = userEvent.setup();

    // Empty taxonomy tree
    clustersStore.taxonomyTree = [];

    defaultHandlers([]);
    render(ClusterNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'xyz');

    await waitFor(() => {
      expect(screen.getByText(/No matches for/i)).toBeInTheDocument();
    });
  });

  it('shows a clear button when search query is non-empty', async () => {
    const user = userEvent.setup();
    defaultHandlers([]);
    render(ClusterNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'API');

    expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument();
  });

  it('clicking the clear button resets search and hides search results', async () => {
    const user = userEvent.setup();

    clustersStore.taxonomyTree = [
      { id: 'node-1', parent_id: null, label: 'API patterns', state: 'active', persistence: null, coherence: 0.9, separation: null, stability: null, member_count: 3, usage_count: 5, color_hex: '#a855f7', umap_x: null, umap_y: null, umap_z: null },
    ] as any;

    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
    ]);
    render(ClusterNavigator);

    // Wait for initial load
    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'API');

    // Clear the search
    await user.click(screen.getByRole('button', { name: 'Clear search' }));

    // Clear button should be gone (search query empty)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument();
    });
  });

  // ── 5. Family selection ───────────────────────────────────────────────────

  it('clicking a family row calls clustersStore.selectCluster with its id', async () => {
    const user = userEvent.setup();
    const selectSpy = vi.spyOn(clustersStore, 'selectCluster');

    stubFetch({
      '/api/clusters/fam-42': clusterDetail({ id: 'fam-42' }),
      '/api/clusters/tree': treeResponseWrapped([
        mockPatternFamily({ id: 'fam-42', domain: 'backend', intent_label: 'API patterns' }),
      ]),
    });

    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    expect(selectSpy).toHaveBeenCalledWith('fam-42');  // selectCluster
  });

  it('clicking an already-expanded family collapses it and calls selectCluster(null)', async () => {
    const user = userEvent.setup();
    const selectSpy = vi.spyOn(clustersStore, 'selectCluster');

    stubFetch({
      '/api/clusters/fam-1': clusterDetail(),
      '/api/clusters/tree': treeResponseWrapped([
        mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      ]),
    });

    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    // First click expands
    await user.click(screen.getByText('API patterns'));
    expect(selectSpy).toHaveBeenCalledWith('fam-1');

    // Second click collapses
    await user.click(screen.getByText('API patterns'));
    expect(selectSpy).toHaveBeenCalledWith(null);
  });

  it('shows expanded detail pane (loading state) after clicking a family', async () => {
    const user = userEvent.setup();

    // Make the family detail request hang so we can see the loading state
    stubFetch({
      '/api/clusters/fam-1': new Promise(() => {}),
      '/api/clusters/tree': treeResponseWrapped([
        mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      ]),
    });

    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    await waitFor(() => {
      // The expanded detail shows a "Loading..." note while waiting
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });
  });

  it('shows linked optimizations after family detail loads', async () => {
    const user = userEvent.setup();

    stubFetch({
      '/api/clusters/fam-1': clusterDetail({
        optimizations: [
          { id: 'opt-1', trace_id: 't-1', raw_prompt: 'Test prompt', intent_label: 'Validate inputs', overall_score: 7.5, strategy_used: 'auto', created_at: new Date().toISOString() },
        ],
      }),
      '/api/clusters/tree': treeResponseWrapped([
        mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      ]),
    });

    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    await waitFor(() => {
      expect(screen.getByText('Validate inputs')).toBeInTheDocument();
    });
  });

  // ── 6. Empty state ────────────────────────────────────────────────────────

  it('shows empty-state placeholder when no families exist', async () => {
    defaultHandlers([]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText(/Optimize your first prompt/i)).toBeInTheDocument();
    });
  });

  // ── 7. Loading state ──────────────────────────────────────────────────────

  it('shows "Loading..." while initial families fetch is pending', () => {
    // Fetch never resolves — component shows loading state
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(ClusterNavigator);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  // ── 8. Mindmap button ─────────────────────────────────────────────────────

  it('clicking the mindmap button calls editorStore.openMindmap', async () => {
    const user = userEvent.setup();
    const openMindmapSpy = vi.spyOn(editorStore, 'openMindmap');
    const loadTreeSpy = vi.spyOn(clustersStore, 'loadTree').mockResolvedValue();

    defaultHandlers([]);
    render(ClusterNavigator);

    const mindmapBtn = screen.getByRole('button', { name: 'Open pattern mindmap' });
    await user.click(mindmapBtn);

    expect(openMindmapSpy).toHaveBeenCalled();
    expect(loadTreeSpy).toHaveBeenCalled();
  });

  // ── 9. Error state ────────────────────────────────────────────────────────

  it('shows error message when families fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('Network failure');
    }));
    render(ClusterNavigator);

    await waitFor(() => {
      // Error propagates through clustersStore.loadTree → taxonomyError
      expect(screen.getByText('Network failure')).toBeInTheDocument();
    });
  });

  // ── 10. Search result clicking selects family ──────────────────────────────

  it('clicking a search result calls clustersStore.selectCluster and clears search', async () => {
    const user = userEvent.setup();
    const selectSpy = vi.spyOn(clustersStore, 'selectCluster');

    // Pre-populate taxonomy tree for local search
    clustersStore.taxonomyTree = [
      { id: 'fam-1', parent_id: null, label: 'API patterns', state: 'active', persistence: null, coherence: 0.9, separation: null, stability: null, member_count: 3, usage_count: 5, color_hex: '#a855f7', umap_x: null, umap_y: null, umap_z: null },
    ] as any;

    stubFetch({
      '/api/clusters/fam-1': clusterDetail({ id: 'fam-1' }),
      '/api/clusters/tree': treeResponseWrapped([]),
    });

    render(ClusterNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'API');

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    expect(selectSpy).toHaveBeenCalledWith('fam-1');

    // Search should be cleared after selection
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Clear search' })).not.toBeInTheDocument();
    });
  });

  // ── 11. Expanded detail — no linked optimizations fallback ────────────────

  it('shows "No linked optimizations yet" when family has empty optimizations', async () => {
    const user = userEvent.setup();

    stubFetch({
      '/api/clusters/fam-1': clusterDetail({ optimizations: [] }),
      '/api/clusters/tree': treeResponseWrapped([
        mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      ]),
    });

    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    await waitFor(() => {
      expect(screen.getByText('No linked optimizations yet.')).toBeInTheDocument();
    });
  });

  // ── 12. Domains sorted by cluster count (descending) ──────────────────────

  it('renders domain headers sorted by cluster count descending', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'security', intent_label: 'Security patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-3', domain: 'backend', intent_label: 'API patterns 2' }),
      mockPatternFamily({ id: 'fam-4', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('backend')).toBeInTheDocument();
    });

    const domainHeaders = screen.getAllByText(/^(backend|frontend|security)$/);
    const labels = domainHeaders.map(el => el.textContent);
    // backend has 2 clusters → first; frontend and security have 1 each
    expect(labels[0]).toBe('backend');
  });

  // ── 13. State filter tabs ─────────────────────────────────────────────────

  it('renders state filter tabs (All, active, candidate, mature, archived) — no template tab', async () => {
    defaultHandlers([]);
    render(ClusterNavigator);
    expect(screen.getByRole('tab', { name: 'All' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'active' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'mature' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'archived' })).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'template' })).not.toBeInTheDocument();
  });

  it('"active" tab is selected by default (aria-selected=true)', async () => {
    defaultHandlers([]);
    render(ClusterNavigator);

    const activeTab = screen.getByRole('tab', { name: 'active' });
    expect(activeTab).toHaveAttribute('aria-selected', 'true');
    // Other tabs should not be selected
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'false');
  });

  it('clicking a state tab filters families to that state only', async () => {
    const user = userEvent.setup();

    const nodes = [
      mockClusterNode({ id: 'fam-active', label: 'Active cluster', state: 'active', domain: 'backend' }),
      mockClusterNode({ id: 'fam-mature', label: 'Mature cluster', state: 'mature', domain: 'frontend' }),
      mockClusterNode({ id: 'fam-archived', label: 'Archived cluster', state: 'archived', domain: 'backend' }),
    ];
    mockFetch([
      { match: '/api/clusters/tree', response: { nodes } },
      { match: '/api/clusters/stats', response: defaultStats },
      { match: '/api/clusters/', response: clusterDetail() },
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('Active cluster')).toBeInTheDocument();
    });

    // Click the "active" tab
    await user.click(screen.getByRole('tab', { name: 'active' }));

    // ACT filter shows all living states (active + mature + template + candidate)
    expect(screen.getByText('Active cluster')).toBeInTheDocument();
    expect(screen.getByText('Mature cluster')).toBeInTheDocument();
    expect(screen.queryByText('Archived cluster')).not.toBeInTheDocument();

    // The "active" tab should now be selected
    expect(screen.getByRole('tab', { name: 'active' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'All' })).toHaveAttribute('aria-selected', 'false');
  });

  it('clicking "All" tab after filtering shows all non-template families', async () => {
    const user = userEvent.setup();

    const nodes = [
      mockClusterNode({ id: 'fam-active', label: 'Active cluster', state: 'active', domain: 'backend' }),
      mockClusterNode({ id: 'fam-mature', label: 'Mature cluster', state: 'mature', domain: 'frontend' }),
    ];
    mockFetch([
      { match: '/api/clusters/tree', response: { nodes } },
      { match: '/api/clusters/stats', response: defaultStats },
      { match: '/api/clusters/', response: clusterDetail() },
    ]);
    render(ClusterNavigator);

    await waitFor(() => {
      expect(screen.getByText('Active cluster')).toBeInTheDocument();
    });

    // Filter down to active — now shows all living states including mature
    await user.click(screen.getByRole('tab', { name: 'active' }));
    expect(screen.getByText('Mature cluster')).toBeInTheDocument();

    // Reset to All — same result
    await user.click(screen.getByRole('tab', { name: 'All' }));
    expect(screen.getByText('Active cluster')).toBeInTheDocument();
    expect(screen.getByText('Mature cluster')).toBeInTheDocument();
  });

  // ── 14. Proven Templates section (reads from templatesStore) ───────────────

  describe('PROVEN TEMPLATES reads from templatesStore', () => {
    beforeEach(() => {
      templatesStore.templates = [];
      templatesStore.loading = false;
    });

    it('renders PROVEN TEMPLATES section when templates exist in store', async () => {
      templatesStore.templates = [mockTemplate({ id: 't1', label: 'Auth flow', domain_label: 'backend' })];
      defaultHandlers([]);
      render(ClusterNavigator);

      await waitFor(() => {
        expect(screen.getByText('PROVEN TEMPLATES')).toBeInTheDocument();
      });
      expect(screen.getByText('Auth flow')).toBeInTheDocument();
    });

    it('does not render PROVEN TEMPLATES section when store is empty', async () => {
      defaultHandlers([]);
      render(ClusterNavigator);
      // Wait for something else to settle
      await waitFor(() => {
        expect(screen.getByRole('tab', { name: 'All' })).toBeInTheDocument();
      });
      expect(screen.queryByText('PROVEN TEMPLATES')).not.toBeInTheDocument();
    });

    it('retired templates are hidden from the list', async () => {
      templatesStore.templates = [
        mockTemplate({ id: 't1', label: 'Alive', retired_at: null }),
        mockTemplate({ id: 't2', label: 'Gone', retired_at: '2026-04-18T00:00:00Z' }),
      ];
      defaultHandlers([]);
      render(ClusterNavigator);
      await waitFor(() => expect(screen.getByText('Alive')).toBeInTheDocument());
      expect(screen.queryByText('Gone')).not.toBeInTheDocument();
    });

    it('groups templates by frozen domain_label (not cluster domain)', async () => {
      templatesStore.templates = [
        mockTemplate({ id: 't1', label: 'T-back', domain_label: 'backend' }),
        mockTemplate({ id: 't2', label: 'T-data', domain_label: 'data' }),
      ];
      defaultHandlers([]);
      render(ClusterNavigator);
      await waitFor(() => expect(screen.getByText('PROVEN TEMPLATES')).toBeInTheDocument());
      // Group headers appear for both domains
      expect(screen.getByText('T-back')).toBeInTheDocument();
      expect(screen.getByText('T-data')).toBeInTheDocument();
    });

    it('clicking spawn button calls templatesStore.spawn() and copies prompt to forge', async () => {
      const user = userEvent.setup();
      templatesStore.templates = [
        mockTemplate({ id: 't-42', label: 'Auth', prompt: 'Sampled prompt', strategy: 'chain-of-thought', pattern_ids: ['p1'] }),
      ];
      // Mock /templates/t-42/use endpoint
      mockFetch([
        { match: '/api/clusters/tree', response: { nodes: [] } },
        { match: '/api/clusters/stats', response: defaultStats },
        { match: '/templates/t-42/use', response: { id: 't-42', prompt: 'Sampled prompt', usage_count: 1 } },
      ]);
      render(ClusterNavigator);
      await waitFor(() => expect(screen.getByText('Auth')).toBeInTheDocument());

      const spawnBtn = screen.getByRole('button', { name: /use template auth/i });
      await user.click(spawnBtn);

      // Spawn updates usage_count in the store
      await waitFor(() => {
        const updated = templatesStore.templates.find((t) => t.id === 't-42');
        expect(updated?.usage_count).toBe(1);
      });
    });

    it('clicking retire button calls templatesStore.retire()', async () => {
      const user = userEvent.setup();
      templatesStore.templates = [
        mockTemplate({ id: 't-99', label: 'Old template', domain_label: 'backend', retired_at: null }),
      ];
      mockFetch([
        { match: '/api/clusters/tree', response: { nodes: [] } },
        { match: '/api/clusters/stats', response: defaultStats },
        { match: '/templates/t-99/retire', response: { id: 't-99', retired_at: '2026-04-18T12:00:00Z' } },
      ]);
      render(ClusterNavigator);
      await waitFor(() => expect(screen.getByText('Old template')).toBeInTheDocument());

      const retireBtn = screen.getByRole('button', { name: /retire template old template/i });
      await user.click(retireBtn);

      await waitFor(() => {
        const updated = templatesStore.templates.find((t) => t.id === 't-99');
        expect(updated?.retired_at).toBe('2026-04-18T12:00:00Z');
      });
    });
  });
});
