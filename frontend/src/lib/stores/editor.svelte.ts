// frontend/src/lib/stores/editor.svelte.ts

export type TabType = 'prompt' | 'result' | 'diff';

export interface Tab {
  id: string;
  title: string;
  type: TabType;
}

class EditorStore {
  tabs = $state<Tab[]>([{ id: 'prompt-1', title: 'New Prompt', type: 'prompt' }]);
  activeTabId = $state('prompt-1');

  get activeTab(): Tab | undefined {
    return this.tabs.find((t) => t.id === this.activeTabId);
  }

  openTab(tab: Tab) {
    const existing = this.tabs.find((t) => t.id === tab.id);
    if (!existing) {
      this.tabs = [...this.tabs, tab];
    }
    this.activeTabId = tab.id;
  }

  closeTab(id: string) {
    this.tabs = this.tabs.filter((t) => t.id !== id);
    if (this.activeTabId === id && this.tabs.length > 0) {
      this.activeTabId = this.tabs[this.tabs.length - 1].id;
    }
  }

  setActive(id: string) {
    this.activeTabId = id;
  }

  openResult(optimizationId: string) {
    this.openTab({ id: `result-${optimizationId}`, title: 'Result', type: 'result' });
  }

  openDiff(optimizationId: string) {
    this.openTab({ id: `diff-${optimizationId}`, title: 'Diff', type: 'diff' });
  }
}

export const editorStore = new EditorStore();
