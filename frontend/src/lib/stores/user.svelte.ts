import type { AuthMeResponse } from '$lib/api/client';
import { patchAuthMe } from '$lib/api/client';

export interface UserPreferences {
  dismissedTips: string[];
  dismissedMilestones: string[];
  walkthroughCompleted: boolean;
  preferredStrategy: string;
}

const defaultPreferences: UserPreferences = {
  dismissedTips: [],
  dismissedMilestones: [],
  walkthroughCompleted: false,
  preferredStrategy: 'auto',
};

class UserStore {
  displayName = $state<string | null>(null);
  avatarUrl    = $state<string | null>(null);
  githubLogin  = $state<string | null>(null);
  email        = $state<string | null>(null);
  onboardingCompleted = $state(false);
  preferences = $state<UserPreferences>({ ...defaultPreferences });
  loading = $state(false);
  error   = $state<string | null>(null);

  get label(): string | null {
    return this.displayName ?? this.githubLogin;
  }

  get isNewUser(): boolean {
    return !this.onboardingCompleted;
  }

  setProfile(p: AuthMeResponse): void {
    this.displayName = p.display_name;
    this.avatarUrl   = p.avatar_url;
    this.githubLogin = p.github_login;
    this.email       = p.email;
    this.onboardingCompleted = p.onboarding_completed;
    // Merge preferences from backend with defaults
    const prefs = p.preferences as Partial<UserPreferences> | undefined;
    if (prefs && typeof prefs === 'object') {
      this.preferences = {
        dismissedTips: Array.isArray(prefs.dismissedTips) ? prefs.dismissedTips : [],
        dismissedMilestones: Array.isArray(prefs.dismissedMilestones) ? prefs.dismissedMilestones : [],
        walkthroughCompleted: !!prefs.walkthroughCompleted,
        preferredStrategy: prefs.preferredStrategy ?? 'auto',
      };
    } else {
      this.preferences = { ...defaultPreferences };
    }
    this.error = null;
  }

  clearProfile(): void {
    this.displayName = null;
    this.avatarUrl   = null;
    this.githubLogin = null;
    this.email       = null;
    this.onboardingCompleted = false;
    this.preferences = { ...defaultPreferences };
    this.loading = false;
    this.error = null;
  }

  dismissTip(tipId: string): void {
    if (!this.preferences.dismissedTips.includes(tipId)) {
      this.preferences = {
        ...this.preferences,
        dismissedTips: [...this.preferences.dismissedTips, tipId],
      };
      this._persistPreferences();
    }
  }

  dismissMilestone(milestoneId: string): void {
    if (!this.preferences.dismissedMilestones.includes(milestoneId)) {
      this.preferences = {
        ...this.preferences,
        dismissedMilestones: [...this.preferences.dismissedMilestones, milestoneId],
      };
      this._persistPreferences();
    }
  }

  setWalkthroughCompleted(): void {
    this.preferences = {
      ...this.preferences,
      walkthroughCompleted: true,
    };
    this._persistPreferences();
  }

  resetTips(): void {
    this.preferences = {
      ...this.preferences,
      dismissedTips: [],
      walkthroughCompleted: false,
    };
    this._persistPreferences();
  }

  private _persistPreferences(): void {
    const { dismissedTips, dismissedMilestones, walkthroughCompleted, preferredStrategy } = this.preferences;
    patchAuthMe({ preferences: { dismissedTips, dismissedMilestones, walkthroughCompleted, preferredStrategy } }).catch(() => {});
  }
}

export const user = new UserStore();
