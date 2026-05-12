// frontend/src/lib/stores/probes.test.ts
//
// v0.4.22 T2 Cycle 13 RED — probesStore.runProbe() 202+polling abstraction.
//
// Contract (per spec § 6 NEW + EXTENDED stores + § 8 202+polling
// architecture + § 10 Cycle 13):
//
//   - `runProbe(req)` returns an async iterable. Consumers iterate via
//     `for await (const e of probes_store.runProbe(req))` and CANNOT
//     distinguish between SSE-source and poll-source events — both paths
//     emit the same probe_* event shapes.
//
//   - Threshold: `n_prompts > 10` auto-attaches `Prefer: respond-async`
//     to the POST /api/probes request. `n_prompts <= 10` omits the
//     header and falls through to the default SSE response path (so the
//     typical 5-10 prompt probe behaves identically to v0.4.21).
//
//   - 202 path: server returns `Location` + `Retry-After: 5`. The store
//     polls `GET /api/probes/{id}` every 5s until the run reaches a
//     terminal status ('completed' | 'failed' | 'partial'), translating
//     each poll snapshot into the same probe_* event shapes a SSE
//     consumer would see.
//
//   - Cancellation: an `AbortSignal` parameter cancels the in-flight
//     poll loop. The contract is the browser-tab-close case in § 10
//     "O5 (browser tab closes mid-probe — store cleans up poll
//     interval, no leaked timers)".
//
//   - No client-side timeout: § 10 "O7 (long probe runs >10min — poll
//     cadence persists, no client-side timeout)". Polling cadence must
//     persist for the full worst-case run duration (10+ min) without
//     the store bailing on its own clock.
//
// RED state: `probes.svelte.ts` does NOT YET export `runProbe()`. The
// file may not even exist. Tests fail at suite collection or at the
// first `runProbe()` call site — both are valid RED signatures.
//
// Loading via dynamic import keeps Vite's static analyzer from
// resolving the (potentially missing) module at suite-collection time;
// each `it()` fails individually with a clear "module not found" or
// "runProbe is not a function" message rather than crashing the entire
// suite. Mirrors the pattern in:
//   - frontend/src/lib/stores/suites.test.ts (Cycle 12 RED)
//   - frontend/src/lib/components/probes/TopicProbeProgressView.test.ts
//   - frontend/src/lib/components/probes/TopicProbeForm.test.ts

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

/** Probe request shape the store consumes (matches `ProbeRunRequest`
 *  on the backend). Kept minimal — the test contract is shape-agnostic
 *  about the OPTIONAL fields. */
interface ProbeRunRequest {
  topic: string;
  n_prompts: number;
  intent?: 'explore' | 'audit' | 'refactor' | 'extend';
  grounding_mode?: 'codebase' | 'topic_only';
}

/**
 * Runtime-computed import — keeps Vite's static analyzer from resolving
 * the (potentially-missing) store module at suite-collection time. Each
 * test fails individually with a clear missing-export message rather
 * than crashing the whole file (RED behavior canon).
 */
