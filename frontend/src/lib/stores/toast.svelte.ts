export type ToastAction = 'created' | 'modified' | 'deleted';

export interface ToastItem {
  id: string;
  symbol: string;
  message: string;
  color: string;
}

const ACTION_CONFIG: Record<ToastAction, { symbol: string; color: string }> = {
  created: { symbol: '+', color: 'var(--color-neon-green)' },
  modified: { symbol: '~', color: 'var(--color-neon-yellow)' },
  deleted: { symbol: '-', color: 'var(--color-neon-red)' },
};

const MAX_VISIBLE = 3;
const DISMISS_MS = 3000;

let _counter = 0;

class ToastStore {
  toasts = $state<ToastItem[]>([]);
  private _timers = new Map<string, ReturnType<typeof setTimeout>>();

  add(action: ToastAction, message: string): void {
    const config = ACTION_CONFIG[action];
    const id = `toast-${Date.now()}-${_counter++}`;

    // Evict oldest if at capacity
    while (this.toasts.length >= MAX_VISIBLE) {
      const oldest = this.toasts[0];
      this._clearTimer(oldest.id);
      this.toasts = this.toasts.slice(1);
    }

    this.toasts = [...this.toasts, { id, symbol: config.symbol, message, color: config.color }];
    this._timers.set(id, setTimeout(() => this.dismiss(id), DISMISS_MS));
  }

  dismiss(id: string): void {
    this._clearTimer(id);
    this.toasts = this.toasts.filter(t => t.id !== id);
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
