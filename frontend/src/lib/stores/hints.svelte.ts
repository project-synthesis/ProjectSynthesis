/**
 * hints — persistent dismissed-hint registry.
 *
 * First-time users see onboarding hint cards (e.g., the topology pattern
 * graph overlay). Dismissing a hint records its key so the card never
 * reappears across reloads.
 *
 * Storage: JSON string[] under `synthesis:hints_dismissed`.
 *
 * Known keys:
 *   - `pattern_graph` — topology pattern graph onboarding card
 *
 * Migration: legacy per-hint keys (e.g., `synthesis:pattern_graph_hints_dismissed`)
 * are folded into the new set on first load and cleaned up.
 */

const STORAGE_KEY = 'synthesis:hints_dismissed';

// Legacy per-hint keys we migrated from. Dropped on first load if set to '1'.
const LEGACY_KEYS: Record<string, string> = {
  pattern_graph: 'synthesis:pattern_graph_hints_dismissed',
};

function loadInitial(): Set<string> {
  if (typeof localStorage === 'undefined') return new Set();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    const set = new Set<string>(
      Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === 'string') : [],
    );

    // One-shot migration: fold legacy per-hint keys into the set, then drop them.
    let migrated = false;
    for (const [hintKey, legacyKey] of Object.entries(LEGACY_KEYS)) {
      try {
        if (localStorage.getItem(legacyKey) === '1' && !set.has(hintKey)) {
          set.add(hintKey);
          migrated = true;
        }
        localStorage.removeItem(legacyKey);
      } catch {
        /* ignore */
      }
    }
    if (migrated) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]));
      } catch {
        /* quota — ignore */
      }
    }
    return set;
  } catch {
    return new Set();
  }
}

function persist(keys: Set<string>): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...keys]));
  } catch {
    // quota exceeded / disabled — silently drop, persistence is best-effort
  }
}

class HintsStore {
  private _dismissed = $state<Set<string>>(loadInitial());

  /** True when the user has explicitly dismissed the hint identified by `key`. */
  isDismissed(key: string): boolean {
    return this._dismissed.has(key);
  }

  /** Record dismissal of a hint. Idempotent. */
  dismiss(key: string): void {
    if (this._dismissed.has(key)) return;
    const next = new Set(this._dismissed);
    next.add(key);
    this._dismissed = next;
    persist(next);
  }

  /** Test-only — clear all dismissals. */
  _reset(): void {
    this._dismissed = new Set();
    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        /* ignore */
      }
    }
  }
}

export const hintsStore = new HintsStore();
