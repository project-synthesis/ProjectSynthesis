export type ToastType = 'info' | 'success' | 'error' | 'warning' | 'milestone';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface MilestoneData {
  id: string;
  title: string;
  description: string;
  color: string;
  celebrationText: string;
}

export interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
  dismissing: boolean;
  action?: ToastAction;
  milestoneData?: MilestoneData;
}

const MAX_VISIBLE = 3;
let nextId = 0;

// Deduplication: tracks the last-shown timestamp per `type:message` key.
// Module-level (not reactive) so it persists across renders without triggering effects.
const _recentMessages = new Map<string, number>();
const DEDUP_WINDOW_MS = 5_000;

class ToastStore {
  toasts = $state<ToastItem[]>([]);

  /** Core toast logic — dedup, queue management, auto-dismiss. */
  private _push(item: Omit<ToastItem, 'id' | 'dismissing'>, dedupeKey: string, duration: number): void {
    const lastShown = _recentMessages.get(dedupeKey) ?? 0;
    if (Date.now() - lastShown < DEDUP_WINDOW_MS) return;
    _recentMessages.set(dedupeKey, Date.now());

    const id = nextId++;
    const toast: ToastItem = { ...item, id, dismissing: false };

    while (this.toasts.filter(t => !t.dismissing).length >= MAX_VISIBLE) {
      const oldest = this.toasts.find(t => !t.dismissing);
      if (oldest) this.dismiss(oldest.id);
    }

    this.toasts = [...this.toasts, toast];
    setTimeout(() => { this.dismiss(id); }, duration);
  }

  show(message: string, type: ToastType = 'info', duration = 5000, action?: ToastAction) {
    this._push({ message, type, action }, `${type}:${message}`, duration);
  }

  dismiss(id: number) {
    const toast = this.toasts.find(t => t.id === id);
    if (!toast || toast.dismissing) return;

    toast.dismissing = true;
    this.toasts = [...this.toasts];

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

  /** Show a milestone achievement celebration toast. */
  milestone(m: { id: string; title: string; description: string; color: string; celebrationText: string }, duration = 8000) {
    this._push(
      { message: m.celebrationText, type: 'milestone', milestoneData: m },
      `milestone:${m.id}`,
      duration,
    );
  }
}

export const toast = new ToastStore();
