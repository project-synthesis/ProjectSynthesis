export type SubTab = 'edit' | 'pipeline' | 'history';

export interface EditorTab {
  id: string;
  label: string;
  type: 'prompt' | 'artifact' | 'chain' | 'strategy-ref';
  promptText?: string;
  dirty?: boolean;
  optimizationId?: string;
  strategy?: string;
}

const MAX_TABS = 8;

class EditorStore {
  openTabs = $state<EditorTab[]>([]);
  private _activeTabId = $state<string | null>(null);
  activeSubTab = $state<SubTab>('edit');
  private _lastAccessed = new Map<string, number>();

  get activeTabId(): string | null { return this._activeTabId; }
  set activeTabId(id: string | null) {
    this._activeTabId = id;
    if (id) this._lastAccessed.set(id, Date.now());
  }

  get activeTab(): EditorTab | undefined {
    return this.openTabs.find(t => t.id === this.activeTabId);
  }

  openTab(tab: EditorTab) {
    const existing = this.openTabs.find(t => t.id === tab.id);
    if (existing) {
      this.activeTabId = tab.id;
      return;
    }
    if (this.openTabs.length >= MAX_TABS) {
      // LRU: evict the tab with the oldest access time that is not currently active
      let oldestId = '';
      let oldestTime = Infinity;
      for (const t of this.openTabs) {
        if (t.id === this.activeTabId) continue;
        const tTime = this._lastAccessed.get(t.id) ?? 0;
        if (tTime < oldestTime) { oldestTime = tTime; oldestId = t.id; }
      }
      if (oldestId) {
        this.openTabs.splice(this.openTabs.findIndex(t => t.id === oldestId), 1);
        this._lastAccessed.delete(oldestId);
      }
    }
    this.openTabs.push(tab);
    this._lastAccessed.set(tab.id, Date.now());
    this._activeTabId = tab.id;
  }

  closeTab(id: string) {
    const idx = this.openTabs.findIndex(t => t.id === id);
    if (idx === -1) return;
    this.openTabs.splice(idx, 1);
    this._lastAccessed.delete(id);
    if (this.activeTabId === id) {
      this.activeTabId = this.openTabs.length > 0
        ? this.openTabs[Math.min(idx, this.openTabs.length - 1)].id
        : null;
    }
  }

  setSubTab(sub: SubTab) {
    this.activeSubTab = sub;
  }

  updateTabPrompt(id: string, text: string) {
    const tab = this.openTabs.find(t => t.id === id);
    if (tab) {
      tab.promptText = text;
      tab.dirty = true;
    }
  }

  saveActiveTab() {
    const tab = this.activeTab;
    if (tab) {
      tab.dirty = false;
    }
  }

  ensureWelcomeTab() {
    if (this.openTabs.length === 0) {
      this.openTab({
        id: 'welcome',
        label: 'Welcome',
        type: 'prompt',
        promptText: '',
        dirty: false
      });
    }
  }
}

export const editor = new EditorStore();
