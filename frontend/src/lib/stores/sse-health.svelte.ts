/**
 * SSE connection health store — tracks latency, detects degradation,
 * and manages reconnection with exponential backoff.
 *
 * Owns the EventSource lifecycle. Consumers call `connect()` with an
 * event handler and `disconnect()` on cleanup.
 */

import { connectEventStream } from '$lib/api/client';
import type { EventHandler, EventMeta } from '$lib/api/client';

// ---------------------------------------------------------------------------
// Configuration — importable for tests
// ---------------------------------------------------------------------------

/** Rolling latency window size. */
export const WINDOW_SIZE = 100;
/** p95 below this → healthy. */
export const HEALTHY_THRESHOLD_MS = 2_000;
/** p95 below this → degraded; above → disconnected. */
export const DEGRADED_THRESHOLD_MS = 5_000;
/** No event for this long → disconnected (2x server keepalive). */
export const STALENESS_MS = 90_000;
/** Maximum reconnection attempts before giving up. */
export const MAX_RETRIES = 10;
/** Initial backoff delay. */
export const BASE_DELAY_MS = 1_000;
/** Maximum backoff delay (cap). */
export const MAX_DELAY_MS = 16_000;
/** Jitter factor: delay varies by +/- this fraction. */
export const JITTER_FACTOR = 0.2;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ConnectionState = 'healthy' | 'degraded' | 'disconnected';

type ReconnectCallback = () => void;

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

class SSEHealthStore {
    // --- Reactive state ---
    connectionState = $state<ConnectionState>('disconnected');
    lastEventAt = $state<number | null>(null);
    retryCount = $state(0);
    retryAt = $state<number | null>(null);
    retryCapped = $state(false);

    // Latency window — $state array so Svelte 5 tracks mutations for $derived.
    _latencies = $state<number[]>([]);

    // Clock for countdown tooltip (ticks every 1s while retrying).
    _now = $state(Date.now());

    // --- Derived percentiles ---
    p50 = $derived.by(() => this._percentile(0.50));
    p95 = $derived.by(() => this._percentile(0.95));
    p99 = $derived.by(() => this._percentile(0.99));

    /** Dynamic tooltip text reflecting current health state. */
    tooltipText = $derived.by(() => {
        if (this.connectionState === 'healthy') {
            if (this._latencies.length === 0) return 'SSE stream \u2014 awaiting events';
            return [
                'SSE stream healthy',
                `p50: ${this.p50}ms \u00B7 p95: ${this.p95}ms`,
                `${this._latencies.length} samples`,
            ].join('\n');
        }
        if (this.connectionState === 'degraded') {
            return [
                'SSE stream degraded',
                `p95: ${this.p95}ms \u00B7 p99: ${this.p99}ms`,
                `${this._latencies.length} samples`,
            ].join('\n');
        }
        // disconnected
        if (this.retryCapped) {
            return 'SSE disconnected\nRetries exhausted';
        }
        if (this.retryAt != null) {
            const remaining = Math.max(0, Math.ceil((this.retryAt - this._now) / 1000));
            return `SSE disconnected\nRetry ${this.retryCount}/${MAX_RETRIES} in ${remaining}s`;
        }
        return 'SSE disconnected';
    });

    // --- Internal (non-reactive) ---
    private _lastSeq = 0;
    private _eventSource: EventSource | null = null;
    private _onEvent: EventHandler | null = null;
    private _onReconnect: ReconnectCallback | null = null;
    private _stalenessTimer: ReturnType<typeof setTimeout> | null = null;
    private _retryTimer: ReturnType<typeof setTimeout> | null = null;
    private _countdownTimer: ReturnType<typeof setInterval> | null = null;
    private _hadError = false;

    // ------------------------------------------------------------------
    // Public API
    // ------------------------------------------------------------------

