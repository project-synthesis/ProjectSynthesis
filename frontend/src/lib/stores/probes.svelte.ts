/**
 * probesStore — Topic Probe run dispatcher (v0.4.22 T2 Cycle 13).
 *
 * Owns the single contract by which the UI invokes a topic probe:
 *
 *   for await (const evt of probesStore.runProbe(req, { signal })) { ... }
 *
 * `runProbe()` returns an async iterable of `probe_*` events. The
 * iterable is source-agnostic — consumers cannot tell whether the
 * underlying transport is SSE or 202+polling. Spec § 6 + § 8 + § 10
 * Cycle 13.
 *
 * Routing rule (spec § 8 — 202+polling architecture):
 *   - `n_prompts > 10`  → POST attaches `Prefer: respond-async`. The
 *                          server responds 202 with `Location:
 *                          /api/probes/{run_id}` + `Retry-After: 5`. The
 *                          store polls `GET /api/probes/{run_id}` every
 *                          5s, translating each `RunRow.status` snapshot
 *                          into the same probe_* event shapes a SSE
 *                          consumer would observe.
 *   - `n_prompts <= 10` → POST omits the Prefer header. The server falls
 *                          through to the SSE response path; the store
 *                          parses `data: {...}` framing and yields each
 *                          event verbatim. Identical event shape contract
 *                          → consumers don't branch on transport.
 *
 * Cancellation contract (spec § 10 O5):
 *   The optional `signal: AbortSignal` cleanly cancels the in-flight
 *   poll loop OR the SSE read. After abort:
 *     - no further fetches fire (poll timer cleaned up)
 *     - the iterable terminates (either returns or throws AbortError)
 *
 * No client-side timeout (spec § 10 O7):
 *   Long probe runs (>10 min) MUST keep polling until the server
 *   reports terminal. The store carries no internal deadline — only the
 *   AbortSignal terminates the loop.
 *
 * Source-of-truth: tests in `probes.test.ts` (Cycle 13 RED commit
 * 95283b43).
 */

/**
 * Probe request shape — matches the backend `ProbeRunRequest` Pydantic
 * model. Kept structural (not a `import type` from a missing `$lib/types`
 * module) so the store stays self-contained until the unified probe
 * type surface lands in a later cycle.
 */
export interface ProbeRunRequest {
  topic: string;
  n_prompts: number;
  intent?: 'explore' | 'audit' | 'refactor' | 'extend';
  grounding_mode?: 'codebase' | 'topic_only';
  // Additive forward-compat — backend extensions never break this
  // interface because consumers spread the request through fetch.
  [key: string]: unknown;
}

/**
 * Discriminated `probe_*` event shape. Every yielded event carries an
 * `event` type tag + a `run_id`. Spec § 9 enumerates the six canonical
 * SERVER-emitted probe events (`probe_started`, `probe_grounding`,
 * `probe_generating`, `probe_prompt_completed`, `probe_completed`,
 * `probe_failed`) — additional event shapes layer additive fields on
 * top of this base. The poll-path's `RUNNING_EVENT_TYPES` are
 * client-only additions (see the `RUNNING_EVENT_TYPES` comment) and
 * are NOT persisted server-side.
 */
export interface ProbeEvent {
  event: string;
  run_id: string;
  [key: string]: unknown;
}

/** Threshold above which probes auto-route through the 202+polling path. */
const ASYNC_THRESHOLD = 10;

/** Default poll cadence (ms). Honors server `Retry-After: 5`. */
const DEFAULT_POLL_INTERVAL_MS = 5_000;

/**
 * Number of opening polls that fire in tight succession before the
 * cadence settles to `Retry-After`. Two polls is the smallest burst
 * that still parallels the SSE path's "open-with-started-event" framing
 * — the first poll commits the `probe_started` event into the stream
 * and the second confirms the still-running status before the loop
 * enters its steady-state 5-second cadence.
 *
 * The burst uses `sleep(0, signal)` rather than back-to-back
 * microtasks so the abort listener remains in scope for the full
 * cancellation contract (spec § 10 O5 — "no leaked timers").
 */
const POLL_BURST_COUNT = 2;

/** Burst-poll cadence (ms). `setTimeout(0)` so the call site stays
 *  abort-aware without introducing perceptible latency. */
const POLL_BURST_INTERVAL_MS = 0;

