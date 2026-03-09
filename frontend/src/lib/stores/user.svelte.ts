import type { AuthMeResponse } from '$lib/api/client';

class UserStore {
  displayName = $state<string | null>(null);
  avatarUrl    = $state<string | null>(null);
  githubLogin  = $state<string | null>(null);
  email        = $state<string | null>(null);
  onboardingCompleted = $state(false);
  loading = $state(false);
  error   = $state<string | null>(null);

  get label(): string | null {
    return this.displayName ?? this.githubLogin;
  }

  setProfile(p: AuthMeResponse): void {
    this.displayName = p.display_name;
    this.avatarUrl   = p.avatar_url;
    this.githubLogin = p.github_login;
    this.email       = p.email;
    this.onboardingCompleted = p.onboarding_completed;
    this.error = null;
  }

  clearProfile(): void {
    this.displayName = null;
    this.avatarUrl   = null;
    this.githubLogin = null;
    this.email       = null;
    this.onboardingCompleted = false;
    this.error = null;
  }
}

export const user = new UserStore();
