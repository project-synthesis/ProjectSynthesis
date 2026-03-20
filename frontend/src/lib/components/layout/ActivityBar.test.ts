import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import ActivityBar from './ActivityBar.svelte';

describe('ActivityBar', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders the navigation element with accessible label', () => {
    render(ActivityBar);
    expect(screen.getByRole('navigation', { name: 'Activity bar' })).toBeInTheDocument();
  });

  it('renders 5 activity icon buttons', () => {
    render(ActivityBar);
    const buttons = screen.getAllByRole('button');
    // 5 activity buttons (may include logo button)
    const activityButtons = screen.getAllByRole('button').filter(b =>
      ['Editor', 'History', 'Patterns', 'GitHub', 'Settings'].includes(b.getAttribute('aria-label') ?? '')
    );
    expect(activityButtons).toHaveLength(5);
  });

  it('renders an Editor button', () => {
    render(ActivityBar);
    expect(screen.getByRole('button', { name: 'Editor' })).toBeInTheDocument();
  });

  it('renders a History button', () => {
    render(ActivityBar);
    expect(screen.getByRole('button', { name: 'History' })).toBeInTheDocument();
  });

  it('renders a Patterns button', () => {
    render(ActivityBar);
    expect(screen.getByRole('button', { name: 'Patterns' })).toBeInTheDocument();
  });

  it('renders a GitHub button', () => {
    render(ActivityBar);
    expect(screen.getByRole('button', { name: 'GitHub' })).toBeInTheDocument();
  });

  it('renders a Settings button', () => {
    render(ActivityBar);
    expect(screen.getByRole('button', { name: 'Settings' })).toBeInTheDocument();
  });

  it('Editor button is active by default (aria-pressed=true)', () => {
    render(ActivityBar);
    const editorBtn = screen.getByRole('button', { name: 'Editor' });
    expect(editorBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('non-active buttons have aria-pressed=false', () => {
    render(ActivityBar);
    expect(screen.getByRole('button', { name: 'History' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Patterns' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Settings' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('clicking History button sets it as active', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    const historyBtn = screen.getByRole('button', { name: 'History' });
    await user.click(historyBtn);
    expect(historyBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('clicking a different button deactivates the previous one', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    const editorBtn = screen.getByRole('button', { name: 'Editor' });
    const historyBtn = screen.getByRole('button', { name: 'History' });

    // Initially Editor is active
    expect(editorBtn).toHaveAttribute('aria-pressed', 'true');

    // Click History
    await user.click(historyBtn);
    expect(historyBtn).toHaveAttribute('aria-pressed', 'true');
    expect(editorBtn).toHaveAttribute('aria-pressed', 'false');
  });

  it('clicking Patterns makes it active', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    await user.click(screen.getByRole('button', { name: 'Patterns' }));
    expect(screen.getByRole('button', { name: 'Patterns' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('clicking Settings makes it active', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    await user.click(screen.getByRole('button', { name: 'Settings' }));
    expect(screen.getByRole('button', { name: 'Settings' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('clicking GitHub makes it active', async () => {
    const user = userEvent.setup();
    render(ActivityBar);
    await user.click(screen.getByRole('button', { name: 'GitHub' }));
    expect(screen.getByRole('button', { name: 'GitHub' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('accepts an initial active prop', () => {
    render(ActivityBar, { props: { active: 'settings' } });
    expect(screen.getByRole('button', { name: 'Settings' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Editor' })).toHaveAttribute('aria-pressed', 'false');
  });
});
