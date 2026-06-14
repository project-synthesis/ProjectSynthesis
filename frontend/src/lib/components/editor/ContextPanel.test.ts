import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import ContextPanel from './ContextPanel.svelte';
// Vite's ?raw query yields the raw source as a string — the brand-CSS
// contract assertions (C8 marker class, C23 reduced-motion media query)
// can't rely on Svelte's compile-time scoped CSS being injected at test
// time, so we lock them against the source directly.
import contextPanelSource from './ContextPanel.svelte?raw';
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
      });
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText('API endpoint patterns')).toBeTruthy();
    });

    it('renders similarity as an integer percentage (C3)', () => {
      clustersStore.suggestion = makeSuggestion({ similarity: 0.842 });
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText(/84%/)).toBeTruthy();
    });

    it('renders the match_level (C4)', () => {
      clustersStore.suggestion = makeSuggestion({ match_level: 'family' });
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText(/family/i)).toBeTruthy();
    });

    it('renders a domain dot styled with taxonomyColor (C5)', () => {
      clustersStore.suggestion = makeSuggestion({
        cluster: { id: 'c1', label: 'x', domain: 'backend', member_count: 2 },
      });
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
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const meta = container.querySelector('[data-test="meta-section"]') as HTMLElement;
      expect(meta.querySelectorAll('input[type="checkbox"]').length).toBe(3);
    });

    it('truncates pattern text longer than 60 chars (C9)', () => {
      const long = 'a'.repeat(80);
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [mockMetaPattern({ id: 'mp1', pattern_text: long, source_count: 1 })],
      });
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
      });
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
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      expect(screen.getByText('2/3 ✔')).toBeTruthy();
    });
  });

  describe('global section', () => {
    it('renders GLOBAL heading and cross-cluster pattern rows (C7)', () => {
      clustersStore.suggestion = mockClusterMatch({
        cross_cluster_patterns: [
          mockMetaPattern({ id: 'gp1', pattern_text: 'Universal practice A', source_count: 5 }),
          mockMetaPattern({ id: 'gp2', pattern_text: 'Universal practice B', source_count: 4 }),
        ],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      expect(screen.getByText('GLOBAL')).toBeTruthy();
      const global = container.querySelector('[data-test="global-section"]') as HTMLElement;
      expect(global.querySelectorAll('input[type="checkbox"]').length).toBe(2);
    });

    it('global section has neon-purple left border (C8)', () => {
      clustersStore.suggestion = mockClusterMatch({
        cross_cluster_patterns: [mockMetaPattern({ id: 'gp1', pattern_text: 'P', source_count: 5 })],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const global = container.querySelector('[data-test="global-section"]') as HTMLElement;
      // Marker class on the DOM (consumer-side contract).
      expect(global.classList.contains('pattern-section--global')).toBe(true);
      // Source-side rule lock (brand contract). Svelte's scoped CSS isn't
      // injected at test time — assert the rule lives in the source. Brand-
      // grep at Task 18 catches drift across the wider component tree.
      expect(contextPanelSource).toMatch(
        /\.pattern-section--global\s*\{[^}]*border-left:\s*1px\s+solid\s+var\(--color-neon-purple\)/,
      );
    });
  });

  describe('apply button', () => {
    function mountWithThreeMeta() {
      clustersStore.suggestion = mockClusterMatch({
        cluster: { id: 'c1', label: 'API endpoint patterns', domain: 'backend', member_count: 3 },
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      });
      clustersStore.suggestionVisible = true;
    }

    it('apply button disabled when selection is empty (C12)', () => {
      mountWithThreeMeta();
      render(ContextPanel);
      const btn = screen.getByRole('button', { name: /apply/i });
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    });

    it('apply button label reflects selection count (C13)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      expect(screen.getByRole('button', { name: /apply 2/i })).toBeTruthy();
    });

    it('apply click populates forgeStore.appliedPatternIds (C14)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[2] as HTMLElement);
      await user.click(screen.getByRole('button', { name: /apply 2/i }));
      expect(forgeStore.appliedPatternIds?.sort()).toEqual(['mp1', 'mp3']);
    });

    it('apply click sets appliedPatternLabel to "cluster (N)" (C15)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      await user.click(screen.getByRole('button', { name: /apply 2/i }));
      expect(forgeStore.appliedPatternLabel).toBe('API endpoint patterns (2)');
    });

    it('apply click does not clear selection — panel remains visible with checkboxes locked (C16)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(screen.getByRole('button', { name: /apply 1/i }));
      expect((checkboxes[0] as HTMLInputElement).checked).toBe(true);
    });
  });

  describe('forceCollapsed prop (initial-default-only)', () => {
    it('forceCollapsed=true defaults isOpen=false when no localStorage entry exists', () => {
      // Fresh user, narrow viewport: panel should mount as the 28px rail.
      localStorage.removeItem('synthesis:context_panel_open');
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel, { props: { forceCollapsed: true } });
      const panel = container.querySelector('[data-test="context-panel"]') as HTMLElement;
      expect(panel.getAttribute('data-collapsed')).toBe('true');
    });

    it('forceCollapsed=false defaults isOpen=true when no localStorage entry exists', () => {
      // Fresh user, wide viewport: panel mounts open.
      localStorage.removeItem('synthesis:context_panel_open');
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel, { props: { forceCollapsed: false } });
      const panel = container.querySelector('[data-test="context-panel"]') as HTMLElement;
      expect(panel.getAttribute('data-collapsed')).toBe('false');
    });

    it('localStorage preference always wins over forceCollapsed (user toggle persists)', () => {
      // User explicitly expanded at narrow viewport — their choice survives
      // the next mount even if forceCollapsed=true. This is the contract
      // that fixes the "chevron does nothing" bug at narrow viewport.
      localStorage.setItem('synthesis:context_panel_open', 'true');
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel, { props: { forceCollapsed: true } });
      const panel = container.querySelector('[data-test="context-panel"]') as HTMLElement;
      expect(panel.getAttribute('data-collapsed')).toBe('false');
    });

    it('chevron renders ▸ caret with --open modifier when isOpen=true (matches navigator chevron motion)', () => {
      // Brand contract: collapse caret animates via 90deg rotate transform,
      // mirroring CollapsibleSectionHeader.svelte (.nsh-caret + .nsh-caret--open).
      // Source-locked because Svelte's scoped CSS isn't injected at test time.
      localStorage.setItem('synthesis:context_panel_open', 'true');
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const caret = container.querySelector('.caret') as HTMLElement;
      expect(caret).not.toBeNull();
      expect(caret.textContent).toBe('▸');
      expect(caret.classList.contains('caret--open')).toBe(true);
      // Source-side motion contract.
      expect(contextPanelSource).toMatch(
        /\.caret\s*\{[^}]*transition:\s*transform\s+var\(--duration-micro\)\s+var\(--ease-spring\)/,
      );
      expect(contextPanelSource).toMatch(
        /\.caret--open\s*\{[^}]*transform:\s*rotate\(90deg\)/,
      );
    });

    it('chevron toggle works at narrow viewport (regression: forceCollapsed must not block toggle)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      localStorage.removeItem('synthesis:context_panel_open');
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel, { props: { forceCollapsed: true } });
      const user = userEvent.setup();
      const panel = container.querySelector('[data-test="context-panel"]') as HTMLElement;
      // Initially collapsed (rail).
      expect(panel.getAttribute('data-collapsed')).toBe('true');
      // Click expand chevron — should expand even though forceCollapsed=true.
      await user.click(screen.getByRole('button', { name: /expand/i }));
      expect(panel.getAttribute('data-collapsed')).toBe('false');
      // localStorage now persists the user's explicit choice.
      expect(localStorage.getItem('synthesis:context_panel_open')).toBe('true');
    });
  });

  describe('collapse / expand', () => {
    it('collapse toggle narrows panel (C17)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const panel = container.querySelector('[data-test="context-panel"]') as HTMLElement;
      expect(panel.getAttribute('data-collapsed')).toBe('false');
      await user.click(screen.getByRole('button', { name: /collapse/i }));
      expect(panel.getAttribute('data-collapsed')).toBe('true');
    });

    it('collapse state persists to localStorage (C18)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      const user = userEvent.setup();
      await user.click(screen.getByRole('button', { name: /collapse/i }));
      expect(localStorage.getItem('synthesis:context_panel_open')).toBe('false');
    });
  });

  describe('synthesis gating', () => {
    it('hides panel when forgeStore.status === "analyzing" (C19)', () => {
      // Implementation removes the element via {#if !isSynthesizing}.
      // Asserted directly against null — no aria-hidden alternative branch.
      clustersStore.suggestion = mockClusterMatch();
      forgeStore.status = 'analyzing';
      const { container } = render(ContextPanel);
      expect(container.querySelector('[data-test="context-panel"]')).toBeNull();
    });
  });

  describe('accessibility', () => {
    it('panel has role=complementary + aria-label (C24)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const panel = container.querySelector('[role="complementary"]');
      expect(panel).not.toBeNull();
      expect(panel!.getAttribute('aria-label')).toBe('Pattern context');
    });

    it('collapse button has aria-expanded and aria-controls (C25)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      const btn = screen.getByRole('button', { name: /collapse|expand/i });
      expect(btn.getAttribute('aria-expanded')).toMatch(/true|false/);
      expect(btn.getAttribute('aria-controls')).toBe('context-panel-body');
    });

    it('respects prefers-reduced-motion (C23)', () => {
      // Source-locked: assert the @media block lives in the .svelte file.
      // Same rationale as C8 — Svelte's compile-time scoped CSS isn't
      // injected into <style> tags in the vitest runner, so this test IS
      // the contract. (Task 18's brand-grep only catches *forbidden*
      // patterns — it does not assert presence.)
      expect(contextPanelSource).toContain('prefers-reduced-motion: reduce');
      expect(contextPanelSource).toMatch(/transition-duration:\s*0\.01ms/);
    });
  });

  describe('edge cases', () => {
    it('renders "no match" state when suggestion is null after a fetch attempt (C20)', () => {
      clustersStore.suggestion = null;
      clustersStore.suggestionVisible = false;
      clustersStore._lastMatchedText = 'x'.repeat(60);  // signal "we attempted"
      render(ContextPanel);
      expect(screen.queryByText(/no (similar|match)/i)).toBeTruthy();
    });

    it('in-flight state fades prior match body to 0.5 opacity (C21)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      clustersStore._matchInFlight = true;
      const { container } = render(ContextPanel);
      const body = container.querySelector('[data-test="panel-body"]') as HTMLElement;
      // jsdom exposes inline style; assert the attribute string.
      expect(body.getAttribute('style') || '').toMatch(/opacity:\s*0\.5/);
    });

    it('network error draws a red-contour class on the header (C22)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      clustersStore._matchError = 'network';
      const { container } = render(ContextPanel);
      const header = container.querySelector('[data-test="panel-header"]') as HTMLElement;
      expect(header.classList.contains('panel-header--error')).toBe(true);
    });
  });

  describe('ANALYSIS section (Tier 2)', () => {
    function makePreview(overrides: Record<string, unknown> = {}) {
      return {
        task_type: { task_type: 'coding', confidence: 0.82, signal_source: 'bootstrap' as const },
        domain: 'backend: auth',
        intent_label: 'build authentication endpoint',
        recommended_strategy: 'meta-prompting',
        top_strategies: [
          { name: 'meta-prompting', score: 8.4, source: 'performance' as const },
          { name: 'role-playing', score: 7.2, source: 'performance' as const },
        ],
        blocked_strategies: ['bad-strategy'],
        weaknesses: ['vague language reduces precision', 'no examples to anchor expected output'],
        divergence_alerts: [],
        domain_relaxed_fallback: false,
        elapsed_ms: 53,
        ...overrides,
      };
    }

    it('renders the ANALYSIS section heading when preview !== null', () => {
      clustersStore.preview = makePreview();
      render(ContextPanel);
      expect(screen.getByText('ANALYSIS')).toBeTruthy();
    });

    it('renders the task-type chip + confidence percent (one decimal)', () => {
      clustersStore.preview = makePreview();
      const { container } = render(ContextPanel);
      const chip = container.querySelector('[data-test="analysis-task-chip"]') as HTMLElement;
      expect(chip).not.toBeNull();
      expect(chip.textContent).toMatch(/coding/);
      expect(chip.textContent).toMatch(/82\.0%/);
    });

    it('renders the domain row with the full qualifier', () => {
      clustersStore.preview = makePreview({ domain: 'backend: auth' });
      const { container } = render(ContextPanel);
      const row = container.querySelector('[data-test="analysis-domain"]') as HTMLElement;
      expect(row.textContent).toMatch(/backend: auth/);
    });

    it('renders the recommended strategy chip', () => {
      clustersStore.preview = makePreview();
      const { container } = render(ContextPanel);
      const chip = container.querySelector('[data-test="analysis-strategy"]') as HTMLElement;
      expect(chip.textContent).toMatch(/meta-prompting/);
    });

    it('renders top-strategy rows with score percent', () => {
      clustersStore.preview = makePreview();
      const { container } = render(ContextPanel);
      const rows = container.querySelectorAll('[data-test="analysis-strategy-row"]');
      expect(rows.length).toBe(2);
    });

    it('renders the "low confidence" dim hint when top_strategies is empty AND task_type is general', () => {
      clustersStore.preview = makePreview({
        task_type: { task_type: 'general', confidence: 0.2, signal_source: 'bootstrap' as const },
        top_strategies: [],
      });
      render(ContextPanel);
      expect(screen.getByText(/low confidence — keep refining/i)).toBeTruthy();
    });

    it('renders the blocked row only when blocked_strategies is non-empty', () => {
      clustersStore.preview = makePreview({ blocked_strategies: [] });
      const { container } = render(ContextPanel);
      expect(container.querySelector('[data-test="analysis-blocked"]')).toBeNull();
    });

    it('renders one weakness row per item, capped at 3', () => {
      clustersStore.preview = makePreview({
        weaknesses: ['w1', 'w2', 'w3', 'w4'],
      });
      const { container } = render(ContextPanel);
      const rows = container.querySelectorAll('[data-test="analysis-weakness-row"]');
      expect(rows.length).toBe(3);
    });

    it('renders divergence rows when present', () => {
      clustersStore.preview = makePreview({
        divergence_alerts: [
          { category: 'database', prompt_tech: 'mongodb', codebase_tech: 'postgresql', severity: 'conflict' as const },
        ],
      });
      const { container } = render(ContextPanel);
      const rows = container.querySelectorAll('[data-test="analysis-divergence-row"]');
      expect(rows.length).toBe(1);
    });

    it('renders the empty-state copy when preview === null and _previewInFlight === false', () => {
      clustersStore.preview = null;
      clustersStore._previewInFlight = false;
      // Make sure the cluster suggestion also is null so the wider panel
      // falls back to its existing empty state and we can verify the
      // ANALYSIS-specific copy below the patterns area.
      clustersStore.suggestion = null;
      render(ContextPanel);
      expect(screen.getByText(/type 30\+ chars to preview/i)).toBeTruthy();
    });

    it('ANALYSIS section is hidden when activeTab type is not "prompt"', () => {
      forgeStore.status = 'optimizing'; // covers SYNTHESIS_STATES gate
      clustersStore.preview = makePreview();
      render(ContextPanel);
      expect(screen.queryByText('ANALYSIS')).toBeNull();
    });

    it('brand-compliance grep — no glow/halo/bloom/breathing vocab in source', () => {
      // Source-level grep gate on ContextPanel.svelte — locks the brand
      // grammar required by frontend CLAUDE.md (v0.4.39 standard).
      const forbidden = ['glow', 'halo', 'bloom', 'radiance', 'breathing'];
      for (const word of forbidden) {
        expect(contextPanelSource.toLowerCase()).not.toContain(word);
      }
    });

    it('brand-compliance grep — no h-7/h-8/p-2/gap-2 padding-drift in source', () => {
      // R-01..R-05 brand recipes — locks the IDE density (20px rows, p-1.5,
      // gap-1.5) per the v0.4.39 brand pass.
      const driftPatterns = [
        /\bh-7\b/, /\bh-8\b/, /\bp-2\b(?!\.|[0-9]|-)/, /\bgap-2\b(?!\.|[0-9]|-)/,
      ];
      for (const pat of driftPatterns) {
        expect(contextPanelSource).not.toMatch(pat);
      }
    });

    it('brand-compliance grep — no shadow-blur vocabulary in source', () => {
      // Multi-px blurred box-shadow violates the v0.4.39 1px-tube standard.
      expect(contextPanelSource).not.toMatch(/box-shadow:.*\d+px\s+[1-9]\d*px/);
    });
  });
});
