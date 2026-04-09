import { describe, it, expect, afterEach, vi } from 'vitest';
import {
  apiFetch,
  tryFetch,
  ApiError,
  getHealth,
  getHistory,
  submitFeedback,
  getProviders,
  getSettings,
  getApiKey,
  setApiKey,
  deleteApiKey,
  getPreferences,
  patchPreferences,
  getStrategies,
  getStrategy,
  updateStrategy,
  getOptimization,
  savePassthrough,
  githubMe,
  githubLogout,
  githubRepos,
  githubLink,
  githubLinked,
  githubUnlink,
  getRefinementVersions,
  rollbackRefinement,
  optimizeSSE,
  refineSSE,
  connectEventStream,
} from './client';
import {
  mockFetch,
  mockHealthResponse,
  mockOptimizationResult,
  mockHistoryItem,
  mockRefinementTurn,
  mockRefinementBranch,
  mockStrategyInfo,
} from '../test-utils';

const BASE_URL = 'http://localhost:8000/api';

afterEach(() => {
  vi.restoreAllMocks();
});

// ── ApiError ────────────────────────────────────────────────────

describe('ApiError', () => {
  it('sets status and message', () => {
    const err = new ApiError(404, 'Not found');
    expect(err.status).toBe(404);
    expect(err.message).toBe('Not found');
  });

  it('is an instance of Error', () => {
    const err = new ApiError(500, 'Server error');
    expect(err).toBeInstanceOf(Error);
  });

  it('has name ApiError', () => {
    const err = new ApiError(400, 'Bad request');
    expect(err.name).toBe('ApiError');
  });
});

// ── apiFetch ────────────────────────────────────────────────────

describe('apiFetch', () => {
  it('parses and returns JSON on success', async () => {
    mockFetch([{ match: '/health', response: { status: 'ok' } }]);
    const result = await apiFetch('/health');
    expect(result).toEqual({ status: 'ok' });
  });

  it('throws ApiError with correct status on HTTP error', async () => {
    mockFetch([{ match: '/health', response: { detail: 'Server error' }, status: 500 }]);
    await expect(apiFetch('/health')).rejects.toMatchObject({
      status: 500,
      message: 'Server error',
    });
  });

  it('uses detail field from error body', async () => {
    mockFetch([{ match: '/test', response: { detail: 'Custom error message' }, status: 422 }]);
    try {
      await apiFetch('/test');
      expect.fail('Should have thrown');
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(422);
      expect((e as ApiError).message).toBe('Custom error message');
    }
  });

  it('throws on network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(apiFetch('/health')).rejects.toThrow('Failed to fetch');
  });

  it('prefixes BASE_URL and sends credentials', async () => {
    const mock = mockFetch([{ match: '/health', response: {} }]);
    await apiFetch('/health');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toBe(`${BASE_URL}/health`);
    expect((opts as RequestInit).credentials).toBe('include');
  });
});

// ── tryFetch ────────────────────────────────────────────────────

describe('tryFetch', () => {
  it('returns parsed data on success', async () => {
    mockFetch([{ match: '/health', response: { status: 'ok' } }]);
    const result = await tryFetch('/health');
    expect(result).toEqual({ status: 'ok' });
  });

  it('returns null on HTTP error', async () => {
    mockFetch([{ match: '/health', response: { detail: 'error' }, status: 401 }]);
    const result = await tryFetch('/health');
    expect(result).toBeNull();
  });

  it('returns null on network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Network failure')));
    const result = await tryFetch('/health');
    expect(result).toBeNull();
  });

  it('returns null on 404', async () => {
    mockFetch([{ match: '/unknown', response: 'Not Found', status: 404 }]);
    const result = await tryFetch('/unknown');
    expect(result).toBeNull();
  });
});

// ── getHealth ───────────────────────────────────────────────────

describe('getHealth', () => {
  it('calls GET /health and returns health data', async () => {
    const health = mockHealthResponse();
    const mock = mockFetch([{ match: '/health', response: health }]);
    const result = await getHealth();
    expect(result.status).toBe('ok');
    expect(result.provider).toBe('claude-cli');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/health');
  });
});

// ── getHistory ──────────────────────────────────────────────────

