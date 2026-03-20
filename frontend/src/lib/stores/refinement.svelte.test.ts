import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { refinementStore } from './refinement.svelte';
import { forgeStore } from './forge.svelte';
import { mockFetch, mockRefinementTurn, mockRefinementBranch } from '../test-utils';

describe('RefinementStore', () => {
  beforeEach(() => {
    refinementStore._reset();
    forgeStore._reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('starts with idle status and empty state', () => {
    expect(refinementStore.status).toBe('idle');
    expect(refinementStore.turns).toHaveLength(0);
    expect(refinementStore.optimizationId).toBeNull();
    expect(refinementStore.error).toBeNull();
    expect(refinementStore.suggestions).toHaveLength(0);
  });

  describe('init', () => {
    it('sets optimizationId and loads versions', async () => {
      const turn = mockRefinementTurn();
      mockFetch([
        { match: '/refine/opt-1/versions', response: { optimization_id: 'opt-1', versions: [turn] } },
      ]);
      await refinementStore.init('opt-1');
      expect(refinementStore.optimizationId).toBe('opt-1');
      expect(refinementStore.turns).toHaveLength(1);
    });

    it('sets activeBranchId from last turn', async () => {
      const turn = mockRefinementTurn({ branch_id: 'branch-main' });
      mockFetch([
        { match: '/refine/opt-1/versions', response: { optimization_id: 'opt-1', versions: [turn] } },
      ]);
      await refinementStore.init('opt-1');
      expect(refinementStore.activeBranchId).toBe('branch-main');
    });

    it('loads suggestions from last turn', async () => {
      const turn = mockRefinementTurn({ suggestions: [{ text: 'Add examples', source: 'model' }] });
      mockFetch([
        { match: '/refine/opt-1/versions', response: { optimization_id: 'opt-1', versions: [turn] } },
      ]);
      await refinementStore.init('opt-1');
      expect(refinementStore.suggestions).toHaveLength(1);
    });

    it('seeds suggestions from forge store when no versions exist', async () => {
      forgeStore.initialSuggestions = [{ text: 'Try chain-of-thought', type: 'strategy' }];
      mockFetch([
        { match: '/refine/opt-1/versions', response: { optimization_id: 'opt-1', versions: [] } },
      ]);
      await refinementStore.init('opt-1');
      expect(refinementStore.suggestions).toEqual(forgeStore.initialSuggestions);
    });

    it('seeds suggestions from forge store when versions API throws', async () => {
      forgeStore.initialSuggestions = [{ text: 'Add specificity', type: 'clarity' }];
      mockFetch([
        { match: '/refine/opt-1/versions', response: { detail: 'Not found' }, status: 404 },
      ]);
      await refinementStore.init('opt-1');
      expect(refinementStore.suggestions).toEqual(forgeStore.initialSuggestions);
    });
  });

  describe('handleEvent (SSE events)', () => {
    it('sets status to complete on refinement_complete event', async () => {
      // Don't set optimizationId so init() is not triggered (which would reset status to idle)
      mockFetch([]);
      (refinementStore as any).handleEvent({ event: 'refinement_complete', type: 'refinement_complete' });
      expect(refinementStore.status).toBe('complete');
    });

    it('sets status to complete on optimization_complete event', () => {
      // Don't set optimizationId to avoid init() resetting status
      mockFetch([]);
      (refinementStore as any).handleEvent({ event: 'optimization_complete', type: 'optimization_complete' });
      expect(refinementStore.status).toBe('complete');
    });

    it('updates suggestions on suggestions event', () => {
      const suggestions = [{ text: 'Try adding examples', source: 'model' }];
      (refinementStore as any).handleEvent({ event: 'suggestions', suggestions, type: 'suggestions' });
      expect(refinementStore.suggestions).toEqual(suggestions);
    });

    it('updates suggestions from items key', () => {
      const items = [{ text: 'Be more specific', source: 'heuristic' }];
      (refinementStore as any).handleEvent({ event: 'suggestions', items, type: 'suggestions' });
      expect(refinementStore.suggestions).toEqual(items);
    });

    it('sets error and status on error event', () => {
      (refinementStore as any).handleEvent({ event: 'error', error: 'Something went wrong', type: 'error' });
      expect(refinementStore.error).toBe('Something went wrong');
      expect(refinementStore.status).toBe('error');
    });

    it('sets error from message key on error event', () => {
      (refinementStore as any).handleEvent({ event: 'error', message: 'Pipeline failed', type: 'error' });
      expect(refinementStore.error).toBe('Pipeline failed');
    });
  });

  describe('rollback', () => {
    it('creates branch fork and reloads versions', async () => {
      refinementStore.optimizationId = 'opt-1';
      const branch = mockRefinementBranch({ id: 'branch-fork-1' });
      const turn = mockRefinementTurn({ branch_id: 'branch-fork-1' });
      mockFetch([
        { match: '/refine/opt-1/rollback', response: branch },
        { match: '/refine/opt-1/versions', response: { optimization_id: 'opt-1', versions: [turn] } },
      ]);
      await refinementStore.rollback(1);
      expect(refinementStore.activeBranchId).toBe('branch-fork-1');
      expect(refinementStore.turns).toHaveLength(1);
      expect(refinementStore.suggestions).toHaveLength(0);
    });

    it('sets error when rollback fails', async () => {
      refinementStore.optimizationId = 'opt-1';
      mockFetch([
        { match: '/refine/opt-1/rollback', response: { detail: 'Version not found' }, status: 404 },
      ]);
      await refinementStore.rollback(99);
      expect(refinementStore.error).toBeTruthy();
    });

    it('does nothing when no optimizationId', async () => {
      const fetchMock = mockFetch([]);
      await refinementStore.rollback(1);
      expect(fetchMock).not.toHaveBeenCalled();
    });
  });

  describe('cancel', () => {
    it('sets status to idle and aborts controller', () => {
      (refinementStore as any).status = 'refining';
      refinementStore.cancel();
      expect(refinementStore.status).toBe('idle');
    });
  });

  describe('reset', () => {
    it('clears all state', () => {
      refinementStore.optimizationId = 'opt-1';
      refinementStore.turns = [mockRefinementTurn() as any];
      refinementStore.error = 'some error';
      (refinementStore as any).status = 'error';
      refinementStore.reset();
      expect(refinementStore.optimizationId).toBeNull();
      expect(refinementStore.turns).toHaveLength(0);
      expect(refinementStore.error).toBeNull();
      expect(refinementStore.status).toBe('idle');
    });
  });

  describe('scoreProgression', () => {
    it('returns average scores for each turn with scores', () => {
      refinementStore.turns = [
        mockRefinementTurn({ scores: { clarity: 8, specificity: 8, structure: 8, faithfulness: 8, conciseness: 8 } }) as any,
        mockRefinementTurn({ scores: { clarity: 9, specificity: 9, structure: 9, faithfulness: 9, conciseness: 9 } }) as any,
      ];
      const progression = refinementStore.scoreProgression;
      expect(progression).toHaveLength(2);
      expect(progression[0]).toBeCloseTo(8.0);
      expect(progression[1]).toBeCloseTo(9.0);
    });

    it('skips turns without scores', () => {
      refinementStore.turns = [
        mockRefinementTurn({ scores: null }) as any,
        mockRefinementTurn({ scores: { clarity: 7, specificity: 7, structure: 7, faithfulness: 7, conciseness: 7 } }) as any,
      ];
      expect(refinementStore.scoreProgression).toHaveLength(1);
    });

    it('returns empty array when no turns', () => {
      expect(refinementStore.scoreProgression).toHaveLength(0);
    });
  });

  describe('selectVersion', () => {
    it('sets selectedVersion', () => {
      const turn = mockRefinementTurn() as any;
      refinementStore.selectVersion(turn);
      expect(refinementStore.selectedVersion).toEqual(turn);
    });

    it('clears selectedVersion when null passed', () => {
      refinementStore.selectedVersion = mockRefinementTurn() as any;
      refinementStore.selectVersion(null);
      expect(refinementStore.selectedVersion).toBeNull();
    });
  });

  describe('currentVersion getter', () => {
    it('returns the last turn', () => {
      const turn1 = mockRefinementTurn({ version: 1 }) as any;
      const turn2 = mockRefinementTurn({ version: 2 }) as any;
      refinementStore.turns = [turn1, turn2];
      expect(refinementStore.currentVersion).toEqual(turn2);
    });

    it('returns null when no turns', () => {
      expect(refinementStore.currentVersion).toBeNull();
    });
  });
});
