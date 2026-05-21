// frontend/src/lib/components/layout/RunRowItem.test.ts
import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
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

  // v0.4.32 — RunsPanel polish: kebab + rename + delete

  it('Test 12: kebab opens menu with Rename + Delete', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(fullRunFixture as never);
    const { getByLabelText, getByRole } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick: vi.fn(),
    });
    const kebab = getByLabelText(/Open actions menu/);
    await fireEvent.click(kebab);
    expect(getByRole('menuitem', { name: /Rename/ })).toBeTruthy();
    expect(getByRole('menuitem', { name: /Delete/ })).toBeTruthy();
  });

  it('Test 13: Rename enters inline edit mode + replaces topic with input', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(fullRunFixture as never);
    const { getByLabelText, getByRole, container } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick: vi.fn(),
    });
    await fireEvent.click(getByLabelText(/Open actions menu/));
    await fireEvent.click(getByRole('menuitem', { name: /Rename/ }));
    const input = container.querySelector('input.input-field') as HTMLInputElement;
    expect(input).toBeTruthy();
    expect(input.value).toBe(sampleRun.topic); // pre-filled
  });

  it('Test 14: Enter on rename input submits PATCH', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(fullRunFixture as never);
    const patchSpy = vi.spyOn(runsApi, 'patchRun').mockResolvedValue({
      ...sampleRun, display_name: 'New Label',
    });
    const { getByLabelText, getByRole, container } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick: vi.fn(),
    });
    await fireEvent.click(getByLabelText(/Open actions menu/));
    await fireEvent.click(getByRole('menuitem', { name: /Rename/ }));
    const input = container.querySelector('input.input-field') as HTMLInputElement;
    await fireEvent.input(input, { target: { value: 'New Label' } });
    await fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => expect(patchSpy).toHaveBeenCalledWith(sampleRun.id, { display_name: 'New Label' }));
  });

  it('Test 15: Delete from kebab shows confirm modal', async () => {
    vi.spyOn(runsApi, 'getRun').mockResolvedValue(fullRunFixture as never);
    const onDeleteConfirm = vi.fn();
    const { getByLabelText, getByRole } = render(RunRowItem, {
      run: sampleRun,
      expanded: false,
      onClick: vi.fn(),
      onDeleteConfirm,
    });
    await fireEvent.click(getByLabelText(/Open actions menu/));
    await fireEvent.click(getByRole('menuitem', { name: /Delete/ }));
    expect(onDeleteConfirm).toHaveBeenCalledWith(sampleRun.id);
  });
});