describe('getHistory', () => {
  const histResp = {
    total: 1, count: 1, offset: 0, has_more: false, next_offset: null,
    items: [mockHistoryItem()],
  };

  it('calls GET /history with no params', async () => {
    const mock = mockFetch([{ match: '/history', response: histResp }]);
    const result = await getHistory();
    expect(result.items).toHaveLength(1);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/history');
    expect(url).not.toContain('?');
  });

  it('appends query params when provided', async () => {
    const mock = mockFetch([{ match: '/history', response: histResp }]);
    await getHistory({ offset: 10, limit: 20, sort_by: 'created_at', sort_order: 'desc' });
    const [url] = mock.mock.calls[0];
    expect(url).toContain('offset=10');
    expect(url).toContain('limit=20');
    expect(url).toContain('sort_by=created_at');
    expect(url).toContain('sort_order=desc');
  });

  it('supports task_type and status filters', async () => {
    const mock = mockFetch([{ match: '/history', response: histResp }]);
    await getHistory({ task_type: 'coding', status: 'complete' });
    const [url] = mock.mock.calls[0];
    expect(url).toContain('task_type=coding');
    expect(url).toContain('status=complete');
  });
});

// ── submitFeedback ──────────────────────────────────────────────

describe('submitFeedback', () => {
  it('sends POST /feedback with correct body', async () => {
    const feedbackResp = { id: 'fb-1', optimization_id: 'opt-1', rating: 'good', comment: null, created_at: '2026-01-01T00:00:00Z' };
    const mock = mockFetch([{ match: '/feedback', response: feedbackResp }]);
    const result = await submitFeedback('opt-1', 'good', 'Great!');
    expect(result.rating).toBe('good');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/feedback');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.optimization_id).toBe('opt-1');
    expect(body.rating).toBe('good');
    expect(body.comment).toBe('Great!');
  });

  it('sends feedback without comment', async () => {
    const mock = mockFetch([{ match: '/feedback', response: { id: 'fb-2', optimization_id: 'opt-1', rating: 'bad', comment: null, created_at: '' } }]);
    await submitFeedback('opt-1', 'bad');
    const [, opts] = mock.mock.calls[0];
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.comment).toBeUndefined();
  });
});

// ── getProviders ────────────────────────────────────────────────

describe('getProviders', () => {
  it('calls GET /providers', async () => {
    const mock = mockFetch([{ match: '/providers', response: { active_provider: 'claude-cli', available: ['claude_cli'], routing_tiers: ['internal'] } }]);
    const result = await getProviders();
    expect(result.active_provider).toBe('claude-cli');
    expect(result.routing_tiers).toEqual(['internal']);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/providers');
  });
});

// ── getSettings ─────────────────────────────────────────────────

describe('getSettings', () => {
  it('calls GET /settings', async () => {
    const mock = mockFetch([{
      match: '/settings',
      response: {
        max_raw_prompt_chars: 10000,
        max_context_tokens: 4096,
        optimize_rate_limit: '10/minute',
        feedback_rate_limit: '20/minute',
        embedding_model: 'all-MiniLM-L6-v2',
        trace_retention_days: 30,
      },
    }]);
    const result = await getSettings();
    expect(result.max_raw_prompt_chars).toBe(10000);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/settings');
  });
});

// ── API Key Management ───────────────────────────────────────────

describe('getApiKey', () => {
  it('calls GET /provider/api-key', async () => {
    const mock = mockFetch([{ match: '/provider/api-key', response: { configured: true, masked_key: 'sk-...xyz' } }]);
    const result = await getApiKey();
    expect(result.configured).toBe(true);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/provider/api-key');
  });
});

describe('setApiKey', () => {
  it('sends PATCH /provider/api-key with api_key in body', async () => {
    const mock = mockFetch([{ match: '/provider/api-key', response: { configured: true, masked_key: 'sk-...abc' } }]);
    const result = await setApiKey('sk-test-key');
    expect(result.configured).toBe(true);
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/provider/api-key');
    expect((opts as RequestInit).method).toBe('PATCH');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.api_key).toBe('sk-test-key');
  });
});

describe('deleteApiKey', () => {
  it('sends DELETE /provider/api-key', async () => {
    const mock = mockFetch([{ match: '/provider/api-key', response: { configured: false, masked_key: null } }]);
    const result = await deleteApiKey();
    expect(result.configured).toBe(false);
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/provider/api-key');
    expect((opts as RequestInit).method).toBe('DELETE');
  });
});

// ── Preferences ─────────────────────────────────────────────────

describe('getPreferences', () => {
  it('calls GET /preferences', async () => {
    const mock = mockFetch([{ match: '/preferences', response: { enable_scoring: true, default_strategy: 'auto' } }]);
    const result = await getPreferences();
    expect(result.enable_scoring).toBe(true);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/preferences');
  });
});

