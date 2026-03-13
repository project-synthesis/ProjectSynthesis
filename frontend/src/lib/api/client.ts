import type { HistoryEntry } from '$lib/stores/history.svelte';
import { auth } from '$lib/stores/auth.svelte';
import { parseSSEStream, POLL_INTERVAL_MS, MAX_POLL_ATTEMPTS, type SSEEvent } from '$lib/utils/sse-parser';

const BASE = '';

// ── JWT-aware fetch wrapper ───────────────────────────────────────────────────

/**
 * Drop-in replacement for ``fetch`` that:
 *  1. Injects ``Authorization: Bearer <token>`` when a JWT is held in memory.
 *  2. On a 401 response, silently refreshes the token once and retries.
 *  3. Falls through to the original behaviour for all non-auth failures.
 *
 * All API helper functions in this module use ``apiFetch`` instead of the
 * global ``fetch`` so that JWT auth is applied consistently.
 */
async function apiFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers ?? {});

  // Snapshot the token at request time. Re-reading auth.accessToken after the
  // await would create a race: a request that started without a token could see
  // a non-null token once auth.refresh() completes during the in-flight wait,
  // and then spuriously trigger a second refresh on an expected 401.
  const tokenAtRequestTime = auth.accessToken;
  if (tokenAtRequestTime) {
    headers.set('Authorization', `Bearer ${tokenAtRequestTime}`);
  }

  // Use global fetch directly inside this wrapper to avoid recursion.
  const attempt = await globalThis.fetch(input, { ...init, headers, credentials: 'include' });

  // Only attempt a silent refresh if the request was made WITH a token.
  // This prevents the timing race described above.
  if (attempt.status === 401 && tokenAtRequestTime) {
    const newToken = await auth.refresh();
    if (newToken) {
      headers.set('Authorization', `Bearer ${newToken}`);
      return globalThis.fetch(input, { ...init, headers, credentials: 'include' });
    }
  }

  return attempt;
}

export interface HealthResponse {
  status: string;
  provider: string;
  model_routing: Record<string, string>;
  github_oauth_enabled: boolean;
  db_connected: boolean;
  mcp_connected: boolean;
  redis_connected: boolean;
  mcp_url: string;
  version: string;
  credential_error?: string;
}

export interface OptimizeRequest {
  prompt: string;
  project?: string;
  tags?: string[];
  title?: string;
  strategy?: string;
  repo_full_name?: string;
  repo_branch?: string;
  github_token?: string;
  file_contexts?: { name: string; content: string }[];
  instructions?: string[];
  url_contexts?: string[];
}

export interface HistoryParams {
  offset?: number;
  limit?: number;
  search?: string;
  sort?: string;
  order?: string;
  project?: string;
  task_type?: string;
  framework?: string;
  has_repo?: boolean;
  min_score?: number;
  max_score?: number;
  status?: string;
}

export interface HistoryResponse {
  items: HistoryEntry[];
  total: number;
  count: number;
  offset: number;
  has_more: boolean;
  next_offset: number | null;
}

export interface OptimizationRecord {
  id: string;
  created_at: string;
  updated_at: string | null;
  raw_prompt: string;
  optimized_prompt: string | null;
  task_type: string | null;
  complexity: string | null;
  weaknesses: string[] | null;
  strengths: string[] | null;
  changes_made: string[] | null;
  primary_framework: string | null;
  framework_applied: string | null;
  optimization_notes: string | null;
  strategy_rationale: string | null;
  clarity_score: number | null;
  specificity_score: number | null;
  structure_score: number | null;
  faithfulness_score: number | null;
  conciseness_score: number | null;
  overall_score: number | null;
  is_improvement: boolean | null;
  verdict: string | null;
  issues: string[] | null;
  duration_ms: number | null;
  stage_durations?: Record<string, { duration_ms?: number; token_count?: number }> | null;
  provider_used: string | null;
  model_explore: string | null;
  model_analyze: string | null;
  model_strategy: string | null;
  model_optimize: string | null;
  model_validate: string | null;
  status: string;
  error_message: string | null;
  project: string | null;
  tags: string[] | null;
  title: string | null;
  version: string | null;
  row_version: number;
  retry_of: string | null;
  linked_repo_full_name: string | null;
  linked_repo_branch: string | null;
  codebase_context_snapshot: string | null;
}

