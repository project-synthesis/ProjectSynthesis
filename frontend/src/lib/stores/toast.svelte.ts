export type ToastType = 'info' | 'success' | 'error' | 'warning';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
  dismissing: boolean;
  action?: ToastAction;
}

const MAX_VISIBLE = 3;
let nextId = 0;

class ToastStore {
  toasts = $state<ToastItem[]>([]);

  show(message: string, type: ToastType = 'info', duration = 5000, action?: ToastAction) {
    const id = nextId++;
    const toast: ToastItem = { id, message, type, dismissing: false, action };

    // If we already have MAX_VISIBLE toasts, dismiss the oldest
    while (this.toasts.filter(t => !t.dismissing).length >= MAX_VISIBLE) {
      const oldest = this.toasts.find(t => !t.dismissing);
      if (oldest) this.dismiss(oldest.id);
    }

    this.toasts = [...this.toasts, toast];

    // Auto-dismiss after duration
    setTimeout(() => {
      this.dismiss(id);
    }, duration);
  }

  dismiss(id: number) {
    const toast = this.toasts.find(t => t.id === id);
    if (!toast || toast.dismissing) return;

    // Start exit animation
    toast.dismissing = true;
    this.toasts = [...this.toasts];

    // Remove after animation completes (300ms)
    setTimeout(() => {
      this.toasts = this.toasts.filter(t => t.id !== id);
    }, 300);
  }

  success(message: string, duration = 5000, action?: ToastAction) {
    this.show(message, 'success', duration, action);
  }

  error(message: string, duration = 5000, action?: ToastAction) {
    this.show(message, 'error', duration, action);
  }

  warning(message: string, duration = 5000, action?: ToastAction) {
    this.show(message, 'warning', duration, action);
  }

  info(message: string, duration = 5000, action?: ToastAction) {
    this.show(message, 'info', duration, action);
  }
}

export const toast = new ToastStore();
