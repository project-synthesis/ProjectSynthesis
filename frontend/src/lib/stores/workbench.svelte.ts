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
}

export const workbench = new WorkbenchStore();
