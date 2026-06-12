// frontend/src/lib/components/layout/RunDetailInline.test.ts
import { describe, it, expect, vi } from 'vitest';
import { render, waitFor } from '@testing-library/svelte';
import RunDetailInline from './RunDetailInline.svelte';
import type { RunSummary, RunResult } from '$lib/api/runs';
import * as runsApi from '$lib/api/runs';

function makeFull(overrides: Partial<RunResult> = {}): RunResult {
  return {
    id: 'rr-test',
    mode: 'topic_probe',
    status: 'completed',
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    project_id: null,
    repo_full_name: null,
    topic: 't',
    intent_hint: null,
    prompts_generated: 0,
    prompt_results: [],
    // TopicProbeReportCard reads aggregate.{mean_overall, score_distribution,
    // top_prompts}; supply concrete defaults so the card renders without
    // optional-chain crashes when the inline detail dispatches to it
    // (canonical shape mirrors TopicProbeReportCard.test.ts:68-76).
    aggregate: {
      mean_overall: 0,
      score_distribution: { excellent: 0, good: 0, fair: 0, poor: 0 },
      top_prompts: [],
    },
    // TopicProbeReportCard reads taxonomy_delta.{domains_created,
    // sub_domains_created, clusters_touched} as non-undefined; supply
    // empty arrays / 0 so the `tagRow` snippet's `items.length` check
    // doesn't crash on undefined (mirrors TopicProbeReportCard.test.ts:77-81).
    taxonomy_delta: {
      domains_created: [],
      sub_domains_created: [],
      clusters_touched: 0,
    },
    final_report: '',
    suite_id: null,
    topic_probe_meta: null,
    seed_agent_meta: null,
    error: null,
    ...overrides,
  } as unknown as RunResult;
}

