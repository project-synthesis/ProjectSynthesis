import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import SuggestionChips from './SuggestionChips.svelte';

describe('SuggestionChips', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders nothing when suggestions array is empty', () => {
    render(SuggestionChips, { props: { suggestions: [], onSelect: vi.fn() } });
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('renders a chip for each suggestion (up to 3)', () => {
    const suggestions = [
      { text: 'Add examples', source: 'model' },
      { text: 'Be concise', source: 'model' },
      { text: 'Add context', source: 'model' },
    ];
    render(SuggestionChips, { props: { suggestions, onSelect: vi.fn() } });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
  });

  it('shows only the first 3 chips when more are provided', () => {
    const suggestions = [
      { text: 'Chip 1', source: 'model' },
      { text: 'Chip 2', source: 'model' },
      { text: 'Chip 3', source: 'model' },
      { text: 'Chip 4', source: 'model' },
    ];
    render(SuggestionChips, { props: { suggestions, onSelect: vi.fn() } });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(3);
    expect(screen.queryByText('Chip 4')).not.toBeInTheDocument();
  });

  it('renders chip text from the "text" field', () => {
    const suggestions = [{ text: 'Make it shorter', source: 'model' }];
    render(SuggestionChips, { props: { suggestions, onSelect: vi.fn() } });
    expect(screen.getByText('Make it shorter')).toBeInTheDocument();
  });

  it('falls back to "action" field if "text" is missing', () => {
    const suggestions = [{ action: 'Restructure for clarity', type: 'style' }];
    render(SuggestionChips, { props: { suggestions, onSelect: vi.fn() } });
    expect(screen.getByText('Restructure for clarity')).toBeInTheDocument();
  });

  it('calls onSelect with chip text on click', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const suggestions = [
      { text: 'Add examples', source: 'model' },
      { text: 'Be concise', source: 'model' },
    ];
    render(SuggestionChips, { props: { suggestions, onSelect } });
    await user.click(screen.getByText('Add examples'));
    expect(onSelect).toHaveBeenCalledWith('Add examples');
  });

  it('calls onSelect with correct text for the clicked chip', async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const suggestions = [
      { text: 'Suggestion A', source: 'model' },
      { text: 'Suggestion B', source: 'model' },
    ];
    render(SuggestionChips, { props: { suggestions, onSelect } });
    await user.click(screen.getByText('Suggestion B'));
    expect(onSelect).toHaveBeenCalledWith('Suggestion B');
    expect(onSelect).not.toHaveBeenCalledWith('Suggestion A');
  });

  it('renders the chips container with accessible label', () => {
    const suggestions = [{ text: 'Try this', source: 'model' }];
    render(SuggestionChips, { props: { suggestions, onSelect: vi.fn() } });
    expect(screen.getByLabelText('Refinement suggestions')).toBeInTheDocument();
  });

  it('renders a single chip for a single suggestion', () => {
    const suggestions = [{ text: 'Just one chip', source: 'model' }];
    render(SuggestionChips, { props: { suggestions, onSelect: vi.fn() } });
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(screen.getByText('Just one chip')).toBeInTheDocument();
  });
});
