import { describe, it, expect, beforeEach } from 'vitest';
import { editorStore, PROMPT_TAB_ID } from './editor.svelte';
import { mockOptimizationResult } from '../test-utils';

describe('EditorStore', () => {
  beforeEach(() => {
    editorStore._reset();
  });

  it('starts with prompt tab active', () => {
    // Two pinned tabs seeded: Prompt + Observatory
    expect(editorStore.tabs).toHaveLength(2);
    expect(editorStore.tabs[0].id).toBe(PROMPT_TAB_ID);
    expect(editorStore.activeTabId).toBe(PROMPT_TAB_ID);
  });

  it('activeTab returns the current tab', () => {
    expect(editorStore.activeTab?.id).toBe(PROMPT_TAB_ID);
  });

  describe('openTab', () => {
    it('adds a new tab and activates it', () => {
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result', optimizationId: 'opt-1' });
      // Prompt + Observatory + result-1
      expect(editorStore.tabs).toHaveLength(3);
      expect(editorStore.activeTabId).toBe('result-1');
    });

    it('activates existing tab instead of duplicating', () => {
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result' });
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result' });
      // Prompt + Observatory + result-1 (no duplication)
      expect(editorStore.tabs).toHaveLength(3);
    });
  });

  describe('closeTab', () => {
    it('removes the tab', () => {
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result' });
      editorStore.closeTab('result-1');
      // Both pinned tabs remain (Prompt + Observatory)
      expect(editorStore.tabs).toHaveLength(2);
    });

    it('activates prompt tab when closing active tab', () => {
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result' });
      editorStore.closeTab('result-1');
      expect(editorStore.activeTabId).toBe(PROMPT_TAB_ID);
    });

    it('does not close pinned prompt tab', () => {
      editorStore.closeTab(PROMPT_TAB_ID);
      // Prompt + Observatory both pinned, count unchanged
      expect(editorStore.tabs).toHaveLength(2);
    });
  });

  describe('setActive', () => {
    it('changes active tab', () => {
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result' });
      editorStore.setActive(PROMPT_TAB_ID);
      expect(editorStore.activeTabId).toBe(PROMPT_TAB_ID);
    });
  });

  describe('focusPrompt', () => {
    it('sets activeTabId to PROMPT_TAB_ID', () => {
      editorStore.openTab({ id: 'result-1', title: 'Result', type: 'result' });
      expect(editorStore.activeTabId).toBe('result-1');
      editorStore.focusPrompt();
      expect(editorStore.activeTabId).toBe(PROMPT_TAB_ID);
    });
  });

  describe('result cache', () => {
    it('caches and retrieves results', () => {
      const result = mockOptimizationResult();
      editorStore.cacheResult('opt-1', result as any);
      expect(editorStore.getResult('opt-1')).toEqual(result);
    });

    it('returns null for uncached result', () => {
      expect(editorStore.getResult('nonexistent')).toBeNull();
    });
  });

  describe('openResult', () => {
    it('creates a result tab for the optimization', () => {
      editorStore.openResult('opt-1');
      expect(editorStore.tabs.some((t) => t.type === 'result')).toBe(true);
    });

    it('caches data when provided', () => {
      const data = mockOptimizationResult();
      editorStore.openResult('opt-1', data as any);
      expect(editorStore.getResult('opt-1')).toBeTruthy();
    });
  });

  describe('openDiff', () => {
    it('creates a diff tab', () => {
      editorStore.openDiff('opt-1');
      expect(editorStore.tabs.some((t) => t.type === 'diff')).toBe(true);
    });
  });

  describe('openMindmap', () => {
    it('creates a mindmap tab', () => {
      editorStore.openMindmap();
      expect(editorStore.tabs.some((t) => t.type === 'mindmap')).toBe(true);
    });
  });

  describe('closeAllResults', () => {
    it('removes all non-pinned tabs', () => {
      editorStore.openResult('opt-1');
      editorStore.openDiff('opt-2');
      editorStore.closeAllResults();
      // Both pinned tabs remain (Prompt + Observatory)
      expect(editorStore.tabs).toHaveLength(2);
      expect(editorStore.tabs[0].id).toBe(PROMPT_TAB_ID);
    });
  });

  describe('feedback caching (F6)', () => {
    it('cacheFeedback stores feedback on cached result', () => {
      const data = { id: 'opt-1', raw_prompt: 'test', status: 'completed' } as any;
      editorStore.openResult('opt-1', data);
      editorStore.cacheFeedback('opt-1', 'thumbs_up');
      const cached = editorStore.getResult('opt-1') as any;
      expect(cached.feedback_rating).toBe('thumbs_up');
    });

    it('activeFeedback returns feedback from active tab cache', () => {
      const data = { id: 'opt-1', raw_prompt: 'test', status: 'completed' } as any;
      editorStore.openResult('opt-1', data);
      editorStore.cacheFeedback('opt-1', 'thumbs_down');
      expect(editorStore.activeFeedback).toBe('thumbs_down');
    });

    it('activeFeedback returns null when no feedback cached', () => {
      const data = { id: 'opt-2', raw_prompt: 'test', status: 'completed' } as any;
      editorStore.openResult('opt-2', data);
      expect(editorStore.activeFeedback).toBeNull();
    });
  });
});
