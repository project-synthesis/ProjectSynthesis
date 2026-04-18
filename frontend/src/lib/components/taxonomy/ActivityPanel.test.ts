import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import type { TaxonomyActivityEvent } from '$lib/api/clusters';

// Stub requestAnimationFrame — ActivityPanel uses it for scroll-to-top,
// but scrollEl is null in jsdom (bind:this doesn't bind in tests).
// Run callbacks synchronously so effects settle within the test frame.
const _origRAF = globalThis.requestAnimationFrame;
globalThis.requestAnimationFrame = (cb: FrameRequestCallback) => { try { cb(0); } catch { /* scrollEl null in jsdom */ } return 0; };

// Mutable activityEvents array — vi.mock factory closes over this reference so each
// describe block can splice in its own fixture without re-mocking the module.
const activityEvents = vi.hoisted<TaxonomyActivityEvent[]>(() => [
  { ts: '2026-04-08T01:00:00Z', path: 'hot', op: 'assign', decision: 'merge_into', cluster_id: 'c1', optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:01:00Z', path: 'warm', op: 'split', decision: 'split_complete', cluster_id: 'c2', optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:02:00Z', path: 'warm', op: 'error', decision: 'failed', cluster_id: 'c3', optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:03:00Z', path: 'cold', op: 'refit', decision: 'accepted', cluster_id: null, optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:04:00Z', path: 'hot', op: 'score', decision: 'scored', cluster_id: null, optimization_id: 'o1', duration_ms: null, context: {} },
]);

vi.mock('$lib/stores/clusters.svelte', () => ({
  clustersStore: {
    activityEvents,
    loadActivity: vi.fn().mockResolvedValue(undefined),
    selectCluster: vi.fn(),
  },
}));

import ActivityPanel from './ActivityPanel.svelte';

// Default fixture (5 events) shared by the existing suite.
const DEFAULT_EVENTS: TaxonomyActivityEvent[] = [
  { ts: '2026-04-08T01:00:00Z', path: 'hot', op: 'assign', decision: 'merge_into', cluster_id: 'c1', optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:01:00Z', path: 'warm', op: 'split', decision: 'split_complete', cluster_id: 'c2', optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:02:00Z', path: 'warm', op: 'error', decision: 'failed', cluster_id: 'c3', optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:03:00Z', path: 'cold', op: 'refit', decision: 'accepted', cluster_id: null, optimization_id: null, duration_ms: null, context: {} },
  { ts: '2026-04-08T01:04:00Z', path: 'hot', op: 'score', decision: 'scored', cluster_id: null, optimization_id: 'o1', duration_ms: null, context: {} },
];

describe('ActivityPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Restore default fixture before each test in the base suite.
    activityEvents.splice(0, activityEvents.length, ...DEFAULT_EVENTS);
  });
  afterEach(() => { cleanup(); });

  it('renders the activity feed container', () => {
    const { container } = render(ActivityPanel);
    expect(container.querySelector('.ap-panel')).toBeTruthy();
  });

  it('displays activity events from the store', () => {
    render(ActivityPanel);
    // Multiple elements may match (filter chips + event rows) — use getAllByText
    expect(screen.getAllByText(/assign/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/split/).length).toBeGreaterThan(0);
  });

  it('shows event count', () => {
    render(ActivityPanel);
    expect(screen.getByText(/5/)).toBeInTheDocument();
  });

  it('applies color coding without errors', () => {
    const { container } = render(ActivityPanel);
    expect(container.querySelector('.ap-panel')).toBeTruthy();
  });

  it('loads activity history on mount', async () => {
    const { clustersStore } = await import('$lib/stores/clusters.svelte');
    render(ActivityPanel);
    expect(clustersStore.loadActivity).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// Regression guard: historical JSONL events carry context.state='template'
// from the pre-0.3.39 cluster lifecycle. ActivityPanel is a debug/history
// surface and MUST render those events as-is — no silent remapping to any
// new state value. If a future refactor introduces a remap (e.g. template →
// active), this test will catch it.
// ---------------------------------------------------------------------------
describe('legacy state=template events render verbatim', () => {
  const LEGACY_EVENTS: TaxonomyActivityEvent[] = [
    {
      // A historical warm-path retire decision recorded before the template
      // table migration. context.state and context.previous_state both carry
      // the literal string 'template' from the old PromptCluster.state enum.
      ts: '2026-03-01T12:00:00Z',
      path: 'warm',
      op: 'retire',
      decision: 'demoted',
      cluster_id: 'c_legacy',
      optimization_id: null,
      duration_ms: null,
      context: { state: 'template', reason: 'auto_demotion', previous_state: 'template' },
    },
    {
      // A second legacy event — assign / merge_into — with winner_state='template'
      // to guard against remapping of winner-side context fields too.
      ts: '2026-03-01T12:01:00Z',
      path: 'hot',
      op: 'assign',
      decision: 'merge_into',
      cluster_id: 'c_legacy2',
      optimization_id: null,
      duration_ms: null,
      context: { winner_state: 'template', winner_label: 'legacy-cluster' },
    },
  ];

  beforeEach(() => {
    vi.clearAllMocks();
    // Swap the shared activityEvents array to the legacy fixture for this suite.
    activityEvents.splice(0, activityEvents.length, ...LEGACY_EVENTS);
  });

  afterEach(() => {
    cleanup();
    // Restore default events so sibling suites are unaffected if run out of order.
    activityEvents.splice(0, activityEvents.length, ...DEFAULT_EVENTS);
  });

  it('shows context.state verbatim as "template" when row is expanded', async () => {
    const user = userEvent.setup();
    const { container } = render(ActivityPanel);

    // Click the first legacy row's summary button to expand its context card.
    const summaries = container.querySelectorAll<HTMLButtonElement>('.ap-row-summary');
    expect(summaries.length).toBeGreaterThan(0);
    await user.click(summaries[0]);

    // The expanded context must contain the literal string 'template' — rendered
    // exactly as stored in the historical JSONL event, with no silent remapping.
    const contextCard = container.querySelector('.ap-context');
    expect(contextCard).toBeTruthy();
    expect(contextCard!.textContent).toContain('template');

    // Both context.state and context.previous_state must appear verbatim.
    // We check the overall textContent rather than individual cells so the test
    // is robust to DOM structure changes while still catching value remapping.
    const text = contextCard!.textContent ?? '';
    expect(text).toContain('template'); // state value
    // Count occurrences: 'template' should appear at least twice (state + previous_state).
    const matches = text.match(/template/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(2);

    // Safety guard: assert that no accidental remap to 'active' has occurred.
    // A future refactor that silently upgrades template → active in the UI
    // would cause this assertion to catch it.
    expect(text).not.toContain('state=active');
    expect(text).not.toContain('"state":"active"');
    expect(text).not.toContain('state: active');
  });

  it('renders legacy decision string verbatim without remapping', async () => {
    const user = userEvent.setup();
    const { container } = render(ActivityPanel);

    const summaries = container.querySelectorAll<HTMLButtonElement>('.ap-row-summary');
    await user.click(summaries[0]);

    // The decision badge for the first event must show the literal 'demoted'
    // string from the historical log — not a translated/remapped label.
    const decisionBadges = container.querySelectorAll('.ap-badge-decision');
    const firstBadge = decisionBadges[0]?.textContent ?? '';
    expect(firstBadge).toBe('demoted');
  });
});
