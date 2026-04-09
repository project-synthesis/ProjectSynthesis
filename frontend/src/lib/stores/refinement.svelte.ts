import { refineSSE, getRefinementVersions, rollbackRefinement } from '$lib/api/client';
import type { RefinementTurn, RefinementBranch, SSEEvent } from '$lib/api/client';
import { forgeStore } from '$lib/stores/forge.svelte';

class RefinementStore {
  optimizationId = $state<string | null>(null);
  turns = $state<RefinementTurn[]>([]);
  branches = $state<RefinementBranch[]>([]);
  activeBranchId = $state<string | null>(null);
  suggestions = $state<Array<Record<string, string>>>([]);
  selectedVersion = $state<RefinementTurn | null>(null);
  status = $state<'idle' | 'refining' | 'complete' | 'error'>('idle');
  error = $state<string | null>(null);

  private controller: AbortController | null = null;
  /** True once the backend sends a refinement_complete event (data is committed). */
  private serverConfirmed = false;
  /** Incremented on every refine()/cancel()/reset() to invalidate stale recovery loops. */
  private generation = 0;

  // ── Derived getters ──────────────────────────────────────────────

  get scoreProgression(): number[] {
    return this.turns
      .filter(t => t.scores)
      .map(t => {
        const s = t.scores!;
        const vals = Object.values(s);
        return vals.length > 0 ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
      });
  }

  get dimensionProgressions(): Record<string, number[]> {
    const dims: Record<string, number[]> = {};
    for (const t of this.turns) {
      if (!t.scores) continue;
      for (const [key, value] of Object.entries(t.scores)) {
        (dims[key] ??= []).push(value);
      }
    }
    return dims;
  }

  get currentVersion(): RefinementTurn | null {
    return this.turns.length > 0 ? this.turns[this.turns.length - 1] : null;
  }

  // ── Public API ───────────────────────────────────────────────────

  selectVersion(turn: RefinementTurn | null) {
    this.selectedVersion = turn;
  }

  async init(optimizationId: string) {
    this.optimizationId = optimizationId;
    this.status = 'idle';
    this.error = null;
    this.selectedVersion = null;
    try {
      const data = await getRefinementVersions(optimizationId);
      this.turns = data.versions;
      if (this.turns.length > 0) {
        this.activeBranchId = this.turns[this.turns.length - 1].branch_id;
        const last = this.turns[this.turns.length - 1];
        this.suggestions = last.suggestions || [];
      } else {
        this.suggestions = forgeStore.initialSuggestions;
      }
    } catch {
      this.suggestions = forgeStore.initialSuggestions;
    }
  }

  refine(request: string) {
    if (!this.optimizationId) return;

    // Invalidate any in-flight stream AND any running recovery loop.
    this.abortCurrent();

    this.suggestions = [];
    this.status = 'refining';
    this.error = null;
    this.serverConfirmed = false;

    const gen = this.generation;
    const branchId = this.activeBranchId;

    this.controller = refineSSE(
      this.optimizationId,
      request,
      branchId,
      // onEvent
      (event: SSEEvent) => {
        if (this.generation !== gen) return; // stale stream
        this.handleEvent(event);
      },
      // onError — stream broke unexpectedly
      (err: Error) => {
        if (this.generation !== gen) return;
        if (this.serverConfirmed) {
          this.reloadTurns(branchId);
          return;
        }
        this.error = `Refinement interrupted: ${err.message}`;
        this.status = 'error';
        console.warn('Refinement stream interrupted — attempting recovery');
        this.attemptRecovery(branchId, gen);
      },
      // onComplete — stream closed (graceful or abrupt)
      () => {
        if (this.generation !== gen) return;
        if (this.status === 'error') return;
        if (this.serverConfirmed) {
          this.status = 'complete';
          this.reloadTurns(branchId);
        } else {
          // Stream ended without confirmation — backend may still be running.
          this.attemptRecovery(branchId, gen);
        }
      },
    );
  }

  async rollback(toVersion: number) {
    if (!this.optimizationId) return;
    try {
      const branch = await rollbackRefinement(this.optimizationId, toVersion);
      const data = await getRefinementVersions(this.optimizationId);
      this.turns = data.versions;
      this.activeBranchId = branch.id;
      this.suggestions = [];
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Rollback failed';
      this.status = 'error';
    }
  }

  cancel() {
    this.abortCurrent();
    if (this.status === 'refining') this.status = 'idle';
  }

  reset() {
    this.abortCurrent();
    this.optimizationId = null;
    this.turns = [];
    this.branches = [];
    this.activeBranchId = null;
    this.suggestions = [];
    this.selectedVersion = null;
    this.status = 'idle';
    this.error = null;
  }

  // ── Private helpers ──────────────────────────────────────────────

  /**
   * Abort the SSE controller AND invalidate any running recovery loop
   * by bumping the generation counter.
   */
  private abortCurrent() {
    this.controller?.abort();
    this.controller = null;
    this.serverConfirmed = false;
    this.generation++;
  }

  private handleEvent(event: SSEEvent) {
    const type = event.event as string || event.type as string;
    if (type === 'refinement_complete' || type === 'optimization_complete') {
      this.serverConfirmed = true;
      this.status = 'complete';
      this.reloadTurns(this.activeBranchId);
    } else if (type === 'suggestions') {
      this.suggestions = (event.suggestions || event.items || []) as Array<Record<string, string>>;
    } else if (type === 'error') {
      this.error = (event.error || event.message) as string;
      this.status = 'error';
    }
  }

  /**
   * Reload turns from the database after a successful refinement.
   */
  private reloadTurns(preserveBranchId: string | null) {
    if (!this.optimizationId) return;
    getRefinementVersions(this.optimizationId)
      .then((data) => {
        this.turns = data.versions;
        if (preserveBranchId) this.activeBranchId = preserveBranchId;
        if (this.turns.length > 0) {
          const last = this.turns[this.turns.length - 1];
          if (last.suggestions?.length) this.suggestions = last.suggestions;
        }
      })
      .catch((err) => {
        console.warn('Failed to reload refinement turns:', err);
      });
  }

  /**
   * Poll the backend to check if the turn was committed despite the
   * SSE stream dying.  Stops immediately if the generation changes
   * (new refine/cancel/reset was called).
   */
  private async attemptRecovery(branchId: string | null, gen: number) {
    if (!this.optimizationId) return;

    const prevCount = this.turns.length;

    for (let attempt = 0; attempt < 10; attempt++) {
      await new Promise(r => setTimeout(r, 2000));

      // Bail if a new operation started while we were sleeping
      if (this.generation !== gen) return;

      try {
        const data = await getRefinementVersions(this.optimizationId!);
        if (this.generation !== gen) return;

        if (data.versions.length > prevCount) {
          this.turns = data.versions;
          if (branchId) this.activeBranchId = branchId;
          const last = data.versions[data.versions.length - 1];
          if (last.suggestions?.length) this.suggestions = last.suggestions;
          this.status = 'complete';
          this.error = null;
          console.info('Refinement recovered from interrupted stream');
          return;
        }
      } catch {
        // Backend may still be restarting — keep trying
      }
    }

    // 20s elapsed, no new turn found
    if (this.generation !== gen) return;
    if (this.status !== 'error') {
      this.status = 'error';
      this.error = 'Refinement may not have completed. Try again.';
    }
  }

  // ── Test helpers ─────────────────────────────────────────────────

  /** @internal Test-only: invoke handleEvent for SSE event simulation */
  _handleEvent(event: SSEEvent) {
    this.handleEvent(event);
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.reset();
  }
}

export const refinementStore = new RefinementStore();
