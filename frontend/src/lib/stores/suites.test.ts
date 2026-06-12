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

  // ── v0.4.37 §4.4 — once-per-selection liveness check ─────────────────

  const detailWithRefs = {
    id: 'suite-1',
    source_run_id: 'run-1',
    label: 'liveness-suite',
    tolerance_abs: 0.5,
    project_id: null,
    repo_full_name: null,
    created_at: '2026-06-12T00:00:00Z',
    retired_at: null,
    retired_reason: null,
    prompts_snapshot: [
      { raw_prompt: 'a', intent_label: null, original_optimization_id: 'opt-1' },
      { raw_prompt: 'b', intent_label: null, original_optimization_id: 'opt-1' }, // dupe → deduped
      { raw_prompt: 'c', intent_label: null, original_optimization_id: 'opt-2' },
      { raw_prompt: 'd', intent_label: null, original_optimization_id: null },    // null → skipped
    ],
    baseline_scores: {
      mean_overall: 7.5, p5_overall: 7.0, p50_overall: 7.5, p95_overall: 8.0,
      per_prompt: [], task_type_distribution: {},
    },
  };

  const emptyReplays = {
    total: 0, count: 0, offset: 0, items: [], has_more: false, next_offset: null,
  };

  it('select fires one deduped liveness check and stores alive ids + trace map', async () => {
    const fetchSpy = mockFetch([
      // Order matters: includes()-matching means the replays handler must
      // precede the bare suite-detail handler.
      { match: '/api/optimizations/exists', response: { alive: ['opt-1'], trace_ids: { 'opt-1': 'tr-1' } } },
      { match: '/api/suites/suite-1/replays', response: emptyReplays },
      { match: '/api/suites/suite-1', response: detailWithRefs },
    ]);

    const { suitesStore } = await loadStore();
    // Route through select() (not loadDetail directly) — loadDetail's
    // stale-selection bail discards responses whose suiteId no longer
    // matches `selectedSuiteId`, and select() is the production caller
    // that sets the selection synchronously first.
    await suitesStore.select('suite-1');

    const existsCalls = fetchSpy.mock.calls.filter(([u]) =>
      String(u).includes('/optimizations/exists'),
    );
    expect(existsCalls).toHaveLength(1);
    expect(JSON.parse((existsCalls[0][1] as RequestInit).body as string)).toEqual({
      ids: ['opt-1', 'opt-2'],
    });
    expect(suitesStore.aliveOriginalIds).toEqual(new Set(['opt-1']));
    expect(suitesStore.originalTraceIds).toEqual({ 'opt-1': 'tr-1' });
  });

  it('liveness failure degrades to null (no tombstones, no history links)', async () => {
    mockFetch([
      { match: '/api/optimizations/exists', status: 500, response: { detail: 'boom' } },
      { match: '/api/suites/suite-1/replays', response: emptyReplays },
      { match: '/api/suites/suite-1', response: detailWithRefs },
    ]);

    const { suitesStore } = await loadStore();
    await suitesStore.select('suite-1');

    expect(suitesStore.aliveOriginalIds).toBeNull();
    expect(suitesStore.originalTraceIds).toEqual({});
    // The detail itself still loaded — liveness is best-effort.
    expect(suitesStore.detail?.id).toBe('suite-1');
  });

  // ── v0.4.37 C2 gate fixes — stale-switch + >100-id regressions ───────

  it('stale suite switch: A\'s late liveness response never leaks into B', async () => {
    const detailA = {
      ...detailWithRefs,
      id: 'suite-a',
      prompts_snapshot: [
        { raw_prompt: 'a', intent_label: null, original_optimization_id: 'opt-a1' },
      ],
    };
    const detailB = {
      ...detailWithRefs,
      id: 'suite-b',
      prompts_snapshot: [
        { raw_prompt: 'b', intent_label: null, original_optimization_id: 'opt-b1' },
      ],
    };

    // Controllable gate parking suite A's /optimizations/exists response
    // until released — models a slow liveness round-trip that resolves
    // AFTER the operator has already switched to suite B.
    let releaseA!: () => void;
    const aGate = new Promise<void>((resolve) => {
      releaseA = resolve;
    });

    // Static routes go through the canonical mockFetch helper; the wrapper
    // intercepts only the exists POST so A's response can be parked.
    const inner = mockFetch([
      { match: '/api/suites/suite-a/replays', response: emptyReplays },
      { match: '/api/suites/suite-a', response: detailA },
      { match: '/api/suites/suite-b/replays', response: emptyReplays },
      { match: '/api/suites/suite-b', response: detailB },
    ]);
    const json = (body: unknown) =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    const fetchSpy = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/optimizations/exists')) {
        const ids: string[] = JSON.parse(String(init?.body ?? '{}')).ids ?? [];
        if (ids.includes('opt-a1')) {
          await aGate; // park A's liveness round-trip until released
          return json({ alive: ['opt-a1'], trace_ids: { 'opt-a1': 'tr-a1' } });
        }
        return json({ alive: ['opt-b1'], trace_ids: { 'opt-b1': 'tr-b1' } });
      }
      return inner(input, init);
    });
    vi.stubGlobal('fetch', fetchSpy);

    const { suitesStore } = await loadStore();

    const selectA = suitesStore.select('suite-a');
    // Wait until A's exists call is in flight (parked on the gate).
    await vi.waitFor(() => {
      expect(
        fetchSpy.mock.calls.some(([u]) => String(u).includes('/optimizations/exists')),
      ).toBe(true);
    });

    // Pre-seed a sentinel so the reset-at-entry is observable (not just
    // the store's initial null).
    suitesStore.aliveOriginalIds = new Set(['sentinel']);
    suitesStore.originalTraceIds = { sentinel: 'tr-x' };

    // Switch to B while A's liveness response is still pending. The
    // unknown-state contract must hold immediately: loadDetail resets the
    // liveness slices synchronously at entry, before its first await.
    const selectB = suitesStore.select('suite-b');
    expect(suitesStore.aliveOriginalIds).toBeNull();
    expect(suitesStore.originalTraceIds).toEqual({});

    await selectB;
    expect(suitesStore.detail?.id).toBe('suite-b');
    expect(suitesStore.aliveOriginalIds).toEqual(new Set(['opt-b1']));

    // Release A's parked response — the stale-selection bail must discard
    // it rather than let it clobber B's liveness state.
    releaseA();
    await selectA;
    expect(suitesStore.selectedSuiteId).toBe('suite-b');
    expect(suitesStore.aliveOriginalIds).toEqual(new Set(['opt-b1']));
    expect(suitesStore.originalTraceIds).toEqual({ 'opt-b1': 'tr-b1' });
  });

  it('over-100 distinct ids degrades liveness to unknown without an exists call', async () => {
    const manyIds = Array.from({ length: 101 }, (_, i) => `opt-${i}`);
    const bigDetail = {
      ...detailWithRefs,
      prompts_snapshot: manyIds.map((id) => ({
        raw_prompt: 'p',
        intent_label: null,
        original_optimization_id: id,
      })),
    };
    const fetchSpy = mockFetch([
      // Would answer an exists call if one were (wrongly) made — the
      // assertion below pins that it never fires above the boundary.
      { match: '/api/optimizations/exists', response: { alive: manyIds, trace_ids: {} } },
      { match: '/api/suites/suite-1/replays', response: emptyReplays },
      { match: '/api/suites/suite-1', response: bigDetail },
    ]);

    const { suitesStore } = await loadStore();
    await suitesStore.select('suite-1');

    const existsCalls = fetchSpy.mock.calls.filter(([u]) =>
      String(u).includes('/optimizations/exists'),
    );
    expect(existsCalls).toHaveLength(0);
    // Whole-suite degrade to unknown: no tombstones, no history links —
    // never a sliced subset that lies about the rest.
    expect(suitesStore.aliveOriginalIds).toBeNull();
    expect(suitesStore.originalTraceIds).toEqual({});
    // The detail itself still loaded.
    expect(suitesStore.detail?.id).toBe('suite-1');
  });
});