export interface HistoryStats {
  total_optimizations: number;
  average_score: number | null;
  task_type_breakdown: Record<string, number>;
  framework_breakdown: Record<string, number>;
  provider_breakdown: Record<string, number>;
  model_usage: Record<string, number>;
  codebase_aware_count: number;
  improvement_rate: number | null;
}

export interface GitHubAuthStatus {
  connected: boolean;
  login: string | null;
  avatar_url: string | null;
  github_user_id: number | null;
  token_type: string | null;
}

export interface RepoInfo {
  full_name: string;
  name: string;
  private: boolean;
  default_branch: string;
  description: string | null;
  language: string | null;
  size_kb: number;
  stars?: number;
  forks?: number;
  open_issues?: number;
  updated_at?: string | null;
  pushed_at?: string | null;
  license_name?: string | null;
  topics?: string[];
}

export interface LinkedRepo {
  id: string;
  full_name: string;
  branch: string;
  linked_at: string;
}

// ---- Health ----

export async function fetchHealth(): Promise<HealthResponse> {
  const res = await apiFetch(`${BASE}/api/health`);
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return res.json();
}

// ---- SSE Optimization Stream ----

// Re-export SSE types from the shared parser module
export type { SSEEvent } from '$lib/utils/sse-parser';

export type SSECallback = (event: SSEEvent) => void;

export async function startOptimization(
  request: OptimizeRequest,
  onEvent: SSECallback,
  onError: (err: Error) => void,
  onComplete: () => void
): Promise<AbortController> {
  const controller = new AbortController();

  let capturedOptId: string | null = null;

  const wrappedOnEvent: SSECallback = (event) => {
    if (event.event === 'complete') {
      const d = event.data as Record<string, unknown>;
      if (typeof d?.optimization_id === 'string') capturedOptId = d.optimization_id;
    }
    onEvent(event);
  };

  try {
    const res = await apiFetch(`${BASE}/api/optimize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: controller.signal
    });

    if (!res.ok) {
      const errorText = await res.text();
      throw new Error(`Optimization failed (${res.status}): ${errorText}`);
    }

    if (!res.body) {
      throw new Error('No response body for SSE stream');
    }

    const processStream = async () => {
      try {
        for await (const sseEvent of parseSSEStream(res.body!, controller.signal)) {
          wrappedOnEvent(sseEvent);
        }
        onComplete();
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        if (capturedOptId) {
          await pollOptimizationStatus(capturedOptId, onEvent, onError, onComplete, controller.signal);
        } else {
          onError(err as Error);
        }
      }
    };

    processStream().catch((err) => {
      if ((err as Error).name !== 'AbortError') onError(err as Error);
    });
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      onError(err as Error);
    }
  }

  return controller;
}

// ---- Optimization CRUD ----

export async function fetchOptimization(id: string): Promise<OptimizationRecord> {
  const res = await apiFetch(`${BASE}/api/optimize/${id}`);
  if (!res.ok) throw new Error(`Fetch optimization failed: ${res.status}`);
  return res.json();
}

async function pollOptimizationStatus(
  id: string,
  onEvent: SSECallback,
  onError: (e: Error) => void,
  onComplete: () => void,
  signal: AbortSignal
): Promise<void> {
  for (let i = 0; i < MAX_POLL_ATTEMPTS; i++) {
    if (signal.aborted) return;
    // Poll immediately on first attempt; sleep between subsequent retries
    if (i > 0) await new Promise(res => setTimeout(res, POLL_INTERVAL_MS));
    if (signal.aborted) return;
    try {
      const record = await fetchOptimization(id);
      if (record.status === 'completed') {
        onEvent({ event: 'complete', data: { optimization_id: id, total_duration_ms: record.duration_ms, total_tokens: null }});
        onComplete(); return;
      }
      if (record.status === 'failed') {
        onError(new Error(record.error_message || 'Optimization failed')); return;
      }
      // status === 'running' — keep polling
    } catch { /* network error during poll — retry */ }
  }
  onError(new Error('Status polling timed out after 60s'));
}

export async function patchOptimization(
  id: string,
  data: { title?: string; tags?: string[]; version?: string; project?: string; expected_version?: number }
): Promise<OptimizationRecord> {
  const res = await apiFetch(`${BASE}/api/optimize/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error(`Patch optimization failed: ${res.status}`);
  return res.json();
}

export async function retryOptimization(
  id: string,
  strategy?: string
): Promise<Response> {
  const res = await apiFetch(`${BASE}/api/optimize/${id}/retry`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ strategy })
  });
  if (!res.ok) throw new Error(`Retry optimization failed: ${res.status}`);
  return res;
}

