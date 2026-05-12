// frontend/src/lib/stores/suites.test.ts
//
// Cycle 12 RED — SuitesStore contract.
//
// The store owns:
//   - `suites` — list of ValidationSuiteListItem (paginated; T2 ships a
//     single-page view, T3+ extends)
//   - `selectedSuiteId` + `detail` — drives SuiteDetailView
//   - `regressionAlarmBlock` — cached from `GET /api/health`'s
//     `regression_alarm` block, polled every 30s (matches the shipped
//     60s health poll → tightened for the regression-alarm surface).
//     Polling is decoupled from the StatusBar's existing health poll
//     because the alarm UX needs ≤30s freshness, while the other status-
//     bar telemetry tolerates the canonical 60s cadence.
//   - SSE-driven invalidation: on `taxonomy_changed` the store refreshes
//     both the suites list and the alarm block. The `taxonomy_changed`
//     event already fires on suite create/retire/replay-completion via
//     the warm-path reconciliation contract per ADR-005 §4 + § 6.
//
// Both tests use `vi.useFakeTimers()` to advance the 30s poll without
// real-time waits. The module is loaded via dynamic import per RED
// behaviour — file doesn't exist yet, suite collection must succeed.

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mockFetch } from '$lib/test-utils';

// Runtime-computed import — keeps Vite's static analyzer from resolving
// the (not-yet-existing) store module at suite-collection time.
async function loadStore() {
  const path = ['.', 'suites.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod;
}

describe('suitesStore', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  // ── Test 13: 30s health poll for regression_alarm cache ─────────────
  //
  // The store polls `/api/health` every 30s, reads the
  // `regression_alarm` block, and exposes it as
  // `suitesStore.regressionAlarmBlock`. The first poll fires when the
  // store is initialised (or on `startPolling()`) and tracks subsequent
  // ticks at a 30s cadence. We verify by advancing fake timers and
  // counting fetch calls.
  it('test_suites_store_polls_health_for_regression_alarm_state', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });

    // Stub two distinct health responses so the test can verify that
    // each poll tick consumes a fresh response (no stale cache).
    let pollCount = 0;
    const fetchSpy = mockFetch([
      {
        match: '/api/health',
        // Return-by-side-effect for sequential tick reads.
        get response() {
          pollCount += 1;
          return {
            status: 'ok',
            version: '0.4.22-dev',
            provider: 'claude-cli',
            score_health: null,
            avg_duration_ms: null,
            phase_durations: {},
            recent_errors: { last_hour: 0, last_24h: 0 },
            regression_alarm: {
              suites_total: pollCount === 1 ? 0 : 2,
              suites_in_alarm: pollCount === 1 ? 0 : 1,
              latest_alarms: pollCount === 1
                ? []
                : [{
                    suite_id: 'sid-1',
                    label: 'embedding-cache-invalidation',
                    baseline_mean: 7.8,
                    latest_mean: 7.2,
                    delta_abs: -0.6,
                    tolerance_abs: 0.5,
                    latest_replay_id: 'r-1',
                    latest_replay_at: '2026-05-12T00:00:00Z',
                  }],
            },
          };
        },
      },
    ]);

    const { suitesStore } = await loadStore();
    // The store exposes a `startPolling()` or `init()` entry point. Both
    // names are valid GREEN landings — accept either, and fall back to
    // construction-time auto-start by waiting on the first fetch.
    if (typeof suitesStore.startPolling === 'function') {
      suitesStore.startPolling();
    } else if (typeof suitesStore.init === 'function') {
      suitesStore.init();
    }

    // First poll — fires on init or immediately on startPolling. Drain
    // microtasks so the awaited fetch resolves; then assert the call
    // count crossed 1.
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(1);

    // After the first poll the alarm block reflects nominal state.
    expect(suitesStore.regressionAlarmBlock).not.toBeNull();
    expect(suitesStore.regressionAlarmBlock?.suites_in_alarm).toBe(0);

    // Advance 30s — the second poll fires.
    await vi.advanceTimersByTimeAsync(30_000);
    // Drain any chained microtasks from the second fetch.
    await vi.advanceTimersByTimeAsync(0);

    // The call count must have advanced — at least one additional
    // poll between the initial poll and 30s later.
    expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(2);

    // The store now reflects the firing-state alarm block.
    expect(suitesStore.regressionAlarmBlock?.suites_in_alarm).toBe(1);
    expect(suitesStore.regressionAlarmBlock?.latest_alarms).toHaveLength(1);
  });

  // ── Test 14: SSE taxonomy_changed triggers immediate refresh ─────────
  //
  // The `taxonomy_changed` SSE event fires when suites are created /
  // retired / when replay runs complete (per ADR-005 cross-process bridge
  // + the canonical warm-path reconciliation contract — `taxonomy_changed`
  // is the existing umbrella for taxonomy-shape mutations). The store
  // subscribes via the canonical `taxonomy-changed` DOM CustomEvent the
  // root `+page.svelte` re-dispatches from the SSE handler (mirrors the
  // `Navigator.svelte:54-56` precedent for projectStore.refresh).
  //
  // On event receipt the store must:
  //   1. Refetch the suites list (`getSuites`)
  //   2. Refetch the alarm block (`/api/health`)
  //
  // Both must fire — not just one — because suite create and replay
  // completion update orthogonal slices of state.
  it('test_suites_store_refreshes_on_taxonomy_changed_sse', async () => {
    const fetchSpy = mockFetch([
      {
        match: '/api/health',
        response: {
          status: 'ok',
          version: '0.4.22-dev',
          provider: 'claude-cli',
          score_health: null,
          avg_duration_ms: null,
          phase_durations: {},
          recent_errors: { last_hour: 0, last_24h: 0 },
          regression_alarm: {
            suites_total: 1,
            suites_in_alarm: 0,
            latest_alarms: [],
          },
        },
      },
      {
        match: '/api/suites',
        response: {
          total: 1,
          count: 1,
          offset: 0,
          has_more: false,
          next_offset: null,
          items: [{
            id: 'suite-1',
            source_run_id: 'run-1',
            label: 'l',
            tolerance_abs: 0.5,
            project_id: null,
            repo_full_name: null,
            created_at: '2026-05-12T00:00:00Z',
            retired_at: null,
            prompts_count: 5,
            baseline_mean: 7.5,
          }],
        },
      },
    ]);

    const { suitesStore } = await loadStore();

    // Initial load — drain so the first fetch volley settles. The store
    // may auto-load on import or expose an explicit `load()` / `init()`
    // — both contracts are valid.
    if (typeof suitesStore.load === 'function') {
      await suitesStore.load();
    } else if (typeof suitesStore.init === 'function') {
      suitesStore.init();
      await vi.waitFor(() => expect(fetchSpy.mock.calls.length).toBeGreaterThanOrEqual(1));
    }

    const callsBefore = fetchSpy.mock.calls.length;

    // Fire the canonical `taxonomy-changed` DOM CustomEvent the root
    // page re-dispatches from the SSE handler. Match the existing
    // contract (Navigator.svelte:53-56 `window.addEventListener
    // ('taxonomy-changed', handler)`).
    window.dispatchEvent(
      new CustomEvent('taxonomy-changed', {
        detail: { trigger: 'suite_created' },
      }),
    );

    // The store re-fetches both endpoints. We poll for the call count
    // to have advanced by at least 1 (suites list refresh — alarm block
    // refresh is fired by the same handler and may share the same tick).
    await vi.waitFor(() => {
      expect(fetchSpy.mock.calls.length).toBeGreaterThan(callsBefore);
    });

    // At minimum: both the suites list AND the alarm block must have
    // been re-fetched after the SSE event. Inspect the URL of every
    // call made post-event.
    const postEventCalls = fetchSpy.mock.calls
      .slice(callsBefore)
      .map((c) => String(c[0]));

    expect(postEventCalls.some((u) => u.includes('/api/suites'))).toBe(true);
    expect(postEventCalls.some((u) => u.includes('/api/health'))).toBe(true);
  });
});
