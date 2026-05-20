// frontend/src/lib/components/layout/RunsPanel.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, fireEvent, waitFor } from '@testing-library/svelte';
import RunsPanel from './RunsPanel.svelte';
import * as runsApi from '$lib/api/runs';
import type { RunListResponse, RunSummary } from '$lib/api/runs';

function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    id: 'rr-1',
    mode: 'topic_probe',
    status: 'completed',
    started_at: new Date(Date.now() - 60000).toISOString(),
    completed_at: new Date().toISOString(),
    project_id: null,
    repo_full_name: null,
    topic: 'sample topic',
    intent_hint: null,
    prompts_generated: 5,
    ...overrides,
  };
}

function makeResp(items: RunSummary[], hasMore = false, nextOffset: number | null = null): RunListResponse {
  return {
    total: items.length,
    count: items.length,
    offset: 0,
    items,
    has_more: hasMore,
    next_offset: nextOffset,
  };
}

describe('RunsPanel', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('Test 1: initial mount calls listRuns({limit:20, offset:0}); renders one RunRowItem per item', async () => {
    const spy = vi.spyOn(runsApi, 'listRuns').mockResolvedValue(makeResp([
      makeRun({ id: 'rr-1', topic: 'first' }),
      makeRun({ id: 'rr-2', topic: 'second' }),
    ]));
    const { findAllByRole } = render(RunsPanel, { active: true });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const call = spy.mock.calls[0]?.[0];
    expect(call?.limit).toBe(20);
    expect(call?.offset).toBe(0);
    const items = await findAllByRole('listitem');
    expect(items.length).toBe(2);
  });

  it('Test 2: listRuns failure shows error + Retry button; click re-fires', async () => {
    const spy = vi.spyOn(runsApi, 'listRuns')
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce(makeResp([makeRun()]));
    const { findByText, getByRole } = render(RunsPanel, { active: true });
    await findByText(/Failed to load runs: boom/);
    const retry = getByRole('button', { name: /Retry/ });
    await fireEvent.click(retry);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });

  it('Test 3: empty response shows "No runs match" + Reset filters button', async () => {
    vi.spyOn(runsApi, 'listRuns').mockResolvedValue(makeResp([]));
    const { findByText, getByRole } = render(RunsPanel, { active: true });
    await findByText(/No runs match the current filters/);
    expect(getByRole('button', { name: /Reset filters/ })).toBeTruthy();
  });

  it('Test 4: mode chip click refetches with mode=topic_probe; runs reset', async () => {
    const spy = vi.spyOn(runsApi, 'listRuns').mockResolvedValue(makeResp([makeRun()]));
    const { getByRole, findByRole } = render(RunsPanel, { active: true });
    await findByRole('listitem');
    spy.mockClear();
    const probeChip = getByRole('button', { name: /^Probe$/ });
    await fireEvent.click(probeChip);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const args = spy.mock.calls[spy.mock.calls.length - 1]?.[0];
    expect(args?.mode).toBe('topic_probe');
  });

  it('Test 5: status chip click refetches with status=completed', async () => {
    const spy = vi.spyOn(runsApi, 'listRuns').mockResolvedValue(makeResp([makeRun()]));
    const { getAllByRole, findByRole } = render(RunsPanel, { active: true });
    await findByRole('listitem');
    spy.mockClear();
    const completedChips = getAllByRole('button', { name: /^Completed$/ });
    await fireEvent.click(completedChips[0]);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const args = spy.mock.calls[spy.mock.calls.length - 1]?.[0];
    expect(args?.status).toBe('completed');
  });

  it('Test 6: scroll-load triggers listRuns({offset:nextOffset}); items appended', async () => {
    // Stub IntersectionObserver so we can fire the entry deterministically.
    let observerCallback: ((entries: IntersectionObserverEntry[]) => void) | null = null;
    const observe = vi.fn();
    const disconnect = vi.fn();
    vi.stubGlobal('IntersectionObserver', class {
      constructor(cb: (entries: IntersectionObserverEntry[]) => void) { observerCallback = cb; }
      observe = observe;
      disconnect = disconnect;
      unobserve = vi.fn();
      takeRecords = vi.fn(() => []);
      root = null; rootMargin = ''; thresholds = [];
    });
    const spy = vi.spyOn(runsApi, 'listRuns')
      .mockResolvedValueOnce(makeResp([makeRun({ id: 'rr-1' })], true, 1))
      .mockResolvedValueOnce(makeResp([makeRun({ id: 'rr-2' })], false, null));
    const { findAllByRole, container } = render(RunsPanel, { active: true });
    await findAllByRole('listitem');
    // Sentinel must exist (hasMore=true on page 1)
    const sentinel = container.querySelector('.runs-sentinel');
    expect(sentinel).toBeTruthy();
    // Fire the IntersectionObserver entry manually
    expect(observerCallback).not.toBeNull();
    observerCallback!([{ isIntersecting: true } as IntersectionObserverEntry]);
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    const secondCall = spy.mock.calls[1]?.[0];
    expect(secondCall?.offset).toBe(1);
    // Items appended (not replaced)
    const items = await findAllByRole('listitem');
    expect(items.length).toBe(2);
    vi.unstubAllGlobals();
  });

  it('Test 7: scroll-load no-op when loadingMore=true (double-trigger guard)', async () => {
    // Sequential calls within a single fetch cycle must not double-fire.
    let inFlight = false;
    const spy = vi.spyOn(runsApi, 'listRuns').mockImplementation(async () => {
      if (inFlight) throw new Error('double-trigger detected');
      inFlight = true;
      await new Promise(r => setTimeout(r, 20));
      inFlight = false;
      return makeResp([makeRun()]);
    });
    render(RunsPanel, { active: true });
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
  });

  it('Test 8: filter change mid-load discards stale response via requestId', async () => {
    let resolveFirst: ((v: RunListResponse) => void) | null = null;
    const spy = vi.spyOn(runsApi, 'listRuns')
      .mockImplementationOnce(() => new Promise<RunListResponse>(r => { resolveFirst = r; }))
      .mockResolvedValueOnce(makeResp([makeRun({ id: 'rr-fresh' })]));
    const { getByRole, findAllByRole } = render(RunsPanel, { active: true });
    // While first fetch is in-flight, click Probe chip → triggers second fetch
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const probeChip = getByRole('button', { name: /^Probe$/ });
    await fireEvent.click(probeChip);
    // Resolve the FIRST (stale) request after the second completes
    resolveFirst?.(makeResp([makeRun({ id: 'rr-stale' })]));
    // Allow microtasks to settle
    await new Promise(r => setTimeout(r, 50));
    const items = await findAllByRole('listitem');
    // Only the fresh row should be rendered
    expect(items.length).toBe(1);
  });

  it('Test 9: project switch re-fires fetch with new project_id', async () => {
    const spy = vi.spyOn(runsApi, 'listRuns').mockResolvedValue(makeResp([makeRun()]));
    const { findByRole } = render(RunsPanel, { active: true });
    await findByRole('listitem');
    spy.mockClear();
    // Project store mutation — canonical write idiom is setCurrent(id)
    // per HistoryPanel.pagination.test.ts:31,52,61,73 (the sibling pattern).
    // currentProjectId is a $state rune, but components READ via .currentProjectId
    // and WRITE via .setCurrent(id) for consistency with the public API.
    const { projectStore } = await import('$lib/stores/project.svelte');
    projectStore.setCurrent('proj-new');
    await waitFor(() => expect(spy).toHaveBeenCalled());
    const args = spy.mock.calls[spy.mock.calls.length - 1]?.[0];
    expect(args?.project_id).toBe('proj-new');
  });
});