async function loadStore() {
  const path = ['.', 'probes.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod;
}

/**
 * Helper: drain an async iterable into an array, with an optional cap
 * to short-circuit infinite SSE streams in tests. Returns the full
 * event list emitted up until the iterable closes OR the cap fires.
 */
async function collectEvents(
  iter: AsyncIterable<unknown>,
  options: { cap?: number } = {},
): Promise<unknown[]> {
  const out: unknown[] = [];
  const cap = options.cap ?? Number.POSITIVE_INFINITY;
  for await (const event of iter) {
    out.push(event);
    if (out.length >= cap) break;
  }
  return out;
}

/**
 * Build a fetch mock that:
 *   - On the initial `POST /api/probes`, returns a 202 with a `Location`
 *     header + `Retry-After: 5` + the spec § 8 body shape.
 *   - On subsequent `GET /api/probes/{run_id}` polls, walks a queue of
 *     canned poll snapshots. Each poll consumes one entry; the test
 *     drives the run to terminal by ending the queue with a
 *     `completed`-status snapshot.
 *
 * Returns the spy + the recorded request inits so tests can introspect
 * headers + URLs.
 */
function buildPolling202Fetch(opts: {
  runId: string;
  pollSnapshots: Array<{
    status: 'running' | 'completed' | 'failed' | 'partial';
    body?: Record<string, unknown>;
  }>;
}) {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
  let pollIdx = 0;
  const spy = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push({ url, init });

      // POST /api/probes — initial dispatch returns 202.
      if (url.includes('/api/probes') && !url.match(/\/api\/probes\/[^/]+/)) {
        return new Response(
          JSON.stringify({
            run_id: opts.runId,
            status: 'running',
            poll_url: `/api/probes/${opts.runId}`,
          }),
          {
            status: 202,
            headers: {
              'Content-Type': 'application/json',
              Location: `/api/probes/${opts.runId}`,
              'Retry-After': '5',
            },
          },
        );
      }

      // GET /api/probes/{run_id} — consume next poll snapshot. If we've
      // exhausted the queue, keep returning the last (terminal) snapshot
      // — tolerant of test-side over-polling.
      if (url.includes(`/api/probes/${opts.runId}`)) {
        const snap =
          opts.pollSnapshots[Math.min(pollIdx, opts.pollSnapshots.length - 1)];
        pollIdx += 1;
        return new Response(
          JSON.stringify({
            run_id: opts.runId,
            status: snap.status,
            ...(snap.body ?? {}),
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        );
      }

      return new Response('Not Found', { status: 404 });
    },
  );
  vi.stubGlobal('fetch', spy);
  return { spy, calls };
}

/**
 * Build a fetch mock for the SSE-path (n_prompts <= 10) — returns a
 * streaming Response whose body emits a single terminal `probe_completed`
 * event so the iterator finishes promptly. The test contract is the
 * absence of the `Prefer: respond-async` header on the POST; the SSE
 * payload shape is asserted via the event-shape parity test.
 */
function buildSseFetch(runId: string) {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const sseBody = [
    `data: ${JSON.stringify({ event: 'probe_started', run_id: runId })}`,
    '',
    `data: ${JSON.stringify({ event: 'probe_completed', run_id: runId, status: 'completed' })}`,
    '',
    '',
  ].join('\n');

  const spy = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push({ url, init });
      return new Response(sseBody, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    },
  );
  vi.stubGlobal('fetch', spy);
  return { spy, calls };
}

