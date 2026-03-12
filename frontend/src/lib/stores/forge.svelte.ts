import { retryOptimization, fetchOptimization, type SSEEvent } from '$lib/api/client';
import { toast } from '$lib/stores/toast.svelte';

export type StageStatus = 'idle' | 'running' | 'done' | 'error' | 'skipped' | 'timed_out' | 'cancelled';

export interface ForgeRecord {
  id: string;
  raw_prompt: string;
  optimized_prompt?: string | null;
  overall_score?: number | null;
  task_type?: string | null;
  complexity?: string | null;
  weaknesses?: string[] | null;
  strengths?: string[] | null;
  // Strategy fields (N13)
  primary_framework?: string | null;
  secondary_frameworks?: string[];
  approach_notes?: string | null;
  strategy_rationale?: string | null;
  // Validation scores
  clarity_score?: number | null;
  specificity_score?: number | null;
  structure_score?: number | null;
  faithfulness_score?: number | null;
  conciseness_score?: number | null;
  // Explore / codebase fields (N18)
  linked_repo_full_name?: string | null;
  codebase_context_snapshot?: string | null;
  // Live-session only — no DB column (N19)
  recommended_frameworks?: string[];
  // Optimize stage fields
  changes_made?: string[] | null;
  optimization_notes?: string | null;
  // Validate stage fields
  issues?: string[] | null;
  verdict?: string | null;
  // Per-stage model names
  model_explore?: string | null;
  model_analyze?: string | null;
  model_strategy?: string | null;
  model_optimize?: string | null;
  model_validate?: string | null;
  // Timing
  duration_ms?: number | null;
  total_tokens?: number | null;
  // Metadata
  tags?: string[] | null;
}

export interface StageResult {
  stage: string;
  data: Record<string, unknown>;
  duration?: number;
  tokenCount?: number;
}

export interface PipelineEvent {
  type: string;
  stage?: string;
  data?: unknown;
  timestamp: number;
}

export interface ContextWarning {
  dropped_files: number;
  dropped_urls: number;
  dropped_instructions: number;
  total_files_sent: number;
  total_urls_sent: number;
  total_instructions_sent: number;
}

class ForgeStore {
  isForging = $state(false);
  currentStage = $state<string | null>(null);
  tags = $state<string[]>([]);
  private _abortController = $state<AbortController | null>(null);
  private _recordCache = new Map<string, ForgeRecord>();
  stageStatuses = $state<Record<string, StageStatus>>({
    explore: 'idle',
    analyze: 'idle',
    strategy: 'idle',
    optimize: 'idle',
    validate: 'idle'
  });
  stageResults = $state<Record<string, StageResult>>({});
  streamingText = $state('');
  rawPrompt = $state('');
  optimizationId = $state<string | null>(null);
  overallScore = $state<number | null>(null);
  pipelineEvents = $state<PipelineEvent[]>([]);
  totalDuration = $state<number | null>(null);
  totalTokens = $state<number | null>(null);
  error = $state<string | null>(null);
  contextWarning = $state<ContextWarning | null>(null);
  stageErrors = $state<Record<string, { error: string; recoverable: boolean }>>({});
  private _activitySeq = 0;
  liveActivity = $state<Array<{
    id: number;
    type: 'tool' | 'reasoning';
    tool?: string;
    input?: Record<string, unknown>;
    content?: string;
    ts: number;
  }>>([]);
  liveStageText = $state<Record<string, string>>({});

  get stages() {
    return ['explore', 'analyze', 'strategy', 'optimize', 'validate'];
  }

  get visibleStages(): string[] {
    return this.stages.filter(s => !(s === 'explore' && this.stageStatuses[s] === 'idle'));
  }

  get completedStages(): number {
    return Object.values(this.stageStatuses).filter(s => s === 'done').length;
  }

  setAbortController(controller: AbortController | null) {
    this._abortController = controller;
  }

  cancel() {
    if (this._abortController) {
      this._abortController.abort();
      this._abortController = null;
    }
    this.isForging = false;
  }

  resetPipeline() {
    if (this._abortController) {
      this._abortController.abort();
      this._abortController = null;
    }
    this.isForging = false;
    this.currentStage = null;
    this.tags = [];
    this.stageStatuses = {
      explore: 'idle',
      analyze: 'idle',
      strategy: 'idle',
      optimize: 'idle',
      validate: 'idle'
    };
    this.stageResults = {};
    this.streamingText = '';
    this.rawPrompt = '';
    this.optimizationId = null;
    this.overallScore = null;
    this.pipelineEvents = [];
    this.totalDuration = null;
    this.totalTokens = null;
    this.error = null;
    this.contextWarning = null;
    this.stageErrors = {};
    this._activitySeq = 0;
    this.liveActivity = [];
    this.liveStageText = {};
  }

