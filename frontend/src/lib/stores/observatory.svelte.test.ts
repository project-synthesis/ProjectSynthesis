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
    const fetchSpy = mockFetch([{
      match: '/taxonomy/pattern-density',
      response: { rows: [], total_domains: 0, total_meta_patterns: 0, total_global_patterns: 0 },
    }]);
    vi.useFakeTimers();
    const { observatoryStore } = await import('./observatory.svelte');
    observatoryStore.setPeriod('24h');
    observatoryStore.setPeriod('30d');
    await vi.advanceTimersByTimeAsync(500);
    expect(fetchSpy).toHaveBeenCalledTimes(0);
    await vi.advanceTimersByTimeAsync(600);  // total 1100 ms > 1 s debounce
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });
});