// ---- History ----

export async function fetchHistory(params: HistoryParams = {}): Promise<HistoryResponse> {
  const searchParams = new URLSearchParams();
  if (params.offset !== undefined) searchParams.set('offset', String(params.offset));
  if (params.limit) searchParams.set('limit', String(params.limit));
  if (params.search) searchParams.set('search', params.search);
  if (params.sort) searchParams.set('sort', params.sort);
  if (params.order) searchParams.set('order', params.order);
  if (params.project) searchParams.set('project', params.project);
  if (params.task_type) searchParams.set('task_type', params.task_type);
  if (params.framework) searchParams.set('framework', params.framework);
  if (params.has_repo !== undefined) searchParams.set('has_repo', String(params.has_repo));
  if (params.min_score !== undefined) searchParams.set('min_score', String(params.min_score));
  if (params.max_score !== undefined) searchParams.set('max_score', String(params.max_score));
  if (params.status) searchParams.set('status', params.status);

  const res = await apiFetch(`${BASE}/api/history?${searchParams.toString()}`);
  if (!res.ok) throw new Error(`Fetch history failed: ${res.status}`);
  return res.json();
}

export async function deleteOptimization(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/api/history/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete optimization failed: ${res.status}`);
}

export interface BatchDeleteResponse {
  deleted_count: number;
  ids: string[];
}

export async function batchDeleteOptimizations(ids: string[]): Promise<BatchDeleteResponse> {
  const res = await apiFetch(`${BASE}/api/history/batch-delete`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Batch delete failed: ${res.status}` }));
    const msg = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
    throw new Error(msg);
  }
  return res.json();
}

export async function fetchHistoryTrash(offset = 0, limit = 20): Promise<HistoryResponse> {
  const res = await apiFetch(`${BASE}/api/history/trash?offset=${offset}&limit=${limit}`);
  if (!res.ok) throw new Error(`Trash fetch failed: ${res.status}`);
  return res.json();
}

export async function restoreOptimization(id: string): Promise<void> {
  const res = await apiFetch(`${BASE}/api/history/${id}/restore`, { method: 'POST' });
  if (!res.ok) throw new Error(`Restore failed: ${res.status}`);
}

export async function fetchHistoryStats(project?: string): Promise<HistoryStats> {
  const params = project ? `?project=${encodeURIComponent(project)}` : '';
  const res = await apiFetch(`${BASE}/api/history/stats${params}`);
  if (!res.ok) throw new Error(`Fetch stats failed: ${res.status}`);
  return res.json();
}

// ---- GitHub Auth ----

export async function fetchGitHubAuthStatus(): Promise<GitHubAuthStatus> {
  const res = await apiFetch(`${BASE}/auth/github/me`);
  if (!res.ok) throw new Error(`GitHub auth check failed: ${res.status}`);
  return res.json();
}

export async function logoutGitHub(): Promise<void> {
  const res = await apiFetch(`${BASE}/auth/github/logout`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`GitHub logout failed: ${res.status}`);
  // Clear in-memory JWT — refresh cookie is cleared server-side.
  auth.clearToken();
}

export async function refreshGitHubToken(): Promise<{
  refreshed: boolean;
  reason?: string;
  expires_at?: string;
}> {
  const res = await apiFetch(`${BASE}/auth/github/token/refresh`, { method: 'POST' });
  if (!res.ok) throw new Error(`Token refresh failed: ${res.status}`);
  return res.json();
}

export function getGitHubLoginUrl(): string {
  return `${BASE}/auth/github/login`;
}

// ---- GitHub Repos ----

export async function fetchGitHubRepos(): Promise<RepoInfo[]> {
  const res = await apiFetch(`${BASE}/api/github/repos`);
  if (!res.ok) throw new Error(`Fetch repos failed: ${res.status}`);
  return res.json();
}

