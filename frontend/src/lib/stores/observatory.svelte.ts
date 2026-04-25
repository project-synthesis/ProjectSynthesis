import { fetchPatternDensity, type PatternDensityRow, type ObservatoryPeriod } from '$lib/api/observatory';

const STORAGE_KEY = 'synthesis:observatory_period';
const VALID_PERIODS = ['24h', '7d', '30d'] as const;
const DEBOUNCE_MS = 1000;

function readInitialPeriod(): ObservatoryPeriod {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw && (VALID_PERIODS as readonly string[]).includes(raw)) {
      return raw as ObservatoryPeriod;
    }
  } catch {
    /* private mode / no storage — fall through */
  }
  return '7d';
}

class ObservatoryStore {
  period = $state<ObservatoryPeriod>(readInitialPeriod());
  patternDensity = $state<PatternDensityRow[] | null>(null);
  patternDensityLoading = $state(false);
  patternDensityError = $state<string | null>(null);

  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;

  setPeriod(next: ObservatoryPeriod): void {
    this.period = next;
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => {
      void this.refreshPatternDensity();
    }, DEBOUNCE_MS);
  }

  async refreshPatternDensity(): Promise<void> {
    this.patternDensityLoading = true;
    this.patternDensityError = null;
    try {
      const resp = await fetchPatternDensity(this.period);
      this.patternDensity = resp.rows;
    } catch {
      this.patternDensityError = 'fetch-failed';
    } finally {
      this.patternDensityLoading = false;
    }
  }
}

export const observatoryStore = new ObservatoryStore();
