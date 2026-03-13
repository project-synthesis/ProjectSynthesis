/**
 * Refinement + branch state store.
 * Manages refinement sessions, branch tree, and comparison state.
 */

import { listBranches, startRefinement, startBranchFork, selectBranch } from '$lib/api/client';
import type { SSEEvent } from '$lib/api/client';

export interface Branch {
  id: string;
  optimizationId: string;
  parentBranchId: string | null;
  label: string;
  optimizedPrompt: string | null;
  scores: Record<string, number> | null;
  turnCount: number;
  status: 'active' | 'selected' | 'abandoned';
  createdAt: string;
}

export interface RefinementTurn {
  turn: number;
  source: 'auto' | 'user';
  messageSummary: string;
  scoresBefore: Record<string, number> | null;
  promptHash: string;
}

class RefinementStore {
  branches = $state<Branch[]>([]);
  activeBranchId = $state<string | null>(null);
  refinementOpen = $state(false);
  refinementStreaming = $state(false);
  protectedDimensions = $state<string[]>([]);
  comparingBranches = $state<[string, string] | null>(null);
  abortController = $state<AbortController | null>(null);

  get activeBranch(): Branch | undefined {
    return this.branches.find((b) => b.id === this.activeBranchId);
  }

  get branchCount(): number {
    return this.branches.length;
  }

  get activeBranches(): Branch[] {
    return this.branches.filter((b) => b.status === 'active');
  }

  async loadBranches(optimizationId: string) {
    try {
      const result = await listBranches(optimizationId);
      this.branches = result.branches.map(mapBranch);
      const active = this.branches.find((b) => b.status === 'active' || b.status === 'selected');
      this.activeBranchId = active?.id ?? null;
    } catch {
      this.branches = [];
    }
  }

  async startRefine(optimizationId: string, message: string) {
    this.refinementStreaming = true;
    this.abortController = startRefinement(
      optimizationId,
      { message, protect_dimensions: this.protectedDimensions.length > 0 ? this.protectedDimensions : undefined },
      (event) => this.handleRefinementEvent(event),
      () => {
        this.refinementStreaming = false;
        this.loadBranches(optimizationId);
      },
      () => { this.refinementStreaming = false; },
    );
  }

  async startFork(optimizationId: string, parentBranchId: string, message: string, label?: string) {
    this.refinementStreaming = true;
    this.abortController = startBranchFork(
      optimizationId,
      { parent_branch_id: parentBranchId, message, label },
      (event) => this.handleRefinementEvent(event),
      () => {
        this.refinementStreaming = false;
        this.loadBranches(optimizationId);
      },
      () => { this.refinementStreaming = false; },
    );
  }

  async selectWinner(optimizationId: string, branchId: string, reason?: string) {
    await selectBranch(optimizationId, { branch_id: branchId, reason });
    await this.loadBranches(optimizationId);
  }

  handleRefinementEvent(event: SSEEvent) {
    const data = typeof event.data === 'string' ? JSON.parse(event.data) : event.data;
    switch (event.event) {
      case 'refinement_complete':
        if (data.branch_id && data.prompt) {
          const branch = this.branches.find((b) => b.id === data.branch_id);
          if (branch) {
            branch.optimizedPrompt = data.prompt;
            branch.turnCount = data.turn;
          }
        }
        break;
      case 'branch_created':
        if (data.branch) {
          this.branches.push(mapBranch(data.branch));
        }
        break;
    }
  }

  toggleProtectDimension(dim: string) {
    const idx = this.protectedDimensions.indexOf(dim);
    if (idx >= 0) {
      this.protectedDimensions.splice(idx, 1);
    } else {
      this.protectedDimensions.push(dim);
    }
  }

  openRefinement() { this.refinementOpen = true; }
  closeRefinement() { this.refinementOpen = false; }

  reset() {
    this.branches = [];
    this.activeBranchId = null;
    this.refinementOpen = false;
    this.refinementStreaming = false;
    this.protectedDimensions = [];
    this.comparingBranches = null;
    this.abortController?.abort();
    this.abortController = null;
  }
}

function mapBranch(raw: any): Branch {
  return {
    id: raw.id,
    optimizationId: raw.optimization_id,
    parentBranchId: raw.parent_branch_id,
    label: raw.label,
    optimizedPrompt: raw.optimized_prompt,
    scores: raw.scores,
    turnCount: raw.turn_count,
    status: raw.status,
    createdAt: raw.created_at,
  };
}

export const refinement = new RefinementStore();
