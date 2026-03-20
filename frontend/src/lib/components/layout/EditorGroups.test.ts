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
vi.mock('$lib/components/patterns/RadialMindmap.svelte', () => ({ default: () => ({ destroy: () => {} }) }));

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
});
