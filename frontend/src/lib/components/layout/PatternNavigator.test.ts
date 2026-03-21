import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { mockFetch, mockPatternFamily, mockMetaPattern } from '$lib/test-utils';

import PatternNavigator from './PatternNavigator.svelte';
import { patternsStore } from '$lib/stores/patterns.svelte';
import { editorStore } from '$lib/stores/editor.svelte';

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Build a pagination envelope for /api/patterns/families */
function familiesResponse(
  items: ReturnType<typeof mockPatternFamily>[],
  opts: { total?: number; has_more?: boolean; next_offset?: number | null } = {}
) {
  return {
    total: opts.total ?? items.length,
    count: items.length,
    offset: 0,
    has_more: opts.has_more ?? false,
    next_offset: opts.next_offset ?? null,
    items,
  };
}

/** Build a FamilyDetail response for /api/patterns/families/:id */
function familyDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...mockPatternFamily({ id: 'fam-1', intent_label: 'API patterns', domain: 'backend' }),
    updated_at: '2026-03-20T12:00:00Z',
    meta_patterns: [mockMetaPattern({ id: 'mp-1', pattern_text: 'Handle errors', source_count: 2 })],
    optimizations: [],
    ...overrides,
  };
}

/** Default fetch handlers for the component's initial data needs. */
function defaultHandlers(
  items: ReturnType<typeof mockPatternFamily>[] = [],
  opts: { has_more?: boolean; next_offset?: number | null; total?: number } = {}
) {
  return mockFetch([
    {
      match: '/api/patterns/families',
      response: familiesResponse(items, opts),
    },
    {
      match: '/api/patterns/families/',
      response: familyDetail(),
    },
  ]);
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('PatternNavigator', () => {
  beforeEach(() => {
    patternsStore._reset();
    editorStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  // ── 1. Family list rendering ────────────────────────────────────────────────

  it('renders domain headers when families from different domains are loaded', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(PatternNavigator);

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
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.getByText('UI patterns')).toBeInTheDocument();
  });

  it('renders usage_count badge for each family', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns', usage_count: 7 }),
    ]);
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('7')).toBeInTheDocument();
    });
  });

  it('renders avg_score for each family', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns', avg_score: 8.3 }),
    ]);
    render(PatternNavigator);

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
    render(PatternNavigator);

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
    render(PatternNavigator);

    await waitFor(() => {
      // totalFamilies = 2, shown in header badge
      expect(screen.getAllByText('2').length).toBeGreaterThanOrEqual(1);
    });
  });

  // ── 2. Pagination ──────────────────────────────────────────────────────────

  it('shows "Load more" button when has_more is true', async () => {
    defaultHandlers(
      [mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' })],
      { has_more: true, next_offset: 50, total: 100 }
    );
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('Load more')).toBeInTheDocument();
    });
  });

  it('hides "Load more" button when has_more is false', async () => {
    defaultHandlers(
      [mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' })],
      { has_more: false }
    );
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Load more')).not.toBeInTheDocument();
  });

  it('clicking "Load more" fetches next page and appends results', async () => {
    const user = userEvent.setup();

    // First page
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-')) {
        return new Response(JSON.stringify(familyDetail()), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('offset=50')) {
        // Second page
        return new Response(JSON.stringify(familiesResponse(
          [mockPatternFamily({ id: 'fam-2', domain: 'backend', intent_label: 'Auth patterns' })],
          { total: 2, has_more: false, next_offset: null }
        )), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/patterns/families')) {
        // First page (no offset param)
        return new Response(JSON.stringify(familiesResponse(
          [mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' })],
          { total: 2, has_more: true, next_offset: 50 }
        )), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchMock);

    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('Load more')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Load more'));

    await waitFor(() => {
      expect(screen.getByText('Auth patterns')).toBeInTheDocument();
    });
    // First page item still present
    expect(screen.getByText('API patterns')).toBeInTheDocument();
    // Load more hidden after fully loaded
    expect(screen.queryByText('Load more')).not.toBeInTheDocument();
  });

  // ── 3. Domain filtering (via search) ───────────────────────────────────────
  //
  // Note: PatternNavigator groups families by domain but does NOT have a
  // separate domain filter UI element — filtering happens by displaying
  // grouped headers. The component uses listFamilies() without a domain param
  // on the initial load; domain filtering is rendered through domain grouping.
  // We verify the grouping reflects different domains correctly.

  it('groups families from the same domain under one header', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'backend', intent_label: 'Auth patterns' }),
      mockPatternFamily({ id: 'fam-3', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(PatternNavigator);

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
    render(PatternNavigator);
    expect(screen.getByPlaceholderText('Search patterns...')).toBeInTheDocument();
  });

  it('shows search results immediately when typing (local filter from taxonomy tree)', async () => {
    const user = userEvent.setup();

    // Pre-populate taxonomy tree for local search
    patternsStore.taxonomyTree = [
      { id: 'node-1', parent_id: null, label: 'API patterns', state: 'confirmed', persistence: null, coherence: 0.9, separation: null, stability: null, member_count: 3, usage_count: 5, color_hex: '#a855f7', umap_x: null, umap_y: null, umap_z: null },
    ] as any;

    defaultHandlers([]);
    render(PatternNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'API');

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
  });

  it('shows no-match message when search query matches no taxonomy nodes', async () => {
    const user = userEvent.setup();

    // Empty taxonomy tree
    patternsStore.taxonomyTree = [];

    defaultHandlers([]);
    render(PatternNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'xyz');

    await waitFor(() => {
      expect(screen.getByText(/No matches for/i)).toBeInTheDocument();
    });
  });

  it('shows a clear button when search query is non-empty', async () => {
    const user = userEvent.setup();
    defaultHandlers([]);
    render(PatternNavigator);

    const input = screen.getByPlaceholderText('Search patterns...');
    await user.type(input, 'API');

    expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument();
  });

  it('clicking the clear button resets search and hides search results', async () => {
    const user = userEvent.setup();

    patternsStore.taxonomyTree = [
      { id: 'node-1', parent_id: null, label: 'API patterns', state: 'confirmed', persistence: null, coherence: 0.9, separation: null, stability: null, member_count: 3, usage_count: 5, color_hex: '#a855f7', umap_x: null, umap_y: null, umap_z: null },
    ] as any;

    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
    ]);
    render(PatternNavigator);

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

  it('clicking a family row calls patternsStore.selectFamily with its id', async () => {
    const user = userEvent.setup();
    const selectSpy = vi.spyOn(patternsStore, 'selectFamily');

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-42')) {
        return new Response(JSON.stringify(familyDetail({ id: 'fam-42' })), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/patterns/families')) {
        return new Response(JSON.stringify(familiesResponse([
          mockPatternFamily({ id: 'fam-42', domain: 'backend', intent_label: 'API patterns' }),
        ])), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    }));

    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    expect(selectSpy).toHaveBeenCalledWith('fam-42');
  });

  it('clicking an already-expanded family collapses it and calls selectFamily(null)', async () => {
    const user = userEvent.setup();
    const selectSpy = vi.spyOn(patternsStore, 'selectFamily');

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
        return new Response(JSON.stringify(familyDetail()), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/patterns/families')) {
        return new Response(JSON.stringify(familiesResponse([
          mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
        ])), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    }));

    render(PatternNavigator);

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
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
        // Hang forever to test loading indicator
        return new Promise(() => {}) as Promise<Response>;
      }
      if (url.includes('/api/patterns/families')) {
        return new Response(JSON.stringify(familiesResponse([
          mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
        ])), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    }));

    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    await waitFor(() => {
      // The expanded detail shows a "Loading..." note while waiting
      expect(screen.getByText('Loading...')).toBeInTheDocument();
    });
  });

  it('shows expanded meta-patterns after family detail loads', async () => {
    const user = userEvent.setup();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
        return new Response(JSON.stringify(familyDetail({
          meta_patterns: [mockMetaPattern({ id: 'mp-1', pattern_text: 'Validate inputs', source_count: 4 })],
        })), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/patterns/families')) {
        return new Response(JSON.stringify(familiesResponse([
          mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
        ])), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    }));

    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    await waitFor(() => {
      expect(screen.getByText('Validate inputs')).toBeInTheDocument();
    });
    expect(screen.getByText('4x')).toBeInTheDocument();
  });

  // ── 6. Empty state ────────────────────────────────────────────────────────

  it('shows empty-state placeholder when no families exist', async () => {
    defaultHandlers([]);
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText(/Optimize your first prompt/i)).toBeInTheDocument();
    });
  });

  // ── 7. Loading state ──────────────────────────────────────────────────────

  it('shows "Loading..." while initial families fetch is pending', () => {
    // Fetch never resolves
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    render(PatternNavigator);
    expect(screen.getByText('Loading...')).toBeInTheDocument();
  });

  // ── 8. Mindmap button ─────────────────────────────────────────────────────

  it('clicking the mindmap button calls editorStore.openMindmap', async () => {
    const user = userEvent.setup();
    const openMindmapSpy = vi.spyOn(editorStore, 'openMindmap');
    const loadTreeSpy = vi.spyOn(patternsStore, 'loadTree').mockResolvedValue();

    defaultHandlers([]);
    render(PatternNavigator);

    const mindmapBtn = screen.getByTitle('Open pattern mindmap');
    await user.click(mindmapBtn);

    expect(openMindmapSpy).toHaveBeenCalled();
    expect(loadTreeSpy).toHaveBeenCalled();
  });

  // ── 9. Error state ────────────────────────────────────────────────────────

  it('shows error message when families fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('Network failure');
    }));
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('Network failure')).toBeInTheDocument();
    });
  });

  // ── 10. Search result clicking selects family ──────────────────────────────

  it('clicking a search result calls patternsStore.selectFamily and clears search', async () => {
    const user = userEvent.setup();
    const selectSpy = vi.spyOn(patternsStore, 'selectFamily');

    // Pre-populate taxonomy tree for local search
    patternsStore.taxonomyTree = [
      { id: 'fam-1', parent_id: null, label: 'API patterns', state: 'confirmed', persistence: null, coherence: 0.9, separation: null, stability: null, member_count: 3, usage_count: 5, color_hex: '#a855f7', umap_x: null, umap_y: null, umap_z: null },
    ] as any;

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
        return new Response(JSON.stringify(familyDetail({ id: 'fam-1' })), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/patterns/families')) {
        return new Response(JSON.stringify(familiesResponse([])), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('Not Found', { status: 404 });
    }));

    render(PatternNavigator);

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

  // ── 11. Expanded detail — no meta-patterns fallback ───────────────────────

  it('shows "No meta-patterns extracted yet" when family has empty meta_patterns', async () => {
    const user = userEvent.setup();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
        return new Response(JSON.stringify(familyDetail({ meta_patterns: [] })), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/patterns/families')) {
        return new Response(JSON.stringify(familiesResponse([
          mockPatternFamily({ id: 'fam-1', domain: 'backend', intent_label: 'API patterns' }),
        ])), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    }));

    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByText('API patterns'));

    await waitFor(() => {
      expect(screen.getByText('No meta-patterns extracted yet.')).toBeInTheDocument();
    });
  });

  // ── 12. Domains sorted alphabetically ─────────────────────────────────────

  it('renders domain headers in alphabetical order', async () => {
    defaultHandlers([
      mockPatternFamily({ id: 'fam-1', domain: 'security', intent_label: 'Security patterns' }),
      mockPatternFamily({ id: 'fam-2', domain: 'backend', intent_label: 'API patterns' }),
      mockPatternFamily({ id: 'fam-3', domain: 'frontend', intent_label: 'UI patterns' }),
    ]);
    render(PatternNavigator);

    await waitFor(() => {
      expect(screen.getByText('backend')).toBeInTheDocument();
    });

    const domainHeaders = screen.getAllByText(/^(backend|frontend|security)$/);
    const labels = domainHeaders.map(el => el.textContent);
    const sorted = [...labels].sort();
    expect(labels).toEqual(sorted);
  });
});