  startForge(rawPrompt?: string) {
    this.resetPipeline();
    if (rawPrompt) this.rawPrompt = rawPrompt;
    this.isForging = true;
  }

  setStageRunning(stage: string) {
    this.currentStage = stage;
    this.stageStatuses[stage] = 'running';
    // Clear accumulated streaming text so retries start fresh
    if (this.liveStageText[stage] !== undefined) {
      this.liveStageText = { ...this.liveStageText, [stage]: '' };
    }
    this.addEvent({ type: 'stage_start', stage, timestamp: Date.now() });
  }

  setStageComplete(stage: string, result?: StageResult) {
    this.stageStatuses[stage] = 'done';
    if (result) {
      this.stageResults[stage] = result;
    }
    this.addEvent({ type: 'stage_complete', stage, timestamp: Date.now() });
  }

  setStageFailed(stage: string, error?: string) {
    this.stageStatuses[stage] = 'error';
    this.error = error || `Stage ${stage} failed`;
    this.addEvent({ type: 'stage_error', stage, data: { error }, timestamp: Date.now() });
  }

  setStageSkipped(stage: string) {
    this.stageStatuses[stage] = 'skipped';
    this.addEvent({ type: 'stage_skipped', stage, timestamp: Date.now() });
  }

  setStageTimedOut(stage: string, error?: string) {
    this.stageStatuses[stage] = 'timed_out';
    this.error = error || `Stage ${stage} timed out`;
    this.addEvent({ type: 'stage_timed_out', stage, data: { error }, timestamp: Date.now() });
  }

  setStageCancelled(stage: string) {
    this.stageStatuses[stage] = 'cancelled';
    this.addEvent({ type: 'stage_cancelled', stage, timestamp: Date.now() });
  }

  appendStreamingText(chunk: string) {
    this.streamingText += chunk;
  }

  addToolCall(tool: string, input: Record<string, unknown>) {
    this.liveActivity = [...this.liveActivity, { id: ++this._activitySeq, type: 'tool', tool, input, ts: Date.now() }];
  }

  addAgentReasoning(content: string) {
    this.liveActivity = [...this.liveActivity, { id: ++this._activitySeq, type: 'reasoning', content, ts: Date.now() }];
  }

  appendStageText(stage: string, chunk: string) {
    this.liveStageText = {
      ...this.liveStageText,
      [stage]: (this.liveStageText[stage] ?? '') + chunk,
    };
  }

  finishForge(score?: number, duration?: number, tokens?: number) {
    this.isForging = false;
    this.currentStage = null;
    this._abortController = null;
    if (score != null) {
      this.overallScore = score;
    }
    if (duration != null) {
      this.totalDuration = duration;
    }
    if (tokens != null) {
      this.totalTokens = tokens;
    }
    this.addEvent({ type: 'forge_complete', timestamp: Date.now() });
  }

  loadFromRecord(record: ForgeRecord) {
    this.resetPipeline();
    this.optimizationId = record.id;
    this.rawPrompt = record.raw_prompt;
    this.streamingText = record.optimized_prompt || '';
    this.overallScore = record.overall_score ?? null;
    this.totalDuration = record.duration_ms ?? null;
    this.totalTokens = record.total_tokens ?? null;
    this.tags = record.tags ?? [];

    // Populate stage results from the record data
    if (record.task_type || record.complexity) {
      this.stageStatuses['analyze'] = 'done';
      this.stageResults['analyze'] = {
        stage: 'analyze',
        data: {
          task_type: record.task_type,
          complexity: record.complexity,
          weaknesses: record.weaknesses || [],
          strengths: record.strengths || [],
          recommended_frameworks: record.recommended_frameworks || [],
          model: record.model_analyze ?? undefined,
        }
      };
    }

    if (record.primary_framework) {
      this.stageStatuses['strategy'] = 'done';
      this.stageResults['strategy'] = {
        stage: 'strategy',
        data: {
          primary_framework: record.primary_framework,
          rationale: record.strategy_rationale,
          secondary_frameworks: record.secondary_frameworks ?? [],  // N13
          approach_notes: record.approach_notes ?? null,            // N13
          model: record.model_strategy ?? undefined,
        }
      };
    }

    // N18: Restore explore stage from codebase_context_snapshot
    if (record.linked_repo_full_name) {
      // Start with bare minimum — repo name always known, quality defaults to 'complete'
      let exploreData: Record<string, unknown> = {
        repo: record.linked_repo_full_name,
        explore_quality: 'complete',
        model: record.model_explore ?? undefined,
      };

      if (record.codebase_context_snapshot) {
        try {
          const snapshot = JSON.parse(record.codebase_context_snapshot);
          // Merge snapshot first, then let explicit fields override (repo always wins)
          exploreData = { ...snapshot, ...exploreData };
        } catch {
          // Malformed snapshot — bare minimal data is fine
        }
      }

      this.stageStatuses['explore'] = 'done';
      this.stageResults['explore'] = {
        stage: 'explore',
        data: exploreData
      };
    }

    if (record.optimized_prompt) {
      this.stageStatuses['optimize'] = 'done';
      this.stageResults['optimize'] = {
        stage: 'optimize',
        data: {
          optimized_prompt: record.optimized_prompt,
          changes_made: record.changes_made ?? [],
          optimization_notes: record.optimization_notes ?? null,
          model: record.model_optimize ?? undefined,
        }
      };
    }

    // Use _score-suffixed keys to match the live validation event shape so the
    // Scores sub-tab renders the same labels regardless of run source.
    const scores: Record<string, number> = {};
    if (record.clarity_score != null) scores.clarity_score = record.clarity_score;
    if (record.specificity_score != null) scores.specificity_score = record.specificity_score;
    if (record.structure_score != null) scores.structure_score = record.structure_score;
    if (record.faithfulness_score != null) scores.faithfulness_score = record.faithfulness_score;
    if (record.conciseness_score != null) scores.conciseness_score = record.conciseness_score;
    // overall_score is excluded — it is displayed via ScoreCircle, not as a dimension bar.

    if (Object.keys(scores).length > 0) {
      this.stageStatuses['validate'] = 'done';
      this.stageResults['validate'] = {
        stage: 'validate',
        data: {
          scores,
          overall_score: record.overall_score,
          issues: record.issues ?? [],
          verdict: record.verdict ?? null,
          model: record.model_validate ?? undefined,
        }
      };
    }
  }

