// frontend/src/lib/api/client.ts

// In dev, Vite proxies aren't configured so we hit the backend directly on :8000.
// In production (Docker/nginx), the frontend is served from the same origin so
// a relative /api path is correct. The env var allows overriding for any setup.
const BASE_URL = import.meta.env.VITE_API_URL ?? (import.meta.env.DEV ? 'http://localhost:8000/api' : '/api');

// ---- Types ----

export interface HealthResponse {
  status: string;
  version: string;
  provider: string | null;
  score_health: { last_n_mean: number; last_n_stddev: number; count: number; clustering_warning: boolean } | null;
  avg_duration_ms: number | null;
  phase_durations: Record<string, number>;
  recent_errors: { last_hour: number; last_24h: number };
  sampling_capable?: boolean | null;
  mcp_disconnected?: boolean;
  available_tiers?: string[];
  domain_count?: number | null;
  domain_ceiling?: number | null;
  injection_stats?: Record<string, number>;
  project_count?: number;  // ADR-005
  global_patterns?: { active: number; demoted: number; retired: number; total: number };  // ADR-005 Phase 2B
}

export interface ApiKeyStatus {
  configured: boolean;
  masked_key: string | null;
}

export interface DimensionScores {
  clarity: number;
  specificity: number;
  structure: number;
  faithfulness: number;
  conciseness: number;
}

export interface OptimizationResult {
  id: string;
  trace_id: string;
  raw_prompt: string;
  optimized_prompt: string;
  task_type: string;
  strategy_used: string;
  changes_summary: string;
  scores: DimensionScores;
  original_scores: DimensionScores;
  score_deltas: Record<string, number>;
  overall_score: number;
  provider: string;
  routing_tier: string | null;
  scoring_mode: string;
  duration_ms: number;
  status: string;
  created_at: string;
  model_used: string;
  models_by_phase: Record<string, string> | null;
  context_sources: Record<string, unknown> | null;
  intent_label: string | null;
  domain: string | null;
  cluster_id: string | null;
  project_id: string | null;
  heuristic_flags: string[];
  repo_full_name?: string | null;
  suggestions: Array<{ text: string; source: string }>;
}

export interface SSEEvent {
  event: string;
  [key: string]: unknown;
}

export interface HistoryItem {
  id: string;
  trace_id: string;
  created_at: string;
  task_type: string;
  strategy_used: string;
  overall_score: number;
  status: string;
  duration_ms: number;
  provider: string;
  routing_tier: string | null;
  raw_prompt: string;
  optimized_prompt: string | null;
  model_used?: string;
  scoring_mode?: string;
  intent_label: string | null;
  domain: string | null;
  cluster_id: string | null;
  project_id: string | null;  // ADR-005
  feedback_rating: string | null;
}

export interface HistoryResponse {
  total: number;
  count: number;
  offset: number;
  has_more: boolean;
  next_offset: number | null;
  items: HistoryItem[];
}

export interface ProvidersResponse {
  active_provider: string | null;
  available: string[];
  routing_tiers: string[];
}

/**
 * Per-tier model capability descriptor — mirrors backend `ModelTierInfo`.
 *
 * `supported_efforts` drives the Navigator's effort dropdown filtering so
 * users can't pick an effort the model will reject (e.g. `xhigh` on Sonnet,
 * any effort on Haiku).
 */
export interface ModelTierInfo {
  tier: 'opus' | 'sonnet' | 'haiku';
  id: string;
  label: string;
  version: string;
  supported_efforts: string[];
  supports_thinking: boolean;
}

export interface SettingsResponse {
  max_raw_prompt_chars: number;
  max_context_tokens: number;
  optimize_rate_limit: string;
  feedback_rate_limit: string;
  refine_rate_limit: string;
  embedding_model: string;
  trace_retention_days: number;
  database_engine: string;
  model_catalog: ModelTierInfo[];
}

export interface GitHubUser {
  login: string;
  avatar_url: string;
  github_user_id?: string;
}