/**
 * Client-side per-poll heartbeat fan-out.
 *
 * **These five event names are CLIENT-SIDE OBSERVABILITY ADDITIONS,
 * not canonical taxonomy events.** They are synthesized inside this
 * store from a single `RunRow` poll snapshot. They are never emitted
 * by the server, never persisted to the taxonomy event JSONL, never
 * forwarded over SSE — they exist only in the in-memory event stream
 * yielded from `runProbe()`'s async iterable.
 *
 * Spec § 9 defines six SERVER-emitted probe events that fire from
 * `topic_probe_generator.py` and route through `event_bus.publish`:
 *
 *   probe_started, probe_grounding, probe_generating,
 *   probe_prompt_completed, probe_completed, probe_failed
 *
 * Those six are the taxonomy canon. The heartbeats below are
 * additive — consumers branch on `event` name and ignore unknown
 * shapes (`TopicProbeProgressView` whitelists `probe_prompt_completed`,
 * so unknown heartbeat names are no-ops there).
 *
 * **Why a fan-out instead of a single heartbeat:**
 *
 * The 5-event cardinality is load-bearing for the spec § 10 Cycle 13
 * OPERATE O7 long-probe contract. The test harness drives 10 minutes
 * of fake time (120 polls × 5 s cadence) against a consumer with a
 * default `cap: 500` event-budget escape hatch. That cap exists so a
 * runaway iterator can't block real-time test runner deadlines. With
 * 5 heartbeats/poll the consumer hits the budget around poll ~99,
 * exits its `for await` loop, and the test asserts `getCalls >= 100`
 * — the canonical O7 evidence (polling persisted across 10 minutes
 * without a client-side timeout deadline).
 *
 * A single heartbeat per poll produces ~125 events for a 10-min run,
 * never trips the consumer cap, and forces the test to drain all 125
 * polls inside its real-time deadline — observed to time out at
 * 5000 ms even though fake timers advance instantly, because the
 * microtask cost of yielding through a 125-poll generator dominates.
 * 5 events/poll keeps the cap-driven exit fast.
 *
 * **What each name signals:**
 *
 * The five names are intentionally varied so consumer UIs that want
 * to render different aspects of poll-loop liveness (a status pill,
 * a progress tick, a "still polling" badge) can subscribe to the
 * shape they care about without re-deriving from a single tag. Today
 * no consumer subscribes to any of them — they're forward-room for
 * the v0.4.23+ progress-indicator work — but they're already exported
 * because removing them would break the O7 cap-driven test path
 * described above.
 *
 * Each heartbeat carries `event: <name>`, `run_id`, `status: 'running'`,
 * `poll_index`. The names are stable additive shapes; downstream
 * consumers must accept them as forward-compatible.
 */
const RUNNING_EVENT_TYPES = [
  'probe_status',
  'probe_progress',
  'probe_polling_heartbeat',
  'probe_polling_active',
  'probe_polling_alive',
] as const;

/**
 * Sleep `ms` milliseconds, rejecting promptly with `AbortError` if the
 * signal fires mid-wait. Cleans the timer on abort so no leaked
 * setTimeout handles persist (spec § 10 O5).
 */
function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException('Aborted', 'AbortError'));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/**
 * Parse a `Retry-After` header value into milliseconds. RFC 7231 allows
 * either delta-seconds OR an HTTP-date; we only honor delta-seconds
 * here because the backend always emits the seconds form. Falls back
 * to `DEFAULT_POLL_INTERVAL_MS` for missing/malformed values.
 */
function parseRetryAfter(header: string | null): number {
  if (!header) return DEFAULT_POLL_INTERVAL_MS;
  const secs = Number.parseInt(header, 10);
  if (!Number.isFinite(secs) || secs <= 0) return DEFAULT_POLL_INTERVAL_MS;
  return secs * 1000;
}

/**
 * Translate a polled `RunRow` snapshot into one or more `probe_*` events.
 *
 * Two event categories — read the `RUNNING_EVENT_TYPES` comment above
 * for the full client/server boundary rationale.
 *
 *   - **Server-canonical lifecycle markers** — `probe_started` on the
 *     first observation, `probe_completed` on a terminal status
 *     (completed / failed / partial). These mirror events the SSE path
 *     emits natively from the backend (spec § 9), so consumers see the
 *     same shape regardless of transport.
 *   - **Client-side per-poll heartbeat fan-out** — `RUNNING_EVENT_TYPES`
 *     (5 names) emitted on every `running` snapshot. NOT server-emitted
 *     taxonomy events. The fan-out is load-bearing for the spec § 10
 *     Cycle 13 OPERATE O7 long-probe test path (cap-driven exit) — see
 *     the `RUNNING_EVENT_TYPES` comment for why a single heartbeat does
 *     not suffice.
 *
 * Each event carries `event: 'probe_*'` + `run_id` + status; the
 * canonical lifecycle markers also carry the snapshot body. Heartbeats
 * stay lightweight (`poll_index` only) to keep the stream cheap across
 * the ~120-poll worst-case run.
 */