    /**
     * Create an EventSource and start tracking health.
     *
     * @param onEvent — forwarded for every typed SSE event (including sync).
     * @param onReconnect — called on successful reconnection after an error
     *   (replaces the sseHadError reconciliation pattern in +page.svelte).
     */
    connect(onEvent: EventHandler, onReconnect?: ReconnectCallback): void {
        // Guard against double-connect (e.g. $effect re-run).
        this._closeEventSource();
        this._clearTimers();

        this._onEvent = onEvent;
        this._onReconnect = onReconnect ?? null;
        this._createEventSource();
    }

    /**
     * Record a real event for latency tracking.
     * Called internally; also exposed for testing.
     */
    recordEvent(seq: number, serverTimestamp: number): void {
        const latency = Math.max(0, Date.now() - serverTimestamp * 1000);
        this._latencies.push(latency);
        if (this._latencies.length > WINDOW_SIZE) {
            this._latencies.shift();
        }
        this._lastSeq = Math.max(this._lastSeq, seq);
        this.lastEventAt = Date.now();
        this._resetStalenessTimer();
        this._deriveState();
    }

    /**
     * Record a sync event or keepalive — resets staleness but does NOT
     * contribute to the latency window.
     */
    recordSyncOrKeepalive(seq?: number): void {
        if (seq != null) this._lastSeq = Math.max(this._lastSeq, seq);
        this.lastEventAt = Date.now();
        this._resetStalenessTimer();
    }

    /** Close the connection and stop all timers. */
    disconnect(): void {
        this._closeEventSource();
        this._clearTimers();
        this.connectionState = 'disconnected';
    }

    /** User-initiated retry after the retry cap is reached. */
    retryNow(): void {
        this.retryCount = 0;
        this.retryCapped = false;
        this.retryAt = null;
        this._reconnect();
    }

    /** Reset all state — for tests only. */
    _reset(): void {
        this._closeEventSource();
        this._clearTimers();
        this.connectionState = 'disconnected';
        this.lastEventAt = null;
        this.retryCount = 0;
        this.retryAt = null;
        this.retryCapped = false;
        this._latencies = [];
        this._lastSeq = 0;
        this._onEvent = null;
        this._onReconnect = null;
        this._hadError = false;
        this._now = Date.now();
    }

    // ------------------------------------------------------------------
    // Private — EventSource lifecycle
    // ------------------------------------------------------------------

    private _createEventSource(): void {
        const lastId = this._lastSeq > 0 ? String(this._lastSeq) : undefined;

        this._eventSource = connectEventStream(
            (type: string, data: Record<string, unknown>, meta: EventMeta) => {
                // Track latency for real events (not sync).
                if (type !== 'sync' && meta.seq) {
                    const seq = parseInt(meta.seq, 10);
                    if (!Number.isNaN(seq) && meta.timestamp != null) {
                        this.recordEvent(seq, meta.timestamp);
                    } else if (!Number.isNaN(seq)) {
                        // Event with seq but no timestamp — still update seq tracking.
                        this._lastSeq = Math.max(this._lastSeq, seq);
                        this.lastEventAt = Date.now();
                        this._resetStalenessTimer();
                    }
                }
                if (type === 'sync') {
                    const seq = meta.seq ? parseInt(meta.seq, 10) : NaN;
                    this.recordSyncOrKeepalive(Number.isNaN(seq) ? undefined : seq);
                }
                // Forward to consumer.
                this._onEvent?.(type, data, meta);
            },
            lastId,
        );

        this._eventSource.addEventListener('open', () => this._onOpen());
        this._eventSource.onerror = () => this._onError();
        this._resetStalenessTimer();
    }

    private _onOpen(): void {
        const wasError = this._hadError;
        this._hadError = false;
        this.retryCount = 0;
        this.retryAt = null;
        this.retryCapped = false;
        this._stopCountdownTimer();

        // Derive state from existing latency data, or default to healthy.
        if (this._latencies.length > 0) {
            this._deriveState();
        } else {
            this.connectionState = 'healthy';
        }

        // Reconnection reconciliation — refetch critical state.
        if (wasError && this._onReconnect) {
            this._onReconnect();
        }
    }

