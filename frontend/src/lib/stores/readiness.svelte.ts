/**
 * Domain readiness store — reactive, API-driven snapshot of sub-domain
 * emergence + domain dissolution pressure for every top-level domain.
 *
 * Backend contract: `backend/app/services/taxonomy/sub_domain_readiness.py`.
 * Backend caches for 30s; this store mirrors that window to minimize refetch
 * chatter and invalidates on taxonomy SSE events (`taxonomy_changed`,
 * `domain_created`, `sub_domain_created`, `sub_domain_dissolved`).
 */

import {
  getAllDomainReadiness,
  getDomainReadiness,
  type DomainReadinessReport,
} from '$lib/api/readiness';

/** Backend TTL is 30s — matching here avoids cache-line thrash. */
const STALE_WINDOW_MS = 30_000;

class ReadinessStore {
  reports = $state<DomainReadinessReport[]>([]);
  loaded = $state(false);
  loading = $state(false);
  lastError = $state<string | null>(null);

  /**
   * Monotonic epoch bumped on every `invalidate()` call. Downstream
   * components that fetch their OWN endpoint (e.g. `DomainReadinessSparkline`
   * hitting `/readiness/history`) read this in a `$effect` to re-trigger
   * their fetch when a tier-crossing SSE arrives. Without this, the store
   * refreshes the summary reports but auxiliary time-series endpoints keep
   * stale points until next mount.
   */
  invalidationEpoch = $state(0);

  /** Last successful batch fetch timestamp (ms since epoch). */
  private _lastLoadedAt = 0;

  /** Generation counter guards against stale responses overwriting fresh data. */
  private _loadGeneration = 0;

  /** Per-domain generation for single-domain fetches. */
  private _perDomainGenerations = new Map<string, number>();

  /** label → report lookup (derived). */
  byLabel = $derived(
    this.reports.reduce<Record<string, DomainReadinessReport>>((acc, r) => {
      acc[r.domain_label] = r;
      return acc;
    }, {}),
  );

  /** id → report lookup (derived). */
  byId = $derived(
    this.reports.reduce<Record<string, DomainReadinessReport>>((acc, r) => {
      acc[r.domain_id] = r;
      return acc;
    }, {}),
  );

  /** Resolve a single report by domain node id. */
  byDomain(domainId: string | null | undefined): DomainReadinessReport | null {
    if (!domainId) return null;
    return this.byId[domainId] ?? null;
  }

  /** Whether the cached batch is still fresh (< TTL). */
  get isFresh(): boolean {
    return this.loaded && Date.now() - this._lastLoadedAt < STALE_WINDOW_MS;
  }

  /**
   * Fetch all domain readiness reports. Short-circuits when a fresh snapshot
   * exists, unless `force=true` is passed (maps to backend `?fresh=true`).
   */
  async loadAll(force = false): Promise<void> {
    if (!force && this.isFresh) return;
    const gen = ++this._loadGeneration;
    this.loading = true;
    try {
      const result = await getAllDomainReadiness(force);
      if (gen !== this._loadGeneration) return; // stale
      this.reports = result;
      this.loaded = true;
      this._lastLoadedAt = Date.now();
      this.lastError = null;
    } catch (err) {
      if (gen !== this._loadGeneration) return;
      const msg = err instanceof Error ? err.message : String(err);
      this.lastError = msg;
      console.warn('Readiness store loadAll failed:', err);
    } finally {
      if (gen === this._loadGeneration) this.loading = false;
    }
  }

  /**
   * Fetch a single domain's readiness. Updates the matching entry in
   * `reports` in place when present, otherwise appends.
   */
  async loadOne(domainId: string, force = false): Promise<DomainReadinessReport | null> {
    const gen = (this._perDomainGenerations.get(domainId) ?? 0) + 1;
    this._perDomainGenerations.set(domainId, gen);
    try {
      const report = await getDomainReadiness(domainId, force);
      if (gen !== this._perDomainGenerations.get(domainId)) return null;
      const idx = this.reports.findIndex((r) => r.domain_id === domainId);
      if (idx >= 0) {
        const next = [...this.reports];
        next[idx] = report;
        this.reports = next;
      } else {
        this.reports = [...this.reports, report];
      }
      this.lastError = null;
      return report;
    } catch (err) {
      if (gen !== this._perDomainGenerations.get(domainId)) return null;
      const msg = err instanceof Error ? err.message : String(err);
      this.lastError = msg;
      console.warn(`Readiness store loadOne(${domainId}) failed:`, err);
      return null;
    }
  }

  /**
   * Invalidate the cached snapshot. Called by SSE handlers on
   * `taxonomy_changed`, `domain_created`, `sub_domain_created`,
   * `sub_domain_dissolved`.
   *
   * Marks data stale and triggers an async refetch — keeps the previous
   * snapshot visible until the fresh batch arrives to avoid UI flicker.
   */
  invalidate(): void {
    this._lastLoadedAt = 0;
    this.invalidationEpoch += 1;
    // Fire-and-forget refetch. The generation counter handles races.
    void this.loadAll(true);
  }

  /** @internal Test-only: restore initial state */
  _reset(): void {
    this.reports = [];
    this.loaded = false;
    this.loading = false;
    this.lastError = null;
    this._lastLoadedAt = 0;
    this._loadGeneration = 0;
    this._perDomainGenerations.clear();
    this.invalidationEpoch = 0;
  }
}

export const readinessStore = new ReadinessStore();