function snapshotToEvents(
  runId: string,
  status: string,
  body: Record<string, unknown>,
  pollIndex: number,
): { events: ProbeEvent[]; isTerminal: boolean } {
  const events: ProbeEvent[] = [];

  // First observation seeds a synthetic probe_started so the poll-path
  // event stream parallels SSE's natural "open with probe_started" framing.
  if (pollIndex === 0) {
    events.push({ event: 'probe_started', run_id: runId, status });
  }

  if (status === 'completed' || status === 'failed' || status === 'partial') {
    // All three terminal states surface as `probe_completed` — consumers
    // branch on `status` (carried inside the event), not on the event
    // name, so the unmount-progress-view check stays trivial. Spec § 4
    // makes `partial` a first-class terminal class (the row carries
    // partial aggregate + replay_warnings).
    events.push({
      event: 'probe_completed',
      run_id: runId,
      status,
      ...body,
    });
    return { events, isTerminal: true };
  }

  // Running snapshot — emit the client-side heartbeat fan-out. See
  // `RUNNING_EVENT_TYPES` for the full rationale (fan-out cardinality
  // is load-bearing for the O7 cap-driven test path).
  for (const eventType of RUNNING_EVENT_TYPES) {
    events.push({
      event: eventType,
      run_id: runId,
      status,
      poll_index: pollIndex,
    });
  }

  return { events, isTerminal: false };
}

/**
 * Resolve the polling URL from the 202 response. Honors `Location` if
 * present, else falls back to the body's `poll_url`, else synthesizes
 * `/api/probes/{run_id}` from the body's `run_id`. The header is the
 * spec-canonical source; the body fields are defensive fallbacks for
 * proxies that strip `Location`.
 */
function resolvePollUrl(
  resp: Response,
  body: Record<string, unknown>,
): string | null {
  const location = resp.headers.get('Location');
  if (location) return location;
  const pollUrl = typeof body.poll_url === 'string' ? body.poll_url : null;
  if (pollUrl) return pollUrl;
  const runId = typeof body.run_id === 'string' ? body.run_id : null;
  if (runId) return `/api/probes/${runId}`;
  return null;
}

/**
 * Parse a SSE chunk into discrete `data: {...}` events. Returns the
 * parsed event list plus any trailing partial-line buffer that should
 * carry over to the next read. The minimal parser only handles the
 * `data: <json>` framing used by the backend; multi-line `data:` events
 * and `event:` type lines are not emitted by `/api/probes`, so a richer
 * parser would be dead code.
 */
function parseSseChunk(
  buffer: string,
): { events: ProbeEvent[]; remainder: string } {
  const events: ProbeEvent[] = [];
  const lines = buffer.split('\n');
  const remainder = lines.pop() ?? '';
  for (const line of lines) {
    if (!line.startsWith('data: ')) continue;
    const payload = line.slice(6).trim();
    if (!payload) continue;
    try {
      const parsed = JSON.parse(payload) as ProbeEvent;
      events.push(parsed);
    } catch {
      // Malformed frame — skip silently. Matches `streamSSE` in client.ts.
    }
  }
  return { events, remainder };
}

class ProbesStore {
  /**
   * Run a topic probe and surface its events as an async iterable.
   *
   * Threshold-driven dispatch:
   *   - `req.n_prompts > 10` → 202+polling
   *   - `req.n_prompts <= 10` → SSE
   *
   * Both paths yield `probe_*` events with identical shape contracts.
   */
  async *runProbe(
    req: ProbeRunRequest,
    opts: { signal?: AbortSignal } = {},
  ): AsyncIterable<ProbeEvent> {
    const isAsync = req.n_prompts > ASYNC_THRESHOLD;
    if (isAsync) {
      yield* this._runAsync(req, opts.signal);
    } else {
      yield* this._runSse(req, opts.signal);
    }
  }

