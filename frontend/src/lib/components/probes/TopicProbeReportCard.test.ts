// frontend/src/lib/components/probes/TopicProbeReportCard.test.ts
//
// Cycle 11 RED — Topic Probe final report card contract.
//
// The card renders the 5-section markdown report from `RunResult` and
// surfaces three primary actions:
//   - Save as Suite       → POST /api/probes/{run_id}/save-as-suite
//   - Replay (when saved) → POST /api/suites/{id}/replay
//   - Copy as markdown    → clipboard + green copy-flash (1500ms)
//
// Per `feedback_visual_feel_over_spec.md`, the copy-flash duration matches
// the shipped `--duration-copy-flash` value (1500ms), NOT the spec's
// original 600ms target.
//
// Dynamic import — the component doesn't exist yet in RED state; static
// imports would crash suite collection.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// Mock the suites API (NEW per-domain module landing in Cycle 11). The
// component must call these helpers rather than reaching for `fetch`.
const suitesMock = vi.hoisted(() => ({
  saveAsSuite: vi.fn(),
  replaySuite: vi.fn(),
}));
vi.mock('$lib/api/suites', () => suitesMock);

// Mock the toast store — the component surfaces an ambient toast after
// save and replay succeed.
const toastMock = vi.hoisted(() => ({
  toastStore: { success: vi.fn(), info: vi.fn(), error: vi.fn() },
}));
vi.mock('$lib/stores/toast.svelte', () => toastMock);

