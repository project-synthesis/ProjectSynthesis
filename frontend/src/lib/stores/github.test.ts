/**
 * github store — connection state machine, device-flow auth, _handleAuthError.
 *
 * Coverage focuses on the user-impact-critical paths: the 7-state
 * connectionState getter, the device-flow polling state machine, 401
 * auth-error handling, and the localStorage-backed UI tab.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/api/client', () => ({
  githubMe: vi.fn(),
  githubLogout: vi.fn(),
  githubRepos: vi.fn(),
  githubLink: vi.fn(),
  githubLinked: vi.fn(),
  githubUnlink: vi.fn(),
  githubDeviceRequest: vi.fn(),
  githubDevicePoll: vi.fn(),
  githubTree: vi.fn(),
  githubBranches: vi.fn(),
  githubFileContent: vi.fn(),
  githubReindex: vi.fn(),
  githubIndexStatus: vi.fn(),
  migrateProjects: vi.fn(),
}));
vi.mock('$lib/stores/clusters.svelte', () => ({
  clustersStore: { invalidate: vi.fn(), invalidateClusters: vi.fn() },
}));
vi.mock('$lib/stores/project.svelte', () => ({
  projectStore: {
    applyLinkResponse: vi.fn(),
    refresh: vi.fn().mockResolvedValue(undefined),
    lastMigrationCandidates: null,
    clearMigrationCandidates: vi.fn(),
    projects: [],
  },
}));
vi.mock('$lib/stores/toast.svelte', () => ({
  toastStore: {
    add: vi.fn(),
    addWithActions: vi.fn(),
  },
}));

import * as client from '$lib/api/client';

// We import the module fresh per test to reset the singleton's $state.
async function fresh() {
  vi.resetModules();
  const m = await import('./github.svelte');
  m.githubStore._reset?.();
  return m.githubStore;
}

afterEach(() => {
  vi.clearAllMocks();
  localStorage.removeItem('synthesis:github_tab');
});

describe('githubStore — connectionState state machine', () => {
  it('disconnected when no user and not expired', async () => {
    const store = await fresh();
    expect(store.connectionState).toBe('disconnected');
  });

  it('expired when authExpired flag set (independent of user)', async () => {
    const store = await fresh();
    store.authExpired = true;
    expect(store.connectionState).toBe('expired');
  });

  it('authenticated when user present but no linked repo', async () => {
    const store = await fresh();
    store.user = { login: 'u', avatar_url: '', name: null } as never;
    expect(store.connectionState).toBe('authenticated');
  });

  it('linked when user + repo present but no indexStatus yet', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    expect(store.connectionState).toBe('linked');
  });

  it('error when index_phase=error', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.indexStatus = { status: 'building', index_phase: 'error', synthesis_status: null } as never;
    expect(store.connectionState).toBe('error');
  });

  it('error when file status=error', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.indexStatus = { status: 'error', index_phase: null, synthesis_status: null } as never;
    expect(store.connectionState).toBe('error');
  });

  it('error when synthesis_status=error', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.indexStatus = { status: 'ready', index_phase: 'ready', synthesis_status: 'error' } as never;
    expect(store.connectionState).toBe('error');
  });

  it('indexing when phase in progress (fetching_tree/embedding/synthesizing)', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    for (const phase of ['fetching_tree', 'embedding', 'synthesizing']) {
      store.indexStatus = { status: 'building', index_phase: phase, synthesis_status: null } as never;
      expect(store.connectionState).toBe('indexing');
    }
  });

  it('indexing when synthesis still pending after file ready', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.indexStatus = { status: 'ready', index_phase: 'synthesizing', synthesis_status: 'running' } as never;
    expect(store.connectionState).toBe('indexing');
  });

  it('ready when file ready + phase ready + synthesis ready/skipped/null', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    for (const synth of ['ready', 'skipped', null]) {
      store.indexStatus = { status: 'ready', index_phase: 'ready', synthesis_status: synth } as never;
      expect(store.connectionState).toBe('ready');
    }
  });

  it('belt-and-braces: file ready + null phase + null synthesis = ready', async () => {
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.indexStatus = { status: 'ready', index_phase: null, synthesis_status: null } as never;
    expect(store.connectionState).toBe('ready');
  });

  it('still indexing if phase is stale "embedding" even when files ready', async () => {
    // Regression: phase must agree with status — guards against missed SSE.
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.indexStatus = { status: 'ready', index_phase: 'embedding', synthesis_status: 'ready' } as never;
    expect(store.connectionState).toBe('indexing');
  });
});

describe('githubStore — phaseLabel + indexErrorText', () => {
  it('phaseLabel reflects index_phase', async () => {
    const store = await fresh();
    expect(store.phaseLabel).toBe('');
    const cases: Array<[string | null, string]> = [
      ['fetching_tree', 'Fetching repo tree…'],
      ['embedding', 'Embedding files…'],
      ['synthesizing', 'Synthesizing context…'],
      ['ready', 'Ready'],
      ['error', 'Error'],
    ];
    for (const [phase, label] of cases) {
      store.indexStatus = { status: 'building', index_phase: phase, synthesis_status: null } as never;
      expect(store.phaseLabel).toBe(label);
    }
  });

  it('phaseLabel falls back to "Ready"/"Preparing…" when phase null', async () => {
    const store = await fresh();
    store.indexStatus = { status: 'ready', index_phase: null, synthesis_status: null } as never;
    expect(store.phaseLabel).toBe('Ready');
    store.indexStatus = { status: 'building', index_phase: null, synthesis_status: null } as never;
    expect(store.phaseLabel).toBe('Preparing…');
  });

  it('indexErrorText surfaces error_message > synthesis_error > null', async () => {
    const store = await fresh();
    expect(store.indexErrorText).toBeNull();
    store.indexStatus = { status: 'error', index_phase: 'error', synthesis_status: null, error_message: 'tree fetch fail' } as never;
    expect(store.indexErrorText).toBe('tree fetch fail');
    store.indexStatus = { status: 'ready', index_phase: 'error', synthesis_status: 'error', error_message: null, synthesis_error: 'haiku 503' } as never;
    expect(store.indexErrorText).toBe('haiku 503');
  });
});

describe('githubStore — UI tab persistence', () => {
  it('setUiTab is idempotent and persists to localStorage', async () => {
    const store = await fresh();
    store.setUiTab('files');
    expect(store.uiTab).toBe('files');
    expect(localStorage.getItem('synthesis:github_tab')).toBe('files');
    store.setUiTab('files'); // no-op
    store.setUiTab('info');
    expect(localStorage.getItem('synthesis:github_tab')).toBe('info');
  });
});

describe('githubStore — checkAuth + login flow', () => {
  it('checkAuth: user returned populates state and calls loadLinked', async () => {
    vi.mocked(client.githubMe).mockResolvedValue({ login: 'u' } as never);
    vi.mocked(client.githubLinked).mockResolvedValue(null);
    const store = await fresh();
    await store.checkAuth();
    expect(store.user?.login).toBe('u');
    expect(client.githubLinked).toHaveBeenCalled();
  });

  it('checkAuth: githubMe returns null clears state without flagging expired', async () => {
    vi.mocked(client.githubMe).mockResolvedValue(null);
    const store = await fresh();
    store.authExpired = true;
    await store.checkAuth();
    expect(store.user).toBeNull();
    expect(store.authExpired).toBe(false);
  });

  it('checkAuth: network error clears user but leaves authExpired alone', async () => {
    vi.mocked(client.githubMe).mockRejectedValue(new Error('DNS down'));
    const store = await fresh();
    store.authExpired = false;
    await store.checkAuth();
    expect(store.user).toBeNull();
    expect(store.authExpired).toBe(false);
  });

  it('login: device request failure surfaces error', async () => {
    vi.mocked(client.githubDeviceRequest).mockRejectedValue(new Error('rate limited'));
    const store = await fresh();
    await store.login();
    expect(store.error).toBe('rate limited');
    expect(store.userCode).toBeNull();
  });

  it('cancelLogin clears polling/userCode/verification/error', async () => {
    const store = await fresh();
    store.polling = true;
    store.userCode = 'ABC-123';
    store.verificationUri = 'https://github.com/login/device';
    store.error = 'something';
    store.cancelLogin();
    expect(store.polling).toBe(false);
    expect(store.userCode).toBeNull();
    expect(store.verificationUri).toBeNull();
    expect(store.error).toBeNull();
  });

  it('reconnect clears repo/tree/branches/indexStatus then calls login', async () => {
    vi.mocked(client.githubDeviceRequest).mockResolvedValue({
      device_code: 'dev', user_code: 'UC', verification_uri: 'https://github.com/login/device',
      interval: 5, expires_in: 900,
    } as never);
    vi.mocked(client.githubDevicePoll).mockResolvedValue({ status: 'authorization_pending' } as never);
    const store = await fresh();
    store.authExpired = true;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.fileTree = [{ name: 'x', path: 'x', type: 'file' }] as never;
    store.branches = ['main'];
    store.indexStatus = { status: 'ready' } as never;
    const promise = store.reconnect();
    // Don't wait for the polling loop — it's an infinite-by-design loop.
    store.cancelLogin();
    await promise;
    expect(store.authExpired).toBe(false);
    expect(store.linkedRepo).toBeNull();
    expect(store.fileTree).toEqual([]);
    expect(store.branches).toEqual([]);
    expect(store.indexStatus).toBeNull();
  });
});

describe('githubStore — logout', () => {
  it('logout clears user/linkedRepo/repos and resets authExpired', async () => {
    vi.mocked(client.githubLogout).mockResolvedValue(undefined as never);
    const store = await fresh();
    store.user = { login: 'u' } as never;
    store.linkedRepo = { full_name: 'org/r' } as never;
    store.repos = [{ full_name: 'org/r' }] as never;
    store.authExpired = true;
    await store.logout();
    expect(store.user).toBeNull();
    expect(store.linkedRepo).toBeNull();
    expect(store.repos).toEqual([]);
    expect(store.authExpired).toBe(false);
  });

  it('logout error surfaces in error field', async () => {
    vi.mocked(client.githubLogout).mockRejectedValue(new Error('500'));
    const store = await fresh();
    await store.logout();
    expect(store.error).toBe('500');
  });
});

describe('githubStore — loadRepos auth-error handling', () => {
  it('401 ApiError-shape sets authExpired and clears user', async () => {
    vi.mocked(client.githubRepos).mockRejectedValue({ status: 401, message: 'no auth' });
    const store = await fresh();
    store.user = { login: 'u' } as never;
    await store.loadRepos();
    expect(store.authExpired).toBe(true);
    expect(store.user).toBeNull();
  });

  it('plain Error containing "401" detected as auth failure', async () => {
    vi.mocked(client.githubRepos).mockRejectedValue(new Error('GitHub 401: token revoked'));
    const store = await fresh();
    store.user = { login: 'u' } as never;
    await store.loadRepos();
    expect(store.authExpired).toBe(true);
  });

  it('error containing "expired or revoked" string detected as auth failure', async () => {
    vi.mocked(client.githubRepos).mockRejectedValue(new Error('token has expired or revoked'));
    const store = await fresh();
    store.user = { login: 'u' } as never;
    await store.loadRepos();
    expect(store.authExpired).toBe(true);
  });

  it('non-auth errors leave authExpired alone, surface in error field', async () => {
    vi.mocked(client.githubRepos).mockRejectedValue(new Error('GitHub 500: internal server'));
    const store = await fresh();
    store.user = { login: 'u' } as never;
    await store.loadRepos();
    expect(store.authExpired).toBe(false);
    expect(store.error).toMatch(/500/);
  });
});