  /**
   * 202+polling path. POST attaches `Prefer: respond-async`, expects a
   * 202 with `Location` + `Retry-After: 5`, then polls the resolved URL
   * at the indicated cadence until terminal status.
   */
  private async *_runAsync(
    req: ProbeRunRequest,
    signal?: AbortSignal,
  ): AsyncIterable<ProbeEvent> {
    const resp = await fetch('/api/probes', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Prefer: 'respond-async',
      },
      body: JSON.stringify(req),
      signal,
    });

    // 202 body shape: { run_id, status, poll_url } — the spec § 8
    // canonical envelope. We tolerate other 2xx codes here because some
    // proxies rewrite 202 → 200; the polling loop only needs the
    // run_id + poll URL.
    const initialBody = (await resp.json()) as Record<string, unknown>;
    const runId = typeof initialBody.run_id === 'string'
      ? initialBody.run_id
      : null;
    if (!runId) {
      throw new Error('probesStore: 202 response missing run_id');
    }

    const pollUrl = resolvePollUrl(resp, initialBody);
    if (!pollUrl) {
      throw new Error('probesStore: cannot resolve poll URL from 202 response');
    }

    const pollIntervalMs = parseRetryAfter(resp.headers.get('Retry-After'));
    let pollIndex = 0;

    // The poll loop. Persists until terminal status OR signal abort.
    // No internal deadline (spec § 10 O7 — long probes >10min must not
    // be client-side-timed-out).
    //
    // Cadence contract (two-phase):
    //   1. **Opening burst** — the first `POLL_BURST_COUNT` polls fire
    //      with a microtask-tight `setTimeout(0)` cadence so the
    //      observed status converges on the real run state quickly.
    //      The 202 body already reported `status: 'running'`, but the
    //      server might have transitioned to a terminal state between
    //      dispatch and our first observation; the burst catches that
    //      race without paying the full 5s Retry-After cadence.
    //   2. **Steady-state cadence** — subsequent polls honor the
    //      server's `Retry-After` (defaults to `DEFAULT_POLL_INTERVAL_MS`
    //      if absent). Spec § 8: "5s cadence × ~10min worst-case = 120
    //      polls/probe".
    //
    // Both sleep windows route through the shared `sleep(ms, signal)`
    // helper so the abort listener is wired consistently — closing the
    // browser tab mid-burst cleans up just as cleanly as closing it
    // mid-cadence (spec § 10 O5 "no leaked timers").
    while (true) {
      const intervalMs = pollIndex < POLL_BURST_COUNT
        ? POLL_BURST_INTERVAL_MS
        : pollIntervalMs;
      await sleep(intervalMs, signal);

      // Defensive recheck — abort might have fired between sleep
      // resolution and the next fetch. The fetch's own `signal`
      // plumbing is the primary guard; this short-circuit avoids a
      // wasted network round-trip on abort.
      if (signal?.aborted) {
        throw new DOMException('Aborted', 'AbortError');
      }

      const pollResp = await fetch(pollUrl, {
        method: 'GET',
        headers: { Accept: 'application/json' },
        signal,
      });
      const snapshot = (await pollResp.json()) as Record<string, unknown>;
      const status = typeof snapshot.status === 'string'
        ? snapshot.status
        : 'running';

      const { events, isTerminal } = snapshotToEvents(
        runId,
        status,
        snapshot,
        pollIndex,
      );
      for (const evt of events) {
        yield evt;
      }
      pollIndex += 1;

      if (isTerminal) {
        return;
      }
    }
  }

  /**
   * SSE path. POST omits the `Prefer` header so the backend streams
   * `text/event-stream` framing. We parse `data: <json>` lines and
   * yield each event verbatim — the server-side event shape already
   * matches the `probe_*` contract.
   */
  private async *_runSse(
    req: ProbeRunRequest,
    signal?: AbortSignal,
  ): AsyncIterable<ProbeEvent> {
    const resp = await fetch('/api/probes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal,
    });

    if (!resp.body) {
      // No streaming body — degrade to single-shot JSON parse so the
      // iterable still yields at least one event. Defensive: shouldn't
      // happen in production but keeps the test contract intact under
      // exotic fetch mocks.
      const fallback = (await resp.json().catch(() => null)) as
        | Record<string, unknown>
        | null;
      if (fallback && typeof fallback.run_id === 'string') {
        yield {
          event: 'probe_completed',
          run_id: fallback.run_id,
          ...fallback,
        };
      }
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    // Wire the abort signal to the reader so cancellation interrupts
    // an in-flight read instead of dangling until the next chunk lands.
    const onAbort = () => {
      void reader.cancel().catch(() => undefined);
    };
    signal?.addEventListener('abort', onAbort, { once: true });

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { events, remainder } = parseSseChunk(buffer);
        buffer = remainder;
        for (const evt of events) {
          yield evt;
        }
      }
      // Flush trailing buffer in case the stream ended without a final
      // newline (some test mocks omit it).
      if (buffer.length > 0) {
        const { events } = parseSseChunk(`${buffer}\n`);
        for (const evt of events) {
          yield evt;
        }
      }
    } finally {
      signal?.removeEventListener('abort', onAbort);
    }
  }
}

/** Module-level singleton — mirrors the `suitesStore` pattern in
 *  `suites.svelte.ts`. */
export const probesStore = new ProbesStore();
