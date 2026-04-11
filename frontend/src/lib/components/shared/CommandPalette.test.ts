import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import CommandPalette from './CommandPalette.svelte';

// Minimal mocks for stores used by CommandPalette
vi.mock('$lib/stores/forge.svelte', () => ({
  forgeStore: {
    reset: vi.fn(),
    forge: vi.fn(),
    traceId: null,
    status: 'idle',
    result: null,
    prompt: '',
  },
}));

vi.mock('$lib/stores/editor.svelte', () => ({
  editorStore: {
    openResult: vi.fn(),
    openDiff: vi.fn(),
    openMindmap: vi.fn(),
    closeTab: vi.fn(),
    focusPrompt: vi.fn(),
    activeResult: null,
    activeTabId: 'prompt',
  },
}));

vi.mock('$lib/stores/clusters.svelte', () => ({
  clustersStore: {
    loadTree: vi.fn(),
  },
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
}));

describe('CommandPalette', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    // Reset mock property values that persist between tests
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    const { editorStore } = await import('$lib/stores/editor.svelte');
    vi.mocked(forgeStore).result = null;
    vi.mocked(forgeStore).prompt = '';
    vi.mocked(forgeStore).status = 'idle' as any;
    vi.mocked(editorStore).activeResult = null;
    vi.mocked(editorStore).activeTabId = 'prompt';
  });

  afterEach(() => {
    cleanup();
  });

  it('does not render the palette by default (closed)', () => {
    render(CommandPalette);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByPlaceholderText('Search commands...')).not.toBeInTheDocument();
  });

  it('opens on Ctrl+K keypress', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Search commands...')).toBeInTheDocument();
  });

  it('displays all default actions when open', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByText('New Prompt')).toBeInTheDocument();
    expect(screen.getByText('Forge')).toBeInTheDocument();
    expect(screen.getByText('View History')).toBeInTheDocument();
    expect(screen.getByText('View Topology')).toBeInTheDocument();
    expect(screen.getByText('Link Repo')).toBeInTheDocument();
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.getByText('Toggle Diff')).toBeInTheDocument();
    expect(screen.getByText('Copy Result')).toBeInTheDocument();
  });

  it('filters actions by search query', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByPlaceholderText('Search commands...');
    await user.type(input, 'hist');
    expect(screen.getByText('View History')).toBeInTheDocument();
    expect(screen.queryByText('New Prompt')).not.toBeInTheDocument();
    expect(screen.queryByText('Forge')).not.toBeInTheDocument();
  });

  it('shows "no results" message when no actions match', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByPlaceholderText('Search commands...');
    await user.type(input, 'xyznonexistent');
    expect(screen.getByText(/No commands match/)).toBeInTheDocument();
  });

  it('closes on Escape key', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('closes when Ctrl+K is pressed again (toggle)', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('navigates down with ArrowDown key', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    // First item should be selected initially
    const items = screen.getAllByRole('option');
    expect(items[0]).toHaveAttribute('aria-selected', 'true');
    expect(items[1]).toHaveAttribute('aria-selected', 'false');
    // Press ArrowDown to move selection
    await user.keyboard('{ArrowDown}');
    const updatedItems = screen.getAllByRole('option');
    expect(updatedItems[0]).toHaveAttribute('aria-selected', 'false');
    expect(updatedItems[1]).toHaveAttribute('aria-selected', 'true');
  });

  it('navigates up with ArrowUp key', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    // Move down first
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{ArrowDown}');
    // Then move up
    await user.keyboard('{ArrowUp}');
    const items = screen.getAllByRole('option');
    expect(items[1]).toHaveAttribute('aria-selected', 'true');
  });

  it('does not go below the last item with ArrowDown', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    // Press ArrowDown many times beyond the list
    for (let i = 0; i < 20; i++) {
      await user.keyboard('{ArrowDown}');
    }
    const items = screen.getAllByRole('option');
    const lastItem = items[items.length - 1];
    expect(lastItem).toHaveAttribute('aria-selected', 'true');
  });

  it('does not go above the first item with ArrowUp', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    // Press ArrowUp when already at top
    await user.keyboard('{ArrowUp}');
    const items = screen.getAllByRole('option');
    expect(items[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('has accessible dialog label', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog', { name: 'Command palette' })).toBeInTheDocument();
  });

  it('resets query when reopened', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    const input = screen.getByPlaceholderText('Search commands...');
    await user.type(input, 'hist');
    expect(input).toHaveValue('hist');
    // Close and reopen
    await user.keyboard('{Escape}');
    await user.keyboard('{Control>}k{/Control}');
    const newInput = screen.getByPlaceholderText('Search commands...');
    expect(newInput).toHaveValue('');
  });

  it('renders the commands listbox', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('listbox', { name: 'Commands' })).toBeInTheDocument();
  });

  it('executes action on Enter key when an action is selected', async () => {
    const user = userEvent.setup();
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    // First item is 'New Prompt' — press Enter to execute
    await user.keyboard('{Enter}');

    expect(forgeStore.reset).toHaveBeenCalled();
  });

  it('executes action on click', async () => {
    const user = userEvent.setup();
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    // Click on 'New Prompt'
    await user.click(screen.getByText('New Prompt'));

    expect(forgeStore.reset).toHaveBeenCalled();
  });

  it('mouseenter selects item on hover', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    const items = screen.getAllByRole('option');
    // Hover over the second item
    await user.hover(items[1]);

    expect(items[1]).toHaveAttribute('aria-selected', 'true');
    expect(items[0]).toHaveAttribute('aria-selected', 'false');
  });

  it('shows toast when Forge is invoked without a prompt', async () => {
    const user = userEvent.setup();
    const { addToast } = await import('$lib/stores/toast.svelte');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Forge'));

    expect(addToast).toHaveBeenCalledWith('modified', 'Enter a prompt first (20+ characters)');
  });

  it('calls forge when prompt is long enough', async () => {
    const user = userEvent.setup();
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    vi.mocked(forgeStore).prompt = 'This is a prompt that is definitely long enough to optimize';
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Forge'));

    expect(forgeStore.forge).toHaveBeenCalled();
  });

  it('executes View History action', async () => {
    const user = userEvent.setup();
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    // Click View History
    await user.click(screen.getByText('View History'));

    expect(dispatchSpy).toHaveBeenCalled();
  });

  it('executes Link Repo action', async () => {
    const user = userEvent.setup();
    const dispatchSpy = vi.spyOn(window, 'dispatchEvent');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Link Repo'));

    expect(dispatchSpy).toHaveBeenCalled();
  });

  it('executes Toggle Diff when result exists', async () => {
    const user = userEvent.setup();
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    const { editorStore } = await import('$lib/stores/editor.svelte');
    // Toggle Diff guards on result.id — set it so the action fires
    vi.mocked(forgeStore).result = { id: 'diff-1' } as any;
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Toggle Diff'));

    expect(editorStore.openDiff).toHaveBeenCalledWith('diff-1');
  });

  it('shows toast when Toggle Diff has no result', async () => {
    const user = userEvent.setup();
    const { addToast } = await import('$lib/stores/toast.svelte');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Toggle Diff'));

    expect(addToast).toHaveBeenCalledWith('modified', 'No result to diff — optimize a prompt first');
  });

  it('shows toast when Copy Result has no result', async () => {
    const user = userEvent.setup();
    const { addToast } = await import('$lib/stores/toast.svelte');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Copy Result'));

    expect(addToast).toHaveBeenCalledWith('modified', 'No result to copy — optimize a prompt first');
  });

  it('copies text and shows toast when Copy Result has a result', async () => {
    const user = userEvent.setup();
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    const { addToast } = await import('$lib/stores/toast.svelte');
    vi.mocked(forgeStore).result = { id: 'copy-1', optimized_prompt: 'Optimized text' } as any;

    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    await user.click(screen.getByText('Copy Result'));

    expect(addToast).toHaveBeenCalledWith('created', 'Copied to clipboard');
    // Palette should close
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('marks unavailable commands with aria-disabled', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    // Forge should be disabled (no prompt), Toggle Diff and Copy Result too (no result)
    const forgeItem = screen.getByText('Forge').closest('[role="option"]');
    const diffItem = screen.getByText('Toggle Diff').closest('[role="option"]');
    const copyItem = screen.getByText('Copy Result').closest('[role="option"]');

    expect(forgeItem).toHaveAttribute('aria-disabled', 'true');
    expect(diffItem).toHaveAttribute('aria-disabled', 'true');
    expect(copyItem).toHaveAttribute('aria-disabled', 'true');
  });

  it('marks always-available commands as not disabled', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    const newPromptItem = screen.getByText('New Prompt').closest('[role="option"]');
    const historyItem = screen.getByText('View History').closest('[role="option"]');

    expect(newPromptItem).toHaveAttribute('aria-disabled', 'false');
    expect(historyItem).toHaveAttribute('aria-disabled', 'false');
  });

  it('closes palette on overlay click', async () => {
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    // Click the overlay (the dialog element itself)
    const overlay = screen.getByRole('dialog');
    await user.click(overlay);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('does not close palette when Escape key handler fires on overlay', async () => {
    // Test the overlay's own keydown handler
    const user = userEvent.setup();
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');
    expect(screen.getByRole('dialog')).toBeInTheDocument();

    const overlay = screen.getByRole('dialog');
    fireEvent.keyDown(overlay, { key: 'Escape' });

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('handles item keydown Enter to execute action', async () => {
    const user = userEvent.setup();
    const { forgeStore } = await import('$lib/stores/forge.svelte');
    render(CommandPalette);
    await user.keyboard('{Control>}k{/Control}');

    const items = screen.getAllByRole('option');
    // Trigger keydown Enter on first item
    fireEvent.keyDown(items[0], { key: 'Enter' });

    expect(forgeStore.reset).toHaveBeenCalled();
  });
});