export async function linkRepo(full_name: string, branch?: string): Promise<LinkedRepo> {
  const res = await apiFetch(`${BASE}/api/github/repos/link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ full_name, branch })
  });
  if (!res.ok) throw new Error(`Link repo failed: ${res.status}`);
  return res.json();
}

export async function fetchLinkedRepo(): Promise<LinkedRepo | null> {
  const res = await apiFetch(`${BASE}/api/github/repos/linked`);
  if (!res.ok) throw new Error(`Fetch linked repo failed: ${res.status}`);
  return res.json();
}

export async function unlinkRepo(): Promise<void> {
  const res = await apiFetch(`${BASE}/api/github/repos/unlink`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Unlink repo failed: ${res.status}`);
}

// ---- Settings ----

export interface AppSettings {
  default_model: string;
  pipeline_timeout: number;
  max_retries: number;
  default_strategy: string | null;
  auto_validate: boolean;
  stream_optimize: boolean;
}

export async function fetchSettings(): Promise<AppSettings> {
  const res = await apiFetch(`${BASE}/api/settings`);
  if (!res.ok) throw new Error(`Fetch settings failed: ${res.status}`);
  return res.json();
}

export async function updateSettings(
  data: Partial<AppSettings>
): Promise<AppSettings> {
  const res = await apiFetch(`${BASE}/api/settings`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (!res.ok) throw new Error(`Update settings failed: ${res.status}`);
  return res.json();
}

// ---- Provider Config (API key management) ----

export interface ProviderConfigResponse {
  provider_active: string;
  provider_available: boolean;
  api_key: {
    configured: boolean;
    source: 'environment' | 'app' | 'none';
    masked: string;
  };
  bootstrap_mode: boolean;
}

export interface SaveApiKeyResponse {
  ok: boolean;
  provider_active: string;
  provider_available: boolean;
  validation_warning?: string;
  api_key: {
    configured: boolean;
    source: 'environment' | 'app' | 'none';
    masked: string;
  };
}

/** Always public — used by UI to check provider setup state. */
export async function getProviderConfig(): Promise<ProviderConfigResponse> {
  const res = await globalThis.fetch(`${BASE}/api/provider/config`);
  if (!res.ok) throw new Error(`Provider config check failed: ${res.status}`);
  return res.json();
}

/** Save an Anthropic API key. Bootstrap mode allows unauthenticated access. */
export async function saveApiKey(apiKey: string): Promise<SaveApiKeyResponse> {
  const res = await apiFetch(`${BASE}/api/provider/api-key`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) {
    let detail = `Save failed (${res.status})`;
    try {
      const err = await res.json();
      if (err.detail) {
        detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
      }
    } catch { /* ignore parse errors */ }
    throw new Error(detail);
  }
  return res.json();
}

/** Remove saved API key. JWT required. */
export async function deleteApiKey(): Promise<SaveApiKeyResponse> {
  const res = await apiFetch(`${BASE}/api/provider/api-key`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Delete API key failed: ${res.status}`);
  return res.json();
}

// ---- Providers ----

export interface ProviderDetectResponse {
  providers: Record<string, { available: boolean; [key: string]: unknown }>;
  active: string;
  model_routing: Record<string, string>;
}

export interface ProviderStatusResponse {
  status: string;
  provider: string | null;
  model_routing: Record<string, string>;
  healthy: boolean;
  message: string;
}

export async function fetchProviderDetect(): Promise<ProviderDetectResponse> {
  const res = await apiFetch(`${BASE}/api/providers/detect`);
  if (!res.ok) throw new Error(`Provider detect failed: ${res.status}`);
  return res.json();
}

export async function fetchProviderStatus(): Promise<ProviderStatusResponse> {
  const res = await apiFetch(`${BASE}/api/providers/status`);
  if (!res.ok) throw new Error(`Provider status failed: ${res.status}`);
  return res.json();
}

// ---- GitHub Repo Tree / Files ----

export interface RepoTreeEntry {
  path: string;
  type?: 'blob' | 'tree' | 'commit';
  sha: string;
  size_bytes?: number;
}

export interface RepoTreeResponse {
  tree: RepoTreeEntry[];
  full_name: string;
  branch: string;
}

export interface RepoFileResponse {
  path: string;
  content: string;
  size_bytes: number;
  sha: string;
}

export async function fetchRepoTree(
  owner: string,
  repo: string,
  branch = 'main'
): Promise<RepoTreeResponse> {
  const params = new URLSearchParams({ branch });
  const res = await apiFetch(
    `${BASE}/api/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/tree?${params}`
  );
  if (!res.ok) throw new Error(`Fetch repo tree failed: ${res.status}`);
  return res.json();
}

export async function fetchFileContent(
  owner: string,
  repo: string,
  path: string,
  branch = 'main'
): Promise<RepoFileResponse> {
  const params = new URLSearchParams({ branch });
  const encodedPath = path.split('/').map(encodeURIComponent).join('/');
  const res = await apiFetch(
    `${BASE}/api/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/files/${encodedPath}?${params}`
  );
  if (!res.ok) throw new Error(`Fetch file content failed: ${res.status}`);
  return res.json();
}

export interface RepoBranch {
  name: string;
  protected: boolean;
}

export async function fetchRepoBranches(owner: string, repo: string): Promise<RepoBranch[]> {
  const res = await apiFetch(
    `${BASE}/api/github/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/branches`
  );
  if (!res.ok) throw new Error(`Fetch branches failed: ${res.status}`);
  return res.json();
}

// ---- GitHub App credential management ----

export interface GitHubAppConfig {
  configured: boolean;
  client_id_masked: string;
  has_secret: boolean;
}

/** Always public — no JWT needed. Used by AuthGate to check bootstrap state. */
export async function fetchGitHubAppConfig(): Promise<GitHubAppConfig> {
  const res = await globalThis.fetch(`${BASE}/api/github/app-config`);
  if (!res.ok) throw new Error(`GitHub app config check failed: ${res.status}`);
  return res.json();
}

/**
 * Save GitHub App credentials. Unauthenticated when unconfigured (bootstrap);
 * sends JWT when credentials already exist. Throws with server detail on error.
 */
export async function saveGitHubAppConfig(
  clientId: string,
  clientSecret: string
): Promise<{ ok: boolean } & GitHubAppConfig> {
  const res = await apiFetch(`${BASE}/api/github/app-config`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId, client_secret: clientSecret })
  });
  if (!res.ok) {
    let detail = `Save failed (${res.status})`;
    try {
      const err = await res.json();
      if (err.detail) {
        detail = typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail);
      }
    } catch { /* ignore parse errors */ }
    throw new Error(detail);
  }
  return res.json();
}

// ---- GitHub convenience wrappers (used by NavigatorGitHub) ----

export async function disconnectGitHub(): Promise<void> {
  return logoutGitHub();
}

// ── Onboarding Event Queue with retry ─────────────────────────────────────────

const _EVENT_QUEUE_KEY = 'pf_event_queue';
const _MAX_RETRIES = 3;
const _MAX_QUEUE_SIZE = 20;

interface QueuedEvent {
  eventType: string;
  metadata?: Record<string, unknown>;
  retries: number;
  queuedAt: number;
}

class OnboardingEventQueue {
  private queue: QueuedEvent[] = [];
  private flushing = false;
  private _authReady = false;

  constructor() {
    // Restore persisted queue on init — do NOT flush yet (auth token not set)
    if (typeof window !== 'undefined') {
      try {
        const stored = localStorage.getItem(_EVENT_QUEUE_KEY);
        if (stored) {
          this.queue = JSON.parse(stored);
          localStorage.removeItem(_EVENT_QUEUE_KEY);
        }
      } catch { /* ignore corrupt data */ }

      // Persist pending events to localStorage on page unload.
      // sendBeacon is NOT used because the endpoint requires a JWT Bearer
      // token which sendBeacon cannot include. The queue is restored and
      // flushed with proper auth on the next page load.
      window.addEventListener('beforeunload', () => {
        if (this.queue.length > 0) {
          localStorage.setItem(_EVENT_QUEUE_KEY, JSON.stringify(this.queue));
        }
      });
    }
  }

  /** Signal that JWT auth is ready — flushes any queued events. */
  onAuthReady(): void {
    this._authReady = true;
    this.flush();
  }

  enqueue(eventType: string, metadata?: Record<string, unknown>): void {
    if (this.queue.length >= _MAX_QUEUE_SIZE) {
      this.queue.shift(); // drop oldest
    }
    this.queue.push({ eventType, metadata, retries: 0, queuedAt: Date.now() });
    if (this._authReady) {
      this.flush();
    }
  }

  private async flush(): Promise<void> {
    if (this.flushing || this.queue.length === 0) return;
    this.flushing = true;
    try {
      while (this.queue.length > 0) {
        const evt = this.queue[0];
        try {
          await apiFetch(`${BASE}/api/onboarding/events`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ event_type: evt.eventType, metadata: evt.metadata }),
          });
          this.queue.shift(); // success — remove from queue
        } catch {
          evt.retries++;
          if (evt.retries >= _MAX_RETRIES) {
            this.queue.shift(); // give up after max retries
          } else {
            // Exponential backoff: 1s, 2s, 4s
            await new Promise(r => setTimeout(r, 1000 * Math.pow(2, evt.retries - 1)));
          }
        }
      }
    } finally {
      this.flushing = false;
    }
  }
}

const _eventQueue = new OnboardingEventQueue();

/** Signal that auth is ready — flushes any queued events that were waiting. */
export function notifyAuthReady(): void {
  _eventQueue.onAuthReady();
}

/** Queue an onboarding event with automatic retry (3 attempts, exponential backoff).
 * Falls back to navigator.sendBeacon on page unload. */
export async function trackOnboardingEvent(
  eventType: string,
  metadata?: Record<string, unknown>
): Promise<void> {
  _eventQueue.enqueue(eventType, metadata);
}

// ── Auth security endpoints (Cycle A, 3, 7) ──────────────────────────────────

/** GET /auth/token — exchanges one-time server-side session token for JWT after OAuth callback. */
export async function getAuthToken(): Promise<{ access_token: string; token_type: string }> {
  const res = await globalThis.fetch(`${BASE}/auth/token`, { credentials: 'include' });
  if (!res.ok) throw new Error(`Token exchange failed: ${res.status}`);
  return res.json();
}

/** PATCH /auth/me — update display name / email, or mark onboarding complete.
 *
 * Pass `onboarding_completed: true` to stamp completion on the server.
 * All fields are optional; only supplied fields are changed.
 */
export async function patchAuthMe(data: {
  display_name?: string | null;
  email?: string | null;
  onboarding_completed?: boolean;
  onboarding_step?: number | null;
  preferences?: Record<string, unknown>;
}): Promise<{ updated: boolean }> {
  const res = await apiFetch(`${BASE}/auth/me`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`Profile update failed: ${res.status}`);
  return res.json();
}

export interface AuthMeResponse {
  id: string;
  github_login: string;
  github_user_id: number;
  role: string;
  email: string | null;
  avatar_url: string | null;
  display_name: string | null;
  onboarding_completed: boolean;
  onboarding_completed_at: string | null;
  onboarding_step: number | null;
  preferences: Record<string, unknown>;
  last_login_at: string | null;
  created_at: string;
}

export async function fetchAuthMe(): Promise<AuthMeResponse> {
  const res = await apiFetch(`${BASE}/auth/me`);
  if (!res.ok) throw new Error(`Profile fetch failed: ${res.status}`);
  return res.json();
}

/** DELETE /auth/sessions — revoke all active refresh tokens across every device.
 *
 * Clears the in-memory JWT so the UI reflects the logged-out state immediately.
 */
export async function logoutAllDevices(): Promise<{ revoked_sessions: number }> {
  const res = await apiFetch(`${BASE}/auth/sessions`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Logout all failed: ${res.status}`);
  const data = await res.json();
  // Clear in-memory token — all server-side sessions are now revoked.
  auth.clearToken();
  return data;
}

/** POST /auth/logout — revoke refresh tokens for the current device only.
 *
 * Clears the in-memory JWT so the UI reflects the logged-out state immediately.
 */
export async function logoutDevice(): Promise<{ revoked_count: number }> {
  const res = await apiFetch(`${BASE}/auth/logout`, { method: 'POST' });
  if (!res.ok) throw new Error(`Logout failed: ${res.status}`);
  const data = await res.json();
  auth.clearToken();
  return data;
}

// ── Feedback API ────────────────────────────────────────────────────

export async function submitFeedback(
  optimizationId: string,
  body: { rating: -1 | 0 | 1; dimension_overrides?: Record<string, number>; comment?: string }
): Promise<{ id: string; status: string }> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Submit feedback failed: ${res.status}`);
  return res.json();
}

export async function getFeedback(
  optimizationId: string
): Promise<{ feedback: any | null; aggregate: any }> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/feedback`);
  if (!res.ok) throw new Error(`Get feedback failed: ${res.status}`);
  return res.json();
}

