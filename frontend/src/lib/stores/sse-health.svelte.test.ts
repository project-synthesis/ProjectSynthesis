import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
    sseHealthStore,
    WINDOW_SIZE,
    HEALTHY_THRESHOLD_MS,
    DEGRADED_THRESHOLD_MS,
    STALENESS_MS,
    MAX_RETRIES,
    BASE_DELAY_MS,
    MAX_DELAY_MS,
    JITTER_FACTOR,
} from './sse-health.svelte';

describe('SSEHealthStore', () => {
    beforeEach(() => {
        vi.useFakeTimers({ shouldAdvanceTime: false });
        sseHealthStore._reset();
    });

    afterEach(() => {
        sseHealthStore._reset();
        vi.useRealTimers();
    });

    // ------------------------------------------------------------------
    // Percentile computation
    // ------------------------------------------------------------------

    describe('percentiles', () => {
        it('returns null when window is empty', () => {
            expect(sseHealthStore.p50).toBeNull();
            expect(sseHealthStore.p95).toBeNull();
            expect(sseHealthStore.p99).toBeNull();
        });

        it('computes correct percentiles from sorted data', () => {
            const now = Date.now() / 1000;
            // Push 100 events with latencies 0..99ms
            for (let i = 0; i < 100; i++) {
                sseHealthStore.recordEvent(i + 1, now - i / 1000);
            }

            // p50 of 0..99 at index floor(0.50 * 99) = 49
            expect(sseHealthStore.p50).toBeGreaterThanOrEqual(0);
            expect(sseHealthStore.p50).toBeLessThan(HEALTHY_THRESHOLD_MS);

            // p95 at index floor(0.95 * 99) = 94
            expect(sseHealthStore.p95).toBeGreaterThanOrEqual(0);
        });

        it('trims window to WINDOW_SIZE', () => {
            const now = Date.now() / 1000;
            for (let i = 0; i < WINDOW_SIZE + 20; i++) {
                sseHealthStore.recordEvent(i + 1, now - 0.05);
            }
            expect(sseHealthStore._latencies.length).toBe(WINDOW_SIZE);
        });

        it('clamps negative latency to zero', () => {
            // Server timestamp slightly in the future (clock skew)
            const futureTimestamp = (Date.now() + 5000) / 1000;
            sseHealthStore.recordEvent(1, futureTimestamp);
            expect(sseHealthStore._latencies[0]).toBe(0);
        });
    });

    // ------------------------------------------------------------------
    // Connection state derivation
    // ------------------------------------------------------------------

    describe('state derivation', () => {
        it('is healthy when p95 < HEALTHY_THRESHOLD_MS', () => {
            const now = Date.now() / 1000;
            // Low-latency events (~50ms each)
            for (let i = 0; i < 20; i++) {
                sseHealthStore.recordEvent(i + 1, now - 0.05);
            }
            expect(sseHealthStore.connectionState).toBe('healthy');
        });

        it('is degraded when HEALTHY_THRESHOLD_MS <= p95 < DEGRADED_THRESHOLD_MS', () => {
            const now = Date.now() / 1000;
            // 95% of events at low latency, 5% at high → p95 above threshold
            for (let i = 0; i < 90; i++) {
                sseHealthStore.recordEvent(i + 1, now - 0.1);
            }
            // Push 10 events with ~3s latency (above healthy, below degraded)
            for (let i = 90; i < 100; i++) {
                sseHealthStore.recordEvent(i + 1, now - 3.0);
            }
            expect(sseHealthStore.connectionState).toBe('degraded');
        });

        it('is disconnected when p95 >= DEGRADED_THRESHOLD_MS', () => {
            const now = Date.now() / 1000;
            // All events with ~6s latency
            for (let i = 0; i < 20; i++) {
                sseHealthStore.recordEvent(i + 1, now - 6.0);
            }
            expect(sseHealthStore.connectionState).toBe('disconnected');
        });
    });

    // ------------------------------------------------------------------
    // Staleness detection
    // ------------------------------------------------------------------

    describe('staleness', () => {
        it('transitions to disconnected after STALENESS_MS with no events', () => {
            // Simulate a connection that received one event then went silent.
            const now = Date.now() / 1000;
            sseHealthStore.recordEvent(1, now - 0.05);
            expect(sseHealthStore.connectionState).toBe('healthy');

            // Advance past staleness threshold.
            vi.advanceTimersByTime(STALENESS_MS + 100);
            expect(sseHealthStore.connectionState).toBe('disconnected');
        });

        it('resets staleness timer on each event', () => {
            const now = Date.now() / 1000;
            sseHealthStore.recordEvent(1, now - 0.05);

            // Advance 80s (below 90s)
            vi.advanceTimersByTime(80_000);
            expect(sseHealthStore.connectionState).toBe('healthy');

            // Another event resets the timer.
            sseHealthStore.recordEvent(2, Date.now() / 1000 - 0.05);

            // Advance another 80s (total 160s from start, but only 80s from last event)
            vi.advanceTimersByTime(80_000);
            expect(sseHealthStore.connectionState).toBe('healthy');

            // Now go past the threshold from the last event.
            vi.advanceTimersByTime(STALENESS_MS);
            expect(sseHealthStore.connectionState).toBe('disconnected');
        });
    });

    // ------------------------------------------------------------------
    // Sync events
    // ------------------------------------------------------------------

    describe('sync events', () => {
        it('updates lastEventAt but NOT latency window', () => {
            sseHealthStore.recordSyncOrKeepalive(42);
            expect(sseHealthStore.lastEventAt).not.toBeNull();

            expect(sseHealthStore._latencies.length).toBe(0);
        });

        it('updates _lastSeq', () => {
            sseHealthStore.recordSyncOrKeepalive(42);

            expect((sseHealthStore as any)._lastSeq).toBe(42);
        });
    });

    // ------------------------------------------------------------------
    // Backoff computation
    // ------------------------------------------------------------------

    describe('backoff', () => {
        it('follows exponential progression with cap', () => {
            const computeBackoff = (count: number) => {
                sseHealthStore.retryCount = count;
                return (sseHealthStore as any)._computeBackoff();
            };

            // Seed Math.random to remove jitter for this test.
            const orig = Math.random;
            Math.random = () => 0.5; // → jitter = 0

            try {
                expect(computeBackoff(0)).toBe(BASE_DELAY_MS);        // 1000
                expect(computeBackoff(1)).toBe(BASE_DELAY_MS * 2);    // 2000
                expect(computeBackoff(2)).toBe(BASE_DELAY_MS * 4);    // 4000
                expect(computeBackoff(3)).toBe(BASE_DELAY_MS * 8);    // 8000
                expect(computeBackoff(4)).toBe(MAX_DELAY_MS);          // 16000 (cap)
                expect(computeBackoff(5)).toBe(MAX_DELAY_MS);          // 16000 (still cap)
            } finally {
                Math.random = orig;
            }
        });

        it('applies jitter within +/- JITTER_FACTOR', () => {
            sseHealthStore.retryCount = 0;
            const samples: number[] = [];
            for (let i = 0; i < 100; i++) {
                samples.push((sseHealthStore as any)._computeBackoff());
            }
            const min = Math.min(...samples);
            const max = Math.max(...samples);
            const expectedMin = BASE_DELAY_MS * (1 - JITTER_FACTOR);
            const expectedMax = BASE_DELAY_MS * (1 + JITTER_FACTOR);
            expect(min).toBeGreaterThanOrEqual(expectedMin - 1); // rounding tolerance
            expect(max).toBeLessThanOrEqual(expectedMax + 1);
        });
    });

    // ------------------------------------------------------------------
    // Retry cap
    // ------------------------------------------------------------------

    describe('retry cap', () => {
        it('caps at MAX_RETRIES attempts', () => {
            sseHealthStore.retryCount = MAX_RETRIES;

            (sseHealthStore as any)._reconnect();
            expect(sseHealthStore.retryCapped).toBe(true);
            expect(sseHealthStore.retryAt).toBeNull();
        });

        it('retryNow resets cap and triggers reconnection', () => {
            sseHealthStore.retryCount = MAX_RETRIES;
            sseHealthStore.retryCapped = true;
            sseHealthStore.retryNow();
            expect(sseHealthStore.retryCapped).toBe(false);
            expect(sseHealthStore.retryCount).toBe(1); // _reconnect increments
        });
    });

    // ------------------------------------------------------------------
    // Reconnection
    // ------------------------------------------------------------------

    describe('reconnection', () => {
        it('increments retryCount on each reconnect attempt', () => {
            expect(sseHealthStore.retryCount).toBe(0);

            (sseHealthStore as any)._reconnect();
            expect(sseHealthStore.retryCount).toBe(1);
            // Clear timer before next reconnect.
            vi.advanceTimersByTime(MAX_DELAY_MS);

            (sseHealthStore as any)._reconnect();
            expect(sseHealthStore.retryCount).toBe(2);
        });

        it('sets retryAt with future timestamp', () => {
            const before = Date.now();

            (sseHealthStore as any)._reconnect();
            expect(sseHealthStore.retryAt).not.toBeNull();
            expect(sseHealthStore.retryAt!).toBeGreaterThanOrEqual(before);
        });
    });

    // ------------------------------------------------------------------
    // Tooltip text
    // ------------------------------------------------------------------

    describe('tooltip', () => {
        it('shows "awaiting events" when healthy with no data', () => {
            sseHealthStore.connectionState = 'healthy';
            expect(sseHealthStore.tooltipText).toContain('awaiting events');
        });

        it('shows p50/p95 when healthy with data', () => {
            const now = Date.now() / 1000;
            for (let i = 0; i < 10; i++) {
                sseHealthStore.recordEvent(i + 1, now - 0.05);
            }
            expect(sseHealthStore.tooltipText).toContain('healthy');
            expect(sseHealthStore.tooltipText).toContain('p50:');
            expect(sseHealthStore.tooltipText).toContain('p95:');
        });

        it('shows p95/p99 when degraded', () => {
            sseHealthStore.connectionState = 'degraded';
            // Push some data so percentiles exist.
            const now = Date.now() / 1000;
            for (let i = 0; i < 20; i++) {
                // Use recordEvent's logic but bypass state derivation by setting state after.
                const latency = Math.max(0, Date.now() - (now - 3.0) * 1000);
                sseHealthStore._latencies.push(latency);
            }
            sseHealthStore.connectionState = 'degraded';
            expect(sseHealthStore.tooltipText).toContain('degraded');
            expect(sseHealthStore.tooltipText).toContain('p95:');
            expect(sseHealthStore.tooltipText).toContain('p99:');
        });

        it('shows retry countdown when disconnected and retrying', () => {
            sseHealthStore.connectionState = 'disconnected';
            sseHealthStore.retryCount = 3;
            sseHealthStore.retryAt = Date.now() + 5000;
            expect(sseHealthStore.tooltipText).toContain('Retry 3/');
        });

        it('shows "Retries exhausted" when capped', () => {
            sseHealthStore.connectionState = 'disconnected';
            sseHealthStore.retryCapped = true;
            expect(sseHealthStore.tooltipText).toContain('Retries exhausted');
        });
    });

    // ------------------------------------------------------------------
    // Event recording
    // ------------------------------------------------------------------

    describe('recordEvent', () => {
        it('updates lastEventAt', () => {
            expect(sseHealthStore.lastEventAt).toBeNull();
            sseHealthStore.recordEvent(1, Date.now() / 1000 - 0.1);
            expect(sseHealthStore.lastEventAt).not.toBeNull();
        });

        it('tracks sequence numbers', () => {
            sseHealthStore.recordEvent(5, Date.now() / 1000);
            sseHealthStore.recordEvent(3, Date.now() / 1000); // out of order

            expect((sseHealthStore as any)._lastSeq).toBe(5); // max preserved
        });
    });
});
