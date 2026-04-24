/**
 * Toast stack store — Svelte 5 runes-backed singleton.
 *
 * Powers the reusable destructive-action primitive set: UndoToast uses
 * the `commit` hook to defer its API call until the grace window expires
 * (clicking Undo cancels the commit; timer expiry fires it).
 */

export type ToastKind = 'undo' | 'info' | 'error';

export type Toast = {
  id: string;
  kind: ToastKind;
  message: string;
  meta?: string;
  durationMs: number;
  undo?: () => void;
  commit?: () => Promise<void>;
};

type TimerState = {
  handle: number;
  startedAt: number;
  remainingMs: number;
};

const MAX_CONCURRENT = 3;

class ToastsStore {
  toasts: Toast[] = $state([]);
  private timers = new Map<string, TimerState>();

  push(t: Omit<Toast, 'id'>): string {
    const id = crypto.randomUUID();
    const toast: Toast = { ...t, id };
    // Cap concurrent — oldest ages out when over MAX_CONCURRENT.
    const next = [...this.toasts, toast];
    if (next.length > MAX_CONCURRENT) {
      const dropped = next.slice(0, next.length - MAX_CONCURRENT);
      dropped.forEach(d => this.clearTimer(d.id));
      this.toasts = next.slice(-MAX_CONCURRENT);
    } else {
      this.toasts = next;
    }
    this.startTimer(id, t.durationMs);
    return id;
  }

  dismiss(id: string): void {
    this.clearTimer(id);
    this.toasts = this.toasts.filter(t => t.id !== id);
  }

  undo(id: string): void {
    const t = this.toasts.find(x => x.id === id);
    t?.undo?.();
    this.dismiss(id);
  }

  pause(id: string): void {
    const state = this.timers.get(id);
    if (!state) return;
    window.clearTimeout(state.handle);
    const elapsed = Date.now() - state.startedAt;
    state.remainingMs = Math.max(0, state.remainingMs - elapsed);
  }

  resume(id: string): void {
    const state = this.timers.get(id);
    if (!state || state.remainingMs <= 0) return;
    this.startTimer(id, state.remainingMs);
  }

  private startTimer(id: string, durationMs: number): void {
    const handle = window.setTimeout(() => this.expire(id), durationMs);
    this.timers.set(id, {
      handle,
      startedAt: Date.now(),
      remainingMs: durationMs,
    });
  }

  private clearTimer(id: string): void {
    const state = this.timers.get(id);
    if (state !== undefined) window.clearTimeout(state.handle);
    this.timers.delete(id);
  }

  private async expire(id: string): Promise<void> {
    const t = this.toasts.find(x => x.id === id);
    if (t?.commit) {
      try {
        await t.commit();
      } catch {
        // commit owns its own error UX via the calling code's try/catch
      }
    }
    this.dismiss(id);
  }
}

export const toastsStore = new ToastsStore();