export async function getFeedbackHistory(
  params: { offset?: number; limit?: number; rating?: number } = {}
): Promise<any> {
  const qs = new URLSearchParams();
  if (params.offset) qs.set('offset', String(params.offset));
  if (params.limit) qs.set('limit', String(params.limit));
  if (params.rating !== undefined) qs.set('rating', String(params.rating));
  const res = await apiFetch(`${BASE}/api/feedback/history?${qs}`);
  if (!res.ok) throw new Error(`Feedback history failed: ${res.status}`);
  return res.json();
}

export async function getFeedbackStats(): Promise<any> {
  const res = await apiFetch(`${BASE}/api/feedback/stats`);
  if (!res.ok) throw new Error(`Feedback stats failed: ${res.status}`);
  return res.json();
}

// ── Refinement API ──────────────────────────────────────────────────

/**
 * Start a refinement turn (SSE stream). Follows the same pattern as
 * startOptimization: get Response, check ok, iterate parseSSEStream.
 */
export function startRefinement(
  optimizationId: string,
  body: { message: string; protect_dimensions?: string[] },
  onEvent: (event: SSEEvent) => void,
  onComplete?: () => void,
  onError?: (error: Error) => void
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/refine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errorText = await res.text();
        throw new Error(`Refinement failed (${res.status}): ${errorText}`);
      }
      if (!res.body) throw new Error('No response body for SSE stream');

      for await (const sseEvent of parseSSEStream(res.body, controller.signal)) {
        onEvent(sseEvent);
      }
      onComplete?.();
    } catch (err) {
      if ((err as Error).name !== 'AbortError') onError?.(err as Error);
    }
  })();

  return controller;
}

