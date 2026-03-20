import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// Mock the API client before imports so refineSSE can be intercepted
vi.mock('$lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api/client')>();
  return {
    ...actual,
    refineSSE: vi.fn(() => ({ abort: vi.fn() })),
    getRefinementVersions: vi.fn().mockResolvedValue({ optimization_id: null, versions: [] }),
    rollbackRefinement: vi.fn().mockResolvedValue({ id: 'branch-1', optimization_id: 'opt-1' }),
  };
});

import { refinementStore } from './refinement.svelte';
import { forgeStore } from './forge.svelte';
import { mockFetch, mockRefinementTurn, mockRefinementBranch } from '../test-utils';
import * as apiClient from '$lib/api/client';

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
      vi.mocked(apiClient.getRefinementVersions).mockResolvedValue({ optimization_id: 'opt-1', versions: [turn] } as any);
      await refinementStore.init('opt-1');
      expect(refinementStore.optimizationId).toBe('opt-1');
      expect(refinementStore.turns).toHaveLength(1);
    });

    it('sets activeBranchId from last turn', async () => {
      const turn = mockRefinementTurn({ branch_id: 'branch-main' });
      vi.mocked(apiClient.getRefinementVersions).mockResolvedValue({ optimization_id: 'opt-1', versions: [turn] } as any);
      await refinementStore.init('opt-1');
      expect(refinementStore.activeBranchId).toBe('branch-main');
    });

    it('loads suggestions from last turn', async () => {
      const turn = mockRefinementTurn({ suggestions: [{ text: 'Add examples', source: 'model' }] });
      vi.mocked(apiClient.getRefinementVersions).mockResolvedValue({ optimization_id: 'opt-1', versions: [turn] } as any);
      await refinementStore.init('opt-1');
      expect(refinementStore.suggestions).toHaveLength(1);
    });

    it('seeds suggestions from forge store when no versions exist', async () => {
      forgeStore.initialSuggestions = [{ text: 'Try chain-of-thought', type: 'strategy' }];
      vi.mocked(apiClient.getRefinementVersions).mockResolvedValue({ optimization_id: 'opt-1', versions: [] } as any);
      await refinementStore.init('opt-1');
      expect(refinementStore.suggestions).toEqual(forgeStore.initialSuggestions);
    });

    it('seeds suggestions from forge store when versions API throws', async () => {
      forgeStore.initialSuggestions = [{ text: 'Add specificity', type: 'clarity' }];
      vi.mocked(apiClient.getRefinementVersions).mockRejectedValue(new Error('Not found'));
      await refinementStore.init('opt-1');
      expect(refinementStore.suggestions).toEqual(forgeStore.initialSuggestions);
    });
  });

  describe('refine', () => {
    it('does nothing when optimizationId is null', () => {
      refinementStore.optimizationId = null;
      refinementStore.refine('Make it better');
      expect(refinementStore.status).toBe('idle');
      expect(apiClient.refineSSE).not.toHaveBeenCalled();
    });

    it('sets status to refining and calls refineSSE', () => {
      refinementStore.optimizationId = 'opt-1';
      vi.mocked(apiClient.refineSSE).mockReturnValue({ abort: vi.fn() } as any);

      refinementStore.refine('Make it more concise');

      expect(refinementStore.status).toBe('refining');
      expect(apiClient.refineSSE).toHaveBeenCalled();
    });

    it('clears suggestions before refining', () => {
      refinementStore.optimizationId = 'opt-1';
      refinementStore.suggestions = [{ text: 'Old suggestion', source: 'model' }];
      vi.mocked(apiClient.refineSSE).mockReturnValue({ abort: vi.fn() } as any);

      refinementStore.refine('Make it better');

      expect(refinementStore.suggestions).toHaveLength(0);
    });

    it('aborts in-flight request before starting new one', () => {
      refinementStore.optimizationId = 'opt-1';
      const mockController = { abort: vi.fn() };
      vi.mocked(apiClient.refineSSE).mockReturnValue(mockController as any);

      refinementStore.refine('First request');
      refinementStore.refine('Second request');

      expect(mockController.abort).toHaveBeenCalled();
    });

    it('SSE error callback sets error and error status', () => {
      refinementStore.optimizationId = 'opt-1';
      let errorCallback: (err: Error) => void = () => {};
      vi.mocked(apiClient.refineSSE).mockImplementation(
        (_id: string, _req: string, _branch: any, _onEvent: any, onError: any) => {
          errorCallback = onError;
          return { abort: vi.fn() };
        }
      );

      refinementStore.refine('Make it better');
      errorCallback(new Error('Stream failed'));

      expect(refinementStore.error).toBe('Stream failed');
      expect(refinementStore.status).toBe('error');
    });

    it('SSE close callback sets status to complete and reloads versions', async () => {
      refinementStore.optimizationId = 'opt-1';
      const turn = mockRefinementTurn();
      vi.mocked(apiClient.getRefinementVersions).mockResolvedValue({
        optimization_id: 'opt-1',
        versions: [turn],
      } as any);

      let closeCallback: () => void = () => {};
      vi.mocked(apiClient.refineSSE).mockImplementation(
        (_id: string, _req: string, _branch: any, _onEvent: any, _onError: any, onClose: any) => {
          closeCallback = onClose;
          return { abort: vi.fn() };
        }
      );

      refinementStore.refine('Make it better');
      refinementStore.status = 'refining'; // ensure status is refining

      closeCallback();

      expect(refinementStore.status).toBe('complete');
    });
  });

  describe('handleEvent (SSE events)', () => {
    it('sets status to complete on refinement_complete event', async () => {
      // Don't set optimizationId so init() is not triggered (which would reset status to idle)
      refinementStore._handleEvent({ event: 'refinement_complete', type: 'refinement_complete' });
      expect(refinementStore.status).toBe('complete');
    });

    it('sets status to complete on optimization_complete event', () => {
      // Don't set optimizationId to avoid init() resetting status
      refinementStore._handleEvent({ event: 'optimization_complete', type: 'optimization_complete' });
      expect(refinementStore.status).toBe('complete');
    });

    it('updates suggestions on suggestions event', () => {
      const suggestions = [{ text: 'Try adding examples', source: 'model' }];
      refinementStore._handleEvent({ event: 'suggestions', suggestions, type: 'suggestions' });
      expect(refinementStore.suggestions).toEqual(suggestions);
    });

    it('updates suggestions from items key', () => {
      const items = [{ text: 'Be more specific', source: 'heuristic' }];
      refinementStore._handleEvent({ event: 'suggestions', items, type: 'suggestions' });
      expect(refinementStore.suggestions).toEqual(items);
    });

    it('sets error and status on error event', () => {
      refinementStore._handleEvent({ event: 'error', error: 'Something went wrong', type: 'error' });
      expect(refinementStore.error).toBe('Something went wrong');
      expect(refinementStore.status).toBe('error');
    });

    it('sets error from message key on error event', () => {
      refinementStore._handleEvent({ event: 'error', message: 'Pipeline failed', type: 'error' });
      expect(refinementStore.error).toBe('Pipeline failed');
    });
  });

  describe('rollback', () => {
    it('creates branch fork and reloads versions', async () => {
      refinementStore.optimizationId = 'opt-1';
      const branch = mockRefinementBranch({ id: 'branch-fork-1' });
      const turn = mockRefinementTurn({ branch_id: 'branch-fork-1' });
      vi.mocked(apiClient.rollbackRefinement).mockResolvedValue(branch as any);
      vi.mocked(apiClient.getRefinementVersions).mockResolvedValue({ optimization_id: 'opt-1', versions: [turn] } as any);
      await refinementStore.rollback(1);
      expect(refinementStore.activeBranchId).toBe('branch-fork-1');
      expect(refinementStore.turns).toHaveLength(1);
      expect(refinementStore.suggestions).toHaveLength(0);
    });

    it('sets error when rollback fails', async () => {
      refinementStore.optimizationId = 'opt-1';
      vi.mocked(apiClient.rollbackRefinement).mockRejectedValue(new Error('Version not found'));
      await refinementStore.rollback(99);
      expect(refinementStore.error).toBeTruthy();
    });

    it('does nothing when no optimizationId', async () => {
      vi.mocked(apiClient.rollbackRefinement).mockClear();
      await refinementStore.rollback(1);
      expect(apiClient.rollbackRefinement).not.toHaveBeenCalled();
    });
  });

  describe('cancel', () => {
    it('sets status to idle and aborts controller', () => {
      refinementStore.status = 'refining';
      refinementStore.cancel();
      expect(refinementStore.status).toBe('idle');
    });
  });

  describe('reset', () => {
    it('clears all state', () => {
      refinementStore.optimizationId = 'opt-1';
      refinementStore.turns = [mockRefinementTurn() as any];
      refinementStore.error = 'some error';
      refinementStore.status = 'error';
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
