/**
 * topologyCache — persistent settled-position cache for the 3D topology.
 *
 * The topology renderer runs a 60-iteration force simulation to settle
 * node positions. Because the expensive part is deterministic given the
 * scene fingerprint (sorted node IDs), we cache the output in localStorage
 * and skip the work when the scene hasn't changed between reloads.
 *
 * Staleness policy: only the most recent fingerprint is retained. Writing
 * a new entry evicts all older `topology_settled_*` keys. There is no
 * wall-clock TTL — the fingerprint itself is the invalidation signal.
 *
 * Keys: `topology_settled_<fingerprint>` where fingerprint is
 *   `[...nodeIds].sort().join('|')`.
 */

const STORAGE_PREFIX = 'topology_settled_';

function readAllPrefixedKeys(): string[] {
  if (typeof localStorage === 'undefined') return [];
  const keys: string[] = [];
  for (let i = 0; i < localStorage.length; i++) {
    const k = localStorage.key(i);
    if (k?.startsWith(STORAGE_PREFIX)) keys.push(k);
  }
  return keys;
}

class TopologyCacheStore {
  /** Build a fingerprint from the scene's node IDs. */
  computeFingerprint(nodeIds: readonly string[]): string {
    return [...nodeIds].sort().join('|');
  }

  /** Fetch cached settled positions for the given fingerprint, or null. */
  get(fingerprint: string): Float32Array | null {
    if (typeof localStorage === 'undefined') return null;
    try {
      const raw = localStorage.getItem(STORAGE_PREFIX + fingerprint);
      if (!raw) return null;
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return null;
      return new Float32Array(parsed as number[]);
    } catch {
      return null;
    }
  }

  /**
   * Persist settled positions for `fingerprint`. Sweeps older prefix-matching
   * keys to keep the cache single-entry. Best-effort — silently drops on
   * quota errors.
   */
  set(fingerprint: string, positions: Float32Array): void {
    if (typeof localStorage === 'undefined') return;
    const key = STORAGE_PREFIX + fingerprint;
    try {
      for (const existing of readAllPrefixedKeys()) {
        if (existing !== key) {
          try {
            localStorage.removeItem(existing);
          } catch {
            /* ignore */
          }
        }
      }
      localStorage.setItem(key, JSON.stringify(Array.from(positions)));
    } catch {
      /* quota exceeded — ignore */
    }
  }

  /** Test-only — evict every `topology_settled_*` key. */
  _reset(): void {
    if (typeof localStorage === 'undefined') return;
    for (const key of readAllPrefixedKeys()) {
      try {
        localStorage.removeItem(key);
      } catch {
        /* ignore */
      }
    }
  }
}

export const topologyCache = new TopologyCacheStore();
