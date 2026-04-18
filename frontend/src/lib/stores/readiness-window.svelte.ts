/**
 * readinessWindowStore — persistent time-window selector for readiness panels.
 *
 * Used by TopologyInfoPanel to keep the sparkline window (24h/7d/30d) stable
 * across page reloads and domain-selection changes. Stored as a single scalar
 * — the current selection — under `synthesis:readiness_window`.
 *
 * Default is `'24h'`. Unknown/invalid persisted values silently fall back to
 * the default (best-effort persistence, matching `nav_collapse.svelte.ts`).
 */

import type { ReadinessWindow } from '$lib/api/readiness';

const STORAGE_KEY = 'synthesis:readiness_window';
const VALID_WINDOWS: readonly ReadinessWindow[] = ['24h', '7d', '30d'];
const DEFAULT_WINDOW: ReadinessWindow = '24h';

function isValid(value: unknown): value is ReadinessWindow {
  return typeof value === 'string' && (VALID_WINDOWS as readonly string[]).includes(value);
}

function loadInitial(): ReadinessWindow {
  if (typeof localStorage === 'undefined') return DEFAULT_WINDOW;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isValid(raw) ? raw : DEFAULT_WINDOW;
  } catch {
    return DEFAULT_WINDOW;
  }
}

function persist(value: ReadinessWindow): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, value);
  } catch {
    // quota exceeded / disabled — silently drop, persistence is best-effort
  }
}

class ReadinessWindowStore {
  private _window = $state<ReadinessWindow>(loadInitial());

  get window(): ReadinessWindow {
    return this._window;
  }

  set(value: ReadinessWindow): void {
    if (this._window === value) return;
    this._window = value;
    persist(value);
  }

  /** Test-only — reset to default and clear localStorage. */
  _reset(): void {
    this._window = DEFAULT_WINDOW;
    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  }

  /** Test-only — re-read from localStorage (simulates fresh page load). */
  _reloadForTest(): void {
    this._window = loadInitial();
  }
}

export const readinessWindowStore = new ReadinessWindowStore();
