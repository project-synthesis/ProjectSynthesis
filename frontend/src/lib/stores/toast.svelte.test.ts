import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { toastStore, addToast } from './toast.svelte';

describe('ToastStore', () => {
  beforeEach(() => {
    toastStore._reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts with empty queue', () => {
    expect(toastStore.toasts).toHaveLength(0);
  });

  it('adds a toast with created action', () => {
    toastStore.add('created', 'Item created');
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].message).toBe('Item created');
    expect(toastStore.toasts[0].symbol).toBe('+');
  });

  it('adds a toast with modified action', () => {
    toastStore.add('modified', 'Item modified');
    expect(toastStore.toasts[0].symbol).toBe('~');
  });

  it('adds a toast with deleted action', () => {
    toastStore.add('deleted', 'Item deleted');
    expect(toastStore.toasts[0].symbol).toBe('-');
  });

  it('limits to 3 visible toasts', () => {
    toastStore.add('created', 'First');
    toastStore.add('created', 'Second');
    toastStore.add('created', 'Third');
    toastStore.add('created', 'Fourth');
    expect(toastStore.toasts.length).toBeLessThanOrEqual(3);
  });

  it('auto-dismisses after timeout', () => {
    toastStore.add('created', 'Temporary');
    expect(toastStore.toasts).toHaveLength(1);
    vi.advanceTimersByTime(4000);
    expect(toastStore.toasts).toHaveLength(0);
  });

  it('dismiss removes specific toast', () => {
    toastStore.add('created', 'Stay');
    toastStore.add('created', 'Go');
    const goId = toastStore.toasts[1].id;
    toastStore.dismiss(goId);
    expect(toastStore.toasts).toHaveLength(1);
    expect(toastStore.toasts[0].message).toBe('Stay');
  });

  it('addToast convenience function works', () => {
    addToast('created', 'Via helper');
    expect(toastStore.toasts).toHaveLength(1);
  });
});
