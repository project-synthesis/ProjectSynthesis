export type Activity = 'files' | 'history' | 'chains' | 'templates' | 'github' | 'settings' | 'search';

function loadNumber(key: string, fallback: number): number {
  if (typeof window === 'undefined') return fallback;
  const v = localStorage.getItem(key);
  if (v !== null) {
    const n = parseInt(v, 10);
    if (!isNaN(n)) return n;
  }
  return fallback;
}

function loadBool(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback;
  const v = localStorage.getItem(key);
  if (v !== null) return v === 'true';
  return fallback;
}

function loadActivity(): Activity {
  if (typeof window === 'undefined') return 'files';
  const v = sessionStorage.getItem('pf_activeActivity');
  if (v && ['files', 'history', 'chains', 'templates', 'github', 'settings', 'search'].includes(v)) {
    return v as Activity;
  }
  return 'files';
}

class WorkbenchStore {
  activeActivity = $state<Activity>(loadActivity());
  navigatorCollapsed = $state(loadBool('pf_navigatorCollapsed', false));
  inspectorCollapsed = $state(loadBool('pf_inspectorCollapsed', false));
  navigatorWidth = $state(Math.max(160, Math.min(480, loadNumber('pf_navigatorWidth', 240))));
  inspectorWidth = $state(Math.max(180, Math.min(480, loadNumber('pf_inspectorWidth', 280))));
  provider = $state<'anthropic' | 'openai' | 'claude_cli' | 'anthropic_api' | 'unknown'>('unknown');
  providerModel = $state('');
  isConnected = $state(false);
  mcpConnected = $state(false);
  githubOAuthEnabled = $state(false);
  showOnboarding = $state(false);

  get navCssWidth() {
    return this.navigatorCollapsed ? '0px' : `${this.navigatorWidth}px`;
  }

  get inspectorCssWidth() {
    return this.inspectorCollapsed ? '0px' : `${this.inspectorWidth}px`;
  }

  setNavigatorCollapsed(v: boolean) {
    this.navigatorCollapsed = v;
    if (typeof window !== 'undefined') localStorage.setItem('pf_navigatorCollapsed', String(v));
  }

  setInspectorCollapsed(v: boolean) {
    this.inspectorCollapsed = v;
    if (typeof window !== 'undefined') localStorage.setItem('pf_inspectorCollapsed', String(v));
  }

  toggleNavigator() {
    this.setNavigatorCollapsed(!this.navigatorCollapsed);
  }

  toggleInspector() {
    this.setInspectorCollapsed(!this.inspectorCollapsed);
  }

  setActivity(activity: Activity) {
    if (this.activeActivity === activity && !this.navigatorCollapsed) {
      this.setNavigatorCollapsed(true);
    } else {
      this.activeActivity = activity;
      this.setNavigatorCollapsed(false);
    }
    if (typeof window !== 'undefined') {
      sessionStorage.setItem('pf_activeActivity', activity);
    }
  }

  setNavigatorWidth(w: number) {
    this.navigatorWidth = Math.max(160, Math.min(480, w));
    if (typeof window !== 'undefined') {
      localStorage.setItem('pf_navigatorWidth', String(this.navigatorWidth));
    }
  }

  setInspectorWidth(w: number) {
    this.inspectorWidth = Math.max(180, Math.min(480, w));
    if (typeof window !== 'undefined') {
      localStorage.setItem('pf_inspectorWidth', String(this.inspectorWidth));
    }
  }

  setGithubOAuthEnabled(v: boolean) {
    this.githubOAuthEnabled = v;
  }
}

export const workbench = new WorkbenchStore();