export interface GitHubRepository {
  id: number;
  name: string;
  full_name: string;
  description: string | null;
  default_branch: string;
  language: string | null;
  private: boolean;
  stargazers_count: number;
  updated_at: string;
  owner: { login: string; avatar_url: string };
}

export interface LinkedRepo {
  full_name: string;
  default_branch: string;
  branch: string | null;
  language: string | null;
  project_node_id?: string | null;  // ADR-005
  project_label?: string | null;    // ADR-005
  linked_at?: string | null;
}

/**
 * ADR-005 B4 — candidates eligible for migration into the newly-linked project.
 *
 * Backend returns this on `POST /github/repos/link` so the UI can offer a
 * post-link toast: "Move N recent Legacy prompts to ProjectX?"  Never
 * auto-migrates — the user is the decider.
 */
export interface MigrationCandidates {
  count: number;
  from_project_id: string | null;
  since: string | null;
}

/**
 * ADR-005 B4 — response shape from `POST /github/repos/link`.
 *
 * Carries the project id so the frontend can switch views, plus migration
 * candidates that drive the transition toast.
 */
export interface LinkRepoResponse {
  full_name: string;
  default_branch: string;
  branch: string;
  language: string | null;
  project_id: string | null;
  migration_candidates: MigrationCandidates | null;
}

export interface FeedbackResponse {
  id: string;
  optimization_id: string;
  rating: string;
  comment: string | null;
  created_at: string;
}

// ---- Error Class ----

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

// ---- Fetch Wrappers ----

/**
 * Non-throwing fetch — returns null on any non-2xx response.
 * Use for optional checks (e.g., auth status) where 401/404 is expected.
 */
export async function tryFetch<T>(path: string, options?: RequestInit): Promise<T | null> {
  try {
    const resp = await fetch(`${BASE_URL}${path}`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', ...options?.headers },
      ...options,
    });
    if (!resp.ok) return null;
    return resp.json();
  } catch {
    return null;
  }
}

export async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new ApiError(resp.status, body.detail || resp.statusText);
  }
  return resp.json();
}

// ---- Health ----

export const getHealth = () => apiFetch<HealthResponse>('/health');

// ---- SSE Stream Helper ----

function streamSSE(
  url: string,
  body: string,
  onEvent: (event: SSEEvent) => void,
  onError: (err: Error) => void,
  onComplete: () => void,
): AbortController {
  const controller = new AbortController();

  fetch(`${BASE_URL}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body,
    signal: controller.signal,
  })
    .then(async (resp) => {
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new ApiError(resp.status, err.detail || resp.statusText);
      }
      const reader = resp.body?.getReader();
      if (!reader) throw new Error('No response body');
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              onEvent(JSON.parse(line.slice(6)));
            } catch { /* skip malformed */ }
          }
        }
      }
      onComplete();
    })
    .catch((err) => {
      if (err.name !== 'AbortError') onError(err);
    });

  return controller;
}

// ---- Optimize (SSE) ----

export function optimizeSSE(
  prompt: string,
  strategy: string | null,
  onEvent: (event: SSEEvent) => void,
  onError: (err: Error) => void,
  onComplete: () => void,
  appliedPatternIds?: string[] | null,
  repoFullName?: string | null,
  projectId?: string | null,
): AbortController {
  return streamSSE(
    '/optimize',
    JSON.stringify({
      prompt,
      strategy: strategy || undefined,
      applied_pattern_ids: appliedPatternIds?.length ? appliedPatternIds : undefined,
      repo_full_name: repoFullName || undefined,
      // ADR-005 F3/B1 — caller-supplied project_id is the authoritative
      // answer to "which project does this prompt belong to?"  Falls
      // through to backend's repo-chain / Legacy default when null.
      project_id: projectId ?? undefined,
    }),
    onEvent,
    onError,
    onComplete,
  );
}

// ---- Optimize (poll for reconnection) ----

export const getOptimization = (traceId: string) =>
  apiFetch<OptimizationResult>(`/optimize/${traceId}`);

/** Update an optimization's metadata (e.g., rename its intent_label). Uses optimization ID (UUID). */
export const updateOptimization = (id: string, updates: { intent_label?: string }) =>
  apiFetch<OptimizationResult>(`/optimize/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });

// ---- Passthrough (no-provider mode) ----

export const savePassthrough = (traceId: string, optimizedPrompt: string, changesSummary?: string) =>
  apiFetch<OptimizationResult>('/optimize/passthrough/save', {
    method: 'POST',
    body: JSON.stringify({
      trace_id: traceId,
      optimized_prompt: optimizedPrompt,
      changes_summary: changesSummary || undefined,
    }),
  });

// ---- History ----

export const getHistory = (params?: {
  offset?: number; limit?: number; sort_by?: string;
  sort_order?: string; task_type?: string; status?: string;
}) => {
  const search = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) search.set(k, String(v));
    });
  }
  const qs = search.toString();
  return apiFetch<HistoryResponse>(`/history${qs ? '?' + qs : ''}`);
};

// ---- Feedback ----

export const submitFeedback = (optimizationId: string, rating: string, comment?: string) =>
  apiFetch<FeedbackResponse>('/feedback', {
    method: 'POST',
    body: JSON.stringify({ optimization_id: optimizationId, rating, comment }),
  });

// ---- Providers ----

export const getProviders = () => apiFetch<ProvidersResponse>('/providers');

// ---- Settings ----

export const getSettings = () => apiFetch<SettingsResponse>('/settings');

// ---- GitHub ----

// githubLogin (authorization code redirect) removed — device flow is primary.
// Backend endpoint /auth/login still exists as fallback for deployed instances.
export const githubMe = () => tryFetch<GitHubUser>('/github/auth/me');
export const githubLogout = () => apiFetch<void>('/github/auth/logout', { method: 'POST' });
export const githubRepos = (page = 1) => apiFetch<{ repos: GitHubRepository[]; count: number }>(`/github/repos?page=${page}`);
export const githubLink = (fullName: string, projectId?: string) =>
  apiFetch<LinkRepoResponse>('/github/repos/link', {
    method: 'POST',
    body: JSON.stringify({ full_name: fullName, project_id: projectId ?? null }),
  });
export const githubLinked = () => tryFetch<LinkedRepo>('/github/repos/linked');
/**
 * ADR-005 B5 — response from `DELETE /github/repos/unlink`.
 * `mode='keep'` leaves attributions alone; `mode='rehome'` moves recent opts
 * back to Legacy.  The project node itself is never deleted — projects are
 * forever in Hybrid.
 */
export interface UnlinkRepoResponse {
  ok: boolean;
  mode: string;
  project_id: string | null;
  rehomed_count: number;
}
export const githubUnlink = (mode: 'keep' | 'rehome' = 'keep') =>
  apiFetch<UnlinkRepoResponse>(
    `/github/repos/unlink?mode=${encodeURIComponent(mode)}`,
    { method: 'DELETE' },
  );
export const githubIndexStatus = () => tryFetch<IndexStatus>('/github/repos/index-status');

export interface ProjectInfo {
  id: string;
  label: string;
  /** Alias of ``prompt_count`` kept for backward compatibility. */
  member_count: number;
  /** Count of ``Optimization.project_id == id`` — ground truth. */
  prompt_count?: number;
  /** Count of active|candidate|mature clusters with matching ``dominant_project_id``. */
  cluster_count?: number;
}
export const listProjects = () => apiFetch<ProjectInfo[]>('/projects');

/**
 * ADR-005 B3 — bulk-move `Optimization` rows between projects.  Always
 * explicit; backend rate-limits to 10/min.  Response `migrated` reflects the
 * actual number of rows moved (dry-run returns the count without touching
 * anything).
 */
export interface MigrateProjectsRequest {
  from_project_id: string;
  to_project_id: string;
  since?: string | null;
  repo_full_name_is_null?: boolean;
  dry_run?: boolean;
}
export interface MigrateProjectsResponse {
  migrated: number;
  dry_run: boolean;
}
export const migrateProjects = (body: MigrateProjectsRequest) =>
  apiFetch<MigrateProjectsResponse>('/projects/migrate', {
    method: 'POST',
    body: JSON.stringify(body),
  });