async function loadComponent() {
  // Runtime-computed path so Vite's static analyzer can't resolve it
  // at suite-collection time — keeps the file from crashing before
  // its tests run (RED behavior: individual test failures, not whole-
  // suite collection errors).
  const path = ['.', 'TopicProbeReportCard.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

/** Minimal mock RunResult matching `schemas/runs.py::RunResult`. */
function makeRunResult() {
  return {
    id: 'run-rpt1',
    mode: 'topic_probe' as const,
    status: 'completed' as const,
    started_at: '2026-05-12T00:00:00Z',
    completed_at: '2026-05-12T00:01:00Z',
    error: null,
    project_id: 'proj-123',
    repo_full_name: 'octocat/demo',
    topic: 'embedding cache invalidation',
    intent_hint: 'explore',
    prompts_generated: 5,
    prompt_results: [
      { prompt_index: 0, overall_score: 8.5, raw_prompt: 'first' },
      { prompt_index: 1, overall_score: 8.1, raw_prompt: 'second' },
      { prompt_index: 2, overall_score: 7.8, raw_prompt: 'third' },
      { prompt_index: 3, overall_score: 7.2, raw_prompt: 'fourth' },
      { prompt_index: 4, overall_score: 6.9, raw_prompt: 'fifth' },
    ],
    aggregate: {
      mean_overall: 7.7,
      score_distribution: { excellent: 1, good: 2, fair: 2, poor: 0 },
      top_prompts: [
        { prompt_index: 0, overall_score: 8.5 },
        { prompt_index: 1, overall_score: 8.1 },
        { prompt_index: 2, overall_score: 7.8 },
      ],
    },
    taxonomy_delta: {
      domains_created: [],
      sub_domains_created: ['backend: cache-invalidation'],
      clusters_touched: 3,
    },
    final_report:
      '# Topic Probe Completed\n\nTop 3 prompts...\n\n## Score Distribution\n\n## Taxonomy Delta\n\n## Recommended Follow-ups\n- follow-up 1\n- follow-up 2\n',
    suite_id: null,
    topic_probe_meta: { grounding_mode: 'codebase' },
    seed_agent_meta: null,
  };
}

describe('TopicProbeReportCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    suitesMock.saveAsSuite.mockResolvedValue({
      id: 'suite-uuid-1',
      label: 'topic-probe-result',
      source_run_id: 'run-rpt1',
      tolerance_abs: 0.5,
      prompts_count: 5,
      baseline_mean: 7.8,
      created_at: '2026-05-12T00:00:00Z',
      retired_at: null,
    });
    suitesMock.replaySuite.mockResolvedValue({
      run_id: 'replay-run-1',
      suite_id: 'suite-uuid-1',
      status: 'running',
      poll_url: '/api/runs/replay-run-1',
    });
    vi.useRealTimers();
  });
  afterEach(() => {
    cleanup();
  });

  // ── Test 9: renders top3, score dist, taxonomy delta, follow-ups ────
  it('test_renders_top3_prompts_score_distribution_taxonomy_delta_followups', async () => {
    const TopicProbeReportCard = await loadComponent();
    const result = makeRunResult();
    const { container } = render(TopicProbeReportCard, { props: { result } });

    // The report card surfaces 4 canonical sections per spec §6 voice:
    //   - Top 3 Prompts
    //   - Score Distribution
    //   - Taxonomy Delta
    //   - Recommended Follow-ups
    const sections = container.querySelectorAll('[data-test="report-section"]');
    expect(sections.length).toBeGreaterThanOrEqual(4);

    // Top-3 section lists exactly 3 prompts.
    const topPrompts = container.querySelectorAll(
      '[data-test="report-top-prompt"]',
    );
    expect(topPrompts.length).toBe(3);

    // Taxonomy delta surfaces the one created sub-domain.
    expect(
      screen.getByText(/backend:?\s*cache-invalidation/i),
    ).toBeInTheDocument();

    // Score distribution section renders.
    const haystack = container.textContent ?? '';
    expect(haystack.toLowerCase()).toMatch(/score\s+distribution|distribution/);

    // Follow-ups list has at least the two mock items.
    const followups = container.querySelectorAll(
      '[data-test="report-followup"]',
    );
    expect(followups.length).toBeGreaterThanOrEqual(2);
  });

  // ── Test 10: save-as-suite click triggers API + toast ──────────────
  it('test_save_as_suite_click_triggers_save_and_toast', async () => {
    const TopicProbeReportCard = await loadComponent();
    const result = makeRunResult();
    const user = userEvent.setup();
    render(TopicProbeReportCard, { props: { result } });

    const saveBtn = screen.getByRole('button', { name: /save\s+suite/i });
    expect(saveBtn).toBeInTheDocument();

    await user.click(saveBtn);

    // The component calls the suites API with the run id; ensure the
    // first positional arg is the run id (label may be defaulted).
    await vi.waitFor(() => {
      expect(suitesMock.saveAsSuite).toHaveBeenCalled();
    });
    const callArgs = suitesMock.saveAsSuite.mock.calls[0];
    expect(callArgs[0]).toBe('run-rpt1');

    // A success toast surfaces (ambient feedback).
    await vi.waitFor(() => {
      expect(toastMock.toastStore.success).toHaveBeenCalled();
    });
  });

  // ── Test 11: copy-md triggers copy + 1500ms green flash ──────────────
  it('test_copy_md_icon_triggers_copy_flash_green_1500ms', async () => {
    const TopicProbeReportCard = await loadComponent();
    const result = makeRunResult();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    const user = userEvent.setup();
    const { container } = render(TopicProbeReportCard, { props: { result } });

    // Find the copy-as-markdown icon button.
    const copyBtn = screen.getByRole('button', { name: /copy.*(markdown|md)/i });
    expect(copyBtn).toBeInTheDocument();

    await user.click(copyBtn);

    // 1. Clipboard write fired with the markdown body.
    expect(writeText).toHaveBeenCalledOnce();
    const written = writeText.mock.calls[0][0] as string;
    expect(written).toMatch(/Topic Probe Completed|Top 3 prompts|Taxonomy Delta/);

    // 2. The button (or a child indicator) gains the copy-flash class
    //    matching the canonical `--duration-copy-flash` token.
    await vi.waitFor(() => {
      const flashEl =
        container.querySelector('.copy-flash') ??
        container.querySelector('[data-state="copy-flash"]') ??
        container.querySelector('[data-test="copy-flash"]');
      expect(flashEl).not.toBeNull();
    });

    // 3. The shipped duration matches the brand token (1500ms) — NOT
    //    the spec's original 600ms (per feedback_visual_feel_over_spec).
    //    We check the brand token rather than waiting 1500ms in the test.
    const { COPY_FLASH_DURATION_MS } = await import(
      '$lib/utils/copy-feedback.svelte'
    );
    expect(COPY_FLASH_DURATION_MS).toBe(1500);
  });

  // ── Test 12: replay button triggers replay endpoint ─────────────────
  it('test_replay_button_triggers_replay_endpoint', async () => {
    const TopicProbeReportCard = await loadComponent();
    // Replay surfaces only when the report references a saved suite.
    const result = { ...makeRunResult(), suite_id: 'suite-uuid-1' };
    const user = userEvent.setup();
    render(TopicProbeReportCard, { props: { result } });

    const replayBtn = screen.getByRole('button', { name: /replay/i });
    expect(replayBtn).toBeInTheDocument();

    await user.click(replayBtn);

    await vi.waitFor(() => {
      expect(suitesMock.replaySuite).toHaveBeenCalledOnce();
    });
    expect(suitesMock.replaySuite.mock.calls[0][0]).toBe('suite-uuid-1');
  });
});
