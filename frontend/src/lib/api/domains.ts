/**
 * Domain API client — fetch active domain nodes from the backend.
 *
 * Domains are taxonomy nodes promoted to domain status, each with
 * an OKLab-computed color. The frontend uses these to replace the
 * former hardcoded DOMAIN_COLORS map.
 *
 * Also exposes the R6 operator-recovery endpoint
 * `POST /api/domains/{id}/rebuild-sub-domains` (audit
 * `docs/audits/sub-domain-regression-2026-04-27.md`).
 */
import { apiFetch } from './client';

// -- Types -----------------------------------------------------------------

export interface DomainInfo {
  id: string;
  label: string;
  color_hex: string;
  member_count: number;
  avg_score: number | null;
  source: string; // seed | discovered | manual
}

/**
 * Backend `RebuildSubDomainsRequest` mirror.
 *
 * `min_consistency` is `≥ 0.25` (= `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR`)
 * and `≤ 1.0`. Pydantic enforces this server-side AND the engine method
 * re-asserts at runtime as a defense-in-depth check (R6 spec).
 *
 * `dry_run = true` returns the proposal list without mutating state.
 */
export interface RebuildSubDomainsRequest {
  min_consistency?: number | null;
  dry_run?: boolean;
}

export interface RebuildSubDomainsResult {
  domain_id: string;
  domain_label: string;
  threshold_used: number;
  proposed: string[];
  created: string[];
  skipped_existing: string[];
  dry_run: boolean;
}

/**
 * R6 floor: the minimum allowed `min_consistency` value. Below this, the
 * Pydantic validator returns 422 — sub-domains created at or below the
 * dissolution floor would be killed on the next Phase 5 cycle.
 */
export const REBUILD_MIN_CONSISTENCY_FLOOR = 0.25;

// -- API functions ---------------------------------------------------------

export const getDomains = () => apiFetch<DomainInfo[]>('/domains');

/**
 * R6 operator recovery: force-rebuild sub-domains under a single
 * domain. Idempotent — existing sub-domains land in `skipped_existing`.
 *
 * @param domainId  PromptCluster id of the parent domain (must be `state="domain"`).
 * @param request   Optional threshold override + dry-run toggle.
 *                  `min_consistency` must be in `[0.25, 1.0]`.
 * @returns         `RebuildSubDomainsResult` describing what was proposed,
 *                  created, and skipped.
 * @throws          `ApiError` on non-2xx (404/422/503/500).
 */
export const rebuildSubDomains = (
  domainId: string,
  request: RebuildSubDomainsRequest = {},
): Promise<RebuildSubDomainsResult> =>
  apiFetch<RebuildSubDomainsResult>(
    `/domains/${encodeURIComponent(domainId)}/rebuild-sub-domains`,
    {
      method: 'POST',
      body: JSON.stringify(request),
    },
  );
