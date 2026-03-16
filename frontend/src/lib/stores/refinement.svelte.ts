import { refineSSE, getRefinementVersions, rollbackRefinement } from '$lib/api/client';
import type { RefinementTurn, RefinementBranch, SSEEvent } from '$lib/api/client';

class RefinementStore {
  optimizationId = $state<string | null>(null);
  turns = $state<RefinementTurn[]>([]);
  branches = $state<RefinementBranch[]>([]);
  activeBranchId = $state<string | null>(null);
  suggestions = $state<Array<Record<string, string>>>([]);
  status = $state<'idle' | 'refining' | 'complete' | 'error'>('idle');
  error = $state<string | null>(null);

  private controller: AbortController | null = null;

  get scoreProgression(): number[] {
    return this.turns
      .filter(t => t.scores)
      .map(t => {
        const s = t.scores!;
        const vals = Object.values(s);
        return vals.reduce((a, b) => a + b, 0) / vals.length;
      });
  }

  get currentVersion(): RefinementTurn | null {
    return this.turns.length > 0 ? this.turns[this.turns.length - 1] : null;
  }

  async init(optimizationId: string) {
    this.optimizationId = optimizationId;
    this.status = 'idle';
    this.error = null;
    try {
      const data = await getRefinementVersions(optimizationId);
      this.turns = data.versions;
      if (this.turns.length > 0) {
        this.activeBranchId = this.turns[this.turns.length - 1].branch_id;
        const last = this.turns[this.turns.length - 1];
        this.suggestions = last.suggestions || [];
      }
    } catch { /* no versions yet */ }
  }

  refine(request: string) {
    if (!this.optimizationId) return;
    // Abort any in-flight stream
    this.controller?.abort();
    this.controller = null;
    this.suggestions = []; // clear old suggestions
    this.status = 'refining';
    this.error = null;

    this.controller = refineSSE(
      this.optimizationId,
      request,
      this.activeBranchId,
      (event: SSEEvent) => this.handleEvent(event),
      (err: Error) => { this.error = err.message; this.status = 'error'; },
      () => {
        if (this.status === 'refining') {
          this.status = 'complete';
          // Reload turns from backend to get the newly created turn
          const branchId = this.activeBranchId;
          if (this.optimizationId) {
            getRefinementVersions(this.optimizationId)
              .then((data) => {
                this.turns = data.versions;
                // Preserve active branch across reload
                if (branchId) this.activeBranchId = branchId;
              })
              .catch(() => {});
          }
        }
      },
    );
  }

  private handleEvent(event: SSEEvent) {
    const type = event.event as string || event.type as string;
    if (type === 'refinement_complete' || type === 'optimization_complete') {
      this.status = 'complete';
      // Reload versions
      if (this.optimizationId) this.init(this.optimizationId);
    } else if (type === 'suggestions') {
      this.suggestions = (event.suggestions || event.items || []) as Array<Record<string, string>>;
    } else if (type === 'error') {
      this.error = (event.error || event.message) as string;
      this.status = 'error';
    }
  }

  async rollback(toVersion: number) {
    if (!this.optimizationId) return;
    try {
      const branch = await rollbackRefinement(this.optimizationId, toVersion);
      // Reload versions first, then set the new branch AFTER loading
      const data = await getRefinementVersions(this.optimizationId);
      this.turns = data.versions;
      this.activeBranchId = branch.id;
      this.suggestions = [];
    } catch (err: any) {
      this.error = err.message;
    }
  }

  cancel() {
    this.controller?.abort();
    this.status = 'idle';
  }

  reset() {
    this.controller?.abort();
    this.controller = null;
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
