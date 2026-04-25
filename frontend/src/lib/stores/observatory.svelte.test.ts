import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mockFetch } from '$lib/test-utils';

describe('observatoryStore', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('default period is "7d" (OS1)', async () => {
    const { observatoryStore } = await import('./observatory.svelte');
    expect(observatoryStore.period).toBe('7d');
  });

  it('restores period from localStorage (OS2)', async () => {
    localStorage.setItem('synthesis:observatory_period', '24h');
    const { observatoryStore } = await import('./observatory.svelte');
    expect(observatoryStore.period).toBe('24h');
  });

  it('setPeriod() persists to localStorage (OS3)', async () => {
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.setPeriod('30d');
    expect(localStorage.getItem('synthesis:observatory_period')).toBe('30d');
  });

  it('invalid localStorage value defaults to 7d (OS4)', async () => {
    localStorage.setItem('synthesis:observatory_period', 'invalid');
    const { observatoryStore } = await import('./observatory.svelte');
    expect(observatoryStore.period).toBe('7d');
  });

  it('refreshPatternDensity() populates data (OS5)', async () => {
    mockFetch([{
      match: '/taxonomy/pattern-density',
      response: {
        rows: [{
          domain_id: 'd1', domain_label: 'backend',
          cluster_count: 2, meta_pattern_count: 5,
          meta_pattern_avg_score: 7.8, global_pattern_count: 1,
          cross_cluster_injection_rate: 0.25,
          period_start: '2026-04-17T00:00:00Z', period_end: '2026-04-24T00:00:00Z',
        }],
        total_domains: 1, total_meta_patterns: 5, total_global_patterns: 1,
      },
    }]);
    const { observatoryStore } = await import('./observatory.svelte');
    await observatoryStore.refreshPatternDensity();
    expect(observatoryStore.patternDensity).toHaveLength(1);
    expect(observatoryStore.patternDensityError).toBeNull();
    expect(observatoryStore.patternDensityLoading).toBe(false);
  });

  it('refreshPatternDensity() captures error on reject (OS6)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValueOnce(new TypeError('oops'));
    const { observatoryStore } = await import('./observatory.svelte');
    await observatoryStore.refreshPatternDensity();
    expect(observatoryStore.patternDensityError).toBe('fetch-failed');
  });

  it('setPeriod() debounces re-fetch by 1 s (OS7)', async () => {
    // setPeriod fires BOTH refreshPatternDensity (heatmap) AND
    // loadTimelineEvents (timeline). loadTimelineEvents itself triggers
    // 2 network calls (ring + history). Total expected fetches per
    // settled debounce: 1 pattern-density + 1 ring + 1 history = 3.
    const fetchSpy = mockFetch([
      {
        match: '/taxonomy/pattern-density',
        response: { rows: [], total_domains: 0, total_meta_patterns: 0, total_global_patterns: 0 },
      },
      {
        match: '/clusters/activity?',
        response: { events: [], total_in_buffer: 0, oldest_ts: null },
      },
      {
        match: '/clusters/activity/history',
        response: { events: [], total: 0, has_more: false },
      },
    ]);
    vi.useFakeTimers();
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.setPeriod('24h');
    observatoryStore.setPeriod('30d');
    await vi.advanceTimersByTimeAsync(500);
    expect(fetchSpy).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(600);  // total 1100 ms > 1 s debounce
    // Settled: pattern-density + activity (ring) + activity/history (range).
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it('loadTimelineEvents() merges ring + JSONL with dedup, newest-first, 200 cap (OS8)', async () => {
    // The Observatory's own period-scoped buffer — `historicalEvents` —
    // replaces the prior in-place mutation of `clustersStore.activityEvents`.
    // Ring + JSONL events are deduped by `ts|op|decision`; live SSE prepends
    // continue to land in the cluster ring (read separately by the Timeline
    // at render time).
    const ringEvent = {
      ts: '2026-04-25T12:00:00Z', path: 'warm', op: 'discover', decision: 'd',
      cluster_id: null, optimization_id: null, duration_ms: null, context: {},
    };
    const jsonlOlder = {
      ts: '2026-04-25T08:00:00Z', path: 'warm', op: 'split', decision: 's',
      cluster_id: null, optimization_id: null, duration_ms: null, context: {},
    };
    const jsonlDup = { ...ringEvent };  // identical key — must be deduped

    mockFetch([
      {
        match: '/clusters/activity?',
        response: { events: [ringEvent], total_in_buffer: 1, oldest_ts: ringEvent.ts },
      },
      {
        match: '/clusters/activity/history',
        response: { events: [jsonlOlder, jsonlDup], total: 2, has_more: false },
      },
    ]);
    const { observatoryStore } = await import('./observatory.svelte');
    await observatoryStore.loadTimelineEvents();
    expect(observatoryStore.historicalEvents.length).toBe(2);
    expect(observatoryStore.historicalEvents[0].ts).toBe('2026-04-25T12:00:00Z');
    expect(observatoryStore.historicalEvents[1].ts).toBe('2026-04-25T08:00:00Z');
    expect(observatoryStore.historicalError).toBeNull();
  });

  it('loadTimelineEvents() captures error on reject (OS9)', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('oops'));
    const { observatoryStore } = await import('./observatory.svelte');
    await observatoryStore.loadTimelineEvents();
    expect(observatoryStore.historicalError).toBe('fetch-failed');
    expect(observatoryStore.historicalLoading).toBe(false);
  });

  /**
   * OS11: 24h → since == until == today UTC. The Observatory's smallest
   * window collapses to today's JSONL bucket. Reviewer flagged this as
   * a regression-test gap when the math moved from clustersStore.
   */
  it('loadTimelineEvents() — 24h period collapses to a single-day range (OS11)', async () => {
    const today = new Date().toISOString().slice(0, 10);
    const fetchSpy = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('activity/history')) {
        return new Response(
          JSON.stringify({ events: [], total: 0, has_more: false }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({ events: [], total_in_buffer: 0, oldest_ts: null }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(fetchSpy);
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.period = '24h';
    await observatoryStore.loadTimelineEvents();
    const historyCall = fetchSpy.mock.calls.find((c) => String(c[0]).includes('activity/history'));
    expect(historyCall).toBeDefined();
    const url = String(historyCall![0]);
    expect(url).toContain(`since=${today}`);
    expect(url).toContain(`until=${today}`);
  });

  /**
   * OS12: 7d → since = today − 6 days. Day-1 of "7d" must include today;
   * the bucket count is six prior days plus today (PERIOD_DAYS[7d]=7,
   * so the math is `(7 - 1) * 86_400_000` = 6 days back).
   */
  it('loadTimelineEvents() — 7d period requests since = today − 6 days (OS12)', async () => {
    const fetchSpy = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('activity/history')) {
        return new Response(
          JSON.stringify({ events: [], total: 0, has_more: false }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({ events: [], total_in_buffer: 0, oldest_ts: null }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(fetchSpy);
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.period = '7d';
    await observatoryStore.loadTimelineEvents();
    const historyCall = fetchSpy.mock.calls.find((c) => String(c[0]).includes('activity/history'));
    const url = String(historyCall![0]);
    const sinceMatch = url.match(/since=(\d{4}-\d{2}-\d{2})/);
    expect(sinceMatch).not.toBeNull();
    const sinceDate = new Date(sinceMatch![1]);
    const today = new Date(new Date().toISOString().slice(0, 10));
    const diffDays = Math.round((today.getTime() - sinceDate.getTime()) / 86_400_000);
    expect(diffDays).toBe(6);
  });

  /**
   * OS13: 30d → since = today − 29 days. Same pattern as OS12; explicit
   * coverage of the longest window guards against off-by-one drift.
   */
  it('loadTimelineEvents() — 30d period requests since = today − 29 days (OS13)', async () => {
    const fetchSpy = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes('activity/history')) {
        return new Response(
          JSON.stringify({ events: [], total: 0, has_more: false }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        );
      }
      return new Response(
        JSON.stringify({ events: [], total_in_buffer: 0, oldest_ts: null }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    });
    vi.spyOn(globalThis, 'fetch').mockImplementation(fetchSpy);
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.period = '30d';
    await observatoryStore.loadTimelineEvents();
    const historyCall = fetchSpy.mock.calls.find((c) => String(c[0]).includes('activity/history'));
    const url = String(historyCall![0]);
    const sinceMatch = url.match(/since=(\d{4}-\d{2}-\d{2})/);
    const sinceDate = new Date(sinceMatch![1]);
    const today = new Date(new Date().toISOString().slice(0, 10));
    const diffDays = Math.round((today.getTime() - sinceDate.getTime()) / 86_400_000);
    expect(diffDays).toBe(29);
  });

  it('loadTimelineEvents() race-guards stale responses (OS10)', async () => {
    // Generation counter discards in-flight responses when a newer fetch
    // is issued — fast period flicks must not let a stale older response
    // overwrite a fresh newer one.
    vi.useFakeTimers();
    const resolveFirstHolder: { fn: (() => void) | null } = { fn: null };
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation((url) => {
      const u = String(url);
      if (u.includes('activity/history')) {
        // First call hangs; second call resolves immediately.
        if (resolveFirstHolder.fn === null) {
          return new Promise((resolve) => {
            resolveFirstHolder.fn = () => resolve(new Response(
              JSON.stringify({ events: [{ ts: '2026-04-22T00:00:00Z', path: 'warm', op: 'discover', decision: 'stale', context: {} }], total: 1, has_more: false }),
              { status: 200, headers: { 'content-type': 'application/json' } },
            ));
          });
        }
        return Promise.resolve(new Response(
          JSON.stringify({ events: [{ ts: '2026-04-25T00:00:00Z', path: 'warm', op: 'discover', decision: 'fresh', context: {} }], total: 1, has_more: false }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ));
      }
      return Promise.resolve(new Response(
        JSON.stringify({ events: [], total_in_buffer: 0, oldest_ts: null }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ));
    });
    const { observatoryStore } = await import('./observatory.svelte');

    // Issue first fetch (hangs).
    const first = observatoryStore.loadTimelineEvents();
    // Issue second fetch (resolves immediately).
    const second = observatoryStore.loadTimelineEvents();
    await second;
    // Now resolve the stale first fetch.
    resolveFirstHolder.fn?.();
    await first;

    // Fresh response must win — stale must NOT overwrite.
    expect(observatoryStore.historicalEvents.some((e) => e.decision === 'fresh')).toBe(true);
    expect(observatoryStore.historicalEvents.some((e) => e.decision === 'stale')).toBe(false);
    expect(fetchSpy.mock.calls.length).toBeGreaterThan(0);
  });
});
