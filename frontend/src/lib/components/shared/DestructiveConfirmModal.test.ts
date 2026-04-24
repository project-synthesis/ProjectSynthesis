import { render, screen, fireEvent, waitFor } from '@testing-library/svelte';
import { describe, it, expect, vi } from 'vitest';
import DestructiveConfirmModal from './DestructiveConfirmModal.svelte';

const baseProps = {
  open: true,
  title: 'DELETE 3 OPTIMIZATIONS?',
  confirmLabel: 'Delete 3',
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe('DestructiveConfirmModal', () => {
  it('renders title and confirm button when open', () => {
    render(DestructiveConfirmModal, { props: baseProps });
    expect(screen.getByText('DELETE 3 OPTIMIZATIONS?')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete 3' })).toBeInTheDocument();
  });

  it('does not render when open=false', () => {
    render(DestructiveConfirmModal, { props: { ...baseProps, open: false } });
    expect(screen.queryByText('DELETE 3 OPTIMIZATIONS?')).toBeNull();
  });

  it('Confirm button is disabled until DELETE is typed exactly', async () => {
    render(DestructiveConfirmModal, { props: baseProps });
    const confirm = screen.getByRole('button', { name: 'Delete 3' });
    expect(confirm).toBeDisabled();

    const input = screen.getByRole('textbox');
    await fireEvent.input(input, { target: { value: 'delete' } });
    expect(confirm).toBeDisabled(); // case-sensitive

    await fireEvent.input(input, { target: { value: 'DELETE' } });
    expect(confirm).toBeEnabled();
  });

  it('clicking Confirm with valid literal invokes onConfirm', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(DestructiveConfirmModal, {
      props: { ...baseProps, onConfirm },
    });

    await fireEvent.input(screen.getByRole('textbox'), {
      target: { value: 'DELETE' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete 3' }));

    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('Esc calls onCancel', async () => {
    const onCancel = vi.fn();
    const { container } = render(DestructiveConfirmModal, {
      props: { ...baseProps, onCancel },
    });
    await fireEvent.keyDown(container.ownerDocument, { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('renders side-effect hint when provided', () => {
    render(DestructiveConfirmModal, {
      props: { ...baseProps, sideEffectHint: '2 clusters will rebalance.' },
    });
    expect(screen.getByText(/clusters will rebalance/)).toBeInTheDocument();
  });

  it('onConfirm rejection keeps modal open and renders error-banner', async () => {
    const onConfirm = vi.fn().mockRejectedValue(new Error('500'));
    render(DestructiveConfirmModal, {
      props: { ...baseProps, onConfirm },
    });

    await fireEvent.input(screen.getByRole('textbox'), {
      target: { value: 'DELETE' },
    });
    await fireEvent.click(screen.getByRole('button', { name: 'Delete 3' }));

    await waitFor(() => {
      expect(screen.queryByTestId('confirm-modal-error')).toBeInTheDocument();
    });
    expect(screen.getByText('DELETE 3 OPTIMIZATIONS?')).toBeInTheDocument();
  });
});
