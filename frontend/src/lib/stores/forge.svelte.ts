// frontend/src/lib/stores/forge.svelte.ts
import { optimizeSSE, getOptimization, submitFeedback as apiFeedback } from '$lib/api/client';
import type { OptimizationResult, DimensionScores, SSEEvent } from '$lib/api/client';

export type ForgeStatus = 'idle' | 'analyzing' | 'optimizing' | 'scoring' | 'complete' | 'error';

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

  private controller: AbortController | null = null;

  forge() {
    if (!this.prompt.trim()) return;

    this.status = 'analyzing';
    this.error = null;
    this.result = null;
    this.feedback = null;
    this.traceId = null;
    this.previewPrompt = null;
    this.scores = null;
    this.originalScores = null;
    this.scoreDeltas = null;

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
    );
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
    } else if (eventType === 'optimization_complete') {
      this.result = event as unknown as OptimizationResult;
      this.status = 'complete';
    } else if (eventType === 'error') {
      this.error = (event.error || event.message) as string;
      this.status = 'error';
    }
  }

  private async reconnect() {
    if (!this.traceId) return;
    const maxAttempts = 30; // 30 * 2s = 60s
    for (let i = 0; i < maxAttempts; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      try {
        const result = await getOptimization(this.traceId);
        if (result.status === 'completed') {
          this.result = result;
          this.status = 'complete';
          return;
        }
      } catch {
        /* keep polling */
      }
    }
    this.error = 'Optimization may still be running. Check history.';
    this.status = 'error';
  }

  cancel() {
    this.controller?.abort();
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

  reset() {
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
  }
}

export const forgeStore = new ForgeStore();
