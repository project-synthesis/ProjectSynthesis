// frontend/src/lib/components/suites/SuitesPanel.test.ts
//
// Cycle 12 RED — SuitesPanel + SuiteRow + SuiteDetailView contracts.
//
// SuitesPanel is the left-nav landing for the validation-suite surface. It
// renders the SUITES navigator entry's body when active, lists every
// non-retired ValidationSuite (project-scoped per ADR-005 when
// `projectStore.currentProjectId` is set), and routes row clicks into
// SuiteDetailView.
//
// Density pins (spec § 6):
//   - h-5 (20px) data rows with `px-1` padding
//   - 6px status dot leads the row (chromatic encoding: green nominal /
//     red firing)
//   - Recipe A hover (border + bg-tint, NO translateY — h-5 is too compact
//     for the lift reserved for Hero buttons per component-patterns.md
//     Recipe E)
//   - Delta column displays signed score deltas vs baseline
//
// All 8 tests load components via runtime-computed dynamic imports so the
// test runner can collect + report on each `it()` even though the
// components don't exist in RED state. Static imports would crash suite
// collection.
//
// Spec anchors: §6 NEW components table, §6 voice (`No suites. Save a
// probe to create one.`), §6 density pins (h-5 + 20px), §6 contour
// tier table (Suite row → Recipe A NOT Large tier).

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// ─── Mocks: suites API + project store + suites store ──────────────────
//
// The panel reads suites from `suitesStore` (NEW) which fetches via
// `$lib/api/suites`. Per-test we either populate the store with fixtures
// directly or stub the API so a load() call resolves with the canned
// response.

const apiMock = vi.hoisted(() => ({
  getSuites: vi.fn(),
  getSuite: vi.fn(),
  getSuiteReplays: vi.fn(),
}));
vi.mock('$lib/api/suites', () => apiMock);

const projectMock = vi.hoisted(() => ({
  projectStore: {
    currentProjectId: null as string | null,
  },
}));
vi.mock('$lib/stores/project.svelte', () => projectMock);

// suitesStore mock — fixture-driven for component tests. The store itself
// is exercised in stores/suites.test.ts (tests 13–14).
const suitesStoreMock = vi.hoisted(() => ({
  suitesStore: {
    suites: [] as Array<Record<string, unknown>>,
    selectedSuiteId: null as string | null,
    detail: null as Record<string, unknown> | null,
    loading: false,
    error: null as string | null,
    regressionAlarmBlock: null as Record<string, unknown> | null,
    load: vi.fn().mockResolvedValue(undefined),
    select: vi.fn(),
    loadDetail: vi.fn().mockResolvedValue(undefined),
  },
}));
vi.mock('$lib/stores/suites.svelte', () => suitesStoreMock);

/**
 * Runtime-computed import path keeps Vite's static analyzer from resolving
 * the (not-yet-existing) component file at suite-collection time. Each
 * test that needs the component awaits this loader so the failure surfaces
 * as a single `it()` skip rather than a whole-suite collection crash.
 */
