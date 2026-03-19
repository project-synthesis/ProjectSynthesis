// frontend/src/lib/stores/forge.svelte.ts
import {
  optimizeSSE, getOptimization, submitFeedback as apiFeedback,
  preparePassthrough, savePassthrough,
} from '$lib/api/client';
import type { OptimizationResult, DimensionScores, SSEEvent } from '$lib/api/client';
import { editorStore } from '$lib/stores/editor.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { patternsStore } from '$lib/stores/patterns.svelte';

export type ForgeStatus = 'idle' | 'analyzing' | 'optimizing' | 'scoring' | 'complete' | 'error' | 'passthrough';

class ForgeStore {
  prompt = $state('');
  strategy = $state<string | null>(null);
  status = $state<ForgeStatus>('idle');
  result = $state<OptimizationResult | null>(null);
  traceId = $state<string | null>(null);
  error = $state<string | null>(null);
  feedback = $state<'thumbs_up' | 'thumbs_down' | null>(null);

  // Progress details
  currentPhase = $state<string | null>(null);
  previewPrompt = $state<string | null>(null);
  scores = $state<DimensionScores | null>(null);
  originalScores = $state<DimensionScores | null>(null);
  scoreDeltas = $state<Record<string, number> | null>(null);

  // Passthrough state
  assembledPrompt = $state<string | null>(null);
  passthroughTraceId = $state<string | null>(null);
  passthroughStrategy = $state<string | null>(null);

  // Initial suggestions from the pipeline (before any refinement turns exist)
  initialSuggestions = $state<Array<Record<string, string>>>([]);

  // Applied meta-pattern IDs from knowledge graph paste detection
  appliedPatternIds = $state<string[] | null>(null);

  /** Set by +page.svelte after health check — true when health.provider is null. */
  noProvider = $state(false);
  /** Set by +page.svelte after health check — null until health is fetched. */
  samplingCapable = $state<boolean | null>(null);

  private controller: AbortController | null = null;

  forge() {
    // Abort any in-flight SSE stream
    this.controller?.abort();
    this.controller = null;

    const trimmed = this.prompt.trim();
    if (!trimmed) return;
    if (trimmed.length < 20) {
      this.error = 'Prompt must be at least 20 characters.';
      this.status = 'error';
      return;
    }

    // Capture applied pattern IDs before clearing state
    const patternIds = this.appliedPatternIds;

    // Deselect pattern family so Inspector shows forge progress
    patternsStore.selectFamily(null);

    // Clear shared state
    this.error = null;
    this.result = null;
    this.feedback = null;
    this.traceId = null;
    this.previewPrompt = null;
    this.scores = null;
    this.originalScores = null;
    this.scoreDeltas = null;
    this.assembledPrompt = null;
    this.passthroughTraceId = null;
    this.passthroughStrategy = null;
    this.initialSuggestions = [];
    this.appliedPatternIds = null;

    // Passthrough mode — no provider, or force_passthrough preference enabled
    if (this.noProvider || preferencesStore.pipeline.force_passthrough) {
      this.status = 'passthrough';
      preparePassthrough(this.prompt, this.strategy)
        .then((res) => {
          this.assembledPrompt = res.assembled_prompt;
          this.passthroughTraceId = res.trace_id;
          this.passthroughStrategy = res.strategy_requested;
        })
        .catch((err) => {
          this.error = err.message;
          this.status = 'error';
        });
      return;
    }

    this.status = 'analyzing';

    this.controller = optimizeSSE(
      this.prompt,
      this.strategy,
      (event: SSEEvent) => this.handleEvent(event),
      (err: Error) => {
        this.error = err.message;
        this.status = 'error';
        // Attempt reconnection if we have a trace ID
        if (this.traceId) this.reconnect();
      },
      () => {
        if (this.status !== 'complete' && this.status !== 'error') {
          // Stream ended without complete event — try reconnection
          if (this.traceId) this.reconnect();
        }
      },
      patternIds,
    );
  }

  async submitPassthrough(optimizedPrompt: string, changesSummary?: string) {
    if (!this.passthroughTraceId) return;
    try {
      const result = await savePassthrough(
        this.passthroughTraceId, optimizedPrompt, changesSummary,
      );
      this.loadFromRecord(result);
    } catch (err: any) {
      this.error = err.message;
      this.status = 'error';
    }
  }

