// frontend/src/lib/stores/github.svelte.ts
import { githubMe, githubLogout, githubRepos, githubLink, githubLinked, githubUnlink, githubDeviceRequest, githubDevicePoll } from '$lib/api/client';
import type { GitHubUser, LinkedRepo, GitHubRepository } from '$lib/api/client';

class GitHubStore {
  user = $state<GitHubUser | null>(null);
  linkedRepo = $state<LinkedRepo | null>(null);
  repos = $state<GitHubRepository[]>([]);
  loading = $state(false);
  error = $state<string | null>(null);

  // Device flow state
  userCode = $state<string | null>(null);
  verificationUri = $state<string | null>(null);
  polling = $state(false);
  private deviceCode: string | null = null;
  private pollInterval = 5;
  private deviceExpiry = 0;

  async checkAuth() {
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
    this.error = null;
    try {
      const data = await githubDeviceRequest();
      this.deviceCode = data.device_code;
      this.userCode = data.user_code;
      this.verificationUri = data.verification_uri;
      this.pollInterval = data.interval || 5;
      this.deviceExpiry = Date.now() + (data.expires_in || 900) * 1000;
      // Open GitHub device page in new tab
      window.open(data.verification_uri, '_blank');
      // Start polling for authorization
      this.startPolling();
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Failed to start GitHub auth';
    }
  }

  cancelLogin() {
    this.polling = false;
    this.userCode = null;
    this.verificationUri = null;
    this.deviceCode = null;
    this.error = null;
  }

  private async startPolling() {
    this.polling = true;
    while (this.polling && Date.now() < this.deviceExpiry) {
      await new Promise(r => setTimeout(r, this.pollInterval * 1000));
      if (!this.polling) break; // cancelled during wait
      try {
        const result = await githubDevicePoll(this.deviceCode!);
        if (result.status === 'success') {
          this.polling = false;
          this.userCode = null;
          this.verificationUri = null;
          this.deviceCode = null;
          await this.checkAuth();
          return;
        }
        if (result.status === 'slow_down') {
          this.pollInterval += 5;
        }
        if (result.status === 'expired_token') {
          this.polling = false;
          this.userCode = null;
          this.verificationUri = null;
          this.deviceCode = null;
          this.error = 'Authorization expired. Please try again.';
          return;
        }
        // authorization_pending — keep polling
      } catch {
        // Network error, keep polling
      }
    }
    // Timed out
    if (this.polling) {
      this.polling = false;
      this.userCode = null;
      this.deviceCode = null;
      this.error = 'Authorization timed out. Please try again.';
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

  async linkRepo(fullName: string, projectId?: string) {
    try {
      this.linkedRepo = await githubLink(fullName, projectId);
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
    this.userCode = null;
    this.verificationUri = null;
    this.polling = false;
    this.deviceCode = null;
  }
}

export const githubStore = new GitHubStore();
