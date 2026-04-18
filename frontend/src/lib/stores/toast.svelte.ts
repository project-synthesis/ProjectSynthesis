export type ToastAction = 'created' | 'modified' | 'deleted' | 'info';

export interface ToastItem {
  id: string;
  symbol: string;
  message: string;
  color: string;
}

export interface ToastOptions {
  dismissMs?: number;
}

const ACTION_CONFIG: Record<ToastAction, { symbol: string; color: string }> = {
  created: { symbol: '+', color: 'var(--color-neon-green)' },
  modified: { symbol: '~', color: 'var(--color-neon-yellow)' },
  deleted: { symbol: '-', color: 'var(--color-neon-red)' },
  info: { symbol: 'i', color: 'var(--color-neon-cyan)' },
};

const MAX_VISIBLE = 3;
const DISMISS_MS = 3000;

let _counter = 0;

function _resolveDismissMs(opts?: ToastOptions): number {
  const ms = opts?.dismissMs;
  return typeof ms === 'number' && Number.isFinite(ms) && ms > 0 ? ms : DISMISS_MS;
}

class ToastStore {
  toasts = $state<ToastItem[]>([]);
  private _timers = new Map<string, ReturnType<typeof setTimeout>>();

  /** Add a toast for a structural action (created/modified/deleted). Auto-dismisses after the default window. */
  add(action: ToastAction, message: string): void {
    this._enqueue(action, message, DISMISS_MS);
  }

  /** Add a neutral info toast. Optional `dismissMs` override (finite positive number); falls back to the default. */
  info(message: string, opts?: ToastOptions): void {
    this._enqueue('info', message, _resolveDismissMs(opts));
  }

  /** Dismiss a toast by id, cancelling its pending auto-dismiss timer. */
  dismiss(id: string): void {
    this._clearTimer(id);
    this.toasts = this.toasts.filter(t => t.id !== id);
  }

  private _enqueue(action: ToastAction, message: string, dismissMs: number): void {
    const config = ACTION_CONFIG[action];
    const id = `toast-${Date.now()}-${_counter++}`;

    while (this.toasts.length >= MAX_VISIBLE) {
      const oldest = this.toasts[0];
      this._clearTimer(oldest.id);
      this.toasts = this.toasts.slice(1);
    }

    this.toasts = [...this.toasts, { id, symbol: config.symbol, message, color: config.color }];
    this._timers.set(id, setTimeout(() => this.dismiss(id), dismissMs));
  }

  private _clearTimer(id: string): void {
    const timer = this._timers.get(id);
    if (timer !== undefined) {
      clearTimeout(timer);
      this._timers.delete(id);
    }
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.toasts = [];
    for (const timer of this._timers.values()) clearTimeout(timer);
    this._timers.clear();
  }
}

export const toastStore = new ToastStore();
export const addToast = (action: ToastAction, message: string) => toastStore.add(action, message);
export const addInfoToast = (message: string, opts?: ToastOptions) => toastStore.info(message, opts);
