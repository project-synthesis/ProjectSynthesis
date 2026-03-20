import '@testing-library/jest-dom/vitest';

// ── EventSource mock (jsdom doesn't provide it) ──────────────────
class MockEventSource {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 2;

  url: string;
  readyState = MockEventSource.OPEN;
  onopen: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  private _listeners: Record<string, Array<(ev: MessageEvent) => void>> = {};

  constructor(url: string) {
    this.url = url;
    // Auto-fire open
    queueMicrotask(() => this.onopen?.(new Event('open')));
  }

  addEventListener(type: string, fn: (ev: MessageEvent) => void) {
    (this._listeners[type] ??= []).push(fn);
  }

  removeEventListener(type: string, fn: (ev: MessageEvent) => void) {
    const list = this._listeners[type];
    if (list) this._listeners[type] = list.filter((f) => f !== fn);
  }

  /** Test helper: simulate server sending a named event */
  __simulateEvent(type: string, data: string) {
    const ev = new MessageEvent(type, { data });
    this._listeners[type]?.forEach((fn) => fn(ev));
  }

  /** Test helper: simulate error */
  __simulateError() {
    this.readyState = MockEventSource.CLOSED;
    this.onerror?.(new Event('error'));
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }
}

Object.assign(globalThis, { EventSource: MockEventSource });

// ── Clipboard mock ───────────────────────────────────────────────
// configurable: true is required so @testing-library/user-event can override it per-test
Object.defineProperty(navigator, 'clipboard', {
  value: { writeText: vi.fn().mockResolvedValue(undefined) },
  writable: true,
  configurable: true,
});

// ── SVG API mocks (for D3 components in jsdom) ──────────────────
if (typeof SVGElement !== 'undefined') {
  SVGElement.prototype.getBBox = () => ({ x: 0, y: 0, width: 100, height: 20 }) as DOMRect;
  SVGElement.prototype.getComputedTextLength = () => 50;
}
