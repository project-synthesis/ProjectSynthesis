// frontend/src/lib/api/client.ts

const BASE_URL = 'http://localhost:8000/api';

// ---- Types ----

export interface HealthResponse {
  status: string;
  version: string;
  provider: string | null;
  score_health: { last_n_mean: number; last_n_stddev: number; count: number; clustering_warning: boolean } | null;
  avg_duration_ms: number | Record<string, number> | null;
  recent_errors: { last_hour: number; last_24h: number };
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
  scoring_mode: string;
  duration_ms: number;
  status: string;
  created_at: string;
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
  raw_prompt: string;
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
}

export interface SettingsResponse {
  max_raw_prompt_chars: number;
  max_context_tokens: number;
  optimize_rate_limit: string;
  feedback_rate_limit: string;
  embedding_model: string;
  trace_retention_days: number;
}

export interface GitHubUser {
  login: string;
  avatar_url: string;
}

export interface LinkedRepo {
  id: string;
  full_name: string;
  default_branch: string;
  branch: string | null;
  language: string | null;
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

// ---- Fetch Wrapper ----

async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
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
): AbortController {
  return streamSSE(
    '/optimize',
    JSON.stringify({ prompt, strategy: strategy || undefined }),
    onEvent,
    onError,
    onComplete,
  );
}

// ---- Optimize (poll for reconnection) ----

export const getOptimization = (traceId: string) =>
  apiFetch<OptimizationResult>(`/optimize/${traceId}`);

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

export const githubLogin = () => apiFetch<{ url: string }>('/github/auth/login');
export const githubMe = () => apiFetch<GitHubUser>('/github/auth/me');
export const githubLogout = () => apiFetch<void>('/github/auth/logout', { method: 'POST' });
export const githubRepos = (page = 1) => apiFetch<{ repos: any[]; count: number }>(`/github/repos?page=${page}`);
export const githubLink = (fullName: string) =>
  apiFetch<LinkedRepo>('/github/repos/link', {
    method: 'POST',
    body: JSON.stringify({ full_name: fullName }),
  });
export const githubLinked = () => apiFetch<LinkedRepo>('/github/repos/linked');
export const githubUnlink = () => apiFetch<void>('/github/repos/unlink', { method: 'DELETE' });

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
  count: number;
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
