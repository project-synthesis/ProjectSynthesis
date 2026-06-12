// frontend/src/lib/components/suites/SuiteDetailView.provenance.test.ts
//
// v0.4.37 Cycle 2 RED — per-prompt expansion, five-state DIFF machine
// (spec §4.1), tombstone, OPEN IN HISTORY, RUN link.
//
// The five states, evaluated per prompt row in order:
//   1. no replay for the suite          → DIFF affordance HIDDEN
//   2. latest optimized + baseline out  → output-vs-output diff
//   3. latest optimized, no baseline    → raw-vs-output diff
//   4. key present, value null          → disabled, rate-limited tooltip
//   5. key absent (pre-v0.4.37 replay)  → disabled, not-captured tooltip
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import SuiteDetailView from './SuiteDetailView.svelte';

const suitesStoreMock = vi.hoisted(() => ({
  suitesStore: {
    aliveOriginalIds: null as Set<string> | null,
    originalTraceIds: {} as Record<string, string>,
    loadDetail: vi.fn().mockResolvedValue(undefined),
  },
}));
vi.mock('$lib/stores/suites.svelte', () => suitesStoreMock);

const editorMock = vi.hoisted(() => ({
  editorStore: {
    openInlineDiff: vi.fn(),
    openResult: vi.fn(),
  },
}));
vi.mock('$lib/stores/editor.svelte', () => editorMock);

const forgeMock = vi.hoisted(() => ({
  forgeStore: { loadFromRecord: vi.fn() },
}));
vi.mock('$lib/stores/forge.svelte', () => forgeMock);

// apiFetch is included because the REAL `$lib/api/suites` module (whose
// replaySuite/retireSuite the component imports) does
// `import { apiFetch } from './client'` — the wholesale module mock must
// keep that named export resolvable.
const clientMock = vi.hoisted(() => ({
  getOptimization: vi.fn(),
  apiFetch: vi.fn(),
}));
vi.mock('$lib/api/client', () => clientMock);

const runsPanelMock = vi.hoisted(() => ({
  runsPanelStore: { requestSelect: vi.fn() },
}));
vi.mock('$lib/stores/runs-panel.svelte', () => runsPanelMock);

function makeSuite(overrides: Record<string, unknown> = {}) {
  return {
    id: 'suite-1',
    source_run_id: 'run-1',
    label: 'prov-suite',
    tolerance_abs: 0.5,
    project_id: null,
    repo_full_name: null,
    created_at: '2026-06-12T00:00:00Z',
    retired_at: null,
    retired_reason: null,
    prompts_snapshot: [{
      raw_prompt: 'the raw prompt text',
      intent_label: 'general',
      original_optimization_id: 'opt-1',
      baseline_optimized_prompt: 'the baseline optimized output',
    }],
    baseline_scores: {
      mean_overall: 7.5, p5_overall: 7.0, p50_overall: 7.5, p95_overall: 8.0,
      per_prompt: [{ raw_prompt_idx: 0, overall: 7.5, dimensions: { clarity: 7.5 } }],
      task_type_distribution: { coding: 1 },
    },
    ...overrides,
  };
}

function makeLatestReplay(promptRow: Record<string, unknown>) {
  return {
    // `as const` pins the union-typed literals so the fixture satisfies
    // the component's `SuiteReplayRow | RunResult` prop without widening
    // to `string` (type-only — no runtime/assertion change).
    id: 'rr-1', mode: 'replay_run' as const, status: 'completed' as const,
    started_at: '2026-06-12T01:00:00Z', completed_at: '2026-06-12T01:05:00Z',
    project_id: null, repo_full_name: null, topic: null, intent_hint: null,
    prompts_generated: 1,
    prompt_results: [{ raw_prompt_idx: 0, overall_score: 7.0, ...promptRow }],
    aggregate: {},
  };
}