describe('probesStore.runProbe — 202+polling abstraction', () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // ── Test 1 ────────────────────────────────────────────────────────────
  // The function must return an async iterable. Consumers drive the run
  // via `for await`. This is the foundational shape — every other test
  // depends on it.
  it('test_run_probe_returns_async_iterable', async () => {
    const runId = 'run-async-iter';
    buildPolling202Fetch({
      runId,
      pollSnapshots: [{ status: 'completed', body: { aggregate: {} } }],
    });

    const { probesStore } = await loadStore();
    const req: ProbeRunRequest = {
      topic: 'embedding cache invalidation',
      n_prompts: 12, // > 10 → 202 path
    };

    const iter = probesStore.runProbe(req);

    // Async-iterable protocol: the returned object MUST expose either
    // `Symbol.asyncIterator` (standard async iterable) OR `next()` so
    // it satisfies `for await ... of`. Mirrors the AsyncIterable<T>
    // TypeScript surface and matches the spec § 6 contract: "an async
    // iterable; source-agnostic for components".
    expect(iter).toBeDefined();
    expect(
      typeof (iter as { [Symbol.asyncIterator]?: unknown })[Symbol.asyncIterator],
    ).toBe('function');

    // Drain the iterable to terminal — confirms at least one event is
    // yielded (the contract is "for await yields events", not "yields
    // anything else").
    const events = await collectEvents(iter as AsyncIterable<unknown>, { cap: 50 });
    expect(events.length).toBeGreaterThan(0);
  });

  // ── Test 2 ────────────────────────────────────────────────────────────
  // n_prompts > 10 → auto-attach `Prefer: respond-async` to POST. Spec
  // § 8: "Frontend: probesStore.runProbe() auto-attaches `Prefer:
  // respond-async` when n_prompts > 10".
  it('test_run_probe_n_prompts_gt_10_auto_attaches_prefer_respond_async_header', async () => {
    const runId = 'run-prefer-async';
    const { spy } = buildPolling202Fetch({
      runId,
      pollSnapshots: [{ status: 'completed', body: { aggregate: {} } }],
    });

    const { probesStore } = await loadStore();
    const req: ProbeRunRequest = {
      topic: 'large probe needs async path',
      n_prompts: 15, // strictly > 10
    };

    // Drain the iterable so the initial POST has fired.
    await collectEvents(probesStore.runProbe(req) as AsyncIterable<unknown>, {
      cap: 50,
    });

    // Find the POST call to /api/probes (the dispatch — not a poll).
    const postCall = spy.mock.calls.find(([input, init]) => {
      const url =
        typeof input === 'string' ? input : (input as URL | Request).toString();
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      return method === 'POST' && url.includes('/api/probes');
    });
    expect(postCall).toBeDefined();

    // Headers must include `Prefer: respond-async` (case-insensitive
    // header lookup — fetch normalizes but we don't assume which case).
    const init = postCall![1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.get('Prefer')?.toLowerCase()).toBe('respond-async');
  });

  // ── Test 3 ────────────────────────────────────────────────────────────
  // n_prompts <= 10 → NO Prefer header → falls through to SSE path.
  // Spec § 8: "Below threshold → SSE (no behavioral change for typical
  // 5-10 prompt probes)".
  it('test_run_probe_n_prompts_le_10_falls_through_to_sse_path', async () => {
    const runId = 'run-sse-path';
    const { spy } = buildSseFetch(runId);

    const { probesStore } = await loadStore();
    const req: ProbeRunRequest = {
      topic: 'small probe stays on SSE',
      n_prompts: 8, // <= 10
    };

    await collectEvents(probesStore.runProbe(req) as AsyncIterable<unknown>, {
      cap: 50,
    });

    const postCall = spy.mock.calls.find(([input, init]) => {
      const url =
        typeof input === 'string' ? input : (input as URL | Request).toString();
      const method = (init as RequestInit | undefined)?.method ?? 'GET';
      return method === 'POST' && url.includes('/api/probes');
    });
    expect(postCall).toBeDefined();

    const init = postCall![1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    // The Prefer header MUST NOT be sent below the threshold — sending
    // it silently breaks the SSE contract for short probes.
    expect(headers.get('Prefer')).toBeNull();
  });

  // ── Test 4 ────────────────────────────────────────────────────────────
  // 202 path polls GET /api/probes/{id} every 5s, driven by the
  // server's `Retry-After: 5` header. Asserts via fake-timers cadence.
  it('test_run_probe_202_path_polls_every_5s_via_retry_after', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const runId = 'run-poll-5s';
    const { spy } = buildPolling202Fetch({
      runId,
      // Three running snapshots, then completed — drives ≥4 poll calls.
      pollSnapshots: [
        { status: 'running' },
        { status: 'running' },
        { status: 'running' },
        { status: 'completed', body: { aggregate: {} } },
      ],
    });

    const { probesStore } = await loadStore();
    const req: ProbeRunRequest = {
      topic: '5s cadence verification',
      n_prompts: 12,
    };

    // Kick off iteration in a background task so timer-advancement can
    // drive it. We don't await collectEvents here — we step the timers
    // and inspect call counts at known timestamps.
    const iter = probesStore.runProbe(req) as AsyncIterable<unknown>;
    const drainPromise = collectEvents(iter, { cap: 50 });

    // Let the initial POST settle (microtask drain).
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(0);

    // After dispatch, exactly the POST has fired (1 call).
    const postCalls = () =>
      spy.mock.calls.filter(([_, init]) => {
        const m = (init as RequestInit | undefined)?.method ?? 'GET';
        return m === 'POST';
      }).length;
    const getCalls = () =>
      spy.mock.calls.filter(([_, init]) => {
        const m = (init as RequestInit | undefined)?.method ?? 'GET';
        return m === 'GET';
      }).length;

    expect(postCalls()).toBe(1);

    // Each 5s tick fires one GET poll. The exact cadence is spec § 8
    // ("Polling cost: 1 indexed PK SELECT on run_row per poll ~1-2ms.
    // 5s cadence × ~10min worst-case = 120 polls/probe"). Drive three
    // ticks and verify GET-call count crossed each threshold.
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(0);
    expect(getCalls()).toBeGreaterThanOrEqual(1);

    const after1 = getCalls();
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(0);
    expect(getCalls()).toBeGreaterThan(after1);

    const after2 = getCalls();
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(0);
    expect(getCalls()).toBeGreaterThan(after2);

    // Drive to completion so the drain promise resolves cleanly.
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(0);
    await drainPromise;
  });

  // ── Test 5 ────────────────────────────────────────────────────────────
  // Browser tab close cancels the in-flight poll. Spec § 10 Cycle 13
  // OPERATE O5: "browser tab closes mid-probe — store cleans up poll
  // interval, no leaked timers". Implemented via an AbortSignal — the
  // store accepts an optional `signal` arg and stops polling when it
  // fires. After abort, no further fetches happen even if more time
  // elapses.
  it('test_run_probe_browser_tab_close_cancels_poll_interval', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const runId = 'run-cancel-mid';
    const { spy } = buildPolling202Fetch({
      runId,
      // Indefinite running — would poll forever absent cancellation.
      pollSnapshots: [{ status: 'running' }],
    });

    const { probesStore } = await loadStore();
    const controller = new AbortController();
    const req: ProbeRunRequest = {
      topic: 'cancellation contract',
      n_prompts: 12,
    };

    const iter = probesStore.runProbe(req, {
      signal: controller.signal,
    }) as AsyncIterable<unknown>;
    const drainPromise = collectEvents(iter, { cap: 50 }).catch(() => []);

    // Initial POST + one poll tick.
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(0);

    const callsBeforeAbort = spy.mock.calls.length;
    expect(callsBeforeAbort).toBeGreaterThan(1); // POST + at least 1 GET

    // Browser tab close → abort the signal. The store must:
    //   - stop polling (no further GETs after this point)
    //   - terminate the iterable so any in-flight consumer unblocks
    controller.abort();
    await vi.advanceTimersByTimeAsync(0);

    // Wait the consumer out — drainPromise must resolve (or reject;
    // both terminate the iterator).
    await drainPromise;

    // Advance well past the next two scheduled poll ticks. No new
    // fetches should fire — the interval is cleaned up.
    await vi.advanceTimersByTimeAsync(15_000);
    await vi.advanceTimersByTimeAsync(0);

    expect(spy.mock.calls.length).toBe(callsBeforeAbort);
  });

  // ── Test 6 ────────────────────────────────────────────────────────────
  // SSE-source and poll-source iterables emit IDENTICAL event shapes.
  // Spec § 10 Cycle 13 INTEGRATE A2: "single async-iterable contract —
  // SSE + poll paths emit identical event shapes; consumers must not
  // branch on source". The shape contract: each yielded item must
  // carry `event: string` (probe_*) and `run_id: string`, with the
  // same keys present across both transports for the same logical
  // state.
  it('test_run_probe_sse_and_poll_paths_emit_identical_event_shapes', async () => {
    // -- SSE path (n_prompts <= 10) --
    const sseRunId = 'run-sse-shape';
    buildSseFetch(sseRunId);

    const mod1 = await loadStore();
    const sseEvents = await collectEvents(
      mod1.probesStore.runProbe({
        topic: 'shape parity sse',
        n_prompts: 5,
      }) as AsyncIterable<unknown>,
      { cap: 50 },
    );

    vi.resetModules();
    vi.unstubAllGlobals();

    // -- Poll path (n_prompts > 10) --
    const pollRunId = 'run-poll-shape';
    buildPolling202Fetch({
      runId: pollRunId,
      // Emit one running snapshot then completed — so the poll-path
      // also produces ≥2 events: a started/in-progress + a terminal.
      pollSnapshots: [
        { status: 'running' },
        { status: 'completed', body: { aggregate: {} } },
      ],
    });

    const mod2 = await loadStore();
    const pollEvents = await collectEvents(
      mod2.probesStore.runProbe({
        topic: 'shape parity poll',
        n_prompts: 15,
      }) as AsyncIterable<unknown>,
      { cap: 50 },
    );

    // Both transports must yield ≥1 event apiece. Empty event lists
    // mean the iterable didn't actually emit — that's a contract bug.
    expect(sseEvents.length).toBeGreaterThan(0);
    expect(pollEvents.length).toBeGreaterThan(0);

    // Each event must carry `event` (probe_* type) and `run_id`. The
    // event-type name MUST start with `probe_` per spec § 9
    // ("Total probe_* event count stays at 7"). Consumers branch on
    // `event` — never on transport.
    const inspect = (events: unknown[], label: string) => {
      for (const evt of events) {
        expect(evt, `${label} event must be object`).toBeTypeOf('object');
        const e = evt as Record<string, unknown>;
        expect(typeof e.event, `${label}.event must be string`).toBe('string');
        expect(
          String(e.event).startsWith('probe_'),
          `${label}.event '${e.event}' must start with 'probe_'`,
        ).toBe(true);
        expect(typeof e.run_id, `${label}.run_id must be string`).toBe('string');
      }
    };
    inspect(sseEvents, 'sse');
    inspect(pollEvents, 'poll');

    // Both paths must include the same canonical terminal event type
    // — `probe_completed`. Consumers rely on this to decide when to
    // unmount the progress view. The poll path translates the
    // `status: 'completed'` snapshot into a `probe_completed` event;
    // the SSE path emits it natively.
    const terminalNames = (events: unknown[]) =>
      events
        .map((e) => (e as Record<string, unknown>).event)
        .filter((n): n is string => typeof n === 'string');
    expect(terminalNames(sseEvents)).toContain('probe_completed');
    expect(terminalNames(pollEvents)).toContain('probe_completed');
  });

  // ── Test 7 ────────────────────────────────────────────────────────────
  // No client-side timeout during long probes. Spec § 10 Cycle 13
  // OPERATE O7: "long probe runs >10min — poll cadence persists, no
  // client-side timeout". Drive 10 minutes of fake time with every
  // poll returning `running`; the store MUST keep polling and MUST NOT
  // bail with a timeout. We then end with a `completed` and verify the
  // iterable terminates normally — proves the timeout-free contract.
  it('test_run_probe_never_times_out_client_side_during_long_probes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: false });
    const runId = 'run-long-10min';

    // Build ~125 running snapshots followed by 1 completed. At 5s
    // cadence that covers >10 minutes (125 * 5s = 625s ≈ 10.4 min)
    // before the run terminates.
    const runningSnaps = Array.from({ length: 125 }, () => ({
      status: 'running' as const,
    }));
    const { spy } = buildPolling202Fetch({
      runId,
      pollSnapshots: [
        ...runningSnaps,
        { status: 'completed', body: { aggregate: {} } },
      ],
    });

    const { probesStore } = await loadStore();
    const req: ProbeRunRequest = {
      topic: 'long probe — no client timeout',
      n_prompts: 12,
    };

    const iter = probesStore.runProbe(req) as AsyncIterable<unknown>;
    const drainPromise = collectEvents(iter, { cap: 500 });

    // Step through 10 minutes of polls at 5s cadence (120 ticks). The
    // store must keep firing GET polls — no internal timeout deadline
    // can short-circuit the iterator while the server is still
    // reporting `running`.
    for (let i = 0; i < 120; i++) {
      await vi.advanceTimersByTimeAsync(5000);
      await vi.advanceTimersByTimeAsync(0);
    }

    // Count GETs to confirm polling persisted across the full window.
    const getCalls = spy.mock.calls.filter(([_, init]) => {
      const m = (init as RequestInit | undefined)?.method ?? 'GET';
      return m === 'GET';
    }).length;
    // 10 min / 5s = 120 polls. Allow slack — the contract is "polling
    // persists", not "exactly N polls". Anything significantly less
    // than 120 means the store bailed early (client-side timeout).
    expect(getCalls).toBeGreaterThanOrEqual(100);

    // Drive to terminal so the iterable closes; the test must not
    // hang. If a client-side timeout HAD fired, the iterator would
    // have already closed and the count above would be much smaller.
    await vi.advanceTimersByTimeAsync(5000);
    await vi.advanceTimersByTimeAsync(0);
    await drainPromise;
  });
});
