export type Activity = 'files' | 'history' | 'chains' | 'github' | 'settings' | 'search';

class WorkbenchStore {
  activeActivity = $state<Activity>('files');
  navigatorCollapsed = $state(false);
  inspectorCollapsed = $state(false);
  navigatorWidth = $state(240);
  inspectorWidth = $state(280);
  provider = $state<'anthropic' | 'openai' | 'claude_cli' | 'anthropic_api' | 'unknown'>('unknown');
  providerModel = $state('');
  isConnected = $state(false);

  get navCssWidth() {
    return this.navigatorCollapsed ? '0px' : `${this.navigatorWidth}px`;
  }

  get inspectorCssWidth() {
    return this.inspectorCollapsed ? '0px' : `${this.inspectorWidth}px`;
  }

  toggleNavigator() {
    this.navigatorCollapsed = !this.navigatorCollapsed;
  }

  toggleInspector() {
    this.inspectorCollapsed = !this.inspectorCollapsed;
  }

  setActivity(activity: Activity) {
    if (this.activeActivity === activity && !this.navigatorCollapsed) {
      this.navigatorCollapsed = true;
    } else {
      this.activeActivity = activity;
      this.navigatorCollapsed = false;
    }
  }

  setNavigatorWidth(w: number) {
    this.navigatorWidth = Math.max(160, Math.min(480, w));
  }

  setInspectorWidth(w: number) {
    this.inspectorWidth = Math.max(180, Math.min(480, w));
  }
}

export const workbench = new WorkbenchStore();