describe('patchPreferences', () => {
  it('sends PATCH /preferences with updates', async () => {
    const mock = mockFetch([{ match: '/preferences', response: { enable_scoring: false, default_strategy: 'chain-of-thought' } }]);
    const result = await patchPreferences({ enable_scoring: false });
    expect(result.enable_scoring).toBe(false);
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/preferences');
    expect((opts as RequestInit).method).toBe('PATCH');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.enable_scoring).toBe(false);
  });
});

// ── Strategies ──────────────────────────────────────────────────

describe('getStrategies', () => {
  it('calls GET /strategies', async () => {
    const mock = mockFetch([{ match: '/strategies', response: [mockStrategyInfo()] }]);
    const result = await getStrategies();
    expect(result).toHaveLength(1);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/strategies');
  });
});

describe('getStrategy', () => {
  it('calls GET /strategies/:name', async () => {
    const mock = mockFetch([{ match: '/strategies/chain-of-thought', response: { name: 'chain-of-thought', content: '## Step by step\n...' } }]);
    const result = await getStrategy('chain-of-thought');
    expect(result.name).toBe('chain-of-thought');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/strategies/chain-of-thought');
  });
});

describe('updateStrategy', () => {
  it('sends PUT /strategies/:name with content', async () => {
    const mock = mockFetch([{ match: '/strategies/chain-of-thought', response: { name: 'chain-of-thought', content: 'New content', warnings: [] } }]);
    const result = await updateStrategy('chain-of-thought', 'New content');
    expect(result.warnings).toEqual([]);
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/strategies/chain-of-thought');
    expect((opts as RequestInit).method).toBe('PUT');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.content).toBe('New content');
  });
});

// ── getOptimization ─────────────────────────────────────────────

describe('getOptimization', () => {
  it('calls GET /optimize/:traceId', async () => {
    const opt = mockOptimizationResult();
    const mock = mockFetch([{ match: '/optimize/trace-1', response: opt }]);
    const result = await getOptimization('trace-1');
    expect(result.trace_id).toBe('trace-1');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/optimize/trace-1');
  });
});

// ── savePassthrough ─────────────────────────────────────────────

describe('savePassthrough', () => {
  it('sends POST /optimize/passthrough/save with correct fields', async () => {
    const opt = mockOptimizationResult();
    const mock = mockFetch([{ match: '/optimize/passthrough/save', response: opt }]);
    await savePassthrough('trace-1', 'Optimized prompt text', 'Added clarity');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/optimize/passthrough/save');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.trace_id).toBe('trace-1');
    expect(body.optimized_prompt).toBe('Optimized prompt text');
    expect(body.changes_summary).toBe('Added clarity');
  });

  it('omits changes_summary when not provided', async () => {
    const mock = mockFetch([{ match: '/optimize/passthrough/save', response: mockOptimizationResult() }]);
    await savePassthrough('trace-1', 'Optimized text');
    const [, opts] = mock.mock.calls[0];
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.changes_summary).toBeUndefined();
  });
});

// ── GitHub ───────────────────────────────────────────────────────

// githubLogin test removed — function deprecated in favor of device flow.

describe('githubMe', () => {
  it('calls GET /github/auth/me', async () => {
    const mock = mockFetch([{ match: '/github/auth/me', response: { login: 'testuser', avatar_url: 'https://example.com/avatar.png' } }]);
    const result = await githubMe();
    expect(result!.login).toBe('testuser');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/github/auth/me');
  });
});

describe('githubLogout', () => {
  it('sends POST /github/auth/logout', async () => {
    const mock = mockFetch([{ match: '/github/auth/logout', response: null }]);
    await githubLogout();
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/github/auth/logout');
    expect((opts as RequestInit).method).toBe('POST');
  });
});

describe('githubRepos', () => {
  it('calls GET /github/repos with page param', async () => {
    const mock = mockFetch([{ match: '/github/repos', response: { repos: [], count: 0 } }]);
    await githubRepos(2);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/github/repos');
    expect(url).toContain('page=2');
  });

  it('defaults to page 1', async () => {
    const mock = mockFetch([{ match: '/github/repos', response: { repos: [], count: 0 } }]);
    await githubRepos();
    const [url] = mock.mock.calls[0];
    expect(url).toContain('page=1');
  });
});

describe('githubLink', () => {
  it('sends POST /github/repos/link with full_name', async () => {
    const linkedRepo = { id: 'repo-1', full_name: 'user/my-repo', default_branch: 'main', branch: null, language: 'TypeScript' };
    const mock = mockFetch([{ match: '/github/repos/link', response: linkedRepo }]);
    const result = await githubLink('user/my-repo');
    expect(result.full_name).toBe('user/my-repo');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/github/repos/link');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.full_name).toBe('user/my-repo');
  });
});

