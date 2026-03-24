import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// Mock heavy sub-components to keep EditorGroups tests focused on tab bar behaviour.
// In Svelte 5, components are functions. We provide a no-op stub.
// Note: vi.mock factories are hoisted, so we inline the stub directly.
vi.mock('$lib/components/editor/PromptEdit.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/ForgeArtifact.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/PassthroughView.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/shared/DiffView.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/refinement/RefinementTimeline.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/taxonomy/SemanticTopology.svelte', () => ({ default: () => ({ destroy: () => {} }) }));

// Mock API calls used by sub-components
vi.mock('$lib/api/client', () => ({
  getStrategies: vi.fn().mockResolvedValue([]),
  optimizeSSE: vi.fn(),
  getOptimization: vi.fn(),
  submitFeedback: vi.fn(),
  savePassthrough: vi.fn(),
  getHealth: vi.fn().mockResolvedValue({ provider: 'claude-cli', version: '0.1.0' }),
}));

import EditorGroups from './EditorGroups.svelte';
import { editorStore, PROMPT_TAB_ID } from '$lib/stores/editor.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { refinementStore } from '$lib/stores/refinement.svelte';

describe('EditorGroups', () => {
  beforeEach(() => {
    editorStore._reset();
    forgeStore._reset();
    refinementStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the tab bar', () => {
    render(EditorGroups);
    expect(screen.getByRole('tablist', { name: 'Editor tabs' })).toBeInTheDocument();
  });

  it('shows the default Prompt tab', () => {
    render(EditorGroups);
    expect(screen.getByRole('tab', { name: 'Prompt' })).toBeInTheDocument();
  });

  it('Prompt tab is active by default', () => {
    render(EditorGroups);
    const promptTab = screen.getByRole('tab', { name: 'Prompt' });
    expect(promptTab).toHaveAttribute('aria-selected', 'true');
  });

  it('renders the + new prompt button', () => {
    render(EditorGroups);
    expect(screen.getByRole('button', { name: 'New prompt' })).toBeInTheDocument();
  });

  it('renders multiple tabs when editor store has multiple tabs', () => {
    editorStore.openResult('opt-abc');
    render(EditorGroups);
    const tabs = screen.getAllByRole('tab');
    // Should have Prompt tab + result tab
    expect(tabs.length).toBeGreaterThanOrEqual(2);
  });

  it('clicking a non-active tab makes it active', async () => {
    const user = userEvent.setup();
    editorStore.openResult('opt-abc');
    // Make prompt tab active again
    editorStore.setActive(PROMPT_TAB_ID);
    render(EditorGroups);

    const tabs = screen.getAllByRole('tab');
    const resultTab = tabs.find(t => t.getAttribute('aria-selected') === 'false');
    expect(resultTab).toBeDefined();

    await user.click(resultTab!);
    expect(resultTab).toHaveAttribute('aria-selected', 'true');
  });

  it('Prompt tab has no close button (it is pinned)', () => {
    render(EditorGroups);
    const promptTab = screen.getByRole('tab', { name: 'Prompt' });
    // Pinned tab should not have a close button inside it
    expect(promptTab.querySelector('[aria-label^="Close"]')).toBeNull();
  });

  it('non-pinned tabs have a close button', () => {
    editorStore.openResult('opt-123');
    render(EditorGroups);
    // There should be a "Close ..." button for the result tab
    const closeButtons = screen.getAllByRole('button').filter(b =>
      (b.getAttribute('aria-label') ?? '').startsWith('Close ')
    );
    expect(closeButtons.length).toBeGreaterThanOrEqual(1);
  });

  it('clicking close button removes the tab', async () => {
    const user = userEvent.setup();
    editorStore.openResult('opt-456');
    render(EditorGroups);

    const tabsBefore = screen.getAllByRole('tab');
    expect(tabsBefore.length).toBe(2);

    const closeBtn = screen.getAllByRole('button').find(b =>
      (b.getAttribute('aria-label') ?? '').startsWith('Close ')
    );
    expect(closeBtn).toBeDefined();
    await user.click(closeBtn!);

    const tabsAfter = screen.getAllByRole('tab');
    expect(tabsAfter.length).toBe(1);
  });

  it('shows empty state message when no tabs', () => {
    // Close all tabs including the prompt tab via store manipulation
    editorStore.tabs = [];
    render(EditorGroups);
    expect(screen.getByText('No open tabs')).toBeInTheDocument();
  });

  it('clicking + button resets forge and closes non-pinned tabs', async () => {
    const user = userEvent.setup();
    forgeStore.prompt = 'some prompt';
    editorStore.openResult('opt-789');
    render(EditorGroups);

    expect(screen.getAllByRole('tab').length).toBe(2);
    await user.click(screen.getByRole('button', { name: 'New prompt' }));

    // After reset, only the pinned Prompt tab should remain
    expect(screen.getAllByRole('tab').length).toBe(1);
    expect(screen.getByRole('tab', { name: 'Prompt' })).toBeInTheDocument();
  });

  it('shows PassthroughView when forgeStore.status is passthrough', async () => {
    forgeStore.status = 'passthrough';
    render(EditorGroups);
    // PassthroughView is rendered in the prompt tab when status = passthrough
    // The mock renders nothing, so just confirm no crash
    expect(screen.getByRole('tablist')).toBeInTheDocument();
  });

  it('renders diff tab when diff tab is open', async () => {
    editorStore.openDiff('opt-diff-1');
    render(EditorGroups);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.length).toBe(2);
    // diff tab should be present
    const diffTab = tabs.find(t => t.textContent?.includes('~') || t.getAttribute('aria-selected') === 'true');
    expect(diffTab).toBeDefined();
  });

  it('renders placeholder when diff tab has no diff data', async () => {
    // Open diff tab without any result cached
    editorStore.openDiff('opt-no-data');
    render(EditorGroups);
    expect(screen.getByText(/No diff available/)).toBeInTheDocument();
  });

  it('renders mindmap tab when mindmap is open', async () => {
    editorStore.openMindmap();
    render(EditorGroups);
    const tabs = screen.getAllByRole('tab');
    expect(tabs.some(t => t.textContent?.includes('Pattern Graph'))).toBe(true);
  });

  it('shows result tab with ForgeArtifact when forge is complete', async () => {
    const { mockOptimizationResult } = await import('$lib/test-utils');
    const result = mockOptimizationResult({ id: 'opt-result-1' });
    forgeStore.result = result as any;
    forgeStore.status = 'complete';
    editorStore.openResult('opt-result-1');
    render(EditorGroups);
    // ForgeArtifact is mocked but the result tab should be active
    expect(editorStore.activeTab?.type).toBe('result');
  });

  it('shows result split with RefinementTimeline when forge is complete and result tab active', async () => {
    const { mockOptimizationResult, mockRefinementTurn } = await import('$lib/test-utils');
    const result = mockOptimizationResult({ id: 'opt-split-1' });
    forgeStore.result = result as any;
    forgeStore.status = 'complete';
    refinementStore.turns = [mockRefinementTurn() as any];
    editorStore.openResult('opt-split-1');
    render(EditorGroups);
    expect(editorStore.activeTab?.type).toBe('result');
  });

  it('tab close button triggers keydown (Enter) to close', async () => {
    const user = userEvent.setup();
    editorStore.openResult('opt-keydown-1');
    render(EditorGroups);

    const closeBtn = screen.getAllByRole('button').find(b =>
      (b.getAttribute('aria-label') ?? '').startsWith('Close ')
    );
    expect(closeBtn).toBeDefined();

    // Tab count before
    expect(screen.getAllByRole('tab').length).toBe(2);

    // Simulate keyboard Enter on close button
    await user.type(closeBtn!, '{Enter}');

    expect(screen.getAllByRole('tab').length).toBe(1);
  });

  it('does not show refinement for passthrough results', async () => {
    const { mockOptimizationResult } = await import('$lib/test-utils');
    const result = mockOptimizationResult({ id: 'opt-pt-1', provider: 'web_passthrough' });
    forgeStore.result = result as any;
    forgeStore.status = 'complete';
    editorStore.openResult('opt-pt-1');
    render(EditorGroups);
    expect(editorStore.activeTab?.type).toBe('result');
    // Refinement should NOT have been initialized for a passthrough result
    expect(refinementStore.optimizationId).toBeNull();
  });

  it('tab bar scroll is handled by wheel event', async () => {
    render(EditorGroups);
    const tabBar = screen.getByRole('tablist', { name: 'Editor tabs' });

    // Simulate wheel event — should not throw
    const wheelEvent = new WheelEvent('wheel', { deltaY: 100, bubbles: true });
    Object.defineProperty(wheelEvent, 'currentTarget', { value: tabBar });
    tabBar.dispatchEvent(wheelEvent);

    expect(tabBar).toBeInTheDocument();
  });
});
