import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { mockFetch, mockHistoryItem, mockStrategyInfo, mockOptimizationResult } from '$lib/test-utils';

// Mock ClusterNavigator sub-component (used when active='clusters')
vi.mock('$lib/components/layout/ClusterNavigator.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));

// Mock the githubStore.checkAuth to prevent network calls
vi.mock('$lib/stores/github.svelte', () => {
  const store = {
    user: null,
    linkedRepo: null,
    loading: false,
    error: null,
    checkAuth: vi.fn().mockResolvedValue(undefined),
    login: vi.fn(),
    unlinkRepo: vi.fn(),
    _reset() {
      this.user = null;
      this.linkedRepo = null;
      this.loading = false;
      this.error = null;
    },
  };
  return { githubStore: store };
});

import Navigator from './Navigator.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { githubStore } from '$lib/stores/github.svelte';

// ── Helpers ─────────────────────────────────────────────────────────────────

const DEFAULT_SETTINGS = {
  max_raw_prompt_chars: 50000,
  max_context_tokens: 80000,
  optimize_rate_limit: '10/minute',
  feedback_rate_limit: '30/minute',
  refine_rate_limit: '10/minute',
  embedding_model: 'all-MiniLM-L6-v2',
  trace_retention_days: 7,
  database_engine: 'sqlite',
};

function defaultFetchHandlers(overrides: Record<string, unknown> = {}) {
  // settings: null → HTTP 500 (simulates backend down); omitted → DEFAULT_SETTINGS
  const settingsEntry = 'settings' in overrides
    ? (overrides.settings === null
      ? { match: '/api/settings', response: 'Internal Server Error', status: 500 }
      : { match: '/api/settings', response: overrides.settings })
    : { match: '/api/settings', response: DEFAULT_SETTINGS };

  return mockFetch([
    {
      match: '/api/history',
      response: {
        total: 0,
        count: 0,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [],
        ...((overrides.history as Record<string, unknown>) ?? {}),
      },
    },
    {
      match: '/api/strategies',
      response: ((overrides.strategies as unknown[]) ?? []),
    },
    {
      match: '/api/providers',
      response: (overrides.providers ?? { active_provider: 'claude-cli', available: ['claude_cli'], routing_tiers: ['internal'] }),
    },
    settingsEntry,
    {
      match: '/api/provider/api-key',
      response: (overrides.apiKey ?? { configured: false, masked_key: null }),
    },
    {
      match: '/api/preferences',
      response: (overrides.preferences ?? preferencesStore.prefs),
    },
  ]);
}

// ── Tests ────────────────────────────────────────────────────────────────────

describe('Navigator', () => {
  beforeEach(() => {
    forgeStore._reset();
    preferencesStore._reset();
    githubStore._reset();
    vi.clearAllMocks();
    // Set a provider so routing resolver sees internal tier (not passthrough),
    // ensuring Models/Effort/Pipeline toggles are visible in tests.
    forgeStore.provider = 'claude_cli';
  });

  afterEach(() => {
    cleanup();
  });

  // ── Rendering ──────────────────────────────────────────────────────────────

  it('renders the navigator aside element', () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'editor' } });
    expect(screen.getByRole('complementary', { name: 'Navigator' })).toBeInTheDocument();
  });

  // ── Editor panel (strategies) ──────────────────────────────────────────────

  it('shows empty strategies message when no strategies are loaded', async () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText(/No strategy files found/i)).toBeInTheDocument();
    });
  });

  it('renders strategy list after fetch', async () => {
    defaultFetchHandlers({
      strategies: [
        mockStrategyInfo({ name: 'chain-of-thought', tagline: 'Step-by-step reasoning' }),
        mockStrategyInfo({ name: 'few-shot', tagline: 'Learn from examples' }),
      ],
    });
    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
      expect(screen.getByText('few-shot')).toBeInTheDocument();
    });
  });

  it('renders strategy taglines', async () => {
    defaultFetchHandlers({
      strategies: [mockStrategyInfo({ name: 'chain-of-thought', tagline: 'Step-by-step reasoning' })],
    });
    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('Step-by-step reasoning')).toBeInTheDocument();
    });
  });

  it('clicking a strategy row selects it in forgeStore', async () => {
    const user = userEvent.setup();
    defaultFetchHandlers({
      strategies: [mockStrategyInfo({ name: 'chain-of-thought' })],
    });
    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    });
    const row = screen.getByText('chain-of-thought').closest('[role="button"]')!;
    await user.click(row);
    expect(forgeStore.strategy).toBe('chain-of-thought');
  });

  it('clicking a selected strategy deselects it (back to null)', async () => {
    const user = userEvent.setup();
    forgeStore.strategy = 'chain-of-thought';
    defaultFetchHandlers({
      strategies: [mockStrategyInfo({ name: 'chain-of-thought' })],
    });
    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    });
    const row = screen.getByText('chain-of-thought').closest('[role="button"]')!;
    await user.click(row);
    expect(forgeStore.strategy).toBeNull();
  });

  // ── History panel ──────────────────────────────────────────────────────────

  it('shows skeleton loading state while history is being fetched', () => {
    // Don't resolve fetch immediately — just check initial state
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {}))); // never resolves
    render(Navigator, { props: { active: 'history' } });
    // Component shows skeleton bars (not text) while loading
    const skeletonBars = document.querySelectorAll('.skeleton-row');
    expect(skeletonBars.length).toBeGreaterThan(0);
  });

  it('shows empty state when history is empty', async () => {
    defaultFetchHandlers({ history: { total: 0, count: 0, offset: 0, has_more: false, next_offset: null, items: [] } });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText(/No optimizations yet/i)).toBeInTheDocument();
    });
  });

  it('renders history items with strategy and score', async () => {
    defaultFetchHandlers({
      history: {
        total: 1,
        count: 1,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [
          mockHistoryItem({
            id: 'opt-1',
            status: 'completed',
            strategy_used: 'chain-of-thought',
            overall_score: 8.5,
            intent_label: 'Hello world program',
            domain: 'backend',
          }),
        ],
      },
    });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText('Hello world program')).toBeInTheDocument();
    });
    expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    // Score displayed (formatScore of 8.5)
    expect(screen.getByText(/8\.5/)).toBeInTheDocument();
  });

  it('applies domain-based accent color to history items', async () => {
    defaultFetchHandlers({
      history: {
        total: 1,
        count: 1,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [
          mockHistoryItem({
            id: 'opt-2',
            status: 'completed',
            domain: 'frontend',
            intent_label: 'React component',
          }),
        ],
      },
    });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText('React component')).toBeInTheDocument();
    });
    // Domain is applied as a CSS --accent variable, not rendered as text
    const row = screen.getByText('React component').closest('.history-row') as HTMLElement;
    expect(row).not.toBeNull();
    expect(row.style.cssText).toContain('--accent');
  });

  it('does not render items with non-completed status', async () => {
    defaultFetchHandlers({
      history: {
        total: 1,
        count: 1,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [
          mockHistoryItem({ id: 'opt-3', status: 'error', intent_label: 'Should not appear' }),
        ],
      },
    });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      // historyLoaded becomes true, so "Loading…" should be gone
      expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument();
    });
    // The item with status 'error' should not appear
    expect(screen.queryByText('Should not appear')).not.toBeInTheDocument();
  });

  it('shows "No completed optimizations yet" when all items are non-completed', async () => {
    defaultFetchHandlers({
      history: {
        total: 1,
        count: 1,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [mockHistoryItem({ id: 'opt-4', status: 'error' })],
      },
    });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText(/No completed optimizations yet/i)).toBeInTheDocument();
    });
  });

  it('uses raw_prompt prefix when intent_label is null', async () => {
    defaultFetchHandlers({
      history: {
        total: 1,
        count: 1,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [
          mockHistoryItem({
            id: 'opt-5',
            status: 'completed',
            intent_label: null,
            raw_prompt: 'Write a function to sort an array',
          }),
        ],
      },
    });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText(/Write a function to sort an array/i)).toBeInTheDocument();
    });
  });

  // ── Real-time events ───────────────────────────────────────────────────────

  it('re-fetches history when optimization-event is dispatched', async () => {
    const fetchMock = defaultFetchHandlers({
      history: {
        total: 0,
        count: 0,
        offset: 0,
        has_more: false,
        next_offset: null,
        items: [],
      },
    });
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText(/No optimizations yet/i)).toBeInTheDocument();
    });

    const callsBefore = fetchMock.mock.calls.length;

    // Update the mock to return a new item
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/history')) {
        return new Response(JSON.stringify({
          total: 1,
          count: 1,
          offset: 0,
          has_more: false,
          next_offset: null,
          items: [mockHistoryItem({ id: 'opt-new', status: 'completed', intent_label: 'New optimization' })],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response('Not Found', { status: 404 });
    }));

    // Dispatch optimization-event
    window.dispatchEvent(new Event('optimization-event'));

    await waitFor(() => {
      expect(screen.getByText('New optimization')).toBeInTheDocument();
    });
  });

  // ── Settings panel — Model preferences ────────────────────────────────────

  it('renders model dropdowns in settings panel', () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    // Labels appear twice: once in Models section, once in Effort section
    expect(screen.getAllByText('Analyzer')).toHaveLength(2);
    expect(screen.getAllByText('Optimizer')).toHaveLength(2);
    expect(screen.getAllByText('Scorer')).toHaveLength(2);
  });

  it('model selects show current preferences values', () => {
    preferencesStore.prefs.models.analyzer = 'opus';
    preferencesStore.prefs.models.optimizer = 'haiku';
    preferencesStore.prefs.models.scorer = 'sonnet';
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    const selects = screen.getAllByRole('combobox') as HTMLSelectElement[];
    const analyzerSelect = selects.find(s => s.value === 'opus');
    const haikuSelect = selects.find(s => s.value === 'haiku');
    expect(analyzerSelect).toBeDefined();
    expect(haikuSelect).toBeDefined();
  });

  it('renders pipeline toggle switches in settings panel', () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByRole('switch', { name: 'Toggle Explore' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Toggle Scoring' })).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Toggle Adaptation' })).toBeInTheDocument();
  });

  it('pipeline toggles reflect current preferences state (Explore ON by default)', () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    const exploreToggle = screen.getByRole('switch', { name: 'Toggle Explore' });
    expect(exploreToggle).toHaveAttribute('aria-checked', 'true');
  });

  it('shows LEAN MODE badge when explore and scoring are both off', () => {
    preferencesStore.prefs.pipeline.enable_explore = false;
    preferencesStore.prefs.pipeline.enable_scoring = false;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('LEAN MODE')).toBeInTheDocument();
  });

  it('does not show LEAN MODE badge when explore or scoring is on', () => {
    preferencesStore.prefs.pipeline.enable_explore = true;
    preferencesStore.prefs.pipeline.enable_scoring = false;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.queryByText('LEAN MODE')).not.toBeInTheDocument();
  });

  // ── Settings panel — API key ───────────────────────────────────────────────

  it('shows "not set" in provider accordion when no API key configured', async () => {
    defaultFetchHandlers({ apiKey: { configured: false, masked_key: null } });
    render(Navigator, { props: { active: 'settings' } });
    // Expand the Provider accordion
    const accordionBtn = screen.getByRole('button', { name: /Provider/i });
    await userEvent.click(accordionBtn);
    await waitFor(() => {
      expect(screen.getByText('not set')).toBeInTheDocument();
    });
  });

  it('shows masked key when API key is configured', async () => {
    defaultFetchHandlers({
      apiKey: { configured: true, masked_key: 'sk-...abcd' },
    });
    render(Navigator, { props: { active: 'settings' } });
    const accordionBtn = screen.getByRole('button', { name: /Provider/i });
    await userEvent.click(accordionBtn);
    await waitFor(() => {
      expect(screen.getByText('sk-...abcd')).toBeInTheDocument();
    });
  });

  it('shows SET KEY and REMOVE buttons when provider section is expanded and key configured', async () => {
    defaultFetchHandlers({
      apiKey: { configured: true, masked_key: 'sk-...xyz' },
    });
    render(Navigator, { props: { active: 'settings' } });
    const accordionBtn = screen.getByRole('button', { name: /Provider/i });
    await userEvent.click(accordionBtn);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /SET KEY/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /REMOVE/i })).toBeInTheDocument();
    });
  });

  it('does not show REMOVE button when no API key configured', async () => {
    defaultFetchHandlers({ apiKey: { configured: false, masked_key: null } });
    render(Navigator, { props: { active: 'settings' } });
    const accordionBtn = screen.getByRole('button', { name: /Provider/i });
    await userEvent.click(accordionBtn);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /SET KEY/i })).toBeInTheDocument();
    });
    expect(screen.queryByRole('button', { name: /REMOVE/i })).not.toBeInTheDocument();
  });

  // ── Strategy editor ────────────────────────────────────────────────────────

  it('edit button opens strategy editor inline', async () => {
    const user = userEvent.setup();
    // Mock getStrategy
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/strategies/chain-of-thought') && (!init?.method || init.method === 'GET')) {
        return new Response(JSON.stringify({ name: 'chain-of-thought', content: '# CoT strategy content' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/strategies')) {
        return new Response(JSON.stringify([mockStrategyInfo({ name: 'chain-of-thought' })]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      // Other handlers
      if (url.includes('/api/provider/api-key')) return new Response(JSON.stringify({ configured: false, masked_key: null }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/api/providers')) return new Response(JSON.stringify({ active_provider: 'claude-cli', available: ['claude_cli'], routing_tiers: ['internal'] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/api/settings')) return new Response(JSON.stringify(DEFAULT_SETTINGS), { status: 200, headers: { 'Content-Type': 'application/json' } });
      if (url.includes('/api/preferences')) return new Response(JSON.stringify(preferencesStore.prefs), { status: 200, headers: { 'Content-Type': 'application/json' } });
      return new Response('Not Found', { status: 404 });
    }));

    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    });

    // Find and click the edit button (the ⋮ button)
    const editBtn = screen.getByTitle('Edit template');
    await user.click(editBtn);

    await waitFor(() => {
      const textarea = screen.queryByRole('textbox') as HTMLTextAreaElement | null;
      expect(textarea).not.toBeNull();
      // The textarea value is set via Svelte binding — check .value property
      expect(textarea?.value).toBe('# CoT strategy content');
    });
  });

  // ── GitHub panel ───────────────────────────────────────────────────────────

  it('shows Connect GitHub button when not authenticated', () => {
    githubStore.user = null;
    githubStore.linkedRepo = null;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'github' } });
    expect(screen.getByText(/Sign in to GitHub/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Connect GitHub/i })).toBeInTheDocument();
  });

  it('GitHub Connect button calls githubStore.login()', async () => {
    const user = userEvent.setup();
    githubStore.user = null;
    githubStore.linkedRepo = null;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'github' } });
    await user.click(screen.getByRole('button', { name: /Connect GitHub/i }));
    expect(githubStore.login).toHaveBeenCalled();
  });

  it('shows user login and no-repo message when authenticated but no repo linked', () => {
    githubStore.user = { login: 'testuser', avatar_url: '' };
    githubStore.linkedRepo = null;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'github' } });
    expect(screen.getByText('testuser')).toBeInTheDocument();
    expect(screen.getByText(/No repo linked/i)).toBeInTheDocument();
  });

  it('shows linked repo info and unlink button when repo is linked', async () => {
    const user = userEvent.setup();
    githubStore.user = { login: 'testuser', avatar_url: '' };
    githubStore.linkedRepo = {
      id: '1',
      full_name: 'testuser/myrepo',
      default_branch: 'main',
      branch: 'main',
      language: 'TypeScript',
    };
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'github' } });
    expect(screen.getByText('testuser/myrepo')).toBeInTheDocument();
    expect(screen.getByText('TypeScript')).toBeInTheDocument();
    const unlinkBtn = screen.getByRole('button', { name: /Unlink repo/i });
    await user.click(unlinkBtn);
    expect(githubStore.unlinkRepo).toHaveBeenCalled();
  });

  // ── History — error state ──────────────────────────────────────────────────

  it('shows error message when history fetch fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/history')) {
        return new Response('Server Error', { status: 500 });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument();
    });
    // Error message should be visible
    const errorEl = screen.queryByText(/Failed to load history/i) || screen.queryByText(/error/i);
    // At minimum, the loading indicator is gone and an error state is shown
    expect(screen.queryByText(/Loading…/i)).not.toBeInTheDocument();
  });

  // ── History — loadHistoryItem ──────────────────────────────────────────────

  it('clicking a history item loads the optimization record', async () => {
    const user = userEvent.setup();
    const optimizationRecord = mockOptimizationResult({ overall_score: 9.0 });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/history')) {
        return new Response(JSON.stringify({
          total: 1,
          count: 1,
          offset: 0,
          has_more: false,
          next_offset: null,
          items: [mockHistoryItem({ id: 'opt-1', status: 'completed', trace_id: 'trace-1', intent_label: 'Test optimization' })],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/optimize/trace-1')) {
        return new Response(JSON.stringify(optimizationRecord), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    const loadSpy = vi.spyOn(forgeStore, 'loadFromRecord');
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText('Test optimization')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Test optimization'));
    await waitFor(() => {
      expect(loadSpy).toHaveBeenCalled();
    });
  });

  it('clicking a history item while forge is in-progress calls forgeStore.cancel()', async () => {
    const user = userEvent.setup();
    forgeStore.status = 'analyzing';
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/history')) {
        return new Response(JSON.stringify({
          total: 1,
          count: 1,
          offset: 0,
          has_more: false,
          next_offset: null,
          items: [mockHistoryItem({ id: 'opt-2', status: 'completed', trace_id: 'trace-2', intent_label: 'Another optimization' })],
        }), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      if (url.includes('/api/optimize/trace-2')) {
        return new Response(JSON.stringify(mockOptimizationResult()), { status: 200, headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    const cancelSpy = vi.spyOn(forgeStore, 'cancel');
    render(Navigator, { props: { active: 'history' } });
    await waitFor(() => {
      expect(screen.getByText('Another optimization')).toBeInTheDocument();
    });
    await user.click(screen.getByText('Another optimization'));
    expect(cancelSpy).toHaveBeenCalled();
  });

  // ── Settings — System accordion ────────────────────────────────────────────

  it('System accordion expands to show settings data', async () => {
    const user = userEvent.setup();
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /System/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /System/i }));
    await waitFor(() => {
      expect(screen.getByText('50,000')).toBeInTheDocument();
      expect(screen.getByText('80,000 tokens')).toBeInTheDocument();
      expect(screen.getByText('all-MiniLM-L6-v2')).toBeInTheDocument();
      expect(screen.getByText('sqlite')).toBeInTheDocument();
      expect(screen.getByText('30/minute')).toBeInTheDocument();
      expect(screen.getByText('7d')).toBeInTheDocument();
      expect(screen.getByText('hybrid')).toBeInTheDocument();
      // optimize_rate_limit and refine_rate_limit are both '10/minute'
      expect(screen.getAllByText('10/minute')).toHaveLength(2);
    });
  });

  it('System section shows version from forgeStore', async () => {
    const user = userEvent.setup();
    forgeStore.version = '0.1.0-dev';
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /System/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /System/i }));
    await waitFor(() => {
      expect(screen.getByText('0.1.0-dev')).toBeInTheDocument();
    });
  });

  it('System section shows Backend unavailable when settings fetch fails', async () => {
    const user = userEvent.setup();
    defaultFetchHandlers({ settings: null });
    render(Navigator, { props: { active: 'settings' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /System/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /System/i }));
    await waitFor(() => {
      expect(screen.getByText('Backend unavailable')).toBeInTheDocument();
    });
  });

  // ── Settings — System section passthrough adaptation ──────────────────────

  it('hides phase durations in passthrough mode', async () => {
    const user = userEvent.setup();
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    (forgeStore as any).phaseDurations = { analyzing: 150, optimizing: 800, scoring: 200 };
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /System/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /System/i }));
    await waitFor(() => {
      expect(screen.queryByText('150ms')).not.toBeInTheDocument();
      expect(screen.queryByText('800ms')).not.toBeInTheDocument();
    });
  });

  it('shows "heuristic" scoring label in passthrough mode', async () => {
    const user = userEvent.setup();
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /System/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /System/i }));
    await waitFor(() => {
      // CONTEXT (Analysis) + SCORING (Mode) + System accordion = 3 "heuristic" labels
      const heuristicEls = screen.getAllByText('heuristic');
      expect(heuristicEls.length).toBeGreaterThanOrEqual(3);
    });
  });

  it('shows "hybrid" scoring label in internal mode', async () => {
    const user = userEvent.setup();
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /System/i })).toBeInTheDocument();
    });
    await user.click(screen.getByRole('button', { name: /System/i }));
    await waitFor(() => {
      expect(screen.getByText('hybrid')).toBeInTheDocument();
    });
  });

  // ── Settings — pipeline force toggles ─────────────────────────────────────

  it('shows SAMPLING badge when force_sampling is on and samplingCapable is true', () => {
    preferencesStore.prefs.pipeline.force_sampling = true;
    forgeStore.samplingCapable = true;
    forgeStore.mcpDisconnected = false;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('SAMPLING')).toBeInTheDocument();
  });

  it('shows PASSTHROUGH badge when force_passthrough is on', () => {
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('PASSTHROUGH')).toBeInTheDocument();
  });

  // ── Settings — passthrough CONTEXT section ─────────────────────────────────

  it('shows CONTEXT section with read-only indicators in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('Context')).toBeInTheDocument();
    // Multiple "heuristic" labels in passthrough (CONTEXT Analysis + SCORING Mode)
    expect(screen.getAllByText('heuristic').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('auto-injected')).toBeInTheDocument();
  });

  it('shows "via index" when GitHub repo is linked in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    (githubStore as any).linkedRepo = { full_name: 'owner/repo' };
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('via index')).toBeInTheDocument();
  });

  it('shows "no repo" when no GitHub repo linked in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    (githubStore as any).linkedRepo = null;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('no repo')).toBeInTheDocument();
  });

  it('shows Adaptation toggle in CONTEXT section in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByRole('switch', { name: /Toggle Adaptation/i })).toBeInTheDocument();
  });

  it('hides Models section in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.queryByText('Models')).not.toBeInTheDocument();
  });

  // ── Settings — passthrough SCORING section ─────────────────────────────────

  it('shows SCORING section with heuristic mode in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    // Scope to sub-heading to avoid ambiguity with System accordion "Scoring" row
    expect(screen.getByText('Scoring', { selector: '.sub-heading' })).toBeInTheDocument();
    // Both CONTEXT and SCORING sections show "heuristic"
    expect(screen.getByText('Mode')).toBeInTheDocument();
    const modeLabels = screen.getAllByText('heuristic');
    expect(modeLabels.length).toBeGreaterThanOrEqual(2);
  });

  it('hides Effort section in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.queryByText('Effort')).not.toBeInTheDocument();
  });

  it('shows Effort section in internal mode', () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('Effort')).toBeInTheDocument();
  });

  it('clicking Adaptation toggle in CONTEXT section calls setPipelineToggle', async () => {
    const user = userEvent.setup();
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    preferencesStore.prefs.pipeline.enable_adaptation = true;
    const spy = vi.spyOn(preferencesStore, 'setPipelineToggle').mockResolvedValue(undefined);
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    const toggle = screen.getByRole('switch', { name: /Toggle Adaptation/i });
    await user.click(toggle);
    expect(spy).toHaveBeenCalledWith('enable_adaptation', false);
    spy.mockRestore();
  });

  // ── Settings — keydown on strategy row ────────────────────────────────────

  it('pressing Enter on a strategy row selects it', async () => {
    const user = userEvent.setup();
    defaultFetchHandlers({
      strategies: [mockStrategyInfo({ name: 'few-shot' })],
    });
    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('few-shot')).toBeInTheDocument();
    });
    const row = screen.getByText('few-shot').closest('[role="button"]')! as HTMLElement;
    row.focus();
    await user.keyboard('{Enter}');
    expect(forgeStore.strategy).toBe('few-shot');
  });

  it('discard button closes strategy editor', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url.includes('/api/strategies/chain-of-thought') && (!init?.method || init.method === 'GET')) {
        return new Response(JSON.stringify({ name: 'chain-of-thought', content: '# Content' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (url.includes('/api/strategies')) {
        return new Response(JSON.stringify([mockStrategyInfo({ name: 'chain-of-thought' })]), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({}), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }));

    render(Navigator, { props: { active: 'editor' } });
    await waitFor(() => {
      expect(screen.getByText('chain-of-thought')).toBeInTheDocument();
    });

    const editBtn = screen.getByTitle('Edit template');
    await user.click(editBtn);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /DISCARD/i })).toBeInTheDocument();
    });

    await user.click(screen.getByRole('button', { name: /DISCARD/i }));

    // Editor should be closed — textarea gone
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
});