describe('githubLinked', () => {
  it('calls GET /github/repos/linked', async () => {
    const mock = mockFetch([{ match: '/github/repos/linked', response: { id: 'repo-1', full_name: 'user/my-repo', default_branch: 'main', branch: null, language: null } }]);
    const result = await githubLinked();
    expect(result!.full_name).toBe('user/my-repo');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/github/repos/linked');
  });
});

describe('githubUnlink', () => {
  it('sends DELETE /github/repos/unlink', async () => {
    const mock = mockFetch([{ match: '/github/repos/unlink', response: null }]);
    await githubUnlink();
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/github/repos/unlink');
    expect((opts as RequestInit).method).toBe('DELETE');
  });
});

// ── Refinement ───────────────────────────────────────────────────

describe('getRefinementVersions', () => {
  it('calls GET /refine/:id/versions', async () => {
    const versResp = { optimization_id: 'opt-1', versions: [mockRefinementTurn()] };
    const mock = mockFetch([{ match: '/refine/opt-1/versions', response: versResp }]);
    const result = await getRefinementVersions('opt-1');
    expect(result.optimization_id).toBe('opt-1');
    expect(result.versions).toHaveLength(1);
    const [url] = mock.mock.calls[0];
    expect(url).toContain('/refine/opt-1/versions');
    expect(url).not.toContain('branch_id');
  });

  it('appends branch_id when provided', async () => {
    const versResp = { optimization_id: 'opt-1', versions: [] };
    const mock = mockFetch([{ match: '/refine/opt-1/versions', response: versResp }]);
    await getRefinementVersions('opt-1', 'branch-abc');
    const [url] = mock.mock.calls[0];
    expect(url).toContain('branch_id=branch-abc');
  });
});

describe('rollbackRefinement', () => {
  it('sends POST /refine/:id/rollback with to_version', async () => {
    const branch = mockRefinementBranch();
    const mock = mockFetch([{ match: '/refine/opt-1/rollback', response: branch }]);
    const result = await rollbackRefinement('opt-1', 3);
    expect(result.id).toBe('branch-main');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/refine/opt-1/rollback');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.to_version).toBe(3);
  });
});

// ── optimizeSSE ──────────────────────────────────────────────────

describe('optimizeSSE', () => {
  it('calls onEvent for each data line and onComplete when done', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"event":"phase","phase":"analyzing"}\n\n'));
        controller.enqueue(new TextEncoder().encode('data: {"event":"complete"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));

    const events: unknown[] = [];
    const onEvent = vi.fn((e) => events.push(e));
    const onError = vi.fn();
    const onComplete = vi.fn();

    const controller = optimizeSSE('My prompt', 'auto', onEvent, onError, onComplete);
    // Wait for the stream to process
    await vi.waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(onEvent).toHaveBeenCalledTimes(2);
    expect(onError).not.toHaveBeenCalled();
    expect(events[0]).toEqual({ event: 'phase', phase: 'analyzing' });
    controller.abort();
  });

  it('sends POST to /optimize with prompt and strategy', async () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const controller = optimizeSSE('Test prompt', 'chain-of-thought', vi.fn(), vi.fn(), vi.fn());
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/optimize');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.prompt).toBe('Test prompt');
    expect(body.strategy).toBe('chain-of-thought');
    controller.abort();
  });

  it('includes applied_pattern_ids when provided', async () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const controller = optimizeSSE('prompt', null, vi.fn(), vi.fn(), vi.fn(), ['mp-1', 'mp-2']);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.applied_pattern_ids).toEqual(['mp-1', 'mp-2']);
    controller.abort();
  });

  it('omits applied_pattern_ids when null', async () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const controller = optimizeSSE('prompt', 'auto', vi.fn(), vi.fn(), vi.fn(), null);
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.applied_pattern_ids).toBeUndefined();
    controller.abort();
  });

  it('calls onError when fetch returns non-OK response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: 'Unauthorized' }), { status: 401 })
    ));

    const onError = vi.fn();
    const onComplete = vi.fn();
    optimizeSSE('prompt', null, vi.fn(), onError, onComplete);
    await vi.waitFor(() => expect(onError).toHaveBeenCalled());
    expect(onComplete).not.toHaveBeenCalled();
    const err = onError.mock.calls[0][0];
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
  });

  it('returns an AbortController', () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));
    const controller = optimizeSSE('prompt', null, vi.fn(), vi.fn(), vi.fn());
    expect(controller).toBeInstanceOf(AbortController);
    controller.abort();
  });

  it('does not call onError when aborted', async () => {
    // A stream that never ends
    const stream = new ReadableStream({
      start() { /* never enqueue */ },
    });
    const fetchMock = vi.fn().mockImplementation(() => {
      return Promise.reject(Object.assign(new Error('AbortError'), { name: 'AbortError' }));
    });
    vi.stubGlobal('fetch', fetchMock);

    const onError = vi.fn();
    const controller = optimizeSSE('prompt', null, vi.fn(), onError, vi.fn());
    controller.abort();
    // Give event loop a tick
    await Promise.resolve();
    await Promise.resolve();
    expect(onError).not.toHaveBeenCalled();
  });
});