  async retryForge(optimizationId: string, strategy?: string) {
    this.resetPipeline();
    this.isForging = true;

    const controller = new AbortController();
    this.setAbortController(controller);

    try {
      const res = await retryOptimization(optimizationId, strategy);
      await this._consumeSSEResponse(res, controller.signal, optimizationId);
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        this.error = (err as Error).message;
        this.isForging = false;
        this.finishForge();
      }
    }
  }

  /**
   * Shared SSE stream consumer used by retryForge (and callable by startForge
   * if it ever moves into the store). Reads the ReadableStream from an SSE
   * Response, dispatches every event through _handleSSEEvent, and falls back
   * to polling when the stream drops mid-run (matching the behaviour of
   * startOptimization in client.ts).
   */
  private async _consumeSSEResponse(
    res: Response,
    signal: AbortSignal,
    capturedOptId?: string
  ): Promise<void> {
    if (!res.body) throw new Error('No response body for SSE stream');

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        if (signal.aborted) break;
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';

        for (const raw of events) {
          if (!raw.trim()) continue;
          const typeMatch = raw.match(/^event: (.+)$/m);
          // Concatenate all `data:` lines per SSE spec (multi-line safe)
          const dataLines = raw.match(/^data: (.+)$/gm);
          if (typeMatch && dataLines) {
            const payload = dataLines.map((l) => l.slice(6)).join('\n');
            let parsed: unknown;
            try {
              parsed = JSON.parse(payload);
            } catch {
              parsed = payload;
            }
            this._handleSSEEvent({ event: typeMatch[1], data: parsed });
            await Promise.resolve(); // yield so Svelte flushes between events
          }
        }
      }
      if (this.isForging) this.finishForge();
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      // Stream dropped mid-run — fall back to polling if we have an opt ID
      if (capturedOptId) {
        await this._pollUntilComplete(capturedOptId, signal);
      } else {
        this.error = (err as Error).message;
        this.finishForge();
      }
    }
  }

  /** Poll GET /api/optimize/:id until the record reaches a terminal status. */
  private async _pollUntilComplete(id: string, signal: AbortSignal): Promise<void> {
    const POLL_INTERVAL_MS = 5000;
    const MAX_POLL_ATTEMPTS = 12; // 60 s max
    for (let i = 0; i < MAX_POLL_ATTEMPTS; i++) {
      if (signal.aborted) return;
      if (i > 0) await new Promise(res => setTimeout(res, POLL_INTERVAL_MS));
      if (signal.aborted) return;
      try {
        const record = await fetchOptimization(id);
        if (record.status === 'completed') {
          this._handleSSEEvent({ event: 'complete', data: { optimization_id: id, total_duration_ms: record.duration_ms, total_tokens: null } });
          this.finishForge();
          return;
        }
        if (record.status === 'failed') {
          this.error = record.error_message || 'Optimization failed';
          this.finishForge();
          return;
        }
        // status === 'running' — keep polling
      } catch { /* network error during poll — retry */ }
    }
    this.error = 'Status polling timed out after 60s';
    this.finishForge();
  }

  /** Dispatch a single SSE event into the store's state machine. */
  private _handleSSEEvent(event: SSEEvent): void {
    if (typeof event.data !== 'object' || event.data === null) return;
    const data = event.data as Record<string, unknown>;
    switch (event.event) {
      case 'stage': {
        const stageName = data.stage as string;
        if (data.status === 'started') {
          this.setStageRunning(stageName);
        } else if (data.status === 'complete') {
          if (this.stageResults[stageName]) {
            this.stageResults[stageName] = {
              ...this.stageResults[stageName],
              duration: data.duration_ms as number | undefined,
              tokenCount: data.token_count as number | undefined
            };
          }
          if (this.stageStatuses[stageName] !== 'done') {
            this.setStageComplete(stageName, {
              stage: stageName,
              data,
              duration: data.duration_ms as number | undefined,
              tokenCount: data.token_count as number | undefined
            });
          }
        } else if (data.status === 'skipped') {
          this.setStageSkipped(stageName);
        } else if (data.status === 'failed') {
          this.setStageFailed(stageName, data.error as string || `Stage ${stageName} failed`);
        }
        break;
      }
      case 'codebase_context': {
        const existingExplore = this.stageResults['explore'];
        const mergedData = { ...(existingExplore?.data ?? {}), ...data };
        this.stageResults['explore'] = { stage: 'explore', data: mergedData };
        if (!data.explore_failed) {
          this.setStageComplete('explore', { stage: 'explore', data: mergedData });
        }
        break;
      }
      case 'explore_info':
        if (this.stageResults['explore']) {
          this.stageResults['explore'] = {
            ...this.stageResults['explore'],
            data: { ...this.stageResults['explore'].data, ...data }
          };
        } else {
          this.stageResults['explore'] = { stage: 'explore', data };
        }
        break;
      case 'analysis':
        this.setStageComplete('analyze', { stage: 'analyze', data, duration: data.duration_ms as number | undefined });
        break;
      case 'strategy':
        this.setStageComplete('strategy', { stage: 'strategy', data, duration: data.duration_ms as number | undefined });
        break;
      case 'agent_text': {
        const agentContent = (data.content as string) ?? '';
        if (agentContent) this.addAgentReasoning(agentContent);
        break;
      }
      case 'step_progress': {
        const step = data.step as string;
        const chunk = (data.content as string) || '';
        if (step === 'optimize') {
          this.appendStreamingText(chunk);
        } else if (step) {
          this.appendStageText(step, chunk);
        }
        break;
      }
      case 'optimization':
        this.setStageComplete('optimize', { stage: 'optimize', data, duration: data.duration_ms as number | undefined });
        if (data.optimized_prompt) {
          this.streamingText = data.optimized_prompt as string;
        }
        break;
      case 'validation':
        this.setStageComplete('validate', { stage: 'validate', data, duration: data.duration_ms as number | undefined });
        if (data.overall_score != null) {
          this.overallScore = data.overall_score as number;
        } else {
          const scores = data.scores as Record<string, number> | undefined;
          if (scores?.overall_score != null) {
            this.overallScore = scores.overall_score;
          }
        }
        break;
      case 'complete':
        if (data.optimization_id) {
          this.optimizationId = data.optimization_id as string;
        }
        this.finishForge(
          this.overallScore ?? undefined,
          data.total_duration_ms as number | undefined,
          data.total_tokens as number | undefined
        );
        break;
      case 'error': {
        const errStage = (data.stage as string) || 'pipeline';
        this.setStageFailed(errStage, data.error as string);
        this.stageErrors[errStage] = {
          error: (data.error as string) || `Stage ${errStage} failed`,
          recoverable: data.recoverable !== false,
        };
        if (data.recoverable === false) {
          this.finishForge();
        }
        break;
      }
      case 'context_warning':
        this.contextWarning = data as unknown as ContextWarning;
        break;
      case 'rate_limit_warning':
        // Non-fatal warning — show toast but do NOT stop the pipeline
        toast.warning((data.message as string) || 'Rate limit warning — retrying');
        break;
      case 'tool_call':
        this.addToolCall(data.tool as string, (data.input as Record<string, unknown>) ?? {});
        break;
      default:
        break;
    }
  }

  cacheRecord(id: string, record: ForgeRecord) {
    this._recordCache.set(id, record);
  }

  getRecord(id: string): ForgeRecord | undefined {
    return this._recordCache.get(id);
  }

  private addEvent(event: PipelineEvent) {
    this.pipelineEvents = [...this.pipelineEvents, event];
  }
}

export const forge = new ForgeStore();
