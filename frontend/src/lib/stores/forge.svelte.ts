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
  primary_framework?: string | null;
  strategy_rationale?: string | null;
  clarity_score?: number | null;
  specificity_score?: number | null;
  structure_score?: number | null;
  faithfulness_score?: number | null;
  conciseness_score?: number | null;
  duration_ms?: number | null;
  total_tokens?: number | null;
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
  }

  startForge(rawPrompt?: string) {
    this.resetPipeline();
    if (rawPrompt) this.rawPrompt = rawPrompt;
    this.isForging = true;
  }

  setStageRunning(stage: string) {
    this.currentStage = stage;
    this.stageStatuses[stage] = 'running';
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

    // Populate stage results from the record data
    if (record.task_type || record.complexity) {
      this.stageStatuses['analyze'] = 'done';
      this.stageResults['analyze'] = {
        stage: 'analyze',
        data: {
          task_type: record.task_type,
          complexity: record.complexity,
          weaknesses: record.weaknesses || [],
          strengths: record.strengths || []
        }
      };
    }

    if (record.primary_framework) {
      this.stageStatuses['strategy'] = 'done';
      this.stageResults['strategy'] = {
        stage: 'strategy',
        data: {
          primary_framework: record.primary_framework,
          rationale: record.strategy_rationale
        }
      };
    }

    if (record.optimized_prompt) {
      this.stageStatuses['optimize'] = 'done';
      this.stageResults['optimize'] = {
        stage: 'optimize',
        data: { optimized_prompt: record.optimized_prompt }
      };
    }

    const scores: Record<string, number> = {};
    if (record.clarity_score != null) scores.clarity = record.clarity_score;
    if (record.specificity_score != null) scores.specificity = record.specificity_score;
    if (record.structure_score != null) scores.structure = record.structure_score;
    if (record.faithfulness_score != null) scores.faithfulness = record.faithfulness_score;
    if (record.conciseness_score != null) scores.conciseness = record.conciseness_score;
    if (record.overall_score != null) scores.overall_score = record.overall_score;

    if (Object.keys(scores).length > 0) {
      this.stageStatuses['validate'] = 'done';
      this.stageResults['validate'] = {
        stage: 'validate',
        data: { scores, overall_score: record.overall_score }
      };
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