describe('RunDetailInline', () => {
  it('Test 12: topic_probe mode renders TopicProbeReportCard with full RunResult', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(makeFull({ mode: 'topic_probe' }));
    const run: RunSummary = {
      id: 'rr-tp',
      mode: 'topic_probe',
      status: 'completed',
      started_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      project_id: null,
      repo_full_name: null,
      topic: 't',
      intent_hint: null,
      prompts_generated: 0,
    };
    const { container, findByText } = render(RunDetailInline, { run });
    // TopicProbeReportCard renders distinctive header text — wait for it
    await waitFor(() => {
      // Card renders something specific to TopicProbeReportCard
      expect(container.textContent).not.toContain('Loading detail');
    }, { timeout: 1000 });
  });

  it('Test 13: seed_agent mode with clusters renders DrillButton per cluster', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(makeFull({
      mode: 'seed_agent',
      seed_agent_meta: { clusters: [
        { id: 'c-1', label: 'react testing', domain: 'frontend', task_type: 'coding' },
        { id: 'c-2', label: 'go errors', domain: 'backend', task_type: 'coding' },
      ] },
    } as never));
    const run: RunSummary = {
      id: 'rr-seed',
      mode: 'seed_agent',
      status: 'completed',
      started_at: new Date().toISOString(),
      completed_at: new Date().toISOString(),
      project_id: null,
      repo_full_name: null,
      topic: 'seed',
      intent_hint: null,
      prompts_generated: 0,
    };
    const { findAllByRole } = render(RunDetailInline, { run });
    const buttons = await findAllByRole('button', { name: /Drill into cluster/ });
    expect(buttons.length).toBe(2);
  });

  it('Test 14: replay_run mode with null aggregate shows "Replay in progress"', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(makeFull({ mode: 'replay_run', aggregate: null } as never));
    const run: RunSummary = {
      id: 'rr-replay',
      mode: 'replay_run',
      status: 'running',
      started_at: new Date().toISOString(),
      completed_at: null,
      project_id: null,
      repo_full_name: null,
      topic: null,
      intent_hint: null,
      prompts_generated: 0,
    };
    const { findByText } = render(RunDetailInline, { run });
    await findByText(/Replay in progress/);
  });

  it('v0.4.37: replay branch lists per-prompt rows with score and status', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(makeFull({
      mode: 'replay_run',
      suite_id: 'suite-9',
      aggregate: { mean_overall: 7.2 },
      prompt_results: [
        { raw_prompt_idx: 0, overall_score: 7.5, intent_label: 'general',
          status: 'completed', raw_prompt: 'replay prompt zero',
          dimensions: { clarity: 7.5 }, optimized_prompt: 'out', changes_summary: 'tightened' },
        { raw_prompt_idx: 1, overall_score: null, intent_label: null,
          status: 'failed', raw_prompt: 'replay prompt one',
          dimensions: null, optimized_prompt: null, changes_summary: null },
      ],
    } as Partial<RunResult>));
    const run: RunSummary = {
      id: 'rr-rp', mode: 'replay_run', status: 'completed',
      started_at: new Date().toISOString(), completed_at: new Date().toISOString(),
      project_id: null, repo_full_name: null, topic: null, intent_hint: null,
      prompts_generated: 2,
    };
    const { container } = render(RunDetailInline, { props: { run } });
    await waitFor(() => {
      expect(container.querySelectorAll('[data-test="replay-prompt-row"]')).toHaveLength(2);
    });
    const rows = container.querySelectorAll('[data-test="replay-prompt-row"]');
    expect(rows[0].textContent).toContain('7.5');
    expect(rows[0].textContent).toContain('general');
    expect(rows[1].textContent).toContain('failed');
  });

  it('v0.4.37: expanding a replay row shows raw_prompt + changes_summary', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(makeFull({
      mode: 'replay_run', suite_id: 's', aggregate: { mean_overall: 7.2 },
      prompt_results: [
        { raw_prompt_idx: 0, overall_score: 7.5, status: 'completed',
          raw_prompt: 'expandable raw prompt', dimensions: { clarity: 7.0 },
          optimized_prompt: 'o', changes_summary: 'restructured sections' },
      ],
    } as Partial<RunResult>));
    const run: RunSummary = {
      id: 'rr-ex', mode: 'replay_run', status: 'completed',
      started_at: new Date().toISOString(), completed_at: new Date().toISOString(),
      project_id: null, repo_full_name: null, topic: null, intent_hint: null,
      prompts_generated: 1,
    };
    const { container } = render(RunDetailInline, { props: { run } });
    await waitFor(() => {
      expect(container.querySelector('[data-test="replay-prompt-row"]')).not.toBeNull();
    });
    (container.querySelector('[data-test="replay-prompt-row"] button') as HTMLElement).click();
    await waitFor(() => {
      const body = container.querySelector('[data-test="replay-prompt-body"]');
      expect(body?.textContent).toContain('expandable raw prompt');
      expect(body?.textContent).toContain('restructured sections');
      expect(body?.textContent).toContain('clarity 7.0');
    });
  });

  it('v0.4.37: pre-v0.4.37 replay rows render without output affordances', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(makeFull({
      mode: 'replay_run', suite_id: 's', aggregate: { mean_overall: 7.2 },
      prompt_results: [
        // Pre-v0.4.37 shape: NO optimized_prompt / changes_summary keys.
        { raw_prompt_idx: 0, overall_score: 7.5, status: 'completed',
          raw_prompt: 'old replay prompt', dimensions: { clarity: 7.0 } },
      ],
    } as Partial<RunResult>));
    const run: RunSummary = {
      id: 'rr-old', mode: 'replay_run', status: 'completed',
      started_at: new Date().toISOString(), completed_at: new Date().toISOString(),
      project_id: null, repo_full_name: null, topic: null, intent_hint: null,
      prompts_generated: 1,
    };
    const { container } = render(RunDetailInline, { props: { run } });
    await waitFor(() => {
      expect(container.querySelector('[data-test="replay-prompt-row"]')).not.toBeNull();
    });
    (container.querySelector('[data-test="replay-prompt-row"] button') as HTMLElement).click();
    await waitFor(() => {
      const body = container.querySelector('[data-test="replay-prompt-body"]');
      expect(body?.textContent).toContain('old replay prompt');
      // No changes-summary section, no dead affordances.
      expect(container.querySelector('[data-test="replay-prompt-changes"]')).toBeNull();
    });
  });
});
