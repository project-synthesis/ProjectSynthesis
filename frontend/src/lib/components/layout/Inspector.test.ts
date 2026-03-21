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
import { patternsStore } from '$lib/stores/patterns.svelte';
import { editorStore } from '$lib/stores/editor.svelte';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Build the FamilyDetail response used in most pattern-family tests. */
function makeFamilyDetail(overrides: Record<string, unknown> = {}) {
  return {
    ...mockPatternFamily({ id: 'fam-1', intent_label: 'API patterns', domain: 'backend', task_type: 'coding', member_count: 3, usage_count: 5, avg_score: 7.8 }),
    updated_at: '2026-03-20T12:00:00Z',
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
    ...overrides,
  };
}

/** Set up fetch mocks for family-detail + feedback endpoints. */
function familyFetchHandlers(familyOverrides: Record<string, unknown> = {}) {
  return mockFetch([
    {
      match: '/api/patterns/families/',
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
    patternsStore._reset();
    editorStore._reset();
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
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;

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
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Usage')).toBeInTheDocument();
    });
    expect(screen.getByText('Members')).toBeInTheDocument();
    expect(screen.getByText('Avg Score')).toBeInTheDocument();
  });

  it('shows domain badge for the selected family', async () => {
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail({ domain: 'frontend' }) as any;
    patternsStore.familyDetailLoading = false;
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
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = detail as any;
    patternsStore.familyDetailLoading = false;
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
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Meta-patterns')).toBeInTheDocument();
    });
  });

  it('does not render meta-patterns section when list is empty', async () => {
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail({ meta_patterns: [] }) as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Meta-patterns')).not.toBeInTheDocument();
  });

  // ── 4. Linked optimizations ──────────────────────────────────────────────────

  it('renders linked optimization entries', async () => {
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Linked optimizations')).toBeInTheDocument();
    });
    // The optimization uses intent_label when present
    expect(screen.getByText('Write API')).toBeInTheDocument();
    // Score displayed
    expect(screen.getByText('8.0')).toBeInTheDocument();
  });

  it('clicking a linked optimization calls openResult on editorStore', async () => {
    const user = userEvent.setup();
    const openResultSpy = vi.spyOn(editorStore, 'openResult');

    // Mock getOptimization fetch
    mockFetch([
      {
        match: '/api/optimize/',
        response: mockOptimizationResult({ id: 'opt-1', trace_id: 'trace-1' }),
      },
    ]);

    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('Write API')).toBeInTheDocument();
    });

    await user.click(screen.getByText('Write API'));

    await waitFor(() => {
      expect(openResultSpy).toHaveBeenCalledWith('opt-1', expect.anything());
    });
  });

  it('does not render linked optimizations section when list is empty', async () => {
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail({ optimizations: [] }) as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });
    expect(screen.queryByText('Linked optimizations')).not.toBeInTheDocument();
  });

  // ── 5. Inline rename ─────────────────────────────────────────────────────────

  it('clicking the family intent label enters rename edit mode', async () => {
    const user = userEvent.setup();
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    // The intent label is a button with title "Click to rename"
    await user.click(screen.getByTitle('Click to rename'));

    // Rename input should now be visible with the current label as value
    const input = screen.getByRole('textbox', { name: 'Family name' }) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.value).toBe('API patterns');
  });

  it('pressing Escape in rename input cancels rename', async () => {
    const user = userEvent.setup();
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByTitle('Click to rename'));
    const input = screen.getByRole('textbox', { name: 'Family name' });
    // Fire keydown directly on the input to trigger the Svelte onkeydown handler
    fireEvent.keyDown(input, { key: 'Escape', code: 'Escape' });

    await waitFor(() => {
      // Rename form gone, original intent label visible again
      expect(screen.queryByRole('textbox', { name: 'Family name' })).not.toBeInTheDocument();
    });
    expect(screen.getByTitle('Click to rename')).toBeInTheDocument();
  });

  it('clicking cancel button in rename form reverts to display mode', async () => {
    const user = userEvent.setup();
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByTitle('Click to rename'));
    // Cancel button has title="Cancel"
    await user.click(screen.getByTitle('Cancel'));

    expect(screen.queryByRole('textbox', { name: 'Family name' })).not.toBeInTheDocument();
    expect(screen.getByTitle('Click to rename')).toBeInTheDocument();
  });

  it('submitting rename form calls renameFamily API and refreshes family', async () => {
    const user = userEvent.setup();

    const fetchMock = mockFetch([
      {
        match: '/api/patterns/families/',
        response: makeFamilyDetail({ intent_label: 'Renamed Family' }),
      },
    ]);

    // Simulate renameFamily (PATCH) and re-fetch (GET)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
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

    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByTitle('Click to rename'));
    const input = screen.getByRole('textbox', { name: 'Family name' });
    await user.clear(input);
    await user.type(input, 'Renamed Family');

    // Click save (checkmark button with title="Save")
    await user.click(screen.getByTitle('Save'));

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

  // ── Domain picker ──────────────────────────────────────────────────────────

  it('clicking domain badge opens domain picker with 7 domain options', async () => {
    const user = userEvent.setup();
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    // Click the domain badge button
    await user.click(screen.getByRole('button', { name: 'Change domain' }));

    // Domain picker should appear with all 7 domain options
    const picker = screen.getByRole('listbox', { name: 'Select domain' });
    expect(picker).toBeInTheDocument();

    const options = screen.getAllByRole('option');
    expect(options).toHaveLength(7);
  });

  it('selecting a domain in picker calls PATCH API with new domain', async () => {
    const user = userEvent.setup();

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/patterns/families/fam-1')) {
        if (init?.method === 'PATCH') {
          return new Response(JSON.stringify({ id: 'fam-1', intent_label: 'API patterns', domain: 'frontend' }), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        return new Response(JSON.stringify(makeFamilyDetail({ domain: 'frontend' })), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response('Not Found', { status: 404 });
    }));

    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    // Open domain picker
    await user.click(screen.getByRole('button', { name: 'Change domain' }));

    // Click 'frontend' option
    const options = screen.getAllByRole('option');
    const frontendOption = options.find(o => o.textContent === 'frontend');
    expect(frontendOption).toBeDefined();
    await user.click(frontendOption!);

    await waitFor(() => {
      const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
      const patchCall = calls.find((c: unknown[]) => {
        const [, init] = c as [RequestInfo | URL, RequestInit?];
        return init?.method === 'PATCH';
      });
      expect(patchCall).toBeDefined();
      // Verify the PATCH body contains the domain
      const [, patchInit] = patchCall as [RequestInfo | URL, RequestInit];
      const body = JSON.parse(patchInit.body as string);
      expect(body.domain).toBe('frontend');
    });
  });

  // ── 6. Dismiss button ────────────────────────────────────────────────────────

  it('clicking dismiss button deselects the family', async () => {
    const user = userEvent.setup();
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    await waitFor(() => {
      expect(screen.getByText('API patterns')).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: 'Close family detail' }));

    expect(patternsStore.selectedFamilyId).toBeNull();
  });

  // ── 7. Loading state ─────────────────────────────────────────────────────────

  it('shows loading spinner when familyDetailLoading is true', () => {
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = null;
    patternsStore.familyDetailLoading = true;
    mockFetch([]);

    render(Inspector);

    expect(screen.getByRole('status', { name: 'Loading family' })).toBeInTheDocument();
  });

  // ── 8. Error state from family detail ────────────────────────────────────────

  it('shows error message when familyDetailError is set', () => {
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = null;
    patternsStore.familyDetailLoading = false;
    patternsStore.familyDetailError = 'Failed to load family';
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
    forgeStore.result = mockOptimizationResult() as any;
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

  it('shows spinner when forge is analyzing', () => {
    forgeStore.status = 'analyzing';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByRole('status', { name: 'Processing' })).toBeInTheDocument();
  });

  it('shows spinner when forge is optimizing', () => {
    forgeStore.status = 'optimizing';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByRole('status', { name: 'Processing' })).toBeInTheDocument();
  });

  it('shows spinner when forge is scoring', () => {
    forgeStore.status = 'scoring';
    mockFetch([]);

    render(Inspector);

    expect(screen.getByRole('status', { name: 'Processing' })).toBeInTheDocument();
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
    patternsStore.selectedFamilyId = 'fam-1';
    patternsStore.familyDetail = makeFamilyDetail() as any;
    patternsStore.familyDetailLoading = false;
    mockFetch([]);

    render(Inspector);

    // Family detail hidden — forge is active
    expect(screen.queryByText('API patterns')).not.toBeInTheDocument();
    // Spinner shown instead
    expect(screen.getByRole('status', { name: 'Processing' })).toBeInTheDocument();
  });
});
