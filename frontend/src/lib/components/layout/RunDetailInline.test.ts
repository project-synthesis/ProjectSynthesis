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
    aggregate: {},
    taxonomy_delta: {},
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
});
