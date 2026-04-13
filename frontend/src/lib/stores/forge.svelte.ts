// frontend/src/lib/stores/forge.svelte.ts
import {
  optimizeSSE, getOptimization, submitFeedback as apiFeedback,
  savePassthrough,
} from '$lib/api/client';
import type { OptimizationResult, DimensionScores, SSEEvent } from '$lib/api/client';
import { editorStore } from '$lib/stores/editor.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { githubStore } from '$lib/stores/github.svelte';
import { addToast } from '$lib/stores/toast.svelte';

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

  // Applied meta-pattern IDs from pattern detection or template selection
  appliedPatternIds = $state<string[] | null>(null);
  appliedPatternLabel = $state<string | null>(null);

  // Cluster link (set when optimization is linked to a prompt cluster)
  clusterId = $state<string | null>(null);

  phaseModels: Record<string, string> = $state({});

  /** Timestamp (ms) when the current synthesis started — drives elapsed timer. */
  synthesisStartedAt = $state<number | null>(null);

  /** Routing decision from the backend's first SSE event per optimize stream. */
  routingDecision = $state<{ tier: string; provider: string | null; reason: string; degraded_from: string | null } | null>(null);

  /** Set by +page.svelte after health check — null until health is fetched. */
  samplingCapable = $state<boolean | null>(null);
  /** Set by +page.svelte — true when health reports MCP activity gap (disconnected). */
  mcpDisconnected = $state(false);

  /** Provider name from health polling / routing SSE events. */
  provider = $state<string | null>(null);
  /** Backend version from health polling. */
  version = $state<string | null>(null);
  /** Recent error counts from health polling. */
  recentErrors = $state<{ last_hour: number; last_24h: number } | null>(null);
  /** Average pipeline duration in ms from health polling. */
  avgDurationMs = $state<number | null>(null);
  /** Score distribution health from health polling. */
  scoreHealth = $state<{ last_n_mean: number; last_n_stddev: number; count: number; clustering_warning: boolean } | null>(null);
  /** Per-phase average durations in ms from health polling. */
  phaseDurations = $state<Record<string, number> | null>(null);
  /** Domain node count from health polling. */
  domainCount = $state<number | null>(null);
  /** Domain ceiling (max allowed domains) from health polling. */
  domainCeiling = $state<number | null>(null);

  /** Canonical routing state updater — both SSE and health poll MUST use this. */
  updateRoutingState(input: {
    sampling_capable: boolean | null;
    mcp_disconnected: boolean;
    provider?: string | null;
    version?: string | null;
  }): { samplingChanged: boolean; reconnected: boolean; disconnected: boolean } {
    const prev = this.samplingCapable;
    const wasDc = this.mcpDisconnected;
    this.samplingCapable = input.sampling_capable;
    this.mcpDisconnected = input.mcp_disconnected;
    if (input.provider !== undefined) this.provider = input.provider;
    if (input.version !== undefined) this.version = input.version;
    return {
      samplingChanged: prev !== true && input.sampling_capable === true,
      reconnected: wasDc && !input.mcp_disconnected,
      disconnected: !wasDc && input.mcp_disconnected,
    };
  }

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
    clustersStore.selectCluster(null);

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
    this.appliedPatternLabel = null;
    this.clusterId = null;
    this.routingDecision = null;
    this.phaseModels = {};

    this.status = 'analyzing';
    this.synthesisStartedAt = Date.now();

    this.controller = optimizeSSE(
      this.prompt,
      this.strategy,
      (event: SSEEvent) => this.handleEvent(event),
      (err: Error) => {
        this.synthesisStartedAt = null;
        // Internal pipeline: attempt reconnection via trace ID polling.
        if (this.traceId && !this.passthroughTraceId) {
          this.error = err.message;
          this.status = 'error';
          this.reconnect();
        } else {
          this.error = err.message;
          this.status = 'error';
        }
      },
      () => {
        if (this.status !== 'complete' && this.status !== 'error') {
          if (this.status === 'passthrough') return;
          if (this.traceId) {
            this.reconnect();
          } else if (this.passthroughTraceId) {
            this.error = 'Connection lost during passthrough setup. Please try again.';
            this.status = 'error';
          }
        }
      },
      patternIds,
      githubStore.linkedRepo?.full_name,
    );
  }

  async submitPassthrough(optimizedPrompt: string, changesSummary?: string) {
    if (!this.passthroughTraceId) return;
    try {
      const result = await savePassthrough(
        this.passthroughTraceId, optimizedPrompt, changesSummary,
      );
      this.loadFromRecord(result);
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Passthrough save failed';
      this.status = 'error';
    }
  }

  private handleEvent(event: SSEEvent) {
    const eventType = event.event as string;

    if (eventType === 'routing') {
      this.routingDecision = {
        tier: event.tier as string,
        provider: (event.provider ?? null) as string | null,
        reason: event.reason as string,
        degraded_from: (event.degraded_from ?? null) as string | null,
      };
      if (this.routingDecision.degraded_from === 'sampling') {
        addToast('modified', `Sampling unavailable from browser \u2014 using ${this.routingDecision.tier}`);
      }
      return;
    }
    if (eventType === 'passthrough') {
      this.assembledPrompt = event.assembled_prompt as string;
      this.passthroughTraceId = event.trace_id as string;
      this.passthroughStrategy = event.strategy as string;
      this.status = 'passthrough';
      this._saveSession();
      return;
    }

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
        const model = event.model as string | undefined;
        if (model && phase) {
          this.phaseModels = { ...this.phaseModels, [phase]: model };
        }
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
      if (data.models_by_phase) {
        this.phaseModels = data.models_by_phase as Record<string, string>;
      }
      this.loadFromRecord(data as OptimizationResult);
      // Auto-switch to editor panel (only fires from user's own SSE stream)
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
      }
    } else if (eventType === 'context_injected') {
      const d = event as unknown as { patterns?: number };
      addToast('created', `${d.patterns ?? 0} patterns auto-injected`);
    } else if (eventType === 'error') {
      this.error = (event.error || event.message) as string;
      this.synthesisStartedAt = null;
      this.status = 'error';
    }
  }

  /**
   * Handle SSE events originating from the global event bus (MCP-triggered
   * optimizations forwarded by +page.svelte).  Maps external event type
   * strings to the internal handleEvent format, guarding against
   * cross-contamination with an active user-initiated forge session.
   */
  handleExternalEvent(type: string, data: Record<string, unknown>): void {
    // optimization_start always applies — it sets the trace for subsequent events
    if (type === 'optimization_start') {
      this.handleEvent({ event: 'optimization_start', trace_id: data.trace_id } as SSEEvent);
      return;
    }

    // Guard: don't overwrite a user-initiated forge session with MCP events
    // targeting a different trace.  When traceId is null (idle), accept all.
    if (this.traceId && data.trace_id && this.traceId !== data.trace_id) {
      return;
    }

    if (type === 'optimization_status') {
      this.handleEvent({
        event: 'status',
        phase: data.phase,
        status: data.status || data.state,
        model: data.model,
      } as SSEEvent);
    } else if (type === 'optimization_score_card') {
      this.handleEvent({
        event: 'score_card',
        optimized_scores: data.optimized_scores,
        original_scores: data.original_scores,
        deltas: data.deltas,
      } as SSEEvent);
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
    this.synthesisStartedAt = null;
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

    // Restore suggestions so refinement store can seed from them on page reload.
    // Guard: don't overwrite if already set via SSE during a live stream.
    if (opt.suggestions?.length && this.initialSuggestions.length === 0) {
      this.initialSuggestions = opt.suggestions;
    }

    // Bidirectional family link — auto-select in patterns store so Inspector shows family detail.
    // Always call selectCluster (even with null) to clear stale Inspector state
    // from a previous optimization that had a different cluster.
    // Guard: only select if the cluster exists in the current tree (avoids 404
    // on startup when restoring a session whose cluster was deleted by recluster).
    this.clusterId = opt.cluster_id ?? null;
    if (this.clusterId) {
      const exists = clustersStore.taxonomyTree.some(n => n.id === this.clusterId);
      if (exists) {
        clustersStore.selectCluster(this.clusterId);
      } else {
        clustersStore.selectCluster(null);
      }
    } else {
      clustersStore.selectCluster(null);
    }

    // Cache the result in the editor store so each result tab has its own data
    if (opt.id) {
      editorStore.cacheResult(opt.id, opt);
    }

    if (opt.models_by_phase) {
      this.phaseModels = opt.models_by_phase;
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
    this.clusterId = null;
    this.routingDecision = null;
    this.synthesisStartedAt = null;
    this.status = 'idle';
  }

  async submitFeedback(rating: 'thumbs_up' | 'thumbs_down') {
    if (!this.result?.id) return;
    try {
      await apiFeedback(this.result.id, rating);
      this.feedback = rating;
      editorStore.cacheFeedback(this.result.id, rating);
    } catch (err) {
      console.error('Feedback failed:', err);
      addToast('deleted', 'Feedback could not be saved');
    }
  }

  private _saveSession(): void {
    try {
      if (this.result?.trace_id) {
        localStorage.setItem('synthesis:last_trace_id', this.result.trace_id);
        localStorage.removeItem('synthesis:passthrough_state');
      } else if (this.passthroughTraceId) {
        localStorage.setItem('synthesis:passthrough_state', JSON.stringify({
          traceId: this.passthroughTraceId,
          assembledPrompt: this.assembledPrompt,
          strategy: this.passthroughStrategy,
          prompt: this.prompt,
        }));
      }
    } catch { /* storage full or unavailable */ }
  }

  async restoreSession(): Promise<void> {
    try {
      // Try completed session first
      const traceId = localStorage.getItem('synthesis:last_trace_id');
      if (traceId) {
        const { getOptimization } = await import('$lib/api/client');
        const opt = await getOptimization(traceId);
        this.loadFromRecord(opt);
        editorStore.openResult(opt.id);
        return;
      }

      // Try passthrough session
      const ptRaw = localStorage.getItem('synthesis:passthrough_state');
      if (ptRaw) {
        const pt = JSON.parse(ptRaw) as {
          traceId: string; assembledPrompt: string | null;
          strategy: string | null; prompt: string;
        };
        // Check if it was completed while we were away
        try {
          const { getOptimization } = await import('$lib/api/client');
          const opt = await getOptimization(pt.traceId);
          if (opt.status === 'completed') {
            this.loadFromRecord(opt);
            editorStore.openResult(opt.id);
            localStorage.removeItem('synthesis:passthrough_state');
            return;
          }
        } catch { /* still pending — restore passthrough state */ }

        this.prompt = pt.prompt || '';
        this.passthroughTraceId = pt.traceId;
        this.assembledPrompt = pt.assembledPrompt;
        this.passthroughStrategy = pt.strategy;
        this.status = 'passthrough';
      }
    } catch {
      // No valid session to restore — start fresh
      localStorage.removeItem('synthesis:last_trace_id');
      localStorage.removeItem('synthesis:passthrough_state');
      addToast('modified', 'Previous session could not be restored');
    }
  }

  /** @internal Test-only: invoke handleEvent for SSE event simulation */
  _handleEvent(event: SSEEvent) {
    this.handleEvent(event);
  }

  /** @internal Test-only: restore initial state (delegates to reset() + clears ambient routing state) */
  _reset() {
    this.reset();
    // Clear ambient routing/health state (not cleared by user-facing reset)
    this.samplingCapable = null;
    this.mcpDisconnected = false;
    this.provider = null;
    this.version = null;
    this.recentErrors = null;
    this.avgDurationMs = null;
    this.scoreHealth = null;
    this.phaseDurations = null;
    this.domainCount = null;
    this.domainCeiling = null;
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
    this.synthesisStartedAt = null;
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
    this.appliedPatternLabel = null;
    this.clusterId = null;
    this.routingDecision = null;
    this.phaseModels = {};
    clustersStore.resetTracking();
    try { localStorage.removeItem('synthesis:passthrough_state'); } catch { /* noop */ }
  }
}

export const forgeStore = new ForgeStore();
