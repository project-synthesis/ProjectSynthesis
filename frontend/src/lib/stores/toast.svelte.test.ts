import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { toastStore, addToast, addInfoToast } from './toast.svelte';

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

  describe('info', () => {
    it('info adds a toast with cyan color and i symbol', () => {
      toastStore.info('System info');
      expect(toastStore.toasts).toHaveLength(1);
      expect(toastStore.toasts[0].symbol).toBe('i');
      expect(toastStore.toasts[0].color).toBe('var(--color-neon-cyan)');
      expect(toastStore.toasts[0].message).toBe('System info');
    });

    it('info auto-dismisses with default 3000ms when no opts', () => {
      toastStore.info('Ephemeral');
      expect(toastStore.toasts).toHaveLength(1);
      vi.advanceTimersByTime(4000);
      expect(toastStore.toasts).toHaveLength(0);
    });

    it('info respects opts.dismissMs override', () => {
      toastStore.info('Long lived', { dismissMs: 10000 });
      expect(toastStore.toasts).toHaveLength(1);
      vi.advanceTimersByTime(4000);
      expect(toastStore.toasts).toHaveLength(1);
      vi.advanceTimersByTime(7000);
      expect(toastStore.toasts).toHaveLength(0);
    });

    it('info ignores non-positive / non-finite dismissMs and uses default', () => {
      toastStore.info('Negative', { dismissMs: -5 });
      expect(toastStore.toasts).toHaveLength(1);
      vi.advanceTimersByTime(4000);
      expect(toastStore.toasts).toHaveLength(0);

      toastStore.info('NaN', { dismissMs: NaN });
      expect(toastStore.toasts).toHaveLength(1);
      vi.advanceTimersByTime(4000);
      expect(toastStore.toasts).toHaveLength(0);
    });

    it('addInfoToast helper produces identical result', () => {
      addInfoToast('Via helper');
      expect(toastStore.toasts).toHaveLength(1);
      expect(toastStore.toasts[0].symbol).toBe('i');
      expect(toastStore.toasts[0].color).toBe('var(--color-neon-cyan)');
    });

    it('info participates in MAX_VISIBLE eviction', () => {
      toastStore.add('created', 'First');
      toastStore.add('created', 'Second');
      toastStore.add('created', 'Third');
      toastStore.info('fourth');
      expect(toastStore.toasts).toHaveLength(3);
      expect(toastStore.toasts.find(t => t.message === 'First')).toBeUndefined();
      expect(toastStore.toasts[toastStore.toasts.length - 1].message).toBe('fourth');
    });
  });
});
