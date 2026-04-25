/**
 * Observatory store — pattern-density telemetry for the Taxonomy Observatory.
 *
 * Owns the period selector (24h/7d/30d) persisted under
 * `synthesis:observatory_period` and fetches `/api/taxonomy/pattern-density`
 * for the active window. `setPeriod()` debounces re-fetch by 1 s so rapid
 * tab-flick interactions collapse into a single network round-trip.
 *
 * Backend contract: `backend/app/services/taxonomy_observatory.py` +
 * `backend/app/routers/taxonomy.py::pattern_density`.
 */

import {
  fetchPatternDensity,
  type PatternDensityRow,
  type ObservatoryPeriod,
} from '$lib/api/observatory';

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

class ObservatoryStore {
  period = $state<ObservatoryPeriod>(readInitialPeriod());
  patternDensity = $state<PatternDensityRow[] | null>(null);
  patternDensityLoading = $state(false);
  patternDensityError = $state<string | null>(null);

  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;
  /** Generation counter guards against stale responses overwriting fresh data
   *  when a user flicks the period selector while a fetch is already in-flight. */
  private _fetchGeneration = 0;

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

  /** @internal Test-only: restore initial state. */
  _reset(): void {
    if (this._debounceTimer) {
      clearTimeout(this._debounceTimer);
      this._debounceTimer = null;
    }
    this._fetchGeneration = 0;
    this.period = DEFAULT_PERIOD;
    this.patternDensity = null;
    this.patternDensityLoading = false;
    this.patternDensityError = null;
  }
}

export const observatoryStore = new ObservatoryStore();
