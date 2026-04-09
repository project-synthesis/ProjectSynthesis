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
  /** Set when the stream receives a refinement_complete event (backend committed) */
  private serverConfirmed = false;

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
        // No refinement turns yet — seed with initial suggestions from the pipeline
        this.suggestions = forgeStore.initialSuggestions;
      }
    } catch {
      // No versions yet — seed with initial suggestions from the pipeline
      this.suggestions = forgeStore.initialSuggestions;
    }
  }

  refine(request: string) {
    if (!this.optimizationId) return;
    // Abort any in-flight stream
    this.controller?.abort();
    this.controller = null;
    this.suggestions = [];
    this.status = 'refining';
    this.error = null;
    this.serverConfirmed = false;

    const branchId = this.activeBranchId;

    this.controller = refineSSE(
      this.optimizationId,
      request,
      branchId,
      // onEvent
      (event: SSEEvent) => this.handleEvent(event),
      // onError — stream broke unexpectedly
      (err: Error) => {
        // If the server already confirmed completion, the data is safe —
        // just reload from DB instead of showing an error.
        if (this.serverConfirmed) {
          this.reloadTurns(branchId);
          return;
        }
        this.error = `Refinement interrupted: ${err.message}`;
        this.status = 'error';
        console.warn('Refinement stream interrupted — attempting recovery');
        // Attempt to recover: the backend may have committed the turn
        // before the stream died.  Poll the DB to find out.
        this.attemptRecovery(branchId);
      },
      // onComplete — stream closed (may be graceful OR abrupt)
      () => {
        if (this.status === 'error') return; // already handled by onError
        if (this.serverConfirmed) {
          // Server confirmed — reload the committed turn from DB
          this.status = 'complete';
          this.reloadTurns(branchId);
        } else {
          // Stream closed without server confirmation — something went wrong.
          // The backend may still be running (SSE disconnect doesn't stop it).
          // Poll to check if the turn was committed.
          this.attemptRecovery(branchId);
        }
      },
    );
  }

  private handleEvent(event: SSEEvent) {
    const type = event.event as string || event.type as string;
    if (type === 'refinement_complete' || type === 'optimization_complete') {
      // The backend has committed the turn to the database.
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
   * Safe to call multiple times — last write wins.
   */
  private reloadTurns(preserveBranchId: string | null) {
    if (!this.optimizationId) return;
    getRefinementVersions(this.optimizationId)
      .then((data) => {
        this.turns = data.versions;
        if (preserveBranchId) this.activeBranchId = preserveBranchId;
        // Update suggestions from the latest turn
        if (this.turns.length > 0) {
          const last = this.turns[this.turns.length - 1];
          if (last.suggestions?.length) this.suggestions = last.suggestions;
        }
      })
      .catch((err) => {
        // DB fetch failed — don't overwrite existing state, just log
        console.warn('Failed to reload refinement turns:', err);
      });
  }

  /**
   * Poll the backend to check if the refinement turn was committed
   * despite the SSE stream dying.  The backend pipeline runs to
   * completion independently of the client connection.
   */
  private async attemptRecovery(branchId: string | null) {
    if (!this.optimizationId) return;

    const prevCount = this.turns.length;

    // Wait briefly for the backend to finish committing
    for (let attempt = 0; attempt < 10; attempt++) {
      await new Promise(r => setTimeout(r, 2000));
      try {
        const data = await getRefinementVersions(this.optimizationId!);
        if (data.versions.length > prevCount) {
          // Backend committed a new turn — recovery successful
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
        // Backend may still be restarting (hot-reload) — keep trying
      }
    }

    // After 20 seconds, give up — the turn was likely not committed
    if (this.status === 'error') {
      // Already showing error from onError — don't override
      return;
    }
    this.status = 'error';
    this.error = 'Refinement may not have completed. Try again.';
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
    this.controller?.abort();
    this.controller = null;
    this.serverConfirmed = false;
    if (this.status === 'refining') this.status = 'idle';
  }

  /** @internal Test-only: invoke handleEvent for SSE event simulation */
  _handleEvent(event: SSEEvent) {
    this.handleEvent(event);
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.cancel();
    this.optimizationId = null;
    this.turns = [];
    this.branches = [];
    this.activeBranchId = null;
    this.suggestions = [];
    this.selectedVersion = null;
    this.status = 'idle';
    this.error = null;
  }

  reset() {
    this.controller?.abort();
    this.controller = null;
    this.serverConfirmed = false;
    this.optimizationId = null;
    this.turns = [];
    this.branches = [];
    this.activeBranchId = null;
    this.suggestions = [];
    this.status = 'idle';
    this.error = null;
  }
}

export const refinementStore = new RefinementStore();
