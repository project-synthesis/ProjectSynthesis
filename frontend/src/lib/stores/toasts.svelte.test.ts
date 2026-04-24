import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { toastsStore } from '$lib/stores/toasts.svelte';

describe('toastsStore', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // reset internal state between tests
    [...toastsStore.toasts].forEach(t => toastsStore.dismiss(t.id));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('push returns a string id and adds the toast', () => {
    const id = toastsStore.push({
      kind: 'info',
      message: 'hi',
      durationMs: 1000,
    });
    expect(typeof id).toBe('string');
    expect(toastsStore.toasts).toHaveLength(1);
    expect(toastsStore.toasts[0].id).toBe(id);
  });

  it('auto-dismisses after durationMs and calls commit when defined', async () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    const id = toastsStore.push({
      kind: 'undo', message: 'x', durationMs: 5000, commit,
    });
    expect(toastsStore.toasts).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(5000);

    expect(commit).toHaveBeenCalledOnce();
    expect(toastsStore.toasts.find(t => t.id === id)).toBeUndefined();
  });

  it('undo calls t.undo and does NOT call commit', async () => {
    const undo = vi.fn();
    const commit = vi.fn();
    const id = toastsStore.push({
      kind: 'undo', message: 'x', durationMs: 5000, undo, commit,
    });

    toastsStore.undo(id);

    expect(undo).toHaveBeenCalledOnce();
    expect(commit).not.toHaveBeenCalled();
    expect(toastsStore.toasts.find(t => t.id === id)).toBeUndefined();
  });

  it('dismiss removes without calling commit or undo', () => {
    const undo = vi.fn();
    const commit = vi.fn();
    const id = toastsStore.push({
      kind: 'undo', message: 'x', durationMs: 5000, undo, commit,
    });

    toastsStore.dismiss(id);

    expect(undo).not.toHaveBeenCalled();
    expect(commit).not.toHaveBeenCalled();
    expect(toastsStore.toasts).toHaveLength(0);
  });

  it('caps concurrent toasts at 3 — oldest ages out on 4th push', () => {
    const a = toastsStore.push({ kind: 'info', message: 'a', durationMs: 10000 });
    const b = toastsStore.push({ kind: 'info', message: 'b', durationMs: 10000 });
    const c = toastsStore.push({ kind: 'info', message: 'c', durationMs: 10000 });
    const d = toastsStore.push({ kind: 'info', message: 'd', durationMs: 10000 });

    expect(toastsStore.toasts).toHaveLength(3);
    expect(toastsStore.toasts.map(t => t.id)).toEqual([b, c, d]);
    expect(toastsStore.toasts.find(t => t.id === a)).toBeUndefined();
  });

  it('pause halts the timer; resume recreates it with remaining ms', async () => {
    const commit = vi.fn().mockResolvedValue(undefined);
    const id = toastsStore.push({
      kind: 'undo', message: 'x', durationMs: 5000, commit,
    });

    await vi.advanceTimersByTimeAsync(2000);
    toastsStore.pause(id);
    await vi.advanceTimersByTimeAsync(10000);
    expect(commit).not.toHaveBeenCalled();

    toastsStore.resume(id);
    await vi.advanceTimersByTimeAsync(3000); // 5000 - 2000 = 3000 remaining
    expect(commit).toHaveBeenCalledOnce();
  });
});
