/**
 * Observatory store — pattern-density telemetry + period-scoped timeline
 * backfill for the Taxonomy Observatory.
 *
 * Owns the period selector (24h/7d/30d) persisted under
 * `synthesis:observatory_period` and fetches:
 *   - `/api/taxonomy/pattern-density` → `patternDensity` (Heatmap)
 *   - `/api/clusters/activity/history?since=...&until=...` →
 *     `historicalEvents` (Timeline backfill)
 *
 * `setPeriod()` debounces re-fetch by 1 s so rapid tab-flick interactions
 * collapse into a single network round-trip per panel.
 *
 * **Separation of concerns** — `historicalEvents` is the Observatory's
 * own period-scoped buffer; the live SSE ring (`clustersStore.activityEvents`)
 * is read separately by the Timeline component and merged at render time.
 * This isolation prevents the period selector from silently mutating the
 * ActivityPanel's terminal feed (which lives in the topology view).
 *
 * Backend contracts:
 *   - `backend/app/services/taxonomy_insights.py` +
 *     `backend/app/routers/taxonomy_insights.py::get_pattern_density`
 *   - `backend/app/routers/clusters.py::get_cluster_activity_history`
 *     (`since`/`until` range variant)
 */

import {
  fetchPatternDensity,
  type PatternDensityRow,
  type ObservatoryPeriod,
} from '$lib/api/observatory';
import {
  getClusterActivity,
  getClusterActivityHistory,
  type TaxonomyActivityEvent,
} from '$lib/api/clusters';

const STORAGE_KEY = 'synthesis:observatory_period';
const VALID_PERIODS = ['24h', '7d', '30d'] as const;
const DEBOUNCE_MS = 1000;
const DEFAULT_PERIOD: ObservatoryPeriod = '7d';

function isValidPeriod(value: unknown): value is ObservatoryPeriod {
  return typeof value === 'string'
    && (VALID_PERIODS as readonly string[]).includes(value);
}

function readInitialPeriod(): ObservatoryPeriod {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (isValidPeriod(raw)) return raw;
  } catch {
    /* private mode / no storage — fall through */
  }
  return DEFAULT_PERIOD;
}

/** Map period chip → number of days for since/until range. */
const PERIOD_DAYS: Record<ObservatoryPeriod, number> = { '24h': 1, '7d': 7, '30d': 30 };

/** Render-side cap on the merged timeline (live SSE + historical) — keeps
 *  the Timeline cheap on long windows. The cap is the canonical limit
 *  shared with `clustersStore.loadActivity()` and the JSONL endpoint
 *  query. */
const TIMELINE_EVENT_CAP = 200;

class ObservatoryStore {
  period = $state<ObservatoryPeriod>(readInitialPeriod());

  /** Per-domain pattern-density rollup (Heatmap data source). */
  patternDensity = $state<PatternDensityRow[] | null>(null);
  patternDensityLoading = $state(false);
  patternDensityError = $state<string | null>(null);

  /**
   * Period-scoped JSONL backfill for the Lifecycle Timeline.
   *
   * Owned by the Observatory — the Timeline component derives a merged
   * view from this PLUS the live SSE ring (`clustersStore.activityEvents`)
   * at render time. Writing into our own state instead of mutating the
   * shared ring keeps the period selector from silently disturbing the
   * ActivityPanel terminal feed in the topology view.
   */
  historicalEvents = $state<TaxonomyActivityEvent[]>([]);
  historicalLoading = $state(false);
  historicalError = $state<string | null>(null);

  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;
  /** Generation counter guards against stale responses overwriting fresh data
   *  when a user flicks the period selector while a fetch is already in-flight. */
  private _fetchGeneration = 0;
  /** Separate generation counter for the timeline-backfill fetch — the two
   *  panels' fetches race independently and a stale heatmap response must
   *  not invalidate a fresh timeline response or vice versa. */
  private _timelineGeneration = 0;

  setPeriod(next: ObservatoryPeriod): void {
    this.period = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* private mode / no storage — period change still applies in memory */
    }
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => {
      void this.refreshPatternDensity();
      void this.loadTimelineEvents();
    }, DEBOUNCE_MS);
  }

  async refreshPatternDensity(): Promise<void> {
    const gen = ++this._fetchGeneration;
    this.patternDensityLoading = true;
    this.patternDensityError = null;
    try {
      const resp = await fetchPatternDensity(this.period);
      if (gen !== this._fetchGeneration) return; // stale — newer fetch in flight
      this.patternDensity = resp.rows;
    } catch {
      if (gen !== this._fetchGeneration) return;
      this.patternDensityError = 'fetch-failed';
    } finally {
      if (gen === this._fetchGeneration) this.patternDensityLoading = false;
    }
  }

  /**
   * Hydrate `historicalEvents` for the active period.
   *
   * Calls the JSONL `since`/`until` range variant and merges with the
   * ring buffer so post-restart sparse-ring instances still surface a
   * meaningful baseline. Dedupes by `ts|op|decision` (matches the
   * convention used by `clustersStore.loadActivity()`).
   *
   * Resilient under fast period flicks: a generation counter discards
   * stale responses so the latest period always wins. Under failure,
   * leaves prior `historicalEvents` intact + sets `historicalError`.
   */
  async loadTimelineEvents(): Promise<void> {
    const gen = ++this._timelineGeneration;
    const today = new Date();
    const until = today.toISOString().slice(0, 10);
    const sinceDate = new Date(today.getTime() - (PERIOD_DAYS[this.period] - 1) * 86_400_000);
    const since = sinceDate.toISOString().slice(0, 10);

    this.historicalLoading = true;
    this.historicalError = null;
    try {
      const [ring, hist] = await Promise.all([
        getClusterActivity({ limit: TIMELINE_EVENT_CAP }),
        getClusterActivityHistory({ since, until, limit: TIMELINE_EVENT_CAP }),
      ]);
      if (gen !== this._timelineGeneration) return; // stale — newer fetch in flight

      const seen = new Set<string>();
      const merged: TaxonomyActivityEvent[] = [];
      for (const e of ring.events) {
        const key = `${e.ts}|${e.op}|${e.decision}`;
        if (!seen.has(key)) { seen.add(key); merged.push(e); }
      }
      for (const e of hist.events) {
        const key = `${e.ts}|${e.op}|${e.decision}`;
        if (!seen.has(key)) { seen.add(key); merged.push(e); }
      }
      merged.sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''));
      this.historicalEvents = merged.slice(0, TIMELINE_EVENT_CAP);
    } catch {
      if (gen !== this._timelineGeneration) return;
      this.historicalError = 'fetch-failed';
    } finally {
      if (gen === this._timelineGeneration) this.historicalLoading = false;
    }
  }

  /** @internal Test-only: restore initial state. */
  _reset(): void {
    if (this._debounceTimer) {
      clearTimeout(this._debounceTimer);
      this._debounceTimer = null;
    }
    this._fetchGeneration = 0;
    this._timelineGeneration = 0;
    this.period = DEFAULT_PERIOD;
    this.patternDensity = null;
    this.patternDensityLoading = false;
    this.patternDensityError = null;
    this.historicalEvents = [];
    this.historicalLoading = false;
    this.historicalError = null;
  }
}

export const observatoryStore = new ObservatoryStore();
