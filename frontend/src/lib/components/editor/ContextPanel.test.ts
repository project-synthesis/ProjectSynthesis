import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import ContextPanel from './ContextPanel.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { mockClusterMatch, mockMetaPattern } from '$lib/test-utils';
// `mockMetaPattern` is used from Task 8 onward; import up front so later
// test additions don't need to edit imports each time.

describe('ContextPanel', () => {
  beforeEach(() => {
    clustersStore._reset();
    forgeStore.status = 'idle';
    forgeStore.appliedPatternIds = null;
    forgeStore.appliedPatternLabel = null;
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  describe('empty state', () => {
    it('renders "waiting for prompt" when suggestion is null (C1)', () => {
      clustersStore.suggestion = null;
      clustersStore.suggestionVisible = false;
      render(ContextPanel);
      expect(screen.getByText(/waiting for prompt/i)).toBeTruthy();
    });
  });

  describe('cluster identity row', () => {
    function makeSuggestion(overrides: Record<string, unknown> = {}) {
      return mockClusterMatch(overrides);
    }

    it('renders the cluster label (C2)', () => {
      clustersStore.suggestion = makeSuggestion({
        cluster: { id: 'c1', label: 'API endpoint patterns', domain: 'backend', member_count: 5 },
      }) as never;
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText('API endpoint patterns')).toBeTruthy();
    });

    it('renders similarity as an integer percentage (C3)', () => {
      clustersStore.suggestion = makeSuggestion({ similarity: 0.842 }) as never;
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText(/84%/)).toBeTruthy();
    });

    it('renders the match_level (C4)', () => {
      clustersStore.suggestion = makeSuggestion({ match_level: 'family' }) as never;
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText(/family/i)).toBeTruthy();
    });

    it('renders a domain dot styled with taxonomyColor (C5)', () => {
      clustersStore.suggestion = makeSuggestion({
        cluster: { id: 'c1', label: 'x', domain: 'backend', member_count: 2 },
      }) as never;
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const dot = container.querySelector('[data-test="domain-dot"]') as HTMLElement | null;
      expect(dot).not.toBeNull();
      // taxonomyColor('backend') resolves to a neon-violet hex; assert non-empty bg.
      expect(dot!.style.backgroundColor || dot!.getAttribute('style')).toMatch(/#|rgb/);
    });
  });

  describe('meta-patterns section', () => {
    it('renders one checkbox per meta-pattern (C6)', () => {
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      }) as never;
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const meta = container.querySelector('[data-test="meta-section"]') as HTMLElement;
      expect(meta.querySelectorAll('input[type="checkbox"]').length).toBe(3);
    });

    it('truncates pattern text longer than 60 chars (C9)', () => {
      const long = 'a'.repeat(80);
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [mockMetaPattern({ id: 'mp1', pattern_text: long, source_count: 1 })],
      }) as never;
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const row = container.querySelector('[data-test="pattern-row"]') as HTMLElement;
      const txt = row.querySelector('.pattern-text')!.textContent ?? '';
      expect(txt.endsWith('…') || txt.endsWith('...')).toBe(true);
      // Truncation contract: slice(0, n-1) + '…' = exactly n chars for any input > n.
      expect(txt.length).toBe(60);
    });

    it('toggles selection on checkbox click (C10)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      }) as never;
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      expect((checkboxes[0] as HTMLInputElement).checked).toBe(true);
      expect(screen.getByText('1/3 ✔')).toBeTruthy();
    });

    it('renders "N/M ✔" selection counter (C11)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      }) as never;
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      expect(screen.getByText('2/3 ✔')).toBeTruthy();
    });
  });
});
