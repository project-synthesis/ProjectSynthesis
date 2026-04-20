import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import ActivityBar from './ActivityBar.svelte';

// ActivityBar uses a tablist pattern — each icon is a tab with aria-selected,
// not a toggle button with aria-pressed. Tests query by role="tab".

describe('ActivityBar', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders the navigation element with accessible label', () => {
    render(ActivityBar);
    expect(screen.getByRole('navigation', { name: 'Activity bar' })).toBeInTheDocument();
  });

  it('renders a tablist with 5 tabs', () => {
    render(ActivityBar);
    expect(screen.getByRole('tablist', { name: 'Primary sections' })).toBeInTheDocument();
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(5);
  });

  it('renders an Editor tab', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'Editor' })).toBeInTheDocument();
  });

  it('renders a History tab', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'History' })).toBeInTheDocument();
  });

  it('renders a Clusters tab', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'Clusters' })).toBeInTheDocument();
  });

  it('renders a GitHub tab', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'GitHub' })).toBeInTheDocument();
  });

  it('renders a Settings tab', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'Settings' })).toBeInTheDocument();
  });

  it('Editor tab is selected by default (aria-selected=true)', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'Editor' })).toHaveAttribute('aria-selected', 'true');
  });

  it('non-selected tabs have aria-selected=false', () => {
    render(ActivityBar);
    expect(screen.getByRole('tab', { name: 'History' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', { name: 'Clusters' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', { name: 'Settings' })).toHaveAttribute('aria-selected', 'false');
  });

  it('clicking History tab selects it', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    const historyTab = screen.getByRole('tab', { name: 'History' });
    await user.click(historyTab);
    expect(historyTab).toHaveAttribute('aria-selected', 'true');
  });

  it('clicking a different tab deselects the previous one', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    const editorTab = screen.getByRole('tab', { name: 'Editor' });
    const historyTab = screen.getByRole('tab', { name: 'History' });

    expect(editorTab).toHaveAttribute('aria-selected', 'true');

    await user.click(historyTab);
    expect(historyTab).toHaveAttribute('aria-selected', 'true');
    expect(editorTab).toHaveAttribute('aria-selected', 'false');
  });

  it('clicking Clusters selects it', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    await user.click(screen.getByRole('tab', { name: 'Clusters' }));
    expect(screen.getByRole('tab', { name: 'Clusters' })).toHaveAttribute('aria-selected', 'true');
  });

  it('clicking Settings selects it', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    await user.click(screen.getByRole('tab', { name: 'Settings' }));
    expect(screen.getByRole('tab', { name: 'Settings' })).toHaveAttribute('aria-selected', 'true');
  });

  it('clicking GitHub selects it', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    await user.click(screen.getByRole('tab', { name: 'GitHub' }));
    expect(screen.getByRole('tab', { name: 'GitHub' })).toHaveAttribute('aria-selected', 'true');
  });

  it('accepts an initial active prop', () => {
    render(ActivityBar, { props: { active: 'settings' } });
    expect(screen.getByRole('tab', { name: 'Settings' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Editor' })).toHaveAttribute('aria-selected', 'false');
  });

  it('ArrowDown cycles to the next tab', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    const tablist = screen.getByRole('tablist', { name: 'Primary sections' });
    tablist.focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('tab', { name: 'History' })).toHaveAttribute('aria-selected', 'true');
  });

  it('ArrowUp from first tab wraps to the last tab', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    const tablist = screen.getByRole('tablist', { name: 'Primary sections' });
    tablist.focus();
    await user.keyboard('{ArrowUp}');
    expect(screen.getByRole('tab', { name: 'Settings' })).toHaveAttribute('aria-selected', 'true');
  });
});