// ── refineSSE ────────────────────────────────────────────────────

describe('refineSSE', () => {
  it('calls onEvent and onComplete for a refinement stream', async () => {
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: {"event":"turn","version":1}\n\n'));
        controller.enqueue(new TextEncoder().encode('data: {"event":"complete"}\n\n'));
        controller.close();
      },
    });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));

    const onEvent = vi.fn();
    const onComplete = vi.fn();
    const controller = refineSSE('opt-1', 'Make it clearer', 'branch-main', onEvent, vi.fn(), onComplete);
    await vi.waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(onEvent).toHaveBeenCalledTimes(2);
    controller.abort();
  });

  it('sends POST to /refine with correct body', async () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const controller = refineSSE('opt-1', 'Refine this', 'branch-abc', vi.fn(), vi.fn(), vi.fn());
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toContain('/refine');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.optimization_id).toBe('opt-1');
    expect(body.refinement_request).toBe('Refine this');
    expect(body.branch_id).toBe('branch-abc');
    controller.abort();
  });

  it('omits branch_id when null', async () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);

    const controller = refineSSE('opt-1', 'Refine this', null, vi.fn(), vi.fn(), vi.fn());
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, opts] = fetchMock.mock.calls[0];
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.branch_id).toBeUndefined();
    controller.abort();
  });

  it('returns an AbortController', () => {
    const stream = new ReadableStream({ start(c) { c.close(); } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { status: 200 })));
    const controller = refineSSE('opt-1', 'request', null, vi.fn(), vi.fn(), vi.fn());
    expect(controller).toBeInstanceOf(AbortController);
    controller.abort();
  });
});

// ── connectEventStream ───────────────────────────────────────────

describe('connectEventStream', () => {
  it('creates an EventSource with the events URL', () => {
    const handler = vi.fn();
    const es = connectEventStream(handler);
    expect(es.url).toContain('/api/events');
    es.close();
  });

  it('dispatches named events to the handler', async () => {
    const handler = vi.fn();
    const es = connectEventStream(handler) as unknown as {
      __simulateEvent: (type: string, data: string) => void;
      close: () => void;
    };

    es.__simulateEvent('optimization_created', JSON.stringify({ id: 'opt-1' }));
    expect(handler).toHaveBeenCalledWith('optimization_created', { id: 'opt-1' });
    es.close();
  });

  it('handles all registered event types', async () => {
    const handler = vi.fn();
    const es = connectEventStream(handler) as unknown as {
      __simulateEvent: (type: string, data: string) => void;
      close: () => void;
    };

    const eventTypes = [
      'optimization_created', 'optimization_analyzed',
      'feedback_submitted', 'refinement_turn',
      'optimization_failed', 'strategy_changed',
      'taxonomy_changed', 'routing_state_changed',
    ];
    for (const type of eventTypes) {
      es.__simulateEvent(type, JSON.stringify({ type }));
    }
    expect(handler).toHaveBeenCalledTimes(eventTypes.length);
    es.close();
  });

  it('ignores malformed JSON in events', async () => {
    const handler = vi.fn();
    const es = connectEventStream(handler) as unknown as {
      __simulateEvent: (type: string, data: string) => void;
      close: () => void;
    };

    // Should not throw
    es.__simulateEvent('optimization_created', 'not-valid-json{{{');
    expect(handler).not.toHaveBeenCalled();
    es.close();
  });

  it('returns the EventSource instance', () => {
    const handler = vi.fn();
    const es = connectEventStream(handler);
    // MockEventSource from test-setup
    expect(es).toBeTruthy();
    expect(typeof es.addEventListener).toBe('function');
    expect(typeof es.close).toBe('function');
    es.close();
  });
});
