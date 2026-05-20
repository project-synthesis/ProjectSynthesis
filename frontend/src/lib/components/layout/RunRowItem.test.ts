// frontend/src/lib/components/layout/RunRowItem.test.ts
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/svelte';
import RunRowItem from './RunRowItem.svelte';
import type { RunSummary } from '$lib/api/runs';
import * as runsApi from '$lib/api/runs';

const sampleRun: RunSummary = {
  id: 'rr-test',
  mode: 'topic_probe',
  status: 'completed',
  started_at: new Date(Date.now() - 60000).toISOString(),
  completed_at: new Date().toISOString(),
  project_id: null,
  repo_full_name: null,
  topic: 'react testing',
  intent_hint: null,
  prompts_generated: 5,
};

describe('RunRowItem', () => {
  it('Test 10: renders mode label + topic + status chip + relative timestamp; aria-expanded=false initially', () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue({ ...sampleRun, prompt_results: [], aggregate: {}, taxonomy_delta: {}, final_report: '', suite_id: null, topic_probe_meta: null, seed_agent_meta: null, error: null } as never);
    const { getByRole, getByText } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick: vi.fn(),
    });
    const btn = getByRole('button', { name: /probe react testing/ });
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(getByText(/probe/)).toBeTruthy();
    expect(getByText('react testing')).toBeTruthy();
    expect(getByText(/completed/)).toBeTruthy();
  });

  it('Test 11: click invokes onClick prop; expanded=true renders RunDetailInline', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue({ ...sampleRun, prompt_results: [], aggregate: {}, taxonomy_delta: {}, final_report: '', suite_id: null, topic_probe_meta: null, seed_agent_meta: null, error: null } as never);
    const onClick = vi.fn();
    const { getByRole, rerender, container } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick,
    });
    await fireEvent.click(getByRole('button'));
    expect(onClick).toHaveBeenCalledTimes(1);

    await rerender({ run: sampleRun, expanded: true, onClick });
    const btn = getByRole('button');
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    // The RunDetailInline component renders inside the expanded wrapper
    expect(container.querySelector('.run-detail')).toBeTruthy();
  });
});
