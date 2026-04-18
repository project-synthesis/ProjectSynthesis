import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import {
  mockFetch,
  mockOptimizationResult,
  mockDimensionScores,
  mockPatternFamily,
  mockMetaPattern,
} from '$lib/test-utils';

// Mock sub-components that use D3 or complex deps
vi.mock('$lib/components/refinement/ScoreSparkline.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));

import Inspector from './Inspector.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { domainStore } from '$lib/stores/domains.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { templatesStore } from '$lib/stores/templates.svelte';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Build the ClusterDetail response used in most cluster tests. */
function makeFamilyDetail(overrides: Record<string, unknown> = {}) {
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
    meta_patterns: [
      mockMetaPattern({ id: 'mp-1', pattern_text: 'Use error handling', source_count: 3 }),
    ],
    optimizations: [
      {
        id: 'opt-1',
        trace_id: 'trace-1',
        raw_prompt: 'Write API endpoint',
        intent_label: 'Write API',
        overall_score: 8.0,
        strategy_used: 'chain-of-thought',
        created_at: '2026-03-20',
      },
    ],
    children: null,
    breadcrumb: null,
    ...overrides,
  };
}

/** Set up fetch mocks for family-detail + feedback endpoints. */
function familyFetchHandlers(familyOverrides: Record<string, unknown> = {}) {
  return mockFetch([
    {
      match: '/api/clusters/',
      response: makeFamilyDetail(familyOverrides),
    },
    {
      match: '/api/feedback',
      response: [],
    },
    {
      match: '/api/optimize/',
      response: mockOptimizationResult(),
    },
  ]);
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('Inspector', () => {
  beforeEach(() => {
    forgeStore._reset();
    clustersStore._reset();
    domainStore._reset();
    // Populate domain store with seed domains for domain picker tests
    domainStore.domains = [
      { id: 'd1', label: 'backend', color_hex: '#b44aff', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd2', label: 'frontend', color_hex: '#ff4895', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd3', label: 'database', color_hex: '#36b5ff', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd4', label: 'security', color_hex: '#ff2255', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd5', label: 'devops', color_hex: '#6366f1', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd6', label: 'fullstack', color_hex: '#d946ef', member_count: 0, avg_score: null, source: 'seed' },
      { id: 'd7', label: 'general', color_hex: '#7a7a9e', member_count: 0, avg_score: null, source: 'seed' },
    ];
    editorStore._reset();
    templatesStore.templates = [];
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  // ── 1. Empty state ───────────────────────────────────────────────────────────

  it('shows placeholder text when no family is selected and forge is idle', () => {
    mockFetch([]);
    render(Inspector);
    expect(screen.getByText(/Enter a prompt and synthesize/i)).toBeInTheDocument();
  });

  it('renders the inspector aside element with correct aria-label', () => {
    mockFetch([]);
    render(Inspector);
    expect(screen.getByRole('complementary', { name: 'Inspector panel' })).toBeInTheDocument();
  });

  // ── 2. Family detail display ─────────────────────────────────────────────────

  it('shows family metadata when a family is selected', async () => {
    familyFetchHandlers();
    // Directly set store state that the component reads
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.getByText('backend')).toBeInTheDocument();
    // Usage count
    expect(screen.getByText('5')).toBeInTheDocument();
    // Member count — use getAllByText since source_count may also be 3
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
    // Avg score (formatScore of 7.8)
    expect(screen.getByText('7.8')).toBeInTheDocument();
  });

  it('shows usage, members and avg-score labels', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Usage')).toBeInTheDocument();
    });
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('Avg Score')).toBeInTheDocument();
  });

  it('shows domain badge for the selected family', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ domain: 'frontend' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('frontend')).toBeInTheDocument();
    });
  });

  // ── 3. Meta-patterns list ────────────────────────────────────────────────────

  it('renders each meta-pattern text with source count', async () => {
    const detail = makeFamilyDetail({
      meta_patterns: [
        mockMetaPattern({ id: 'mp-1', pattern_text: 'Use error handling', source_count: 3 }),
        mockMetaPattern({ id: 'mp-2', pattern_text: 'Validate input parameters', source_count: 7 }),
      ],
    });
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = detail as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Use error handling')).toBeInTheDocument();
    });
    expect(screen.getByText('Validate input parameters')).toBeInTheDocument();
    // source_count values shown in source-badge spans — use getAllByText since member_count can overlap
    expect(screen.getAllByText('3').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('shows the Meta-patterns section heading', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Meta-patterns')).toBeInTheDocument();
    });
  });

  it('does not render meta-patterns section when list is empty', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ meta_patterns: [] }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Meta-patterns')).not.toBeInTheDocument();
  });

  // ── 4. Linked optimizations moved to ClusterNavigator ────────────────────────
  // (Tests for linked optimizations are in ClusterNavigator.test.ts)

  // ── 5. Inline rename ─────────────────────────────────────────────────────────

  it('clicking the family intent label enters rename edit mode', async () => {
    const user = userEvent.setup();
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    // The intent label is a button with title "Click to rename"
    await user.click(screen.getByRole('button', { name: /Click to rename/ }));

    // Rename input should now be visible with the current label as value
    const input = screen.getByRole('textbox', { name: 'Family name' }) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe('API patterns');
  });

  it('pressing Escape in rename input cancels rename', async () => {
    const user = userEvent.setup();
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Click to rename/ }));
    const input = screen.getByRole('textbox', { name: 'Family name' });
    // Fire keydown directly on the input to trigger the Svelte onkeydown handler
    fireEvent.keyDown(input, { key: 'Escape', code: 'Escape' });

    await waitFor(() => {
      // Rename form gone, original intent label visible again
      expect(screen.queryByRole('textbox', { name: 'Family name' })).not.toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /Click to rename/ })).toBeInTheDocument();
  });

  it('clicking cancel button in rename form reverts to display mode', async () => {
    const user = userEvent.setup();
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Click to rename/ }));
    // Cancel button has title="Cancel"
    await user.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.queryByRole('textbox', { name: 'Family name' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Click to rename/ })).toBeInTheDocument();
  });

  it('submitting rename form calls renameFamily API and refreshes family', async () => {
    const user = userEvent.setup();

    const fetchMock = mockFetch([
      {
        match: '/api/clusters/',
        response: makeFamilyDetail({ intent_label: 'Renamed Family' }),
      },
    ]);

    // Simulate renameFamily (PATCH) and re-fetch (GET)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/clusters/fam-1')) {
        if (init?.method === 'PATCH') {
          return new Response(JSON.stringify({ id: 'fam-1', intent_label: 'Renamed Family' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        // GET after refresh
        return new Response(JSON.stringify(makeFamilyDetail({ intent_label: 'Renamed Family' })), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('Not Found', { status: 404 });
    }));

    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /Click to rename/ }));
    const input = screen.getByRole('textbox', { name: 'Family name' });
    await user.clear(input);
    await user.type(input, 'Renamed Family');

    // Click save (checkmark button with title="Save")
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => {
      // PATCH should have been called
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
      const patchCall = calls.find((c: unknown[]) => {
        const [, init] = c as [RequestInfo | URL, RequestInit?];
        return init?.method === 'PATCH';
      });
      expect(patchCall).toBeDefined();
    });
  });

  // Domain picker removed — domain reassignment is not allowed (causes
  // cluster fragmentation, wrong merges, and corrupt tree topology).
  // Domain is set automatically by the taxonomy engine.

  // ── 6. Dismiss button ────────────────────────────────────────────────────────

  it('clicking dismiss button deselects the family', async () => {
    const user = userEvent.setup();
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Close family detail' }));

    expect(clustersStore.selectedClusterId).toBeNull();
  });

  // ── 7. Loading state ─────────────────────────────────────────────────────────

  it('shows loading spinner when clusterDetailLoading is true', () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = null;
    clustersStore.clusterDetailLoading = true;
    mockFetch([]);

    render(Inspector);

    expect(screen.getByRole('status', { name: 'Loading family' })).toBeInTheDocument();
  });

  // ── 8. Error state from cluster detail ───────────────────────────────────────

  it('shows error message when clusterDetailError is set', () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = null;
    clustersStore.clusterDetailLoading = false;
    clustersStore.clusterDetailError = 'Failed to load family';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText('Failed to load family')).toBeInTheDocument();
  });

  // ── 9. Score display (complete state) ────────────────────────────────────────

  it('renders ScoreCard when forge result has scores', async () => {
    forgeStore.status = 'complete';
    forgeStore.result = mockOptimizationResult() as any;
    forgeStore.scores = mockDimensionScores();
    mockFetch([]);

    render(Inspector);

    // ScoreCard renders dimension labels
    await waitFor(() => {
      expect(screen.getByText('Clarity')).toBeInTheDocument();
    });
    expect(screen.getByText('Specificity')).toBeInTheDocument();
    expect(screen.getByText('Structure')).toBeInTheDocument();
  });

  it('shows scoring disabled state when forge result has no scores', async () => {
    forgeStore.status = 'complete';
    forgeStore.result = mockOptimizationResult({ scores: null, original_scores: null, score_deltas: null }) as any;
    forgeStore.scores = null;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Scoring')).toBeInTheDocument();
      expect(screen.getByText('disabled')).toBeInTheDocument();
    });
  });

  it('shows strategy metadata when forge result has strategy_used', async () => {
    forgeStore.status = 'complete';
    forgeStore.result = mockOptimizationResult({ strategy_used: 'chain-of-thought' }) as any;
    forgeStore.scores = mockDimensionScores();
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Strategy')).toBeInTheDocument();
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    });
  });

  it('shows provider metadata when forge result has provider', async () => {
    forgeStore.status = 'complete';
    forgeStore.result = mockOptimizationResult({ provider: 'claude-cli' }) as any;
    forgeStore.scores = mockDimensionScores();
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Provider')).toBeInTheDocument();
      expect(screen.getByText('claude-cli')).toBeInTheDocument();
    });
  });

  // ── 10. Feedback state sync via feedback-event ──────────────────────────────

  it('syncs feedback state when feedback-event fires for the current optimization', async () => {
    forgeStore.status = 'complete';
    forgeStore.result = mockOptimizationResult({ id: 'opt-42' }) as any;
    forgeStore.scores = mockDimensionScores();
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Clarity')).toBeInTheDocument();
    });

    // Fire feedback-event with matching optimization_id
    window.dispatchEvent(new CustomEvent('feedback-event', {
      detail: { optimization_id: 'opt-42', rating: 'thumbs_up' },
    }));

    await waitFor(() => {
      expect(forgeStore.feedback).toBe('thumbs_up');
    });
  });

  it('does not sync feedback when feedback-event optimization_id does not match', async () => {
    forgeStore.status = 'complete';
    forgeStore.result = mockOptimizationResult({ id: 'opt-42' }) as any;
    forgeStore.scores = mockDimensionScores();
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Clarity')).toBeInTheDocument();
    });

    window.dispatchEvent(new CustomEvent('feedback-event', {
      detail: { optimization_id: 'opt-999', rating: 'thumbs_down' },
    }));

    // Wait a tick
    await new Promise(r => setTimeout(r, 50));
    expect(forgeStore.feedback).toBeNull();
  });

  // ── 11. Active phase states ──────────────────────────────────────────────────

  it('shows phase steps when forge is analyzing', () => {
    forgeStore.status = 'analyzing';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText('Analyzing')).toBeInTheDocument();
    expect(screen.getByText('Optimizing')).toBeInTheDocument();
    expect(screen.getByText('Scoring')).toBeInTheDocument();
  });

  it('shows phase steps when forge is optimizing', () => {
    forgeStore.status = 'optimizing';
    mockFetch([]);

    render(Inspector);

    // Analyzing should show checkmark (done), Optimizing active
    expect(screen.getByText('Analyzing')).toBeInTheDocument();
    expect(screen.getByText('Optimizing')).toBeInTheDocument();
  });

  it('shows phase steps when forge is scoring', () => {
    forgeStore.status = 'scoring';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText('Scoring')).toBeInTheDocument();
  });

  // ── 12. Error state ──────────────────────────────────────────────────────────

  it('shows error text when forge status is error', () => {
    forgeStore.status = 'error';
    forgeStore.error = 'Something went wrong';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
  });

  it('shows "Unknown error" fallback when forge error is null', () => {
    forgeStore.status = 'error';
    forgeStore.error = null;
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText('Unknown error')).toBeInTheDocument();
  });

  // ── 13. Passthrough state ────────────────────────────────────────────────────

  it('shows passthrough label when forge is in passthrough status', () => {
    forgeStore.status = 'passthrough';
    forgeStore.assembledPrompt = 'Some assembled prompt content';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText('Manual passthrough')).toBeInTheDocument();
  });

  it('shows "Preparing prompt..." when passthrough has no assembled prompt yet', () => {
    forgeStore.status = 'passthrough';
    forgeStore.assembledPrompt = null;
    mockFetch([]);

    render(Inspector);

    expect(screen.getByText(/Preparing prompt/i)).toBeInTheDocument();
  });

  // ── 14. Family detail not shown when forge is active ────────────────────────

  it('does not show family detail when forge is actively running (analyzing)', () => {
    forgeStore.status = 'analyzing';
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail() as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    // Family detail hidden — forge is active
    expect(screen.queryByText('API patterns')).not.toBeInTheDocument();
    // Phase steps shown instead
    expect(screen.getByText('Analyzing')).toBeInTheDocument();
  });

  // ── 15. State badge ──────────────────────────────────────────────────────────

  it('renders state badge with correct text for active state', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'active' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('active')).toBeInTheDocument();
    });
  });

  it('renders state badge with correct text for archived state', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'archived' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('archived')).toBeInTheDocument();
    });
  });

  // ── 16. Promote to template button (removed) ────────────────────────────────

  it('no longer renders Promote to template button on active clusters', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'active' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Promote to template')).not.toBeInTheDocument();
  });

  // ── 17. Unarchive button ─────────────────────────────────────────────────────

  it('renders "Unarchive" button when state is archived', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'archived' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Unarchive')).toBeInTheDocument();
    });
  });

  // ── 18. Buttons NOT shown for other states ───────────────────────────────────

  it('does not render promote/unarchive when state is candidate', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'candidate' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Promote to template')).not.toBeInTheDocument();
    expect(screen.queryByText('Unarchive')).not.toBeInTheDocument();
  });

  it('no longer renders Promote to template button on active clusters (section 18 variant)', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'active' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Promote to template')).not.toBeInTheDocument();
    expect(screen.queryByText('Unarchive')).not.toBeInTheDocument();
  });

  // ── 19. preferred_strategy display ──────────────────────────────────────────

  it('shows preferred_strategy row when non-null', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ preferred_strategy: 'chain-of-thought' }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Strategy')).toBeInTheDocument();
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    });
  });

  it('does not show Strategy row when preferred_strategy is null', async () => {
    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ preferred_strategy: null }) as any;
    clustersStore.clusterDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    // "Strategy" label only appears in the meta-section when preferred_strategy is set
    expect(screen.queryByText('Strategy')).not.toBeInTheDocument();
  });

  // ── 20. Promote / Unarchive click calls updateCluster ───────────────────────

  it('clicking "Unarchive" calls updateCluster with state: active', async () => {
    const user = userEvent.setup();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/clusters/fam-1') && init?.method === 'PATCH') {
        return new Response(JSON.stringify({ id: 'fam-1' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      // Handle loadTree API calls triggered by invalidateClusters
      if (url.includes('/clusters/tree')) return new Response(JSON.stringify({ nodes: [{ id: 'fam-1', label: 'test', state: 'active', domain: 'general', member_count: 1, usage_count: 1 }] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/clusters/stats')) return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/clusters/similarity-edges')) return new Response(JSON.stringify({ edges: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/clusters/injection-edges')) return new Response(JSON.stringify({ edges: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify(makeFamilyDetail({ state: 'active' })), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    clustersStore.selectedClusterId = 'fam-1';
    clustersStore.clusterDetail = makeFamilyDetail({ state: 'archived' }) as any;
    clustersStore.clusterDetailLoading = false;

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Unarchive')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Unarchive'));

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
      const patchCall = calls.find((c: unknown[]) => {
        const [, init] = c as [RequestInfo | URL, RequestInit?];
        return init?.method === 'PATCH';
      });
      expect(patchCall).toBeDefined();
      const [, patchInit] = patchCall as [RequestInfo | URL, RequestInit];
      const body = JSON.parse(patchInit.body as string);
      expect(body.state).toBe('active');
    });
  });

  // ── 21. Template surface (cluster-view Templates collapsible) ───────────────

  describe('Templates collapsible in cluster detail view', () => {
    beforeEach(() => {
      templatesStore.templates = [];
    });

    it('does not render Templates section when no templates forked from this cluster', async () => {
      clustersStore.selectedClusterId = 'fam-1';
      clustersStore.clusterDetail = makeFamilyDetail({ id: 'fam-1', state: 'mature' }) as any;
      clustersStore.clusterDetailLoading = false;
      mockFetch([]);
      render(Inspector);
      await waitFor(() => expect(screen.getByText('API patterns')).toBeInTheDocument());
      expect(screen.queryByText(/^Templates \(/)).not.toBeInTheDocument();
    });

    it('renders Templates section when templates reference this cluster', async () => {
      templatesStore.templates = [
        {
          id: 't1', source_cluster_id: 'fam-1', source_optimization_id: 'o1',
          project_id: null, label: 'Auth checklist', prompt: 'Stub prompt',
          strategy: 'chain-of-thought', score: 8.2, pattern_ids: [],
          domain_label: 'backend', promoted_at: '2026-04-15T00:00:00Z',
          retired_at: null, retired_reason: null, usage_count: 3, last_used_at: null,
        },
      ];
      clustersStore.selectedClusterId = 'fam-1';
      clustersStore.clusterDetail = makeFamilyDetail({ id: 'fam-1', state: 'mature', domain: 'backend' }) as any;
      clustersStore.clusterDetailLoading = false;
      mockFetch([]);
      render(Inspector);
      await waitFor(() => expect(screen.getByText(/Templates \(1\)/)).toBeInTheDocument());
      expect(screen.getByText('Auth checklist')).toBeInTheDocument();
    });

    it('reparented template shows annotation when source cluster now in different domain', async () => {
      templatesStore.templates = [
        {
          id: 't1', source_cluster_id: 'fam-1', source_optimization_id: 'o1',
          project_id: null, label: 'Migrated template', prompt: 'p',
          strategy: null, score: 7.5, pattern_ids: [],
          domain_label: 'backend', // frozen origin
          promoted_at: '2026-04-15T00:00:00Z', retired_at: null, retired_reason: null,
          usage_count: 0, last_used_at: null,
        },
      ];
      clustersStore.selectedClusterId = 'fam-1';
      clustersStore.clusterDetail = makeFamilyDetail({ id: 'fam-1', state: 'mature', domain: 'data' }) as any; // different from domain_label
      clustersStore.clusterDetailLoading = false;
      mockFetch([]);
      render(Inspector);
      await waitFor(() => expect(screen.getByText('Migrated template')).toBeInTheDocument());
      expect(screen.getByText(/reparented/i)).toBeInTheDocument();
    });

    it('non-reparented template does not show annotation', async () => {
      templatesStore.templates = [
        {
          id: 't1', source_cluster_id: 'fam-1', source_optimization_id: 'o1',
          project_id: null, label: 'Stable template', prompt: 'p',
          strategy: null, score: 7.5, pattern_ids: [],
          domain_label: 'backend',
          promoted_at: '2026-04-15T00:00:00Z', retired_at: null, retired_reason: null,
          usage_count: 0, last_used_at: null,
        },
      ];
      clustersStore.selectedClusterId = 'fam-1';
      clustersStore.clusterDetail = makeFamilyDetail({ id: 'fam-1', state: 'mature', domain: 'backend' }) as any; // matches
      clustersStore.clusterDetailLoading = false;
      mockFetch([]);
      render(Inspector);
      await waitFor(() => expect(screen.getByText('Stable template')).toBeInTheDocument());
      expect(screen.queryByText(/reparented/i)).not.toBeInTheDocument();
    });

    it('retired templates are hidden from the list', async () => {
      templatesStore.templates = [
        {
          id: 't1', source_cluster_id: 'fam-1', source_optimization_id: 'o1',
          project_id: null, label: 'Alive', prompt: 'p', strategy: null, score: 7,
          pattern_ids: [], domain_label: 'backend', promoted_at: '2026-04-15T00:00:00Z',
          retired_at: null, retired_reason: null, usage_count: 0, last_used_at: null,
        },
        {
          id: 't2', source_cluster_id: 'fam-1', source_optimization_id: 'o2',
          project_id: null, label: 'Retired', prompt: 'p', strategy: null, score: 7,
          pattern_ids: [], domain_label: 'backend', promoted_at: '2026-04-15T00:00:00Z',
          retired_at: '2026-04-16T00:00:00Z', retired_reason: 'manual', usage_count: 0, last_used_at: null,
        },
      ];
      clustersStore.selectedClusterId = 'fam-1';
      clustersStore.clusterDetail = makeFamilyDetail({ id: 'fam-1', state: 'mature', domain: 'backend' }) as any;
      clustersStore.clusterDetailLoading = false;
      mockFetch([]);
      render(Inspector);
      await waitFor(() => expect(screen.getByText(/Templates \(1\)/)).toBeInTheDocument());
      expect(screen.getByText('Alive')).toBeInTheDocument();
      expect(screen.queryByText('Retired')).not.toBeInTheDocument();
    });
  });
});