async function loadSuitesPanel() {
  const path = ['.', 'SuitesPanel.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

async function loadSuiteRow() {
  const path = ['.', 'SuiteRow.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

async function loadSuiteDetailView() {
  const path = ['.', 'SuiteDetailView.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

// ─── Test fixtures ─────────────────────────────────────────────────────

function makeSuiteListItem(overrides: Record<string, unknown> = {}) {
  return {
    id: 'suite-1',
    source_run_id: 'run-1',
    label: 'embedding-cache-invalidation',
    tolerance_abs: 0.5,
    project_id: 'proj-1',
    repo_full_name: 'octocat/demo',
    created_at: '2026-05-11T00:00:00Z',
    retired_at: null,
    prompts_count: 5,
    baseline_mean: 7.85,
    ...overrides,
  };
}

function makeSuiteDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 'suite-1',
    source_run_id: 'run-1',
    label: 'embedding-cache-invalidation',
    tolerance_abs: 0.5,
    project_id: 'proj-1',
    repo_full_name: 'octocat/demo',
    created_at: '2026-05-11T00:00:00Z',
    retired_at: null,
    retired_reason: null,
    prompts_snapshot: [
      { raw_prompt: 'prompt 1', intent_label: null, original_optimization_id: null },
      { raw_prompt: 'prompt 2', intent_label: null, original_optimization_id: null },
    ],
    baseline_scores: {
      mean_overall: 7.85,
      p5_overall: 6.5,
      p50_overall: 7.9,
      p95_overall: 9.0,
      per_prompt: [
        { raw_prompt_idx: 0, overall: 8.0, dimensions: {} },
        { raw_prompt_idx: 1, overall: 7.7, dimensions: {} },
      ],
      task_type_distribution: { coding: 2 },
    },
    ...overrides,
  };
}

// ─── SuitesPanel — 3 tests ─────────────────────────────────────────────

describe('SuitesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    suitesStoreMock.suitesStore.suites = [];
    suitesStoreMock.suitesStore.selectedSuiteId = null;
    suitesStoreMock.suitesStore.detail = null;
    suitesStoreMock.suitesStore.loading = false;
    suitesStoreMock.suitesStore.error = null;
    suitesStoreMock.suitesStore.regressionAlarmBlock = null;
    projectMock.projectStore.currentProjectId = null;
    apiMock.getSuites.mockResolvedValue({
      total: 0, count: 0, offset: 0, items: [], has_more: false, next_offset: null,
    });
  });
  afterEach(() => {
    cleanup();
  });

  // ── Test 1: empty state ──────────────────────────────────────────────
  //
  // When `suitesStore.suites` is empty the panel renders the canonical
  // empty-state copy verbatim per spec § 6 voice table:
  //   "No suites. Save a probe to create one."
  // Title-case prose, no all-caps shouting — matches the SeedModal voice
  // anchor for empty/explanatory surfaces (vs UPPERCASE action buttons).
  it('test_suites_panel_renders_with_no_suites_empty_state', async () => {
    const SuitesPanel = await loadSuitesPanel();
    render(SuitesPanel, { props: { active: true } });

    // The canonical empty-state copy — exact-text match on the noun
    // phrase. Allow whitespace flex via regex; the load-bearing words
    // are "No suites" + "Save a probe".
    expect(
      screen.getByText(/No suites\.\s*Save a probe to create one\./i),
    ).toBeInTheDocument();
  });

  // ── Test 2: ADR-005 project filter ──────────────────────────────────
  //
  // When `projectStore.currentProjectId` is non-null the panel must
  // request the suites list filtered to that project. Per ADR-005 multi-
  // project isolation the suites surface honours the same scope as
  // history/topology/templates — a project-scoped view never bleeds
  // suites from sibling projects.
  it('test_suites_panel_filters_by_project_id_when_set', async () => {
    projectMock.projectStore.currentProjectId = 'proj-abc';

    const SuitesPanel = await loadSuitesPanel();
    render(SuitesPanel, { props: { active: true } });

    // The panel calls `suitesStore.load(projectId?)` on mount; that load
    // call must forward the current project scope. Either the store's
    // `load()` is called with the project_id positional / kwarg, OR the
    // direct API call is parameterised — both are valid GREEN landings.
    await vi.waitFor(() => {
      const loadCalls = suitesStoreMock.suitesStore.load.mock.calls;
      const apiCalls = apiMock.getSuites.mock.calls;

      // The call must reach one of: store.load(...) OR api.getSuites(...).
      expect(loadCalls.length + apiCalls.length).toBeGreaterThanOrEqual(1);
    });

    // Extract the project_id forwarded — accept either positional kwarg
    // or first-positional-arg form to keep room for the implementer to
    // choose the natural signature.
    const allCallArgs = [
      ...suitesStoreMock.suitesStore.load.mock.calls,
      ...apiMock.getSuites.mock.calls,
    ].flat();
    const haystack = JSON.stringify(allCallArgs);
    expect(haystack).toContain('proj-abc');
  });

  // ── Test 3: SUITES nav entry routes correctly ───────────────────────
  //
  // The SUITES entry is mounted in the left-nav (Navigator extension per
  // § 6 extended components table). Clicking it must surface the suites
  // body — verified here by mounting the panel with `active=true` and
  // confirming the panel root renders with the matching aria-label or
  // role; clicking the entry while `active=false` must NOT render the
  // body (the active prop is the routing surface).
  it('test_suites_panel_navigator_entry_routes_correctly', async () => {
    const SuitesPanel = await loadSuitesPanel();

    // Seed two suites so the panel has non-empty content to verify.
    suitesStoreMock.suitesStore.suites = [
      makeSuiteListItem(),
      makeSuiteListItem({ id: 'suite-2', label: 'second-suite' }),
    ];

    const { container, rerender } = render(SuitesPanel, {
      props: { active: true },
    });

    // The panel renders an identifiable container — accept any of:
    //   - data-test attribute
    //   - aria-label
    //   - role region with the matching name
    const root =
      container.querySelector('[data-test="suites-panel"]') ??
      container.querySelector('[aria-label="Suites"]') ??
      container.querySelector('[role="region"][aria-label*="suites" i]');
    expect(root).not.toBeNull();

    // When the nav entry deactivates (active=false) the body must not
    // render — same lazy-mount contract as the other Navigator panels
    // (StrategiesPanel, HistoryPanel, GitHubPanel, SettingsPanel).
    cleanup();
    const { container: containerInactive } = render(SuitesPanel, {
      props: { active: false },
    });
    const inactiveRoot =
      containerInactive.querySelector('[data-test="suites-panel"]') ??
      containerInactive.querySelector('[aria-label="Suites"]') ??
      containerInactive.querySelector('[role="region"][aria-label*="suites" i]');
    expect(inactiveRoot).toBeNull();

    // Silence rerender warning — the prop-driven contract is asserted.
    void rerender;
  });
});

// ─── SuiteRow — 3 tests ─────────────────────────────────────────────────

describe('SuiteRow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
  });

  // ── Test 4: h-5 + 6px status dot + delta ────────────────────────────
  //
  // Density pin (spec § 6 density-pins table): suite rows are h-5 (20px)
  // with the row leading-element being a 6px chromatic status dot. Delta
  // text renders alongside the label — signed score delta vs baseline.
  it('test_suite_row_h_5_data_row_with_status_dot_and_delta', async () => {
    const SuiteRow = await loadSuiteRow();
    const suite = makeSuiteListItem({
      // Latest replay regressed −0.30 vs baseline.
      baseline_mean: 7.85,
    });

    const { container } = render(SuiteRow, {
      props: {
        suite,
        // Delta is passed in as a prop / derived from a replay context;
        // tests pin the signed-value rendering not the source.
        delta: -0.3,
        // Status feeds the dot color: 'nominal' (green) / 'firing' (red)
        // / 'none' (no replay yet — dim). 'firing' chosen here to verify
        // the chromatic encoding path.
        status: 'firing' as const,
        onClick: vi.fn(),
      },
    });

    // (a) row height — accept either Tailwind class `h-5` OR computed
    // inline height of 20px.
    const row =
      container.querySelector('[data-test="suite-row"]') ??
      container.querySelector('.suite-row');
    expect(row).not.toBeNull();
    const classList = (row as HTMLElement).className;
    expect(classList).toMatch(/\bh-5\b/);

    // (b) status dot — 6px square (per spec § 6 voice table reference to
    // chromatic 6px dots). Accept either a `.status-dot` class or a
    // data-test marker; verify the chromatic encoding via the firing
    // (red) state.
    const dot =
      container.querySelector('[data-test="suite-row-dot"]') ??
      container.querySelector('.status-dot') ??
      container.querySelector('[role="img"][aria-label*="firing" i]');
    expect(dot).not.toBeNull();

    // (c) delta text — signed; `-0.30` or `−0.30` or `-0.3` all valid.
    // The Unicode minus (`−`) is the canonical chromatic minus per
    // SKILL.md numeric voice — accept either ASCII `-` or U+2212.
    const txt = container.textContent ?? '';
    expect(txt).toMatch(/[−-]0\.30?/);
  });

  // ── Test 5: Recipe A hover, NO translateY ───────────────────────────
  //
  // Recipe A (component-patterns.md line 128–141): hover transitions
  // border-subtle → border-accent + bg-transparent → bg-bg-hover/40,
  // single 200ms transition. h-5 rows are too compact for the Recipe E
  // translateY lift reserved for Hero buttons.
  //
  // We assert this contract by inspecting the component's static CSS
  // (raw import) for the Recipe A markers AND the absence of
  // `translateY` on the row's :hover declaration.
  it('test_suite_row_recipe_a_hover_border_plus_bg_tint_no_translate_y', async () => {
    // `?raw` import returns the .svelte source as a string. The runtime-
    // computed path keeps Vite's static analyzer from resolving the
    // file at suite-collection time (the file doesn't exist in RED).
    const path = ['.', 'SuiteRow.svelte?raw'].join('/');
    const mod = await import(/* @vite-ignore */ path);
    const src: string = mod.default;

    // Recipe A — border tint + bg tint on :hover. Both must appear; the
    // exact form (Tailwind utility OR CSS rule) is implementation-private
    // but the brand-canon classes/var refs are stable.
    expect(src).toMatch(/border-accent|border-border-accent|--color-border-accent/);
    expect(src).toMatch(/bg-hover|bg-bg-hover|--color-bg-hover/);

    // Single uniform-duration transition (spec § 6 axiom 5) — accept any
    // 200ms binding tied to background/border-color/color.
    expect(src).toMatch(/200ms|0\.2s/);

    // Recipe E translate lift is FORBIDDEN on h-5 rows per spec § 6
    // contour-tier table ("no `translateY` — active-state lift is
    // reserved for Hero buttons per `component-patterns.md` Recipe E;
    // meaningless on 20px row"). Scope the prohibition to :hover and
    // :active CSS so we don't false-positive on unrelated transforms.
    const hoverBlocks = src.match(/:hover[\s\S]*?\}/g) ?? [];
    const activeBlocks = src.match(/:active[\s\S]*?\}/g) ?? [];
    const interactionCss = [...hoverBlocks, ...activeBlocks].join('\n');
    expect(interactionCss).not.toMatch(/translateY\s*\(/);
  });

  // ── Test 6: click navigates ─────────────────────────────────────────
  //
  // Row click fires the onClick callback with the suite payload so the
  // parent panel can call `suitesStore.select(id)` and surface the
  // SuiteDetailView.
  it('test_suite_row_click_opens_suite_detail_view', async () => {
    const SuiteRow = await loadSuiteRow();
    const suite = makeSuiteListItem({ id: 'suite-clk' });
    const onClick = vi.fn();

    const { container } = render(SuiteRow, {
      props: { suite, status: 'nominal' as const, delta: 0.1, onClick },
    });

    // The row must be either a <button> (preferred — keyboard focusable)
    // or have an explicit role="button". Both are valid GREEN landings.
    const row =
      container.querySelector('button[data-test="suite-row"]') ??
      container.querySelector('[role="button"][data-test="suite-row"]') ??
      container.querySelector('button.suite-row') ??
      container.querySelector('[role="button"].suite-row');
    expect(row).not.toBeNull();

    const user = userEvent.setup();
    await user.click(row as HTMLElement);

    expect(onClick).toHaveBeenCalledTimes(1);
    // The onClick payload exposes the suite id so the parent can route
    // — exact shape (id string OR suite object) is implementer's choice;
    // verify the id is reachable in the call args.
    const args = onClick.mock.calls[0];
    expect(JSON.stringify(args)).toContain('suite-clk');
  });
});

