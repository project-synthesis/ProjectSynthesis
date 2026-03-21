import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { patternsStore } from './patterns.svelte';
import { mockFetch, mockPatternFamily, mockMetaPattern, mockPatternMatch } from '../test-utils';

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

describe('PatternStore', () => {
  beforeEach(() => {
    patternsStore._reset();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('starts with no suggestion and no selected family', () => {
    expect(patternsStore.suggestion).toBeNull();
    expect(patternsStore.suggestionVisible).toBe(false);
    expect(patternsStore.selectedFamilyId).toBeNull();
  });

  describe('checkForPatterns', () => {
    it('does not trigger API call for small delta (< 50 chars)', async () => {
      const fetchMock = mockFetch([
        { match: '/patterns/match', response: { match: null } },
      ]);
      patternsStore.checkForPatterns('short text');
      await flushAll();
      // Delta from 0 to 10 = 10, which is < 50
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('does not trigger before debounce delay', () => {
      const bigText = 'A'.repeat(60);
      const fetchMock = mockFetch([
        { match: '/patterns/match', response: { match: null } },
      ]);
      patternsStore.checkForPatterns(bigText);
      vi.advanceTimersByTime(299);
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('triggers API call when delta >= 50 chars after debounce', async () => {
      const bigText = 'A'.repeat(60);
      const patternMatch = mockPatternMatch();
      const fetchMock = mockFetch([
        { match: '/patterns/match', response: { match: patternMatch } },
      ]);
      patternsStore.checkForPatterns(bigText);
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('debounces multiple rapid calls into one API call', async () => {
      const bigText = 'A'.repeat(60);
      const fetchMock = mockFetch([
        { match: '/patterns/match', response: { match: null } },
      ]);
      // Rapid calls — each resets the debounce timer
      patternsStore.checkForPatterns(bigText);
      patternsStore.checkForPatterns(bigText + 'B');
      patternsStore.checkForPatterns(bigText + 'BC');
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();
    });

    it('sets suggestion and shows it when match found', async () => {
      const patternMatch = mockPatternMatch();
      mockFetch([
        { match: '/patterns/match', response: { match: patternMatch } },
      ]);
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(patternsStore.suggestion).not.toBeNull();
      expect(patternsStore.suggestionVisible).toBe(true);
    });

    it('clears suggestion when no match returned', async () => {
      patternsStore.suggestion = mockPatternMatch() as any;
      patternsStore.suggestionVisible = true;
      mockFetch([
        { match: '/patterns/match', response: { match: null } },
      ]);
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(patternsStore.suggestion).toBeNull();
      expect(patternsStore.suggestionVisible).toBe(false);
    });

    it('auto-dismisses suggestion after 10 seconds', async () => {
      const patternMatch = mockPatternMatch();
      mockFetch([
        { match: '/patterns/match', response: { match: patternMatch } },
      ]);
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(patternsStore.suggestionVisible).toBe(true);
      // Now advance 10s to fire the auto-dismiss timer
      vi.advanceTimersByTime(10_000);
      expect(patternsStore.suggestionVisible).toBe(false);
      expect(patternsStore.suggestion).toBeNull();
    });

    it('uses delta from previous call to calculate change', async () => {
      const fetchMock = mockFetch([
        { match: '/patterns/match', response: { match: null } },
      ]);
      // First call: 30 chars from baseline 0 → delta=30, not triggered
      patternsStore.checkForPatterns('A'.repeat(30));
      await flushAll();
      expect(fetchMock).not.toHaveBeenCalled();

      // Second call: goes from 30 to 60 → delta=30, still not triggered
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(fetchMock).not.toHaveBeenCalled();

      // Third call: goes from 60 to 0 → delta=60, should trigger
      patternsStore.checkForPatterns('');
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();
    });
  });

  describe('applySuggestion', () => {
    it('returns meta-pattern IDs from suggestion', () => {
      const mp1 = mockMetaPattern({ id: 'mp-1' });
      const mp2 = mockMetaPattern({ id: 'mp-2' });
      patternsStore.suggestion = {
        family: mockPatternFamily() as any,
        meta_patterns: [mp1 as any, mp2 as any],
        similarity: 0.85,
      };
      const ids = patternsStore.applySuggestion();
      expect(ids).toEqual(['mp-1', 'mp-2']);
    });

    it('clears suggestion after apply', () => {
      patternsStore.suggestion = mockPatternMatch() as any;
      patternsStore.suggestionVisible = true;
      patternsStore.applySuggestion();
      expect(patternsStore.suggestion).toBeNull();
      expect(patternsStore.suggestionVisible).toBe(false);
    });

    it('returns null when no suggestion', () => {
      expect(patternsStore.applySuggestion()).toBeNull();
    });
  });

  describe('dismissSuggestion', () => {
    it('clears suggestion and hides it', () => {
      patternsStore.suggestion = mockPatternMatch() as any;
      patternsStore.suggestionVisible = true;
      patternsStore.dismissSuggestion();
      expect(patternsStore.suggestion).toBeNull();
      expect(patternsStore.suggestionVisible).toBe(false);
    });

    it('cancels the dismiss timer so it does not re-fire', async () => {
      const patternMatch = mockPatternMatch();
      mockFetch([
        { match: '/patterns/match', response: { match: patternMatch } },
      ]);
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(patternsStore.suggestionVisible).toBe(true);

      // Manually dismiss — this should cancel the auto-dismiss timer
      patternsStore.dismissSuggestion();
      expect(patternsStore.suggestion).toBeNull();

      // Advance past the dismiss timer — should not cause any side effects
      vi.advanceTimersByTime(10_000);
      expect(patternsStore.suggestion).toBeNull();
    });
  });

  describe('resetTracking', () => {
    it('resets _lastLength to 0 so next small input does not trigger', async () => {
      // Simulate a paste that sets _lastLength to 60
      const fetchMock = mockFetch([
        { match: '/patterns/match', response: { match: null } },
      ]);
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();
      expect(fetchMock).toHaveBeenCalledOnce();

      fetchMock.mockClear();

      // After resetTracking, _lastLength should be 0 again
      patternsStore.resetTracking();

      // A 10-char input from 0 = delta 10, which is < 50, so should NOT trigger
      patternsStore.checkForPatterns('A'.repeat(10));
      await flushAll();
      expect(fetchMock).not.toHaveBeenCalled();
    });

    it('allows re-triggering paste detection after reset', async () => {
      // Set _lastLength to 60 via a paste
      mockFetch([{ match: '/patterns/match', response: { match: null } }]);
      patternsStore.checkForPatterns('A'.repeat(60));
      await flushAll();

      patternsStore.resetTracking();

      // Now a 60-char input from 0 = delta 60, should trigger
      const fetchMock2 = mockFetch([{ match: '/patterns/match', response: { match: null } }]);
      patternsStore.checkForPatterns('B'.repeat(60));
      await flushAll();
      expect(fetchMock2).toHaveBeenCalledOnce();
    });
  });

  describe('selectFamily', () => {
    it('sets selectedFamilyId and loads family detail', async () => {
      const detail = {
        ...mockPatternFamily(),
        updated_at: null,
        meta_patterns: [],
        optimizations: [],
      };
      mockFetch([
        { match: '/patterns/families/fam-1', response: detail },
      ]);
      patternsStore.selectFamily('fam-1');
      expect(patternsStore.selectedFamilyId).toBe('fam-1');
      await flushAll();
      expect(patternsStore.familyDetail).not.toBeNull();
    });

    it('clears selectedFamilyId and detail when null passed', () => {
      patternsStore.selectedFamilyId = 'fam-1';
      patternsStore.familyDetail = { ...mockPatternFamily(), updated_at: null, meta_patterns: [], optimizations: [] } as any;
      patternsStore.selectFamily(null);
      expect(patternsStore.selectedFamilyId).toBeNull();
      expect(patternsStore.familyDetail).toBeNull();
    });

    it('sets familyDetailError on load failure', async () => {
      mockFetch([
        { match: '/patterns/families/bad-id', response: { detail: 'Not found' }, status: 404 },
      ]);
      patternsStore.selectFamily('bad-id');
      await flushAll();
      expect(patternsStore.familyDetailError).toBeTruthy();
      expect(patternsStore.familyDetail).toBeNull();
    });

    it('sets familyDetailLoading true during fetch then false', async () => {
      mockFetch([
        { match: '/patterns/families/fam-1', response: { ...mockPatternFamily(), updated_at: null, meta_patterns: [], optimizations: [] } },
      ]);
      patternsStore.selectFamily('fam-1');
      // After selectFamily, loading starts synchronously
      expect(patternsStore.familyDetailLoading).toBe(true);
      await flushAll();
      expect(patternsStore.familyDetailLoading).toBe(false);
    });
  });
});
