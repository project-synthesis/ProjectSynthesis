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

    it('suggestion stays visible until explicitly dismissed (no auto-dismiss)', async () => {
      const clusterMatch = mockClusterMatch();
      mockFetch([
        { match: '/clusters/match', response: { match: clusterMatch } },
      ]);
      clustersStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(clustersStore.suggestionVisible).toBe(true);
      // Advance well past old 10s timer — suggestion should persist
      vi.advanceTimersByTime(30_000);
      expect(clustersStore.suggestionVisible).toBe(true);
    });

    it('fires on typing path when prompt >= 30 chars with 800ms debounce', async () => {
      const fetchMock = mockFetch([
        { match: '/clusters/match', response: { match: null } },
      ]);
      // Type 30 chars one at a time — delta=1 each time (typing path)
      for (let i = 1; i <= 30; i++) {
        clustersStore.checkForPatterns('A'.repeat(i));
      }
      // 300ms (paste debounce) — should NOT have fired yet (typing uses 800ms)
      vi.advanceTimersByTime(300);
      await vi.runAllTimersAsync();
      // At this point the 800ms timer should have fired
      expect(fetchMock).toHaveBeenCalledOnce();
    });
  });

  describe('applySuggestion', () => {
    it('returns meta-pattern IDs and cluster label from suggestion', () => {
      const mp1 = mockMetaPattern({ id: 'mp-1' });
      const mp2 = mockMetaPattern({ id: 'mp-2' });
      clustersStore.suggestion = mockClusterMatch({
        cluster: { id: 'c-1', label: 'Test Cluster', domain: 'backend', member_count: 5 },
        meta_patterns: [mp1 as any, mp2 as any],
      }) as any;
      const result = clustersStore.applySuggestion();
      expect(result).toEqual({ ids: ['mp-1', 'mp-2'], clusterLabel: 'Test Cluster' });
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
        state: 'active',
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
        id: 'fam-1', label: 'test', state: 'active', domain: 'backend',
        task_type: 'coding', member_count: 3, usage_count: 5, avg_score: 7.8,
        coherence: null, separation: null, preferred_strategy: null,
        promoted_at: null, meta_patterns: [], optimizations: [],
        children: null, breadcrumb: null,
      } as any;
      clustersStore.selectCluster(null);
      expect(clustersStore.selectedClusterId).toBeNull();
      expect(clustersStore.clusterDetail).toBeNull();
    });

    it('clears selection and error on load failure (ghost node cleanup)', async () => {
      mockFetch([
        { match: '/clusters/bad-id', response: { detail: 'Not found' }, status: 404 },
      ]);
      clustersStore.selectCluster('bad-id');
      await flushAll();
      // On 404, the store clears the selection entirely to avoid
      // showing stale "Cluster not found" errors for ghost nodes.
      expect(clustersStore.selectedClusterId).toBeNull();
      expect(clustersStore.clusterDetail).toBeNull();
      expect(clustersStore.clusterDetailError).toBeNull();
    });

    it('sets clusterDetailLoading true during fetch then false', async () => {
      mockFetch([
        { match: '/clusters/fam-1', response: {
          id: 'fam-1', label: 'test', state: 'active', domain: 'backend',
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

  describe('invalidateClusters ghost selection (F3)', () => {
    const treeNode = { id: 'fam-1', label: 'Test', state: 'active', coherence_score: 0.8, member_count: 3, usage_count: 1, domain: 'general', parent_id: null, children: null, breadcrumb: null };
    const detailNode = { ...treeNode, optimizations: [] };

    /** Mock all 4 loadTree API calls + optional cluster detail */
    function mockTreeLoad(nodes: any[], includeDetail = false) {
      const routes: any[] = [
        { match: '/clusters/tree', response: { nodes } },
        { match: '/clusters/stats', response: {} },
        { match: '/clusters/similarity-edges', response: { edges: [] } },
        { match: '/clusters/injection-edges', response: { edges: [] } },
      ];
      if (includeDetail) {
        routes.push({ match: `/clusters/${nodes[0]?.id || 'fam-1'}`, response: detailNode });
      }
      mockFetch(routes);
    }

    it('clears selection when cluster no longer exists after tree reload', async () => {
      // Pre-set selected cluster directly (skip async detail fetch)
      clustersStore.selectedClusterId = 'fam-1' as any;

      // Tree reload: fam-1 is gone
      mockTreeLoad([]);
      await clustersStore.invalidateClusters();
      expect(clustersStore.selectedClusterId).toBeNull();
    });

    it('preserves selection when cluster still exists after tree reload', async () => {
      // Pre-set selected cluster directly
      clustersStore.selectedClusterId = 'fam-1' as any;

      // Tree reload: fam-1 still present
      mockTreeLoad([treeNode], true);
      await clustersStore.invalidateClusters();
      await flushAll();
      expect(clustersStore.selectedClusterId).toBe('fam-1');
    });
  });

  describe('seed batch state (F8)', () => {
    it('updateSeedProgress sets active state and progress', () => {
      clustersStore.updateSeedProgress({ phase: 'optimize', completed: 5, total: 30, current_prompt: 'test prompt' });
      expect(clustersStore.seedBatchActive).toBe(true);
      expect(clustersStore.seedBatchProgress).toEqual({ completed: 5, total: 30, current: 'test prompt' });
    });

    it('clearSeedBatch resets seed state', () => {
      clustersStore.updateSeedProgress({ phase: 'optimize', completed: 10, total: 30 });
      clustersStore.clearSeedBatch();
      expect(clustersStore.seedBatchActive).toBe(false);
      expect(clustersStore.seedBatchProgress).toEqual({ completed: 0, total: 0, current: '' });
    });

    it('ignores non-optimize phase events', () => {
      clustersStore.updateSeedProgress({ phase: 'analyze', completed: 1, total: 5 });
      expect(clustersStore.seedBatchActive).toBe(false);
    });
  });
});
