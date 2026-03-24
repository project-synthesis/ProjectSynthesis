import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

import PassthroughGuide from './PassthroughGuide.svelte';
import { passthroughGuide } from '$lib/stores/passthrough-guide.svelte';

describe('PassthroughGuide', () => {
  beforeEach(() => {
    passthroughGuide.close();
    passthroughGuide.resetDismissal();
  });

  afterEach(() => {
    cleanup();
    passthroughGuide.close();
  });

  it('does not render when closed', () => {
    render(PassthroughGuide);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('renders when open', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('PASSTHROUGH PROTOCOL')).toBeInTheDocument();
  });

  it('renders WHY section', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    expect(screen.getByText('WHY PASSTHROUGH')).toBeInTheDocument();
    expect(screen.getByText(/Zero-dependency fallback/)).toBeInTheDocument();
  });

  it('renders all 6 step titles', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    expect(screen.getByText('System assembles your prompt')).toBeInTheDocument();
    expect(screen.getByText('Copy the assembled prompt')).toBeInTheDocument();
    expect(screen.getByText('Paste into your LLM')).toBeInTheDocument();
    expect(screen.getByText('Copy the LLM response')).toBeInTheDocument();
    expect(screen.getByText('Paste result back')).toBeInTheDocument();
    expect(screen.getByText('System scores and persists')).toBeInTheDocument();
  });

  it('renders first step expanded by default', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    // First step's description should be visible
    expect(screen.getByText(/Strategy template, scoring rubric/)).toBeInTheDocument();
    // Second step's description should NOT be visible
    expect(screen.queryByText(/Click COPY or select all text/)).not.toBeInTheDocument();
  });

  it('clicking a collapsed step expands it', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    // Click step 2 title
    await user.click(screen.getByText('Copy the assembled prompt'));

    // Step 2 description should now be visible
    expect(screen.getByText(/Click COPY or select all text/)).toBeInTheDocument();
    // Step 1 description should be hidden
    expect(screen.queryByText(/Strategy template, scoring rubric/)).not.toBeInTheDocument();
  });

  it('NEXT button advances to next step', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    // Click NEXT from step 1
    await user.click(screen.getByText('NEXT'));

    // Step 2 should now be expanded
    expect(screen.getByText(/Click COPY or select all text/)).toBeInTheDocument();
  });

  it('PREV button goes back', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    passthroughGuide.setStep(2);
    render(PassthroughGuide);

    await user.click(screen.getByText('PREV'));

    // Step 2 (index 1) should now be expanded
    expect(screen.getByText(/Click COPY or select all text/)).toBeInTheDocument();
  });

  it('NEXT on last step closes modal', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    passthroughGuide.setStep(5); // last step
    render(PassthroughGuide);

    // On last step, the inline nav button says GOT IT
    await user.click(screen.getByLabelText('Complete guide'));
    expect(passthroughGuide.open).toBe(false);
  });

  it('renders feature matrix table', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    expect(screen.getByText('FEATURE MATRIX')).toBeInTheDocument();
    expect(screen.getByText('Analyze phase')).toBeInTheDocument();
    expect(screen.getByText('Pattern injection')).toBeInTheDocument();
    expect(screen.getByText('Dependencies')).toBeInTheDocument();
  });

  it('renders comparison table headers', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    expect(screen.getByText('Internal')).toBeInTheDocument();
    expect(screen.getByText('Sampling')).toBeInTheDocument();
    expect(screen.getByText('Passthrough')).toBeInTheDocument();
  });

  it('Escape key closes the guide', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    await user.keyboard('{Escape}');
    expect(passthroughGuide.open).toBe(false);
  });

  it('backdrop click closes the guide', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    const overlay = screen.getByRole('dialog');
    await user.click(overlay);
    expect(passthroughGuide.open).toBe(false);
  });

  it('close button closes the guide', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    await user.click(screen.getByLabelText('Close guide'));
    expect(passthroughGuide.open).toBe(false);
  });

  it('footer GOT IT button closes the guide', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    // There are two GOT IT contexts — the footer always has one
    const gotItButtons = screen.getAllByText('GOT IT');
    // Footer button is the last one
    await user.click(gotItButtons[gotItButtons.length - 1]);
    expect(passthroughGuide.open).toBe(false);
  });

  it('"Don\'t show on toggle" checkbox triggers dismiss on close', async () => {
    const user = userEvent.setup();
    passthroughGuide.show(false);
    render(PassthroughGuide);

    const checkbox = screen.getByRole('checkbox');
    await user.click(checkbox);
    expect(checkbox).toBeChecked();

    // Close via close button
    await user.click(screen.getByLabelText('Close guide'));

    // Should have persisted dismissal
    expect(localStorage.getItem('synthesis:passthrough_guide_dismissed')).toBe('1');
  });

  it('renders dismiss checkbox unchecked by default', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).not.toBeChecked();
  });

  it('has proper ARIA attributes', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAttribute('aria-label', 'Passthrough workflow guide');
  });

  it('step headers have aria-expanded attribute', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    const stepButtons = screen.getAllByRole('button').filter(
      (b) => b.getAttribute('aria-expanded') !== null,
    );
    expect(stepButtons.length).toBe(6);
    // First step should be expanded
    expect(stepButtons[0]).toHaveAttribute('aria-expanded', 'true');
    // Others should be collapsed
    expect(stepButtons[1]).toHaveAttribute('aria-expanded', 'false');
  });
});
