// frontend/src/lib/api/seed.ts
import { apiFetch } from './client';

export interface SeedRequest {
  project_description: string;
  workspace_path?: string | null;
  repo_full_name?: string | null;
  prompt_count?: number;
  agents?: string[] | null;
  prompts?: string[] | null;
}

export interface SeedOutput {
  // Foundation P3 (v0.4.18): includes 'running' for forward-compat with the
  // unified RunRow substrate. POST /api/seed sync-mode currently only
  // returns terminal states, but the underlying RunRow.status enum is
  // shared with topic_probe and may surface 'running' on degraded paths.
  status: 'running' | 'completed' | 'partial' | 'failed';
  // batch_id and tier are nullable on the failed orchestrator-unavailable
  // path (backend _build_failed_output returns batch_id=null, tier=null).
  batch_id: string | null;
  tier: string | null;
  prompts_generated: number;
  prompts_optimized: number;
  prompts_failed: number;
  estimated_cost_usd: number | null;
  // NOTE: actual_cost_usd is intentionally absent — matches Python SeedOutput.
  // The backend provides estimation only.
  domains_touched: string[];
  clusters_created: number;
  summary: string;
  duration_ms: number;
  // Foundation P3 cycle 12 (v0.4.18) additive field. Maps to RunRow.id —
  // callers can correlate the synchronous response with cross-channel SSE
  // (`seed_batch_progress` events carry the same id) and GET /api/runs/{id}
  // / GET /api/seed/{id} reads. null on degraded paths only.
  run_id: string | null;
}

export interface SeedAgent {
  name: string;
  description: string;
  task_types: string[];
  prompts_per_run: number;
  enabled: boolean;
}

export async function seedTaxonomy(req: SeedRequest): Promise<SeedOutput> {
  return apiFetch<SeedOutput>('/seed', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function listSeedAgents(): Promise<SeedAgent[]> {
  return apiFetch<SeedAgent[]>('/seed/agents');
}
