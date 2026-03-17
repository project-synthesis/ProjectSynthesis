export interface ToastItem {
  id: string;
  symbol: string;
  message: string;
  color: string;
}

const ACTION_CONFIG: Record<string, { symbol: string; verb: string; color: string }> = {
  created: { symbol: '+', verb: 'detected', color: 'var(--color-neon-green)' },
  modified: { symbol: '~', verb: 'updated', color: 'var(--color-neon-yellow)' },
  deleted: { symbol: '-', verb: 'removed', color: 'var(--color-neon-red)' },
};

let _counter = 0;

class ToastStore {
  toasts = $state<ToastItem[]>([]);

  add(action: string, name: string): void {
    const config = ACTION_CONFIG[action] ?? ACTION_CONFIG.modified;
    const id = `toast-${Date.now()}-${_counter++}`;
    const item: ToastItem = {
      id,
      symbol: config.symbol,
      message: `${name} ${config.verb}`,
      color: config.color,
    };

    if (this.toasts.length >= 3) {
      this.toasts = this.toasts.slice(-2);
    }
    this.toasts = [...this.toasts, item];

    setTimeout(() => this.dismiss(id), 3000);
  }

  dismiss(id: string): void {
    this.toasts = this.toasts.filter(t => t.id !== id);
  }
}

export const toastStore = new ToastStore();
export const addToast = (action: string, name: string) => toastStore.add(action, name);