    private _onError(): void {
        this._hadError = true;
        this.connectionState = 'disconnected';
        // Close the current EventSource to prevent browser auto-reconnect,
        // then manage reconnection ourselves with backoff.
        this._closeEventSource();
        this._reconnect();
    }

    private _closeEventSource(): void {
        if (this._eventSource) {
            this._eventSource.close();
            this._eventSource = null;
        }
    }

    // ------------------------------------------------------------------
    // Private — reconnection with exponential backoff
    // ------------------------------------------------------------------

    private _reconnect(): void {
        // Clear any pending retry timer.
        if (this._retryTimer != null) {
            clearTimeout(this._retryTimer);
            this._retryTimer = null;
        }

        if (this.retryCount >= MAX_RETRIES) {
            this.retryCapped = true;
            this.retryAt = null;
            this._stopCountdownTimer();
            return;
        }

        const delay = this._computeBackoff();
        this.retryCount++;
        this.retryAt = Date.now() + delay;
        this._now = Date.now();
        this._startCountdownTimer();

        this._retryTimer = setTimeout(() => {
            this._retryTimer = null;
            this.retryAt = null;
            this._stopCountdownTimer();
            this._createEventSource();
        }, delay);
    }

    private _computeBackoff(): number {
        const base = Math.min(
            BASE_DELAY_MS * Math.pow(2, this.retryCount),
            MAX_DELAY_MS,
        );
        const jitter = (Math.random() * 2 - 1) * JITTER_FACTOR;
        return Math.round(base * (1 + jitter));
    }

    // ------------------------------------------------------------------
    // Private — staleness detection
    // ------------------------------------------------------------------

    private _resetStalenessTimer(): void {
        if (this._stalenessTimer != null) {
            clearTimeout(this._stalenessTimer);
        }
        this._stalenessTimer = setTimeout(() => {
            this._stalenessTimer = null;
            this.connectionState = 'disconnected';
            // Stale connection — close and attempt reconnection.
            this._closeEventSource();
            this._reconnect();
        }, STALENESS_MS);
    }

    // ------------------------------------------------------------------
    // Private — health state derivation
    // ------------------------------------------------------------------

    private _deriveState(): void {
        const p95 = this._percentile(0.95);
        if (p95 == null) {
            // No data yet — stay at current state.
            return;
        }
        if (p95 < HEALTHY_THRESHOLD_MS) {
            this.connectionState = 'healthy';
        } else if (p95 < DEGRADED_THRESHOLD_MS) {
            this.connectionState = 'degraded';
        } else {
            this.connectionState = 'disconnected';
        }
    }

    // ------------------------------------------------------------------
    // Private — percentile computation
    // ------------------------------------------------------------------

    /**
     * Compute the p-th percentile from the latency window.
     * Returns null if the window is empty.
     */
    private _percentile(p: number): number | null {
        const len = this._latencies.length;
        if (len === 0) return null;
        const sorted = [...this._latencies].sort((a, b) => a - b);
        const idx = Math.floor(p * (len - 1));
        return Math.round(sorted[idx]);
    }

    // ------------------------------------------------------------------
    // Private — timer management
    // ------------------------------------------------------------------

    private _startCountdownTimer(): void {
        this._stopCountdownTimer();
        this._countdownTimer = setInterval(() => {
            this._now = Date.now();
        }, 1000);
    }

    private _stopCountdownTimer(): void {
        if (this._countdownTimer != null) {
            clearInterval(this._countdownTimer);
            this._countdownTimer = null;
        }
    }

    private _clearTimers(): void {
        if (this._stalenessTimer != null) {
            clearTimeout(this._stalenessTimer);
            this._stalenessTimer = null;
        }
        if (this._retryTimer != null) {
            clearTimeout(this._retryTimer);
            this._retryTimer = null;
        }
        this._stopCountdownTimer();
    }
}

export const sseHealthStore = new SSEHealthStore();
