import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import { toastStore } from '$lib/stores/toast.svelte';
import Toast from './Toast.svelte';

describe('Toast', () => {
  beforeEach(() => {
    toastStore._reset();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders nothing when store is empty', () => {
    const { container } = render(Toast);
    expect(container.querySelector('.toast-container')).toBeNull();
  });

  it('renders a toast message from the store', () => {
    toastStore.add('created', 'Hello world');
    render(Toast);
    expect(screen.getByText('Hello world')).toBeInTheDocument();
  });

  it('renders the + symbol for created action', () => {
    toastStore.add('created', 'Created item');
    render(Toast);
    expect(screen.getByText('+')).toBeInTheDocument();
  });

  it('renders the ~ symbol for modified action', () => {
    toastStore.add('modified', 'Modified item');
    render(Toast);
    expect(screen.getByText('~')).toBeInTheDocument();
  });

  it('renders the - symbol for deleted action', () => {
    toastStore.add('deleted', 'Deleted item');
    render(Toast);
    expect(screen.getByText('-')).toBeInTheDocument();
  });

  it('renders multiple toasts', () => {
    toastStore.add('created', 'First toast');
    toastStore.add('modified', 'Second toast');
    render(Toast);
    expect(screen.getByText('First toast')).toBeInTheDocument();
    expect(screen.getByText('Second toast')).toBeInTheDocument();
  });

  it('renders the toast container with aria-live attribute', () => {
    toastStore.add('created', 'Accessible toast');
    render(Toast);
    const container = document.querySelector('[aria-live="polite"]');
    expect(container).toBeInTheDocument();
  });

  it('removing a toast from the store hides it', async () => {
    toastStore.add('deleted', 'Will be removed');
    render(Toast);
    expect(screen.getByText('Will be removed')).toBeInTheDocument();

    const id = toastStore.toasts[0].id;
    toastStore.dismiss(id);
    // After dismiss, store is empty, so component should show nothing
    expect(toastStore.toasts).toHaveLength(0);
  });
});
