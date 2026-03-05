export type SubTab = 'edit' | 'pipeline' | 'history';

export interface EditorTab {
  id: string;
  label: string;
  type: 'prompt' | 'artifact' | 'chain';
  promptText?: string;
  dirty?: boolean;
}

const MAX_TABS = 8;

class EditorStore {
  openTabs = $state<EditorTab[]>([]);
  activeTabId = $state<string | null>(null);
  activeSubTab = $state<SubTab>('edit');

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
      // LRU: remove the first non-active tab
      const idx = this.openTabs.findIndex(t => t.id !== this.activeTabId);
      if (idx !== -1) {
        this.openTabs.splice(idx, 1);
      }
    }
    this.openTabs.push(tab);
    this.activeTabId = tab.id;
  }

  closeTab(id: string) {
    const idx = this.openTabs.findIndex(t => t.id === id);
    if (idx === -1) return;
    this.openTabs.splice(idx, 1);
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
        label: 'New Prompt',
        type: 'prompt',
        promptText: '',
        dirty: false
      });
    }
  }
}

export const editor = new EditorStore();