// ---- GitHub Repo Browsing ----
export interface RepoTreeEntry {
  path: string;
  sha: string;
  size: number;
}
export interface IndexStatus {
  status: string;
  file_count: number;
  head_sha?: string;
  indexed_at: string | null;
  synthesis_status?: string | null;
  synthesis_error?: string | null;
  // Phase-tracking fields (orthogonal to status + synthesis_status).
  // Backfilled values: index_phase defaults to "pending" when absent.
  index_phase?: string | null;
  files_seen?: number;
  files_total?: number;
  error_message?: string | null;
}
export const githubTree = (owner: string, repo: string, branch?: string) =>
  apiFetch<{ tree: RepoTreeEntry[]; full_name: string; branch: string }>(
    `/github/repos/${owner}/${repo}/tree${branch ? `?branch=${branch}` : ''}`,
  );
export const githubFileContent = (owner: string, repo: string, path: string, branch?: string) =>
  apiFetch<{ path: string; content: string; full_name: string }>(
    `/github/repos/${owner}/${repo}/files/${path.split('/').map(encodeURIComponent).join('/')}${branch ? `?branch=${branch}` : ''}`,
  );
export const githubBranches = (owner: string, repo: string) =>
  apiFetch<{ branches: string[] }>(`/github/repos/${owner}/${repo}/branches`);
export const githubReindex = () =>
  apiFetch<{ status: string }>('/github/repos/reindex', { method: 'POST' });

// ---- GitHub Device Flow ----
export interface DeviceCodeResponse {
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}
export interface DevicePollResponse {
  status: 'authorization_pending' | 'slow_down' | 'expired_token' | 'success' | 'error';
  user?: GitHubUser;
}
export const githubDeviceRequest = () =>
  apiFetch<DeviceCodeResponse>('/github/auth/device', { method: 'POST' });
export const githubDevicePoll = (deviceCode: string) =>
  apiFetch<DevicePollResponse>('/github/auth/device/poll', {
    method: 'POST',
    body: JSON.stringify({ device_code: deviceCode }),
  });

// ---- Refinement Types ----

export interface RefinementTurn {
  id: string;
  optimization_id: string;
  version: number;
  branch_id: string;
  parent_version: number | null;
  refinement_request: string | null;
  prompt: string;
  scores: Record<string, number> | null;
  deltas: Record<string, number> | null;
  deltas_from_original: Record<string, number> | null;
  strategy_used: string | null;
  suggestions: Array<{ text: string; source: string }> | null;
  created_at: string;
}

export interface RefinementBranch {
  id: string;
  optimization_id: string;
  parent_branch_id: string | null;
  forked_at_version: number | null;
  created_at: string;
}

export interface VersionsResponse {
  optimization_id: string;
  versions: RefinementTurn[];
}

// ---- Refinement (SSE) ----

export function refineSSE(
  optimizationId: string,
  refinementRequest: string,
  branchId: string | null,
  onEvent: (event: SSEEvent) => void,
  onError: (err: Error) => void,
  onComplete: () => void,
): AbortController {
  return streamSSE(
    '/refine',
    JSON.stringify({
      optimization_id: optimizationId,
      refinement_request: refinementRequest,
      branch_id: branchId || undefined,
    }),
    onEvent,
    onError,
    onComplete,
  );
}

export const getRefinementVersions = (optimizationId: string, branchId?: string) => {
  const params = branchId ? `?branch_id=${branchId}` : '';
  return apiFetch<VersionsResponse>(`/refine/${optimizationId}/versions${params}`);
};

export const rollbackRefinement = (optimizationId: string, toVersion: number) =>
  apiFetch<RefinementBranch>(`/refine/${optimizationId}/rollback`, {
    method: 'POST',
    body: JSON.stringify({ to_version: toVersion }),
  });

// ---- API Key Management ----

export const getApiKey = () => apiFetch<ApiKeyStatus>('/provider/api-key');
export const setApiKey = (apiKey: string) =>
  apiFetch<ApiKeyStatus>('/provider/api-key', {
    method: 'PATCH',
    body: JSON.stringify({ api_key: apiKey }),
  });
