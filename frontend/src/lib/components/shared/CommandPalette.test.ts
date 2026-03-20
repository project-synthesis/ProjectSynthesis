import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
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
  },
}));

vi.mock('$lib/stores/editor.svelte', () => ({
  editorStore: {
    openResult: vi.fn(),
    openDiff: vi.fn(),
  },
}));

describe('CommandPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks();
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
    expect(screen.getByText('Link Repo')).toBeInTheDocument();
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
});
