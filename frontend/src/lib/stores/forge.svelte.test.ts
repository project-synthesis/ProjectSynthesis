import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';

// Mock $lib/api/client before any imports that use it
vi.mock('$lib/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/api/client')>();
  return {
    ...actual,
    optimizeSSE: vi.fn(() => ({ abort: vi.fn() })),
    getOptimization: vi.fn().mockResolvedValue(null),
    submitFeedback: vi.fn().mockResolvedValue({}),
    savePassthrough: vi.fn().mockResolvedValue({}),
  };
});

import { forgeStore } from './forge.svelte';
import { editorStore } from './editor.svelte';
import { patternsStore } from './patterns.svelte';
import { mockFetch, mockOptimizationResult, mockDimensionScores } from '../test-utils';
import type { SSEEvent } from '$lib/api/client';
import * as apiClient from '$lib/api/client';

describe('ForgeStore', () => {
  beforeEach(() => {
    forgeStore._reset();
    editorStore._reset();
    patternsStore._reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('starts with idle status and empty state', () => {
    expect(forgeStore.status).toBe('idle');
    expect(forgeStore.prompt).toBe('');
    expect(forgeStore.strategy).toBeNull();
    expect(forgeStore.result).toBeNull();
    expect(forgeStore.error).toBeNull();
    expect(forgeStore.traceId).toBeNull();
    expect(forgeStore.feedback).toBeNull();
    expect(forgeStore.samplingCapable).toBeNull();
    expect(forgeStore.mcpDisconnected).toBe(false);
  });

  describe('handleEvent — routing event', () => {
    it('sets routingDecision from routing event', () => {
      forgeStore._handleEvent({
        event: 'routing',
        tier: 'internal',
        provider: 'claude-cli',
        reason: 'Internal provider available',
        degraded_from: null,
      } as SSEEvent);
      expect(forgeStore.routingDecision).not.toBeNull();
      expect(forgeStore.routingDecision?.tier).toBe('internal');
      expect(forgeStore.routingDecision?.provider).toBe('claude-cli');
      expect(forgeStore.routingDecision?.reason).toBe('Internal provider available');
    });

    it('sets degraded_from from routing event', () => {
      forgeStore._handleEvent({
        event: 'routing',
        tier: 'passthrough',
        provider: null,
        reason: 'Degraded',
        degraded_from: 'sampling',
      } as SSEEvent);
      expect(forgeStore.routingDecision?.degraded_from).toBe('sampling');
    });
  });

  describe('handleEvent — passthrough event', () => {
    it('sets assembledPrompt, passthroughTraceId, strategy, and passthrough status', () => {
      forgeStore._handleEvent({
        event: 'passthrough',
        assembled_prompt: 'Assembled content here',
        trace_id: 'pt-trace-1',
        strategy: 'chain-of-thought',
      } as SSEEvent);
      expect(forgeStore.assembledPrompt).toBe('Assembled content here');
      expect(forgeStore.passthroughTraceId).toBe('pt-trace-1');
      expect(forgeStore.passthroughStrategy).toBe('chain-of-thought');
      expect(forgeStore.status).toBe('passthrough');
    });
  });

  describe('handleEvent — phase/status events', () => {
    it('sets status to analyzing on analyze phase', () => {
      forgeStore._handleEvent({
        event: 'status',
        phase: 'analyze',
        status: 'running',
      } as SSEEvent);
      expect(forgeStore.status).toBe('analyzing');
    });

    it('sets status to optimizing on optimize phase', () => {
      forgeStore._handleEvent({
        event: 'status',
        phase: 'optimize',
        status: 'running',
      } as SSEEvent);
      expect(forgeStore.status).toBe('optimizing');
    });

    it('sets status to scoring on score phase', () => {
      forgeStore._handleEvent({
        event: 'status',
        phase: 'score',
        status: 'running',
      } as SSEEvent);
      expect(forgeStore.status).toBe('scoring');
    });

    it('handles stage key alias for phase', () => {
      forgeStore._handleEvent({
        event: 'status',
        stage: 'analyzing',
        state: 'running',
      } as SSEEvent);
      expect(forgeStore.status).toBe('analyzing');
    });
  });

  describe('handleEvent — preview event', () => {
    it('sets previewPrompt from optimized_prompt', () => {
      forgeStore._handleEvent({
        event: 'prompt_preview',
        optimized_prompt: 'Preview of optimized output',
      } as SSEEvent);
      expect(forgeStore.previewPrompt).toBe('Preview of optimized output');
    });

    it('sets previewPrompt from prompt key alias', () => {
      forgeStore._handleEvent({
        event: 'prompt_preview',
        prompt: 'Preview from prompt key',
      } as SSEEvent);
      expect(forgeStore.previewPrompt).toBe('Preview from prompt key');
    });
  });

  describe('handleEvent — analysis/score_card event', () => {
    it('sets scores, originalScores, and scoreDeltas from score_card event', () => {
      const scores = mockDimensionScores();
      const originalScores = mockDimensionScores({ clarity: 5.0 });
      const deltas = { clarity: 2.5, specificity: 0, structure: 0, faithfulness: 0, conciseness: 0 };
      forgeStore._handleEvent({
        event: 'score_card',
        optimized_scores: scores,
        original_scores: originalScores,
        deltas,
      } as SSEEvent);
      expect(forgeStore.scores).toEqual(scores);
      expect(forgeStore.originalScores).toEqual(originalScores);
      expect(forgeStore.scoreDeltas).toEqual(deltas);
    });

    it('also accepts scores key (not optimized_scores)', () => {
      const scores = mockDimensionScores();
      forgeStore._handleEvent({
        event: 'score_card',
        scores,
        original_scores: null,
        deltas: null,
      } as SSEEvent);
      expect(forgeStore.scores).toEqual(scores);
    });
  });

  describe('handleEvent — suggestions event', () => {
    it('sets initialSuggestions from suggestions event', () => {
      const suggestions = [{ text: 'Add examples', type: 'clarity' }];
      forgeStore._handleEvent({
        event: 'suggestions',
        suggestions,
      } as SSEEvent);
      expect(forgeStore.initialSuggestions).toEqual(suggestions);
    });
  });

  describe('handleEvent — result (optimization_complete) event', () => {
    it('sets result, status to complete, and caches in editorStore', () => {
      const result = mockOptimizationResult({ id: 'opt-complete-1' });
      forgeStore._handleEvent({
        event: 'optimization_complete',
        ...result,
        optimized_scores: result.scores,
      });
      expect(forgeStore.status).toBe('complete');
      expect(forgeStore.result).not.toBeNull();
      expect(editorStore.getResult('opt-complete-1')).not.toBeNull();
    });

    it('normalizes optimized_scores to scores', () => {
      const scores = mockDimensionScores({ clarity: 9.0 });
      const result = mockOptimizationResult({ id: 'opt-normalize-1', scores });
      forgeStore._handleEvent({
        event: 'optimization_complete',
        ...result,
        optimized_scores: scores,
        scores: undefined,
      });
      expect(forgeStore.scores).toEqual(scores);
    });

    it('captures suggestions from complete event if not already set', () => {
      const suggestions = [{ text: 'From complete event', type: 'strategy' }];
      const result = mockOptimizationResult({ id: 'opt-2' });
      forgeStore._handleEvent({
        event: 'optimization_complete',
        ...result,
        suggestions,
      });
      expect(forgeStore.initialSuggestions).toEqual(suggestions);
    });

    it('does not override suggestions already set via SSE', () => {
      forgeStore.initialSuggestions = [{ text: 'Already set', type: 'clarity' }];
      const result = mockOptimizationResult({ id: 'opt-3' });
      forgeStore._handleEvent({
        event: 'optimization_complete',
        ...result,
        suggestions: [{ text: 'From complete event', type: 'strategy' }],
      });
      expect(forgeStore.initialSuggestions[0].text).toBe('Already set');
    });
  });

  describe('handleEvent — error event', () => {
    it('sets error and status to error from error key', () => {
      forgeStore._handleEvent({
        event: 'error',
        error: 'Pipeline failed',
      } as SSEEvent);
      expect(forgeStore.error).toBe('Pipeline failed');
      expect(forgeStore.status).toBe('error');
    });

    it('sets error from message key', () => {
      forgeStore._handleEvent({
        event: 'error',
        message: 'Something went wrong',
      } as SSEEvent);
      expect(forgeStore.error).toBe('Something went wrong');
    });
  });

  describe('loadFromRecord', () => {
    it('hydrates all state from optimization result', () => {
      const result = mockOptimizationResult({ id: 'opt-load-1', family_id: null });
      forgeStore.loadFromRecord(result as any);
      expect(forgeStore.result).toEqual(result);
      expect(forgeStore.status).toBe('complete');
      expect(forgeStore.prompt).toBe(result.raw_prompt);
      expect(forgeStore.error).toBeNull();
      expect(forgeStore.feedback).toBeNull();
    });

    it('sets scores from result.scores', () => {
      const result = mockOptimizationResult({ id: 'opt-scores-1' });
      forgeStore.loadFromRecord(result as any);
      expect(forgeStore.scores).toEqual(result.scores);
    });

    it('calls editorStore.cacheResult with opt id', () => {
      const cacheResult = vi.spyOn(editorStore, 'cacheResult');
      const result = mockOptimizationResult({ id: 'opt-cache-1' });
      forgeStore.loadFromRecord(result as any);
      expect(cacheResult).toHaveBeenCalledWith('opt-cache-1', result);
    });

    it('selects family in patternsStore when family_id set', () => {
      const fetchMock = mockFetch([
        { match: '/patterns/families/fam-1', response: { id: 'fam-1', intent_label: 'API patterns', domain: 'backend', task_type: 'coding', usage_count: 3, member_count: 2, avg_score: 8.0, created_at: null, updated_at: null, meta_patterns: [], optimizations: [] } },
      ]);
      const result = mockOptimizationResult({ id: 'opt-family-1', family_id: 'fam-1' });
      forgeStore.loadFromRecord(result as any);
      expect(patternsStore.selectedFamilyId).toBe('fam-1');
    });

    it('clears assembledPrompt and passthroughStrategy', () => {
      forgeStore.assembledPrompt = 'Assembled content';
      forgeStore.passthroughStrategy = 'chain-of-thought';
      const result = mockOptimizationResult({ id: 'opt-clear-1' });
      forgeStore.loadFromRecord(result as any);
      expect(forgeStore.assembledPrompt).toBeNull();
      expect(forgeStore.passthroughStrategy).toBeNull();
    });

    it('saves trace_id to localStorage', () => {
      const setItem = vi.spyOn(Storage.prototype, 'setItem');
      const result = mockOptimizationResult({ id: 'opt-session-1', trace_id: 'trace-session-1' });
      forgeStore.loadFromRecord(result as any);
      expect(setItem).toHaveBeenCalledWith('synthesis:last_trace_id', 'trace-session-1');
    });
  });

  describe('submitFeedback', () => {
    it('sends API call and updates feedback state', async () => {
      const result = mockOptimizationResult({ id: 'opt-fb-1' });
      forgeStore.result = result as any;
      mockFetch([
        { match: '/feedback', response: { id: 'fb-1', optimization_id: 'opt-fb-1', rating: 'thumbs_up', comment: null, created_at: '2026-03-20' } },
      ]);
      await forgeStore.submitFeedback('thumbs_up');
      expect(forgeStore.feedback).toBe('thumbs_up');
    });

    it('sets thumbs_down feedback', async () => {
      forgeStore.result = mockOptimizationResult({ id: 'opt-fb-2' }) as any;
      mockFetch([
        { match: '/feedback', response: { id: 'fb-2', optimization_id: 'opt-fb-2', rating: 'thumbs_down', comment: null, created_at: '2026-03-20' } },
      ]);
      await forgeStore.submitFeedback('thumbs_down');
      expect(forgeStore.feedback).toBe('thumbs_down');
    });

    it('does nothing when result is null', async () => {
      const fetchMock = mockFetch([]);
      forgeStore.result = null;
      await forgeStore.submitFeedback('thumbs_up');
      expect(fetchMock).not.toHaveBeenCalled();
    });
  });

  describe('reset', () => {
    it('clears all state', () => {
      forgeStore.prompt = 'Some prompt';
      forgeStore.result = mockOptimizationResult() as any;
      forgeStore.status = 'complete';
      forgeStore.error = 'some error';
      forgeStore.scores = mockDimensionScores() as any;
      forgeStore.reset();
      expect(forgeStore.prompt).toBe('');
      expect(forgeStore.result).toBeNull();
      expect(forgeStore.status).toBe('idle');
      expect(forgeStore.error).toBeNull();
      expect(forgeStore.scores).toBeNull();
    });

    it('calls patternsStore.resetTracking()', () => {
      const resetTracking = vi.spyOn(patternsStore, 'resetTracking');
      forgeStore.reset();
      expect(resetTracking).toHaveBeenCalledOnce();
    });

    it('clears passthrough and routing state', () => {
      forgeStore.assembledPrompt = 'Assembled';
      forgeStore.passthroughTraceId = 'pt-1';
      forgeStore.passthroughStrategy = 'chain-of-thought';
      forgeStore.routingDecision = { tier: 'internal', provider: 'cli', reason: 'test', degraded_from: null };
      forgeStore.appliedPatternIds = ['mp-1'];
      forgeStore.familyId = 'fam-1';
      forgeStore.reset();
      expect(forgeStore.assembledPrompt).toBeNull();
      expect(forgeStore.passthroughTraceId).toBeNull();
      expect(forgeStore.passthroughStrategy).toBeNull();
      expect(forgeStore.routingDecision).toBeNull();
      expect(forgeStore.appliedPatternIds).toBeNull();
      expect(forgeStore.familyId).toBeNull();
    });
  });

  describe('_saveSession', () => {
    it('writes traceId to localStorage', () => {
      const setItem = vi.spyOn(Storage.prototype, 'setItem');
      const result = mockOptimizationResult({ trace_id: 'trace-save-1' });
      forgeStore.loadFromRecord(result as any);
      expect(setItem).toHaveBeenCalledWith('synthesis:last_trace_id', 'trace-save-1');
    });

    it('does not throw when localStorage is unavailable', () => {
      vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
        throw new Error('QuotaExceededError');
      });
      const result = mockOptimizationResult({ trace_id: 'trace-full' });
      // Should not throw
      expect(() => forgeStore.loadFromRecord(result as any)).not.toThrow();
    });
  });

  describe('forge input validation', () => {
    it('sets error when prompt is too short', () => {
      forgeStore.prompt = 'Short';
      forgeStore.forge();
      expect(forgeStore.error).toBeTruthy();
      expect(forgeStore.status).toBe('error');
    });

    it('does nothing when prompt is empty', () => {
      forgeStore.prompt = '';
      forgeStore.forge();
      expect(forgeStore.status).toBe('idle');
    });
  });

  describe('forge() — SSE call', () => {
    it('calls optimizeSSE and sets status to analyzing when prompt is valid', () => {
      const mockController = { abort: vi.fn() };
      vi.mocked(apiClient.optimizeSSE).mockReturnValue(mockController);

      forgeStore.prompt = 'This is a valid prompt with more than 20 characters for testing';
      forgeStore.forge();

      expect(forgeStore.status).toBe('analyzing');
      expect(apiClient.optimizeSSE).toHaveBeenCalled();
    });

    it('aborts in-flight request when forge is called again', () => {
      const mockController = { abort: vi.fn() };
      vi.mocked(apiClient.optimizeSSE).mockReturnValue(mockController);

      forgeStore.prompt = 'This is a valid prompt with more than 20 characters';
      forgeStore.forge();
      forgeStore.forge();

      expect(mockController.abort).toHaveBeenCalled();
    });

    it('deselects pattern family before forging', () => {
      vi.mocked(apiClient.optimizeSSE).mockReturnValue({ abort: vi.fn() });

      patternsStore.selectedFamilyId = 'fam-existing';
      forgeStore.prompt = 'This is a valid prompt with more than 20 characters';
      forgeStore.forge();

      expect(patternsStore.selectedFamilyId).toBeNull();
    });

    it('clears all state before forging', () => {
      vi.mocked(apiClient.optimizeSSE).mockReturnValue({ abort: vi.fn() });

      forgeStore.error = 'Previous error';
      forgeStore.result = mockOptimizationResult() as any;
      forgeStore.traceId = 'old-trace';
      forgeStore.routingDecision = { tier: 'internal', provider: 'claude-cli', reason: 'test', degraded_from: null };

      forgeStore.prompt = 'This is a valid prompt with more than 20 characters';
      forgeStore.forge();

      expect(forgeStore.error).toBeNull();
      expect(forgeStore.result).toBeNull();
      expect(forgeStore.traceId).toBeNull();
      expect(forgeStore.routingDecision).toBeNull();
    });

    it('passes appliedPatternIds to optimizeSSE (captured before clearing)', () => {
      vi.mocked(apiClient.optimizeSSE).mockClear();
      vi.mocked(apiClient.optimizeSSE).mockReturnValue({ abort: vi.fn() });

      forgeStore.appliedPatternIds = ['mp-1', 'mp-2'];
      forgeStore.prompt = 'This is a valid prompt with more than 20 characters';
      forgeStore.forge();

      // optimizeSSE was called — the 6th arg is the patternIds array captured before clearing
      expect(apiClient.optimizeSSE).toHaveBeenCalledOnce();
      const callArgs = vi.mocked(apiClient.optimizeSSE).mock.calls[0];
      expect(callArgs[5]).toEqual(['mp-1', 'mp-2']);
    });

    it('SSE error callback sets error status', () => {
      vi.mocked(apiClient.getOptimization).mockRejectedValue(new Error('Not found'));

      let errorCallback: (err: Error) => void = () => {};
      vi.mocked(apiClient.optimizeSSE).mockImplementation(
        (_p: string, _s: any, _onEvent: any, onError: any) => {
          errorCallback = onError;
          return { abort: vi.fn() };
        }
      );

      forgeStore.prompt = 'This is a valid prompt with more than 20 characters';
      forgeStore.forge();

      errorCallback(new Error('Connection dropped'));

      expect(forgeStore.error).toBe('Connection dropped');
      expect(forgeStore.status).toBe('error');
    });

    it('SSE close callback without complete event leaves status until reconnect', () => {
      vi.mocked(apiClient.getOptimization).mockResolvedValue({ status: 'completed', ...mockOptimizationResult() } as any);

      let closeCallback: () => void = () => {};
      vi.mocked(apiClient.optimizeSSE).mockImplementation(
        (_p: string, _s: any, _onEvent: any, _onError: any, onClose: any) => {
          closeCallback = onClose;
          return { abort: vi.fn() };
        }
      );

      forgeStore.prompt = 'This is a valid prompt with more than 20 characters';
      forgeStore.forge();
      forgeStore.traceId = 'trace-close-1';
      forgeStore.status = 'analyzing';

      // Close without complete event — reconnect should be triggered
      closeCallback();

      expect(forgeStore.traceId).toBe('trace-close-1');
    });
  });

  describe('submitPassthrough', () => {
    it('does nothing when passthroughTraceId is null', async () => {
      forgeStore.passthroughTraceId = null;
      // Should not throw
      await forgeStore.submitPassthrough('some optimized prompt');
      expect(forgeStore.status).toBe('idle');
    });

    it('calls savePassthrough and loads result on success', async () => {
      const result = mockOptimizationResult({ id: 'opt-pt-1' });
      vi.mocked(apiClient.savePassthrough).mockResolvedValue(result as any);

      forgeStore.passthroughTraceId = 'pt-trace-1';
      await forgeStore.submitPassthrough('Optimized content', 'Summary here');

      expect(forgeStore.result).not.toBeNull();
      expect(forgeStore.status).toBe('complete');
    });

    it('sets error on savePassthrough failure', async () => {
      vi.mocked(apiClient.savePassthrough).mockRejectedValue(new Error('Passthrough save failed'));

      forgeStore.passthroughTraceId = 'pt-trace-fail';
      await forgeStore.submitPassthrough('Some content');

      expect(forgeStore.status).toBe('error');
      expect(forgeStore.error).toBeTruthy();
    });
  });

  describe('restoreSession', () => {
    it('restores session from localStorage trace_id', async () => {
      const result = mockOptimizationResult({ id: 'opt-restore-1', trace_id: 'trace-restore-1' });
      vi.spyOn(Storage.prototype, 'getItem').mockReturnValue('trace-restore-1');
      vi.mocked(apiClient.getOptimization).mockResolvedValue(result as any);

      await forgeStore.restoreSession();

      expect(forgeStore.result).not.toBeNull();
    });

    it('does nothing when no trace_id in localStorage', async () => {
      vi.spyOn(Storage.prototype, 'getItem').mockReturnValue(null);
      vi.mocked(apiClient.getOptimization).mockClear();

      await forgeStore.restoreSession();

      expect(apiClient.getOptimization).not.toHaveBeenCalled();
    });

    it('removes invalid trace_id from localStorage on error', async () => {
      vi.spyOn(Storage.prototype, 'getItem').mockReturnValue('bad-trace');
      const removeItem = vi.spyOn(Storage.prototype, 'removeItem');
      vi.mocked(apiClient.getOptimization).mockRejectedValue(new Error('Not found'));

      await forgeStore.restoreSession();

      expect(removeItem).toHaveBeenCalledWith('synthesis:last_trace_id');
    });
  });

  describe('cancel', () => {
    it('sets status to idle and clears state', () => {
      forgeStore.traceId = 'trace-1';
      forgeStore.assembledPrompt = 'Assembled';
      forgeStore.passthroughTraceId = 'pt-trace-1';
      forgeStore.routingDecision = { tier: 'internal', provider: 'claude-cli', reason: 'test', degraded_from: null };
      forgeStore.cancel();
      expect(forgeStore.status).toBe('idle');
      expect(forgeStore.traceId).toBeNull();
      expect(forgeStore.assembledPrompt).toBeNull();
      expect(forgeStore.passthroughTraceId).toBeNull();
      expect(forgeStore.routingDecision).toBeNull();
    });
  });

  describe('handleEvent — optimization_start event', () => {
    it('sets traceId from optimization_start event', () => {
      forgeStore._handleEvent({
        event: 'optimization_start',
        trace_id: 'trace-start-1',
      } as SSEEvent);
      expect(forgeStore.traceId).toBe('trace-start-1');
    });
  });
});
