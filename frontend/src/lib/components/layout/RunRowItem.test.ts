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

// Enriched `getRun` fixture mirrors `RunDetailInline.test.ts:makeFull()`.
// `RunRowItem` mounts `RunDetailInline` when `expanded=true`, which in turn
// dispatches to `TopicProbeReportCard`. The card reads `aggregate.{mean_overall,
// score_distribution, top_prompts}` + `taxonomy_delta.{domains_created,
// sub_domains_created, clusters_touched}` non-defensively in its `tagRow`
// snippet (TopicProbeReportCard.svelte:296 — `items.length` on undefined
// throws an unhandled rejection in vitest). Supplying the canonical
// empty/zero values from the `TopicProbeReportCard.test.ts:68-81` fixture
// keeps the vitest runner free of unhandled-rejection noise during the
// expanded-row render path.
const fullRunFixture = {
  ...sampleRun,
  prompt_results: [],
  aggregate: {
    mean_overall: 0,
    score_distribution: { excellent: 0, good: 0, fair: 0, poor: 0 },
    top_prompts: [],
  },
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
};

describe('RunRowItem', () => {
  it('Test 10: renders mode label + topic + status chip + relative timestamp; aria-expanded=false initially', () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(fullRunFixture as never);
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
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(fullRunFixture as never);
    const onClick = vi.fn();
    const { getByRole, rerender, container } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick,
    });
    // Narrow to the row-header (matches the aria-label set in
    // `RunRowItem.svelte:33`); the enriched fixture lets the expanded
    // `RunDetailInline` → `TopicProbeReportCard` render its own action
    // buttons (Save Suite / Copy md), so a bare `getByRole('button')`
    // would now find multiple matches.
    await fireEvent.click(getByRole('button', { name: /probe react testing/ }));
    expect(onClick).toHaveBeenCalledTimes(1);

    await rerender({ run: sampleRun, expanded: true, onClick });
    const btn = getByRole('button', { name: /probe react testing/ });
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    // The RunDetailInline component renders inside the expanded wrapper
    expect(container.querySelector('.run-detail')).toBeTruthy();
  });
});
