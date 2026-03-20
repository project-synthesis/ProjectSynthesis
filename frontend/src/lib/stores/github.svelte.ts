// frontend/src/lib/stores/github.svelte.ts
import { githubMe, githubLogin, githubLogout, githubRepos, githubLink, githubLinked, githubUnlink } from '$lib/api/client';
import type { GitHubUser, LinkedRepo, GitHubRepository } from '$lib/api/client';

class GitHubStore {
  user = $state<GitHubUser | null>(null);
  linkedRepo = $state<LinkedRepo | null>(null);
  repos = $state<GitHubRepository[]>([]);
  loading = $state(false);
  error = $state<string | null>(null);

  async checkAuth() {
    // Use tryFetch to avoid 401 console noise (apiFetch throws on non-2xx)
    try {
      const { tryFetch } = await import('$lib/api/client');
      const user = await tryFetch<GitHubUser>('/github/auth/me');
      if (user) {
        this.user = user;
        await this.loadLinked();
      } else {
        this.user = null;
      }
    } catch {
      this.user = null;
    }
  }

  async login() {
    try {
      const { url } = await githubLogin();
      window.location.href = url;
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  async logout() {
    try {
      await githubLogout();
      this.user = null;
      this.linkedRepo = null;
      this.repos = [];
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  async loadRepos() {
    this.loading = true;
    try {
      const response = await githubRepos();
      this.repos = response.repos;
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    } finally {
      this.loading = false;
    }
  }

  async linkRepo(fullName: string) {
    try {
      this.linkedRepo = await githubLink(fullName);
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  async loadLinked() {
    try {
      this.linkedRepo = await githubLinked();
    } catch {
      this.linkedRepo = null;
    }
  }

  async unlinkRepo() {
    try {
      await githubUnlink();
      this.linkedRepo = null;
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Operation failed';
    }
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.user = null;
    this.linkedRepo = null;
    this.repos = [];
    this.loading = false;
    this.error = null;
  }
}

export const githubStore = new GitHubStore();
