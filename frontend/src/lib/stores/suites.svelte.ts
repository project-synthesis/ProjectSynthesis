/**
 * SuitesStore — owns the ValidationSuite surface state for the Topic
 * Probe Tier 2 (v0.4.22) SUITES navigator entry + StatusBar regression
 * badge.
 *
 * Cycle 12 ships the read paths:
 *   - `suites`                — paginated list (single-page in T2; T3+
 *                               extends pagination + filters)
 *   - `selectedSuiteId` +     — drives SuiteDetailView mount; `select()`
 *     `detail` + `replays`      fans out the per-suite + replay fetches
 *   - `regressionAlarmBlock`  — cached from `GET /api/health`'s
 *                               `regression_alarm` block, polled every
 *                               30s. Polling is intentionally decoupled
 *                               from the StatusBar's canonical 60s health
 *                               poll: the alarm UX requires ≤30s freshness
 *                               while the rest of the bar tolerates the
 *                               slower cadence (spec § 11).
 *   - SSE-driven invalidation: on the `taxonomy-changed` DOM CustomEvent
 *                               (re-dispatched by `+page.svelte` from the
 *                               canonical `taxonomy_changed` SSE event)
 *                               the store refetches BOTH the suites list
 *                               and the alarm block — suite create and
 *                               replay completion update orthogonal slices
 *                               of state, so a single event-handler must
 *                               touch both endpoints.
 *
 * Lifecycle contract:
 *   - `startPolling()` is idempotent — a second call returns immediately
 *     and the existing interval keeps ticking. Safe under HMR + SSR-mount
 *     scenarios.
 *   - The first poll fires synchronously on `startPolling()` so the
 *     initial render of `RegressionBadge` doesn't block on the 30s tick.
 *     The SSE listener is registered on the same call.
 *   - `stopPolling()` clears the interval and unregisters the listener;
 *     consumed by tests + the root `+page.svelte` `$effect` teardown.
 *
 * State invariants:
 *   - `loading` is true ONLY while `load()` is in flight; not raised
 *     during health-poll ticks (`refreshHealth()` failures are silent —
 *     the StatusBar owns global outage messaging).
 *   - `error` is the load() error message; cleared on the next successful
 *     `load()`.
 *   - Health-poll failures preserve the last-good `regressionAlarmBlock`;
 *     the badge stays pinned to its last known state until the next
 *     successful poll. Replacing the block on every poll guarantees
 *     stale-cache surfaces resolve within 30s of recovery.
 */

import {
  getSuites,
  getSuite,
  getSuiteReplays,
  type ValidationSuiteListItem,
  type ValidationSuiteOut,
} from '$lib/api/suites';
import { getHealth } from '$lib/api/client';
import type { RunListResponse } from '$lib/api/runs';

/** Mirrors backend `RegressionAlarmEntry`. */
export interface RegressionAlarmEntry {
  suite_id: string;
  label: string;
  baseline_mean: number;
  latest_mean: number;
  delta_abs: number;
  tolerance_abs: number;
  latest_replay_id: string;
  latest_replay_at: string;
}

/** Mirrors backend `RegressionAlarmBlock` (the `regression_alarm` key on
 *  `GET /api/health`). */
export interface RegressionAlarmBlock {
  suites_total: number;
  suites_in_alarm: number;
  latest_alarms: RegressionAlarmEntry[];
}

/** Poll cadence for the `/api/health` regression-alarm block. 30s matches the
 *  spec § 11 readiness-cache TTL — fresh enough for the alarm UX without
 *  hammering the backend. */
const HEALTH_POLL_INTERVAL_MS = 30_000;

class SuitesStore {
  // ── List state ───────────────────────────────────────────────────
  suites = $state<ValidationSuiteListItem[]>([]);

  // ── Detail state ─────────────────────────────────────────────────
  selectedSuiteId = $state<string | null>(null);
  detail = $state<ValidationSuiteOut | null>(null);
  replays = $state<RunListResponse | null>(null);

  // ── Loading / error transients ───────────────────────────────────
  loading = $state(false);
  error = $state<string | null>(null);

  // ── Alarm block (refreshed on every 30s health tick + SSE) ───────
  regressionAlarmBlock = $state<RegressionAlarmBlock | null>(null);

  // ── Internal poll bookkeeping ────────────────────────────────────
  private _pollHandle: ReturnType<typeof setInterval> | null = null;
  private _taxonomyListener: (() => void) | null = null;

  /**
   * Fetch the list. Pass a `projectId` to scope; otherwise returns the
   * unscoped list. Per ADR-005 the suites surface honours the same project
   * scope as history / topology / templates.
   */
  async load(projectId?: string | null): Promise<void> {
    this.loading = true;
    try {
      const params: { project_id?: string } = {};
      if (projectId) params.project_id = projectId;
      const resp = await getSuites(params);
      this.suites = resp.items;
      this.error = null;
    } catch (err) {
      this.error = err instanceof Error ? err.message : 'Failed to load suites';
    } finally {
      this.loading = false;
    }
  }

  /**
   * Select a suite, fetching detail + replay history. Pass `null` to clear
   * the selection.
   */
  async select(suiteId: string | null): Promise<void> {
    this.selectedSuiteId = suiteId;
    if (!suiteId) {
      this.detail = null;
      this.replays = null;
      return;
    }
    await this.loadDetail(suiteId);
  }

  /** Fetch detail + replay history for a specific suite. */
  async loadDetail(suiteId: string): Promise<void> {
    try {
      const [detail, replays] = await Promise.all([
        getSuite(suiteId),
        getSuiteReplays(suiteId),
      ]);
      this.detail = detail;
      this.replays = replays;
    } catch (err) {
      this.error = err instanceof Error ? err.message : 'Failed to load suite detail';
    }
  }

  /** Pull `/api/health` and cache the `regression_alarm` block. */
  async refreshHealth(): Promise<void> {
    try {
      const health = await getHealth();
      const block = (
        health as unknown as { regression_alarm?: RegressionAlarmBlock }
      ).regression_alarm;
      if (block) {
        this.regressionAlarmBlock = block;
      }
    } catch {
      // Health-poll failures are non-fatal; the last-good alarm block stays
      // pinned. The StatusBar's existing health-poll surface owns global
      // outage messaging.
    }
  }

  /**
   * Start the 30s `/api/health` poll + register the `taxonomy-changed` DOM
   * listener that refetches both the suites list and the alarm block.
   * Idempotent — calling twice is a no-op.
   */
  startPolling(): void {
    if (this._pollHandle !== null) return;

    // Fire the first poll immediately so the initial render has a live
    // alarm block before the 30s tick window.
    void this.refreshHealth();

    this._pollHandle = setInterval(() => {
      void this.refreshHealth();
    }, HEALTH_POLL_INTERVAL_MS);

    if (typeof window !== 'undefined') {
      const handler = () => {
        void this.load();
        void this.refreshHealth();
      };
      this._taxonomyListener = handler;
      window.addEventListener('taxonomy-changed', handler);
    }
  }

  /** Stop the poll + unregister the SSE listener. */
  stopPolling(): void {
    if (this._pollHandle !== null) {
      clearInterval(this._pollHandle);
      this._pollHandle = null;
    }
    if (typeof window !== 'undefined' && this._taxonomyListener) {
      window.removeEventListener('taxonomy-changed', this._taxonomyListener);
      this._taxonomyListener = null;
    }
  }
}

export const suitesStore = new SuitesStore();