// ─── SuiteDetailView — 2 tests ──────────────────────────────────────────

describe('SuiteDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.getSuiteReplays.mockResolvedValue({
      total: 0, count: 0, offset: 0, items: [], has_more: false, next_offset: null,
    });
  });
  afterEach(() => {
    cleanup();
  });

  // ── Test 7: replay history table renders run summary rows ───────────
  //
  // SuiteDetailView shows a paginated table of replays against the suite
  // — backs `GET /api/suites/{id}/replays`. Each row is a RunSummary
  // (id, started_at, completed_at, status, prompts_generated). The
  // component must render at least one row per replay and use
  // semantically-correct table-ish markup (rows + columns).
  it('test_suite_detail_view_replay_history_table_renders_run_summary_rows', async () => {
    const detail = makeSuiteDetail();
    const replays = {
      total: 2,
      count: 2,
      offset: 0,
      has_more: false,
      next_offset: null,
      items: [
        {
          id: 'replay-1',
          mode: 'topic_probe', // RunSummary.mode is the wider Literal;
                               //   T2 extends to include 'replay_run'
                               //   but the existing topic_probe literal
                               //   is forward-compatible at the schema
                               //   level for fixture purposes.
          status: 'completed',
          started_at: '2026-05-12T00:00:00Z',
          completed_at: '2026-05-12T00:10:00Z',
          project_id: 'proj-1',
          repo_full_name: 'octocat/demo',
          topic: null,
          intent_hint: null,
          prompts_generated: 5,
        },
        {
          id: 'replay-2',
          mode: 'topic_probe',
          status: 'completed',
          started_at: '2026-05-13T00:00:00Z',
          completed_at: '2026-05-13T00:10:00Z',
          project_id: 'proj-1',
          repo_full_name: 'octocat/demo',
          topic: null,
          intent_hint: null,
          prompts_generated: 5,
        },
      ],
    };
    apiMock.getSuiteReplays.mockResolvedValue(replays);

    const SuiteDetailView = await loadSuiteDetailView();
    const { container } = render(SuiteDetailView, {
      props: { suite: detail, replays },
    });

    // Replay history rows — accept either <tr> rows inside a <table> OR
    // <li>/<div> rows with a data-test marker. The brand IDE-density
    // canon prefers data-grid markup over <table>, but either form
    // surfaces the same axe-core a11y semantics with role="row".
    const replayRows =
      container.querySelectorAll('[data-test="replay-row"]').length ||
      container.querySelectorAll('[role="row"]').length ||
      container.querySelectorAll('tr').length;
    expect(replayRows).toBeGreaterThanOrEqual(2);

    // The replay IDs must surface so the user can correlate a replay row
    // with a `/api/runs/{id}` deep-link.
    const txt = container.textContent ?? '';
    expect(txt).toMatch(/replay-1/);
    expect(txt).toMatch(/replay-2/);
  });

  // ── Test 8: baseline vs latest diff per prompt ──────────────────────
  //
  // Per spec § 10 cycle 12 INTEGRATE focus: SuiteDetailView consumes
  // `ValidationSuiteOut.baseline_scores.per_prompt[i].overall` and the
  // matching index from the latest replay row's `prompt_results[i]
  // .overall_score`, rendering a delta column.
  it('test_suite_detail_view_per_prompt_baseline_vs_latest_diff_renders', async () => {
    const detail = makeSuiteDetail();
    // Latest replay scored 7.5/7.2 vs baseline 8.0/7.7 → delta −0.5/−0.5.
    const latestReplay = {
      id: 'replay-latest',
      mode: 'topic_probe',
      status: 'completed',
      started_at: '2026-05-13T00:00:00Z',
      completed_at: '2026-05-13T00:10:00Z',
      project_id: 'proj-1',
      repo_full_name: 'octocat/demo',
      topic: null,
      intent_hint: null,
      prompts_generated: 2,
      prompt_results: [
        // Production shape: replay rows carry `raw_prompt_idx` only.
        { raw_prompt_idx: 0, overall_score: 7.5, raw_prompt: 'prompt 1' },
        // Legacy alias coverage: `prompt_index` must still join (tolerant
        // fallback pinned by the live-replay diff regression of 2026-06-12).
        { prompt_index: 1, overall_score: 7.2, raw_prompt: 'prompt 2' },
      ],
    };

    const SuiteDetailView = await loadSuiteDetailView();
    const { container } = render(SuiteDetailView, {
      props: { suite: detail, latestReplay },
    });

    // The per-prompt diff table renders one row per prompt — accept
    // table OR data-grid markup.
    const perPromptRows =
      container.querySelectorAll('[data-test="per-prompt-row"]').length ||
      container.querySelectorAll('[role="row"][data-prompt-idx]').length;
    expect(perPromptRows).toBeGreaterThanOrEqual(2);

    // Each row surfaces baseline + latest + delta. Accept either Unicode
    // minus (U+2212) or ASCII `-` for the delta value.
    const txt = container.textContent ?? '';

    // Baseline values must appear.
    expect(txt).toMatch(/8\.0/);
    expect(txt).toMatch(/7\.7/);

    // Latest values must appear.
    expect(txt).toMatch(/7\.5/);
    expect(txt).toMatch(/7\.2/);

    // Delta values (signed). The spec scoring convention shows two
    // decimals (`−0.64 vs baseline`); accept one-or-two-decimal forms
    // for the implementer.
    expect(txt).toMatch(/[−-]0\.50?/);
  });
});