export function startBranchFork(
  optimizationId: string,
  body: { parent_branch_id: string; message: string; label?: string },
  onEvent: (event: SSEEvent) => void,
  onComplete?: () => void,
  onError?: (error: Error) => void
): AbortController {
  const controller = new AbortController();

  (async () => {
    try {
      const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!res.ok) {
        const errorText = await res.text();
        throw new Error(`Branch fork failed (${res.status}): ${errorText}`);
      }
      if (!res.body) throw new Error('No response body for SSE stream');

      for await (const sseEvent of parseSSEStream(res.body, controller.signal)) {
        onEvent(sseEvent);
      }
      onComplete?.();
    } catch (err) {
      if ((err as Error).name !== 'AbortError') onError?.(err as Error);
    }
  })();

  return controller;
}

export async function listBranches(
  optimizationId: string
): Promise<{ branches: any[]; total: number }> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches`);
  if (!res.ok) throw new Error(`List branches failed: ${res.status}`);
  return res.json();
}

export async function getBranch(
  optimizationId: string,
  branchId: string
): Promise<any> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches/${branchId}`);
  if (!res.ok) throw new Error(`Get branch failed: ${res.status}`);
  return res.json();
}

export async function selectBranch(
  optimizationId: string,
  body: { branch_id: string; reason?: string }
): Promise<any> {
  const res = await apiFetch(`${BASE}/api/optimize/${optimizationId}/branches/select`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`Select branch failed: ${res.status}`);
  return res.json();
}

export async function compareBranches(
  optimizationId: string,
  branchA: string,
  branchB: string
): Promise<any> {
  const res = await apiFetch(
    `${BASE}/api/optimize/${optimizationId}/branches/compare?branch_a=${encodeURIComponent(branchA)}&branch_b=${encodeURIComponent(branchB)}`
  );
  if (!res.ok) throw new Error(`Compare branches failed: ${res.status}`);
  return res.json();
}
