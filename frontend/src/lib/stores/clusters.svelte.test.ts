import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { clustersStore } from './clusters.svelte';
import { mockFetch, mockPatternFamily, mockMetaPattern, mockClusterMatch } from '../test-utils';

/**
 * Run all fake timers AND flush the microtask queue multiple passes.
 * Needed because async callbacks inside setTimeout create promise chains
 * that require extra microtask drains after each timer fires.
 */
async function flushAll() {
  // First flush: fire timers
  vi.runAllTimers();
  // Then drain microtasks from the async callback chain (fetch -> json -> state update)
  // We need multiple passes because each await in the async callback creates a new microtask
  for (let i = 0; i < 20; i++) await Promise.resolve();
}

describe('ClusterStore', () => {
  beforeEach(() => {
    clustersStore._reset();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('starts with no suggestion and no selected cluster', () => {
    expect(clustersStore.suggestion).toBeNull();
    expect(clustersStore.suggestionVisible).toBe(false);
    expect(clustersStore.selectedClusterId).toBeNull();
  });

  describe('checkForPatterns', () => {
    it('does not trigger API call for small delta (< 50 chars)', async () => {
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      clustersStore.checkForPatterns('short text');
      await flushAll();
      // Delta from 0 to 10 = 10, which is < 50
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('does not trigger before debounce delay', () => {
      const bigText = 'A'.repeat(60);
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      clustersStore.checkForPatterns(bigText);
      vi.advanceTimersByTime(299);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('triggers API call when delta >= 50 chars after debounce', async () => {
      const bigText = 'A'.repeat(60);
      const clusterMatch = mockClusterMatch();
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: clusterMatch } },
      ]);
      clustersStore.checkForPatterns(bigText);
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('debounces multiple rapid calls into one API call', async () => {
      const bigText = 'A'.repeat(60);
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      // Rapid calls — each resets the debounce timer
      clustersStore.checkForPatterns(bigText);
      clustersStore.checkForPatterns(bigText + 'B');
      clustersStore.checkForPatterns(bigText + 'BC');
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('sets suggestion and shows it when match found', async () => {
      const clusterMatch = mockClusterMatch();
      mockFetch([
        { match: '/clusters/match', response: { match: clusterMatch } },
      ]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(clustersStore.suggestion).not.toBeNull();
      expect(clustersStore.suggestionVisible).toBe(true);
    });

    it('clears suggestion when no match returned', async () => {
      clustersStore.suggestion = mockClusterMatch() as any;
      clustersStore.suggestionVisible = true;
      mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(clustersStore.suggestion).toBeNull();
      expect(clustersStore.suggestionVisible).toBe(false);
    });

    it('auto-dismisses suggestion after 10 seconds', async () => {
      const clusterMatch = mockClusterMatch();
      mockFetch([
        { match: '/clusters/match', response: { match: clusterMatch } },
      ]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(clustersStore.suggestionVisible).toBe(true);
      // Now advance 10s to fire the auto-dismiss timer
      vi.advanceTimersByTime(10_000);
      expect(clustersStore.suggestionVisible).toBe(false);
      expect(clustersStore.suggestion).toBeNull();
    });

    it('uses delta from previous call to calculate change', async () => {
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      // First call: 30 chars from baseline 0 -> delta=30, not triggered
      clustersStore.checkForPatterns('A'.repeat(30));
      await flushAll();
      expect(fetchMock).not.toHaveBeenCalled();

      // Second call: goes from 30 to 60 -> delta=30, still not triggered
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(fetchMock).not.toHaveBeenCalled();

      // Third call: goes from 60 to 0 -> delta=60, should trigger
      clustersStore.checkForPatterns('');
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();
    });
  });

  describe('applySuggestion', () => {
    it('returns meta-pattern IDs from suggestion', () => {
      const mp1 = mockMetaPattern({ id: 'mp-1' });
      const mp2 = mockMetaPattern({ id: 'mp-2' });
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [mp1 as any, mp2 as any],
      }) as any;
      const ids = clustersStore.applySuggestion();
      expect(ids).toEqual(['mp-1', 'mp-2']);
    });

    it('clears suggestion after apply', () => {
      clustersStore.suggestion = mockClusterMatch() as any;
      clustersStore.suggestionVisible = true;
      clustersStore.applySuggestion();
      expect(clustersStore.suggestion).toBeNull();
      expect(clustersStore.suggestionVisible).toBe(false);
    });

    it('returns null when no suggestion', () => {
      expect(clustersStore.applySuggestion()).toBeNull();
    });
  });

  describe('dismissSuggestion', () => {
    it('clears suggestion and hides it', () => {
      clustersStore.suggestion = mockClusterMatch() as any;
      clustersStore.suggestionVisible = true;
      clustersStore.dismissSuggestion();
      expect(clustersStore.suggestion).toBeNull();
      expect(clustersStore.suggestionVisible).toBe(false);
    });

    it('cancels the dismiss timer so it does not re-fire', async () => {
      const clusterMatch = mockClusterMatch();
      mockFetch([
        { match: '/clusters/match', response: { match: clusterMatch } },
      ]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(clustersStore.suggestionVisible).toBe(true);

      // Manually dismiss — this should cancel the auto-dismiss timer
      clustersStore.dismissSuggestion();
      expect(clustersStore.suggestion).toBeNull();

      // Advance past the dismiss timer — should not cause any side effects
      vi.advanceTimersByTime(10_000);
      expect(clustersStore.suggestion).toBeNull();
    });
  });

  describe('resetTracking', () => {
    it('resets _lastLength to 0 so next small input does not trigger', async () => {
      // Simulate a paste that sets _lastLength to 60
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();

      fetchMock.mockClear();

      // After resetTracking, _lastLength should be 0 again
      clustersStore.resetTracking();

      // A 10-char input from 0 = delta 10, which is < 50, so should NOT trigger
      clustersStore.checkForPatterns('A'.repeat(10));
      await flushAll();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('allows re-triggering paste detection after reset', async () => {
      // Set _lastLength to 60 via a paste
      mockFetch([{ match: '/clusters/match', response: { match: null } }]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();

      clustersStore.resetTracking();

      // Now a 60-char input from 0 = delta 60, should trigger
      const fetchMock2 = mockFetch([{ match: '/clusters/match', response: { match: null } }]);
      clustersStore.checkForPatterns('B'.repeat(60));
      await flushAll();
      expect(fetchMock2).toHaveBeenCalledOnce();
    });
  });

  describe('spawnTemplate', () => {
    function makeDetail(overrides: Record<string, unknown> = {}) {
      return {
        id: 'tmpl-1',
        parent_id: null,
        label: 'API Design Patterns',
        state: 'template',
        domain: 'backend',
        task_type: 'coding',
        member_count: 3,
        usage_count: 10,
        avg_score: 8.2,
        coherence: null,
        separation: null,
        preferred_strategy: 'chain-of-thought',
        promoted_at: null,
        meta_patterns: [],
        children: null,
        breadcrumb: null,
        optimizations: [
          { id: 'opt-1', raw_prompt: 'low score prompt', overall_score: 5.0, created_at: '2026-01-01T00:00:00Z', strategy_used: null },
          { id: 'opt-2', raw_prompt: 'high score prompt', overall_score: 9.0, created_at: '2026-01-02T00:00:00Z', strategy_used: null },
        ],
        ...overrides,
      };
    }

    it('happy path: returns prompt from highest-scoring optimization', async () => {
      mockFetch([{ match: '/clusters/tmpl-1', response: makeDetail() }]);
      const result = await clustersStore.spawnTemplate('tmpl-1');
      expect(result).not.toBeNull();
      expect(result?.prompt).toBe('high score prompt');
    });

    it('happy path: returns strategy and label', async () => {
      mockFetch([{ match: '/clusters/tmpl-1', response: makeDetail() }]);
      const result = await clustersStore.spawnTemplate('tmpl-1');
      expect(result?.strategy).toBe('chain-of-thought');
      expect(result?.label).toBe('API Design Patterns');
    });

    it('returns null strategy when preferred_strategy is null', async () => {
      mockFetch([{ match: '/clusters/tmpl-1', response: makeDetail({ preferred_strategy: null }) }]);
      const result = await clustersStore.spawnTemplate('tmpl-1');
      expect(result?.strategy).toBeNull();
    });

    it('returns null when optimizations array is empty', async () => {
      mockFetch([{ match: '/clusters/tmpl-1', response: makeDetail({ optimizations: [] }) }]);
      const result = await clustersStore.spawnTemplate('tmpl-1');
      expect(result).toBeNull();
    });

    it('returns null and logs warning on API failure', async () => {
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      mockFetch([{ match: '/clusters/tmpl-1', response: { detail: 'Not found' }, status: 404 }]);
      const result = await clustersStore.spawnTemplate('tmpl-1');
      expect(result).toBeNull();
      expect(warnSpy).toHaveBeenCalledWith('spawnTemplate failed:', expect.anything());
    });
  });

  describe('selectCluster', () => {
    it('sets selectedClusterId and loads cluster detail', async () => {
      const detail = {
        id: 'fam-1',
        label: 'API endpoint patterns',
        state: 'confirmed',
        domain: 'backend',
        task_type: 'coding',
        member_count: 3,
        usage_count: 5,
        avg_score: 7.8,
        coherence: null,
        separation: null,
        preferred_strategy: null,
        promoted_at: null,
        meta_patterns: [],
        optimizations: [],
        children: null,
        breadcrumb: null,
      };
      mockFetch([
        { match: '/clusters/fam-1', response: detail },
      ]);
      clustersStore.selectCluster('fam-1');
      expect(clustersStore.selectedClusterId).toBe('fam-1');
      await flushAll();
      expect(clustersStore.clusterDetail).not.toBeNull();
    });

    it('clears selectedClusterId and detail when null passed', () => {
      clustersStore.selectedClusterId = 'fam-1';
      clustersStore.clusterDetail = {
        id: 'fam-1', label: 'test', state: 'confirmed', domain: 'backend',
        task_type: 'coding', member_count: 3, usage_count: 5, avg_score: 7.8,
        coherence: null, separation: null, preferred_strategy: null,
        promoted_at: null, meta_patterns: [], optimizations: [],
        children: null, breadcrumb: null,
      } as any;
      clustersStore.selectCluster(null);
      expect(clustersStore.selectedClusterId).toBeNull();
      expect(clustersStore.clusterDetail).toBeNull();
    });

    it('sets clusterDetailError on load failure', async () => {
      mockFetch([
        { match: '/clusters/bad-id', response: { detail: 'Not found' }, status: 404 },
      ]);
      clustersStore.selectCluster('bad-id');
      await flushAll();
      expect(clustersStore.clusterDetailError).toBeTruthy();
      expect(clustersStore.clusterDetail).toBeNull();
    });

    it('sets clusterDetailLoading true during fetch then false', async () => {
      mockFetch([
        { match: '/clusters/fam-1', response: {
          id: 'fam-1', label: 'test', state: 'confirmed', domain: 'backend',
          task_type: 'coding', member_count: 3, usage_count: 5, avg_score: 7.8,
          coherence: null, separation: null, preferred_strategy: null,
          promoted_at: null, meta_patterns: [], optimizations: [],
          children: null, breadcrumb: null,
        } },
      ]);
      clustersStore.selectCluster('fam-1');
      // After selectCluster, loading starts synchronously
      expect(clustersStore.clusterDetailLoading).toBe(true);
      await flushAll();
      expect(clustersStore.clusterDetailLoading).toBe(false);
    });
  });
});
