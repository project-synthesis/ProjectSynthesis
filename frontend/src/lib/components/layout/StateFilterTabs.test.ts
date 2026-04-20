import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import StateFilterTabs from './StateFilterTabs.svelte';

describe('StateFilterTabs', () => {
  afterEach(() => cleanup());

  it('renders all five state tabs', () => {
    render(StateFilterTabs, { props: { stateFilter: null, candidateCount: 0, onChange: vi.fn() } });
    expect(screen.getByRole('tab', { name: 'All' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'active' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'candidate' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'mature' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'archived' })).toBeInTheDocument();
  });

  it('marks the active filter with aria-selected=true', () => {
    render(StateFilterTabs, { props: { stateFilter: 'mature', candidateCount: 0, onChange: vi.fn() } });
    expect(screen.getByRole('tab', { name: 'mature' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'active' })).toHaveAttribute('aria-selected', 'false');
  });

  it('shows candidate badge when candidateCount > 0', () => {
    render(StateFilterTabs, { props: { stateFilter: null, candidateCount: 7, onChange: vi.fn() } });
    expect(screen.getByText('7')).toBeInTheDocument();
  });

  it('omits candidate badge when candidateCount === 0', () => {
    render(StateFilterTabs, { props: { stateFilter: null, candidateCount: 0, onChange: vi.fn() } });
    expect(screen.queryByText('0')).not.toBeInTheDocument();
  });

  it('clicking a tab invokes onChange with its filter value', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(StateFilterTabs, { props: { stateFilter: null, candidateCount: 0, onChange } });

    await user.click(screen.getByRole('tab', { name: 'mature' }));
    expect(onChange).toHaveBeenCalledWith('mature');
  });

  it('ArrowRight key moves selection forward via handleTablistArrowKeys', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(StateFilterTabs, { props: { stateFilter: 'active', candidateCount: 0, onChange } });

    const activeTab = screen.getByRole('tab', { name: 'active' });
    activeTab.focus();
    await user.keyboard('{ArrowRight}');
    expect(onChange).toHaveBeenCalledWith('candidate');
  });

  it('ArrowLeft wraps to last tab from first', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(StateFilterTabs, { props: { stateFilter: null, candidateCount: 0, onChange } });

    const firstTab = screen.getByRole('tab', { name: 'All' });
    firstTab.focus();
    await user.keyboard('{ArrowLeft}');
    expect(onChange).toHaveBeenCalledWith('archived');
  });

  it('tablist has horizontal orientation', () => {
    render(StateFilterTabs, { props: { stateFilter: null, candidateCount: 0, onChange: vi.fn() } });
    expect(screen.getByRole('tablist')).toHaveAttribute('aria-orientation', 'horizontal');
  });
});
