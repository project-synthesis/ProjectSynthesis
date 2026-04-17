/**
 * navCollapse — persistent collapse state for sidebar sections.
 *
 * Default-open policy: a key absent from the set means expanded. First-time
 * users see everything open; explicit collapse persists across refreshes.
 *
 * Key convention:
 *   - `readiness`                    — DOMAIN READINESS panel
 *   - `templates`                    — PROVEN TEMPLATES list
 *   - `domain:<name>`                — top-level domain group (BACKEND, etc.)
 *   - `subdomain:<id>`               — sub-domain group under a domain
 *
 * Storage: serialized as a JSON string[] under `synthesis:navigator_collapsed`.
 * Set is not JSON-serializable, so we round-trip via Array.
 */

const STORAGE_KEY = 'synthesis:navigator_collapsed';

function loadInitial(): Set<string> {
  if (typeof localStorage === 'undefined') return new Set();
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((v): v is string => typeof v === 'string'));
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

class NavCollapseStore {
  private _collapsed = $state<Set<string>>(loadInitial());

  /** True when the section identified by `key` is currently collapsed. */
  isCollapsed(key: string): boolean {
    return this._collapsed.has(key);
  }

  /** Default-open convenience — inverse of `isCollapsed`. */
  isOpen(key: string): boolean {
    return !this._collapsed.has(key);
  }

  toggle(key: string): void {
    const next = new Set(this._collapsed);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    this._collapsed = next;
    persist(next);
  }

  set(key: string, collapsed: boolean): void {
    if (this._collapsed.has(key) === collapsed) return;
    const next = new Set(this._collapsed);
    if (collapsed) next.add(key);
    else next.delete(key);
    this._collapsed = next;
    persist(next);
  }

  /** Test-only — reset to a clean slate. */
  _reset(): void {
    this._collapsed = new Set();
    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  }
}

export const navCollapse = new NavCollapseStore();
