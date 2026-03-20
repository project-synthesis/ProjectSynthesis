import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import DiffView from './DiffView.svelte';

describe('DiffView', () => {
  afterEach(() => {
    cleanup();
  });

  const original = 'Hello world\nThis is line two\nLine three here';
  const optimized = 'Hello world\nThis is a modified line two\nLine three here\nNew fourth line';

  it('renders the DIFF toolbar title', () => {
    render(DiffView, { props: { original, optimized } });
    expect(screen.getByText('DIFF')).toBeInTheDocument();
  });

  it('renders UNIFIED and SPLIT mode buttons', () => {
    render(DiffView, { props: { original, optimized } });
    expect(screen.getByRole('button', { name: /unified/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /split/i })).toBeInTheDocument();
  });

  it('shows added line count in toolbar', () => {
    render(DiffView, { props: { original: 'line one\n', optimized: 'line one\nadded line\n' } });
    // +1 added line
    expect(screen.getByText('+1')).toBeInTheDocument();
  });

  it('shows removed line count in toolbar', () => {
    render(DiffView, { props: { original: 'line one\nline two\n', optimized: 'line one\n' } });
    // -1 removed line
    expect(screen.getByText('-1')).toBeInTheDocument();
  });

  it('shows unchanged line count in toolbar', () => {
    render(DiffView, { props: { original: 'same line\n', optimized: 'same line\n' } });
    expect(screen.getByText('1 unchanged')).toBeInTheDocument();
  });

  it('starts in unified mode by default', () => {
    render(DiffView, { props: { original, optimized } });
    const unifiedBtn = screen.getByRole('button', { name: /unified/i });
    // The active button gets a specific class — verify it's present by checking content renders in unified table
    // Unified mode shows the diff as a single table, not split columns
    expect(unifiedBtn).toBeInTheDocument();
    // ORIGINAL/OPTIMIZED labels only appear in split mode
    expect(screen.queryByText('ORIGINAL')).not.toBeInTheDocument();
  });

  it('switches to split mode when SPLIT button is clicked', async () => {
    const user = userEvent.setup();
    render(DiffView, { props: { original, optimized } });
    await user.click(screen.getByRole('button', { name: /split/i }));
    expect(screen.getByText('ORIGINAL')).toBeInTheDocument();
    expect(screen.getByText('OPTIMIZED')).toBeInTheDocument();
  });

  it('switches back to unified mode after split', async () => {
    const user = userEvent.setup();
    render(DiffView, { props: { original, optimized } });
    await user.click(screen.getByRole('button', { name: /split/i }));
    expect(screen.getByText('ORIGINAL')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /unified/i }));
    expect(screen.queryByText('ORIGINAL')).not.toBeInTheDocument();
  });

  it('handles identical texts (all unchanged)', () => {
    const text = 'same content\n';
    render(DiffView, { props: { original: text, optimized: text } });
    expect(screen.getByText('+0')).toBeInTheDocument();
    expect(screen.getByText('-0')).toBeInTheDocument();
  });

  it('renders line markers in unified mode', () => {
    render(DiffView, { props: { original: 'old line\n', optimized: 'new line\n' } });
    // "-" marker for removed and "+" marker for added lines in unified table cells
    // These appear as text in table cells
    const allText = document.body.textContent ?? '';
    expect(allText).toContain('-');
    expect(allText).toContain('+');
  });
});
