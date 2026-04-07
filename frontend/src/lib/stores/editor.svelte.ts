// frontend/src/lib/stores/editor.svelte.ts

import type { OptimizationResult } from '$lib/api/client';

export type TabType = 'prompt' | 'result' | 'diff' | 'mindmap';

/** The prompt tab ID is a constant — there is always exactly one prompt tab. */
export const PROMPT_TAB_ID = 'prompt';

export interface Tab {
  id: string;
  title: string;
  type: TabType;
  /** If true, the tab cannot be closed by the user. */
  pinned?: boolean;
  /** The optimization ID this tab is associated with (result and diff tabs). */
  optimizationId?: string;
}

class EditorStore {
  tabs = $state<Tab[]>([
    { id: PROMPT_TAB_ID, title: 'Prompt', type: 'prompt', pinned: true },
  ]);
  activeTabId = $state(PROMPT_TAB_ID);

  /** Per-optimization result cache: optimization ID → data. */
  private _resultCache = $state<Record<string, OptimizationResult>>({});

  get activeTab(): Tab | undefined {
    return this.tabs.find((t) => t.id === this.activeTabId);
  }

  /** Get the cached optimization data for the currently active tab. */
  get activeResult(): OptimizationResult | null {
    const tab = this.activeTab;
    if (!tab?.optimizationId) return null;
    return this._resultCache[tab.optimizationId] ?? null;
  }

  /** Get cached result by optimization ID. */
  getResult(optimizationId: string): OptimizationResult | null {
    return this._resultCache[optimizationId] ?? null;
  }

  /** Cache an optimization result by its ID and update any tab titles that reference it. */
  cacheResult(optimizationId: string, data: OptimizationResult) {
    this._resultCache = { ...this._resultCache, [optimizationId]: data };

    // Update tab titles: prefer intent_label from knowledge graph, fall back to raw_prompt derivation
    const updated = this.tabs.map((t) => {
      if (t.optimizationId !== optimizationId) return t;
      const prefix = t.type === 'diff' ? '~' : '';
      const label = data.intent_label ?? this._tabTitle(data.raw_prompt, '');
      const title = prefix ? `${prefix} ${label}` : label;
      return { ...t, title };
    });
    // Only reassign if something changed
    if (updated.some((t, i) => t !== this.tabs[i])) {
      this.tabs = updated;
    }
  }

  openTab(tab: Tab) {
    const existing = this.tabs.find((t) => t.id === tab.id);
    if (!existing) {
      this.tabs = [...this.tabs, tab];
    }
    this.activeTabId = tab.id;
  }

  closeTab(id: string) {
    const tab = this.tabs.find((t) => t.id === id);
    if (tab?.pinned) return;
    this.tabs = this.tabs.filter((t) => t.id !== id);

    // Clean up cache if no other tab references this optimization
    if (tab?.optimizationId) {
      const stillReferenced = this.tabs.some(
        (t) => t.optimizationId === tab.optimizationId,
      );
      if (!stillReferenced) {
        const next = { ...this._resultCache };
        delete next[tab.optimizationId];
        this._resultCache = next;
      }
    }

    if (this.activeTabId === id) {
      this.activeTabId = this.tabs.length > 0
        ? this.tabs[this.tabs.length - 1].id
        : PROMPT_TAB_ID;
    }
  }

  setActive(id: string) {
    this.activeTabId = id;
  }

  focusPrompt() {
    this.activeTabId = PROMPT_TAB_ID;
  }

  /** Derive a short tab title from the raw prompt text. */
  private _tabTitle(rawPrompt: string | undefined, prefix: string): string {
    if (!rawPrompt) return prefix || 'Result';
    // Take first 3 words, hard cap at 16 chars
    const words = rawPrompt.split(/\s+/).slice(0, 3).join(' ');
    const capped = words.length > 16 ? words.slice(0, 16).trimEnd() : words;
    const label = rawPrompt.length > capped.length ? capped + '...' : capped;
    return prefix ? `${prefix} ${label}` : label;
  }

  /** Open a result tab and optionally cache the optimization data. */
  openResult(optimizationId: string, data?: OptimizationResult) {
    if (data) {
      this.cacheResult(optimizationId, data);
    }
    const cached = this._resultCache[optimizationId];
    const title = cached?.intent_label ?? this._tabTitle(cached?.raw_prompt, '');
    this.openTab({
      id: `result-${optimizationId}`,
      title,
      type: 'result',
      optimizationId,
    });
  }

  openDiff(optimizationId: string) {
    const cached = this._resultCache[optimizationId];
    const label = cached?.intent_label ?? this._tabTitle(cached?.raw_prompt, '');
    this.openTab({
      id: `diff-${optimizationId}`,
      title: `~ ${label}`,
      type: 'diff',
      optimizationId,
    });
  }

  /** Open (or activate) the pattern graph mindmap tab. */
  openMindmap() {
    this.openTab({
      id: 'mindmap',
      title: 'Pattern Graph',
      type: 'mindmap',
    });
  }

  /** Close all non-pinned tabs and clear the cache. */
  closeAllResults() {
    this.tabs = this.tabs.filter((t) => t.pinned);
    this._resultCache = {};
    this.activeTabId = PROMPT_TAB_ID;
  }

  /** Update tab titles for all tabs referencing the given optimization. */
  updateTabTitle(optimizationId: string, newTitle: string) {
    const updated = this.tabs.map((t) => {
      if (t.optimizationId !== optimizationId) return t;
      const prefix = t.type === 'diff' ? '~ ' : '';
      return { ...t, title: `${prefix}${newTitle}` };
    });
    if (updated.some((t, i) => t !== this.tabs[i])) {
      this.tabs = updated;
    }
    // Also update the result cache so future tabs derive the correct title
    const cached = this._resultCache[optimizationId];
    if (cached) {
      this._resultCache = {
        ...this._resultCache,
        [optimizationId]: { ...cached, intent_label: newTitle },
      };
    }
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.tabs = [{ id: PROMPT_TAB_ID, title: 'Prompt', type: 'prompt', pinned: true }];
    this.activeTabId = PROMPT_TAB_ID;
    this._resultCache = {};
  }
}

export const editorStore = new EditorStore();
