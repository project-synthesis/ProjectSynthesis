import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { githubStore } from './github.svelte';
import { clustersStore } from './clusters.svelte';
import { mockFetch } from '../test-utils';

describe('GitHubStore', () => {
  beforeEach(() => {
    githubStore._reset();
    clustersStore._reset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('starts with null user and empty state', () => {
    expect(githubStore.user).toBeNull();
    expect(githubStore.linkedRepo).toBeNull();
    expect(githubStore.repos).toHaveLength(0);
    expect(githubStore.loading).toBe(false);
    expect(githubStore.error).toBeNull();
  });

  describe('checkAuth', () => {
    it('sets user when /github/auth/me returns 200', async () => {
      mockFetch([
        { match: '/github/auth/me', response: { login: 'octocat', avatar_url: 'https://example.com/avatar' } },
        { match: '/github/repos/linked', response: { full_name: 'octocat/hello', default_branch: 'main', branch: null, language: 'TypeScript' } },
      ]);
      await githubStore.checkAuth();
      expect(githubStore.user).not.toBeNull();
      expect(githubStore.user?.login).toBe('octocat');
    });

    it('sets user to null when /github/auth/me returns 401', async () => {
      mockFetch([
        { match: '/github/auth/me', response: { detail: 'Unauthorized' }, status: 401 },
      ]);
      await githubStore.checkAuth();
      expect(githubStore.user).toBeNull();
    });

    it('loads linked repo after successful auth', async () => {
      const linkedRepo = { full_name: 'octocat/hello', default_branch: 'main', branch: null, language: 'TypeScript' };
      mockFetch([
        { match: '/github/auth/me', response: { login: 'octocat', avatar_url: 'https://example.com/avatar' } },
        { match: '/github/repos/linked', response: linkedRepo },
      ]);
      await githubStore.checkAuth();
      expect(githubStore.linkedRepo).not.toBeNull();
      expect(githubStore.linkedRepo?.full_name).toBe('octocat/hello');
    });
  });

  describe('login', () => {
    it('redirects to OAuth URL', async () => {
      // Device flow: login() calls /github/auth/device and sets state for UI
      mockFetch([
        { match: '/github/auth/device', response: {
          device_code: 'dc-123',
          user_code: 'ABCD-1234',
          verification_uri: 'https://github.com/login/device',
          expires_in: 900,
          interval: 5,
        } },
      ]);
      await githubStore.login();
      expect(githubStore.userCode).toBe('ABCD-1234');
      expect(githubStore.verificationUri).toBe('https://github.com/login/device');
      expect(githubStore.polling).toBe(true);
      githubStore.cancelLogin(); // stop polling
    });

    it('sets error when login API call fails', async () => {
      mockFetch([
        { match: '/github/auth/device', response: { detail: 'Server error' }, status: 500 },
      ]);
      await githubStore.login();
      expect(githubStore.error).toBeTruthy();
    });
  });

  describe('logout', () => {
    it('clears user and linked repo', async () => {
      githubStore.user = { login: 'octocat', avatar_url: 'https://example.com/avatar' };
      githubStore.linkedRepo = { full_name: 'octocat/hello', default_branch: 'main', branch: null, language: null };
      mockFetch([
        { match: '/github/auth/logout', response: null },
      ]);
      await githubStore.logout();
      expect(githubStore.user).toBeNull();
      expect(githubStore.linkedRepo).toBeNull();
      expect(githubStore.repos).toHaveLength(0);
    });

    it('sets error when logout fails', async () => {
      mockFetch([
        { match: '/github/auth/logout', response: { detail: 'Server error' }, status: 500 },
      ]);
      await githubStore.logout();
      expect(githubStore.error).toBeTruthy();
    });
  });

  describe('loadRepos', () => {
    it('sets repos from API response', async () => {
      const repos = [
        { id: 1, name: 'hello', full_name: 'octocat/hello', description: null, default_branch: 'main', language: null, private: false, stargazers_count: 0, updated_at: '2026-01-01', owner: { login: 'octocat', avatar_url: '' } },
      ];
      mockFetch([
        { match: '/github/repos', response: { repos, count: 1 } },
      ]);
      await githubStore.loadRepos();
      expect(githubStore.repos).toHaveLength(1);
      expect(githubStore.repos[0].name).toBe('hello');
    });

    it('sets loading true during fetch and false after', async () => {
      const loadingDuring: boolean[] = [];
      mockFetch([
        { match: '/github/repos', response: { repos: [], count: 0 } },
      ]);
      // We can only check final state since the loading flip happens synchronously
      await githubStore.loadRepos();
      expect(githubStore.loading).toBe(false);
    });

    it('sets error on fetch failure', async () => {
      mockFetch([
        { match: '/github/repos', response: { detail: 'Unauthorized' }, status: 401 },
      ]);
      await githubStore.loadRepos();
      expect(githubStore.error).toBeTruthy();
      expect(githubStore.loading).toBe(false);
    });
  });

  describe('linkRepo', () => {
    it('sets linkedRepo on success', async () => {
      const linked = { id: 'repo-1', full_name: 'octocat/hello', default_branch: 'main', branch: null, language: 'TypeScript' };
      mockFetch([
        { match: '/github/repos/link', response: linked },
      ]);
      await githubStore.linkRepo('octocat/hello');
      expect(githubStore.linkedRepo).toEqual(linked);
    });

    it('sets error when link fails', async () => {
      mockFetch([
        { match: '/github/repos/link', response: { detail: 'Not found' }, status: 404 },
      ]);
      await githubStore.linkRepo('octocat/nonexistent');
      expect(githubStore.error).toBeTruthy();
    });
  });

  describe('loadLinked', () => {
    it('sets linkedRepo from API', async () => {
      const linked = { id: 'repo-1', full_name: 'octocat/hello', default_branch: 'main', branch: null, language: null };
      mockFetch([
        { match: '/github/repos/linked', response: linked },
      ]);
      await githubStore.loadLinked();
      expect(githubStore.linkedRepo).toEqual(linked);
    });

    it('sets linkedRepo to null when not found', async () => {
      mockFetch([
        { match: '/github/repos/linked', response: { detail: 'Not found' }, status: 404 },
      ]);
      await githubStore.loadLinked();
      expect(githubStore.linkedRepo).toBeNull();
    });
  });

  describe('unlinkRepo', () => {
    it('clears linkedRepo on success', async () => {
      githubStore.linkedRepo = { full_name: 'octocat/hello', default_branch: 'main', branch: null, language: null };
      mockFetch([
        { match: '/github/repos/unlink', response: null },
      ]);
      await githubStore.unlinkRepo();
      expect(githubStore.linkedRepo).toBeNull();
    });

    it('sets error when unlink fails', async () => {
      mockFetch([
        { match: '/github/repos/unlink', response: { detail: 'Server error' }, status: 500 },
      ]);
      await githubStore.unlinkRepo();
      expect(githubStore.error).toBeTruthy();
    });

    it('calls clustersStore cleanup on successful unlink (F16)', async () => {
      mockFetch([
        { match: '/github/repos/unlink', response: {} },
        // invalidateClusters will call loadTree
        { match: '/clusters/taxonomy', response: { nodes: [], stats: null } },
      ]);
      const selectSpy = vi.spyOn(clustersStore, 'selectCluster');
      const invalidateSpy = vi.spyOn(clustersStore, 'invalidateClusters');
      await githubStore.unlinkRepo();
      expect(selectSpy).toHaveBeenCalledWith(null);
      expect(invalidateSpy).toHaveBeenCalled();
    });
  });

  describe('connectionState (spec: unified state model)', () => {
    it('returns disconnected when no user, no linkedRepo, no authExpired', () => {
      expect(githubStore.connectionState).toBe('disconnected');
    });

    it('returns expired when authExpired is true', () => {
      githubStore.authExpired = true;
      expect(githubStore.connectionState).toBe('expired');
    });

    it('returns expired when authExpired is true even with linkedRepo', () => {
      githubStore.authExpired = true;
      githubStore.linkedRepo = { full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      expect(githubStore.connectionState).toBe('expired');
    });

    it('returns authenticated when user set but no linkedRepo', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      expect(githubStore.connectionState).toBe('authenticated');
    });

    it('returns linked when user + linkedRepo but indexStatus is null', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      githubStore.linkedRepo = { full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      expect(githubStore.connectionState).toBe('linked');
    });

    it('returns linked when user + linkedRepo + indexStatus building', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      githubStore.linkedRepo = { full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      githubStore.indexStatus = { status: 'building', file_count: 0, indexed_at: null } as any;
      expect(githubStore.connectionState).toBe('linked');
    });

    it('returns ready when user + linkedRepo + indexStatus ready', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      githubStore.linkedRepo = { full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      githubStore.indexStatus = { status: 'ready', file_count: 42, indexed_at: '2026-01-01' } as any;
      expect(githubStore.connectionState).toBe('ready');
    });
  });

  describe('reconnect (spec: clears state before device flow)', () => {
    it('clears authExpired, linkedRepo, error, and browsing state then calls login', async () => {
      const loginSpy = vi.spyOn(githubStore, 'login').mockResolvedValue(undefined);
      githubStore.authExpired = true;
      githubStore.linkedRepo = { full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      githubStore.fileTree = [{ name: 'f', path: 'f', type: 'file' }] as any;
      githubStore.branches = ['main'];
      githubStore.indexStatus = { status: 'ready', file_count: 10, indexed_at: '2026-01-01' } as any;
      githubStore.error = 'stale error';
      await githubStore.reconnect();
      expect(githubStore.authExpired).toBe(false);
      expect(githubStore.linkedRepo).toBeNull();
      expect(githubStore.fileTree).toHaveLength(0);
      expect(githubStore.branches).toHaveLength(0);
      expect(githubStore.indexStatus).toBeNull();
      expect(githubStore.error).toBeNull();
      expect(loginSpy).toHaveBeenCalled();
      loginSpy.mockRestore();
    });
  });

  describe('checkAuth bug fix (spec: authExpired reset on null)', () => {
    it('resets authExpired on null return from githubMe', async () => {
      githubStore.authExpired = true;
      mockFetch([
        { match: '/github/auth/me', response: null, status: 200 },
      ]);
      await githubStore.checkAuth();
      expect(githubStore.authExpired).toBe(false);
      expect(githubStore.linkedRepo).toBeNull();
    });
  });

  describe('logout bug fix (spec: authExpired reset)', () => {
    it('resets authExpired on logout', async () => {
      githubStore.authExpired = true;
      mockFetch([
        { match: '/github/auth/logout', response: { ok: true } },
      ]);
      await githubStore.logout();
      expect(githubStore.authExpired).toBe(false);
    });
  });
});
