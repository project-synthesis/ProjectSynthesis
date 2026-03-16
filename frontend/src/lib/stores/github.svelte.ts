// frontend/src/lib/stores/github.svelte.ts
import { githubMe, githubLogin, githubLogout, githubRepos, githubLink, githubLinked, githubUnlink } from '$lib/api/client';
import type { GitHubUser, LinkedRepo } from '$lib/api/client';

class GitHubStore {
  user = $state<GitHubUser | null>(null);
  linkedRepo = $state<LinkedRepo | null>(null);
  repos = $state<any[]>([]);
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
    } catch (err: any) {
      this.error = err.message;
    }
  }

  async logout() {
    try {
      await githubLogout();
      this.user = null;
      this.linkedRepo = null;
      this.repos = [];
    } catch (err: any) {
      this.error = err.message;
    }
  }

  async loadRepos() {
    this.loading = true;
    try {
      const response = await githubRepos();
      this.repos = response.repos;
    } catch (err: any) {
      this.error = err.message;
    } finally {
      this.loading = false;
    }
  }

  async linkRepo(fullName: string) {
    try {
      this.linkedRepo = await githubLink(fullName);
    } catch (err: any) {
      this.error = err.message;
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
    } catch (err: any) {
      this.error = err.message;
    }
  }
}

export const githubStore = new GitHubStore();