describe('SuiteDetailView provenance (v0.4.37)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    suitesStoreMock.suitesStore.aliveOriginalIds = null;
    suitesStoreMock.suitesStore.originalTraceIds = {};
  });
  afterEach(() => cleanup());

  it('state 1: no replay → DIFF affordance hidden entirely', () => {
    const { container } = render(SuiteDetailView, {
      props: { suite: makeSuite(), replays: null, latestReplay: null },
    });
    expect(container.querySelector('[data-test="prompt-diff-btn"]')).toBeNull();
  });

  it('state 2: baseline output + latest output → output-vs-output diff', async () => {
    const user = userEvent.setup();
    const { container } = render(SuiteDetailView, {
      props: {
        suite: makeSuite(),
        replays: null,
        latestReplay: makeLatestReplay({ optimized_prompt: 'the latest optimized output' }),
      },
    });
    const btn = container.querySelector('[data-test="prompt-diff-btn"]') as HTMLButtonElement;
    expect(btn.dataset.diffState).toBe('output-vs-output');
    await user.click(btn);
    expect(editorMock.editorStore.openInlineDiff).toHaveBeenCalledWith({
      key: 'suite-1:0',
      title: 'prov-suite #0',
      before: 'the baseline optimized output',
      after: 'the latest optimized output',
      beforeLabel: 'BASELINE',
      afterLabel: 'LATEST',
    });
  });

  it('state 3: no baseline output → raw-vs-output diff', async () => {
    const user = userEvent.setup();
    const suite = makeSuite();
    (suite.prompts_snapshot as Array<Record<string, unknown>>)[0].baseline_optimized_prompt = null;
    const { container } = render(SuiteDetailView, {
      props: {
        suite, replays: null,
        latestReplay: makeLatestReplay({ optimized_prompt: 'the latest optimized output' }),
      },
    });
    const btn = container.querySelector('[data-test="prompt-diff-btn"]') as HTMLButtonElement;
    expect(btn.dataset.diffState).toBe('raw-vs-output');
    await user.click(btn);
    const call = editorMock.editorStore.openInlineDiff.mock.calls[0][0];
    expect(call.before).toBe('the raw prompt text');
    expect(call.beforeLabel).toBe('RAW');
  });

  it('state 4: key present with null → disabled + exact rate-limited tooltip', () => {
    const { container } = render(SuiteDetailView, {
      props: {
        suite: makeSuite(), replays: null,
        latestReplay: makeLatestReplay({ optimized_prompt: null }),
      },
    });
    const btn = container.querySelector('[data-test="prompt-diff-btn"]') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.dataset.diffTooltip).toBe('no output for this prompt (rate-limited or failed)');
  });

  it('state 5: key absent (pre-v0.4.37 replay) → disabled + exact not-captured tooltip', () => {
    const { container } = render(SuiteDetailView, {
      props: {
        suite: makeSuite(), replays: null,
        latestReplay: makeLatestReplay({}),  // no optimized_prompt key at all
      },
    });
    const btn = container.querySelector('[data-test="prompt-diff-btn"]') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.dataset.diffTooltip).toBe('output not captured (pre-v0.4.37 replay)');
  });

  it('expanding a row shows the full raw_prompt + dimension scores', async () => {
    const user = userEvent.setup();
    const { container } = render(SuiteDetailView, {
      props: {
        suite: makeSuite(), replays: null,
        latestReplay: makeLatestReplay({
          optimized_prompt: 'x', dimensions: { clarity: 8.2, specificity: 7.1 },
        }),
      },
    });
    await user.click(container.querySelector('[data-test="prompt-expand-btn"]') as HTMLElement);
    const body = container.querySelector('[data-test="prompt-expanded"]') as HTMLElement;
    expect(body.textContent).toContain('the raw prompt text');
    expect(body.textContent).toContain('clarity 8.2');
    expect(body.textContent).toContain('specificity 7.1');
  });

  it('tombstone renders the exact copy for dead originals', async () => {
    const user = userEvent.setup();
    suitesStoreMock.suitesStore.aliveOriginalIds = new Set<string>(); // opt-1 dead
    const { container } = render(SuiteDetailView, {
      props: { suite: makeSuite(), replays: null, latestReplay: null },
    });
    await user.click(container.querySelector('[data-test="prompt-expand-btn"]') as HTMLElement);
    const tomb = container.querySelector('[data-test="prompt-tombstone"]') as HTMLElement;
    expect(tomb.textContent).toBe(
      'original optimization deleted — content preserved in this snapshot',
    );
    expect(container.querySelector('[data-test="open-in-history"]')).toBeNull();
  });

  it('OPEN IN HISTORY renders only for live originals and loads via trace_id', async () => {
    const user = userEvent.setup();
    suitesStoreMock.suitesStore.aliveOriginalIds = new Set(['opt-1']);
    suitesStoreMock.suitesStore.originalTraceIds = { 'opt-1': 'tr-99' };
    clientMock.getOptimization.mockResolvedValue({ id: 'opt-1', raw_prompt: 'r' });
    const { container } = render(SuiteDetailView, {
      props: { suite: makeSuite(), replays: null, latestReplay: null },
    });
    await user.click(container.querySelector('[data-test="prompt-expand-btn"]') as HTMLElement);
    expect(container.querySelector('[data-test="prompt-tombstone"]')).toBeNull();
    await user.click(container.querySelector('[data-test="open-in-history"]') as HTMLElement);
    expect(clientMock.getOptimization).toHaveBeenCalledWith('tr-99');
    expect(forgeMock.forgeStore.loadFromRecord).toHaveBeenCalled();
    expect(editorMock.editorStore.openResult).toHaveBeenCalledWith('opt-1');
  });

  it('switching the suite prop resets the per-prompt expansion', async () => {
    const user = userEvent.setup();
    const { container, rerender } = render(SuiteDetailView, {
      props: { suite: makeSuite(), replays: null, latestReplay: null },
    });
    await user.click(container.querySelector('[data-test="prompt-expand-btn"]') as HTMLElement);
    expect(container.querySelector('[data-test="prompt-expanded"]')).not.toBeNull();

    // Same snapshot shape, different suite id — the suite.id-keyed $effect
    // collapses the open row so suite B never renders suite A's expansion
    // state (row indexes are per-suite).
    await rerender({
      suite: makeSuite({ id: 'suite-2', label: 'other-suite' }),
      replays: null,
      latestReplay: null,
    });
    expect(container.querySelector('[data-test="prompt-expanded"]')).toBeNull();
  });

  it('RUN link requests run selection in the Runs panel', async () => {
    const user = userEvent.setup();
    const { container } = render(SuiteDetailView, {
      props: { suite: makeSuite(), replays: null, latestReplay: null },
    });
    await user.click(container.querySelector('[data-test="suite-run-link"]') as HTMLElement);
    expect(runsPanelMock.runsPanelStore.requestSelect).toHaveBeenCalledWith('run-1');
  });
});