export const deleteApiKey = () =>
  apiFetch<ApiKeyStatus>('/provider/api-key', { method: 'DELETE' });

// ---- Preferences ----

export const getPreferences = () => apiFetch<Record<string, any>>('/preferences');

export const patchPreferences = (updates: Record<string, any>) =>
  apiFetch<Record<string, any>>('/preferences', {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });

// ---- Strategies ----

export interface StrategyInfo {
  name: string;
  tagline: string;
  description: string;
}

export interface StrategyDetail {
  name: string;
  content: string;
}

export const getStrategies = () => apiFetch<StrategyInfo[]>('/strategies');

export const getStrategy = (name: string) => apiFetch<StrategyDetail>(`/strategies/${name}`);

export interface StrategyUpdateResponse {
  name: string;
  content: string;
  warnings: string[];
}

export const updateStrategy = (name: string, content: string) =>
  apiFetch<StrategyUpdateResponse>(`/strategies/${name}`, {
    method: 'PUT',
    body: JSON.stringify({ content }),
  });

// --- Update ---
export interface UpdateStatusResponse {
  current_version: string;
  latest_version: string | null;
  latest_tag: string | null;
  update_available: boolean;
  changelog: string | null;
  changelog_entries: { category: string; text: string }[] | null;
  checked_at: string | null;
  detection_tier: string;
}

export const getUpdateStatus = () =>
  apiFetch<UpdateStatusResponse>('/update/status');

export const applyUpdate = (tag: string) =>
  apiFetch<{ status: string; tag: string; message: string }>('/update/apply', {
    method: 'POST',
    body: JSON.stringify({ tag }),
  });

// ---- Real-time event stream ----

/** Metadata extracted from each SSE event for health tracking. */
export interface EventMeta {
    /** The SSE `id` field (sequence number as string), or null. */
    seq: string | null;
    /** Server-side Unix timestamp (seconds) from the event payload. */
    timestamp: number | undefined;
}

export type EventHandler = (
    type: string,
    data: Record<string, unknown>,
    meta: EventMeta,
) => void;

/**
 * Connect to the SSE event stream at `/api/events`.
 *
 * @param onEvent - Handler called for each typed event (including `sync`).
 * @param lastEventId - Optional sequence number for manual reconnection replay.
 *   Appended as `?last_event_id=` query param (browser auto-reconnect uses
 *   the `Last-Event-ID` header instead).
 */
export function connectEventStream(
    onEvent: EventHandler,
    lastEventId?: string,
): EventSource {
    let url = `${BASE_URL.replace('/api', '')}/api/events`;
    if (lastEventId) url += `?last_event_id=${encodeURIComponent(lastEventId)}`;
    const es = new EventSource(url);

    const eventTypes = [
        'optimization_created', 'optimization_analyzed',
        'optimization_failed', 'optimization_status',
        'optimization_score_card', 'optimization_start',
        'feedback_submitted', 'refinement_turn',
        'strategy_changed', 'preferences_changed',
        'taxonomy_changed', 'taxonomy_activity',
        'domain_created', 'routing_state_changed',
        'seed_batch_progress',
        'agent_changed',
        'update_available',
        'update_complete',
    ];
    for (const type of eventTypes) {
        es.addEventListener(type, (e: MessageEvent) => {
            try {
                const parsed = JSON.parse(e.data);
                onEvent(type, parsed, {
                    seq: e.lastEventId || null,
                    timestamp: typeof parsed.timestamp === 'number' ? parsed.timestamp : undefined,
                });
            } catch { /* malformed event */ }
        });
    }

    // Sync event — forwarded for staleness timer reset but excluded from latency tracking.
    es.addEventListener('sync', (e: MessageEvent) => {
        try {
            const parsed = JSON.parse(e.data);
            onEvent('sync', parsed, {
                seq: e.lastEventId || null,
                timestamp: undefined,
            });
        } catch { /* ignore */ }
    });

    return es;
}
