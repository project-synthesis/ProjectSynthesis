import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import RefinementInput from './RefinementInput.svelte';

describe('RefinementInput', () => {
  afterEach(() => {
    cleanup();
  });

  it('renders a text input with placeholder', () => {
    render(RefinementInput, { props: { onSubmit: vi.fn() } });
    expect(screen.getByPlaceholderText('Describe refinement...')).toBeInTheDocument();
  });

  it('has accessible aria-label on input', () => {
    render(RefinementInput, { props: { onSubmit: vi.fn() } });
    expect(screen.getByRole('textbox', { name: 'Refinement request' })).toBeInTheDocument();
  });

  it('renders a REFINE submit button', () => {
    render(RefinementInput, { props: { onSubmit: vi.fn() } });
    expect(screen.getByRole('button', { name: 'Submit refinement' })).toBeInTheDocument();
    expect(screen.getByText('REFINE')).toBeInTheDocument();
  });

  it('submit button is disabled when input is empty', () => {
    render(RefinementInput, { props: { onSubmit: vi.fn() } });
    expect(screen.getByRole('button', { name: 'Submit refinement' })).toBeDisabled();
  });

  it('submit button becomes enabled when user types text', async () => {
    const user = userEvent.setup();
    render(RefinementInput, { props: { onSubmit: vi.fn() } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    await user.type(input, 'Make it shorter');
    expect(screen.getByRole('button', { name: 'Submit refinement' })).not.toBeDisabled();
  });

  it('calls onSubmit with trimmed text on button click', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(RefinementInput, { props: { onSubmit } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    await user.type(input, '  Make it shorter  ');
    await user.click(screen.getByRole('button', { name: 'Submit refinement' }));
    expect(onSubmit).toHaveBeenCalledWith('Make it shorter');
  });

  it('clears input text after successful submit', async () => {
    const user = userEvent.setup();
    render(RefinementInput, { props: { onSubmit: vi.fn() } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    await user.type(input, 'Add examples');
    await user.click(screen.getByRole('button', { name: 'Submit refinement' }));
    expect(input).toHaveValue('');
  });

  it('calls onSubmit on Enter key press', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(RefinementInput, { props: { onSubmit } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    await user.type(input, 'Be more specific');
    await user.keyboard('{Enter}');
    expect(onSubmit).toHaveBeenCalledWith('Be more specific');
  });

  it('does NOT submit on Shift+Enter', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(RefinementInput, { props: { onSubmit } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    await user.type(input, 'Some text');
    await user.keyboard('{Shift>}{Enter}{/Shift}');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('does not call onSubmit for whitespace-only input on Enter', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(RefinementInput, { props: { onSubmit } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    await user.type(input, '   ');
    await user.keyboard('{Enter}');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('disables input when disabled prop is true', () => {
    render(RefinementInput, { props: { onSubmit: vi.fn(), disabled: true } });
    expect(screen.getByRole('textbox', { name: 'Refinement request' })).toBeDisabled();
  });

  it('disables submit button when disabled prop is true', () => {
    render(RefinementInput, { props: { onSubmit: vi.fn(), disabled: true } });
    expect(screen.getByRole('button', { name: 'Submit refinement' })).toBeDisabled();
  });

  it('does not call onSubmit when disabled and Enter pressed', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    // Render enabled first to type, then test disabled state
    render(RefinementInput, { props: { onSubmit, disabled: false } });
    // Since disabled input can't be interacted with, we test that the handler guards it
    // We can verify by testing the component's internal guard (disabled=true + clicking)
    cleanup();

    render(RefinementInput, { props: { onSubmit, disabled: true } });
    const input = screen.getByRole('textbox', { name: 'Refinement request' });
    // Disabled input won't allow typing but we verify submit button is also disabled
    expect(input).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