  private handleEvent(event: SSEEvent) {
    const eventType = event.event as string;

    if (eventType === 'optimization_start') {
      this.traceId = event.trace_id as string;
    } else if (eventType === 'status') {
      const phase = (event.phase || event.stage) as string;
      const state = (event.status || event.state) as string;
      if (state === 'running' || state === 'complete') {
        this.currentPhase = phase;
        if (phase === 'analyze' || phase === 'analyzing') this.status = 'analyzing';
        else if (phase === 'optimize' || phase === 'optimizing') this.status = 'optimizing';
        else if (phase === 'score' || phase === 'scoring') this.status = 'scoring';
      }
    } else if (eventType === 'prompt_preview') {
      this.previewPrompt = (event.optimized_prompt || event.prompt) as string;
    } else if (eventType === 'score_card') {
      this.scores = (event.optimized_scores as DimensionScores) || (event.scores as DimensionScores);
      this.originalScores = event.original_scores as DimensionScores;
      this.scoreDeltas = event.deltas as Record<string, number>;
    } else if (eventType === 'suggestions') {
      this.initialSuggestions = (event.suggestions || []) as Array<Record<string, string>>;
    } else if (eventType === 'optimization_complete') {
      const data = event as any;
      // Normalize: SSE sends optimized_scores, REST sends scores
      if (data.optimized_scores && !data.scores) {
        data.scores = data.optimized_scores;
      }
      // Capture suggestions from the complete event if not already set via SSE
      if (data.suggestions && this.initialSuggestions.length === 0) {
        this.initialSuggestions = data.suggestions;
      }
      this.loadFromRecord(data as OptimizationResult);
    } else if (eventType === 'error') {
      this.error = (event.error || event.message) as string;
      this.status = 'error';
    }
  }

  private _reconnecting = false;

  private async reconnect() {
    if (!this.traceId || this._reconnecting) return;
    this._reconnecting = true;
    try {
      const maxAttempts = 30; // 30 * 2s = 60s
      for (let i = 0; i < maxAttempts; i++) {
        if (this.status === 'idle' || this.status === 'complete') break; // cancelled or already resolved
        await new Promise((r) => setTimeout(r, 2000));
        try {
          const result = await getOptimization(this.traceId!);
          if (result.status === 'completed') {
            this.loadFromRecord(result);
            return;
          }
        } catch {
          /* keep polling */
        }
      }
      if (this.status !== 'complete' && this.status !== 'idle') {
        this.error = 'Optimization may still be running. Check history.';
        this.status = 'error';
      }
    } finally {
      this._reconnecting = false;
    }
  }

  loadFromRecord(opt: OptimizationResult): void {
    this.result = opt;
    this.prompt = opt.raw_prompt || '';
    this.status = 'complete';
    this.error = null;
    this.feedback = null;

    // Clear assembled prompt (no longer needed) but keep passthroughTraceId
    // alive so the SSE event filter in +page.svelte can suppress the self-toast.
    // passthroughTraceId is cleared on next forge()/reset()/cancel().
    this.assembledPrompt = null;
    this.passthroughStrategy = null;

    // Normalize: SSE sends optimized_scores, REST sends scores
    const scores = opt.scores ?? (opt as any).optimized_scores ?? null;
    if (scores) this.scores = scores;
    this.originalScores = opt.original_scores ?? null;
    this.scoreDeltas = opt.score_deltas ?? null;

    // Cache the result in the editor store so each result tab has its own data
    if (opt.id) {
      editorStore.cacheResult(opt.id, opt);
    }

    this._saveSession();
  }

  cancel() {
    this.controller?.abort();
    this.controller = null;
    this.traceId = null; // prevent reconnect from running
    this.assembledPrompt = null;
    this.passthroughTraceId = null;
    this.passthroughStrategy = null;
    this.status = 'idle';
  }

  async submitFeedback(rating: 'thumbs_up' | 'thumbs_down') {
    if (!this.result?.id) return;
    try {
      await apiFeedback(this.result.id, rating);
      this.feedback = rating;
    } catch (err) {
      console.error('Feedback failed:', err);
    }
  }

  private _saveSession(): void {
    if (this.result?.trace_id) {
      try {
        localStorage.setItem('synthesis:last_trace_id', this.result.trace_id);
      } catch { /* storage full or unavailable */ }
    }
  }

  async restoreSession(): Promise<void> {
    try {
      const traceId = localStorage.getItem('synthesis:last_trace_id');
      if (!traceId) return;
      const { getOptimization } = await import('$lib/api/client');
      const opt = await getOptimization(traceId);
      this.loadFromRecord(opt);
    } catch {
      // No valid session to restore — start fresh
      localStorage.removeItem('synthesis:last_trace_id');
    }
  }

  reset() {
    this.controller?.abort();
    this.controller = null;
    this.prompt = '';
    this.strategy = null;
    this.status = 'idle';
    this.result = null;
    this.traceId = null;
    this.error = null;
    this.feedback = null;
    this.currentPhase = null;
    this.previewPrompt = null;
    this.scores = null;
    this.originalScores = null;
    this.scoreDeltas = null;
    this.assembledPrompt = null;
    this.passthroughTraceId = null;
    this.passthroughStrategy = null;
    this.initialSuggestions = [];
    this.appliedPatternIds = null;
    patternsStore.resetTracking();
  }
}

export const forgeStore = new ForgeStore();
