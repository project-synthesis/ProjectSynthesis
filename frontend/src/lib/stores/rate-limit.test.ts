import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { rateLimitStore } from './rate-limit.svelte';

const FUTURE = () => new Date(Date.now() + 60_000).toISOString();
const PAST = () => new Date(Date.now() - 60_000).toISOString();

describe('rate-limit store — applyActive / applyCleared', () => {
  beforeEach(() => {
    rateLimitStore._reset();
  });
  afterEach(() => {
    rateLimitStore._reset();
  });

  it('starts empty: isAnyActive=false, activeList empty', () => {
    expect(rateLimitStore.isAnyActive).toBe(false);
    expect(rateLimitStore.activeList).toHaveLength(0);
  });

  it('applyActive marks isAnyActive=true and adds to activeList', () => {
    rateLimitStore.applyActive({
      provider: 'claude_cli',
      reset_at_iso: FUTURE(),
      estimated_wait_seconds: 60,
    });
    expect(rateLimitStore.isAnyActive).toBe(true);
    expect(rateLimitStore.activeList).toHaveLength(1);
    expect(rateLimitStore.activeList[0].provider).toBe('claude_cli');
    expect(rateLimitStore.activeList[0].seconds_remaining).toBeGreaterThan(0);
  });

  it('applyActive falls back to "unknown" provider when payload omits it', () => {
    rateLimitStore.applyActive({ reset_at_iso: FUTURE() });
    expect(rateLimitStore.activeList[0].provider).toBe('unknown');
  });

  it('applyActive is idempotent within the same provider — re-applying replaces metadata', () => {
    rateLimitStore.applyActive({ provider: 'claude_cli', reset_at_iso: FUTURE() });
    rateLimitStore.applyActive({ provider: 'claude_cli', reset_at_iso: FUTURE() });
    expect(rateLimitStore.activeList).toHaveLength(1);
  });

  it('applyCleared removes the matching entry; no-op when provider absent', () => {
    rateLimitStore.applyActive({ provider: 'a', reset_at_iso: FUTURE() });
    rateLimitStore.applyActive({ provider: 'b', reset_at_iso: FUTURE() });
    rateLimitStore.applyCleared({ provider: 'a' });
    expect(rateLimitStore.activeList.map((x) => x.provider)).toEqual(['b']);
    rateLimitStore.applyCleared({ provider: 'never-existed' });
    expect(rateLimitStore.activeList).toHaveLength(1);
  });

  it('applyCleared with no provider falls back to "unknown" key', () => {
    rateLimitStore.applyActive({ reset_at_iso: FUTURE() });
    rateLimitStore.applyCleared({});
    expect(rateLimitStore.isAnyActive).toBe(false);
  });

  it('activeList is sorted by remaining time (longest wait first)', () => {
    rateLimitStore.applyActive({
      provider: 'short',
      reset_at_iso: new Date(Date.now() + 10_000).toISOString(),
    });
    rateLimitStore.applyActive({
      provider: 'long',
      reset_at_iso: new Date(Date.now() + 600_000).toISOString(),
    });
    const list = rateLimitStore.activeList;
    expect(list.length).toBe(2);
    expect(list[0].provider).toBe('long');
    expect(list[1].provider).toBe('short');
  });

  it('reset_at in the past is treated as no longer active', () => {
    rateLimitStore.applyActive({ provider: 'expired', reset_at_iso: PAST() });
    expect(rateLimitStore.isAnyActive).toBe(false);
    expect(rateLimitStore.activeList).toHaveLength(0);
  });

  it('null reset_at_iso falls back to 5-minute grace from detection', () => {
    rateLimitStore.applyActive({ provider: 'ratelimited-no-iso', reset_at_iso: null });
    expect(rateLimitStore.isAnyActive).toBe(true);
  });

  it('NaN reset_at_iso falls back to 5-minute grace from detection', () => {
    rateLimitStore.applyActive({ provider: 'invalid', reset_at_iso: 'not-a-date' });
    expect(rateLimitStore.isAnyActive).toBe(true);
  });

  it('seconds_remaining null when no reset_at and no estimated_wait', () => {
    rateLimitStore.applyActive({ provider: 'no-info', reset_at_iso: null });
    const entry = rateLimitStore.activeList[0];
    expect(entry.seconds_remaining).toBeNull();
  });

  it('seconds_remaining derived from estimated_wait_seconds when reset_at_iso missing', () => {
    rateLimitStore.applyActive({
      provider: 'wait-only',
      reset_at_iso: null,
      estimated_wait_seconds: 30,
    });
    const entry = rateLimitStore.activeList[0];
    expect(entry.seconds_remaining).toBeGreaterThan(0);
    expect(entry.seconds_remaining!).toBeLessThanOrEqual(30);
  });
});

describe('rate-limit store — applyHeuristicFlags', () => {
  beforeEach(() => rateLimitStore._reset());
  afterEach(() => rateLimitStore._reset());

  it('null payload is a no-op', () => {
    rateLimitStore.applyHeuristicFlags(null);
    expect(rateLimitStore.isAnyActive).toBe(false);
  });

  it('empty/non-rate-limited payload is a no-op', () => {
    rateLimitStore.applyHeuristicFlags({ rate_limited: false });
    expect(rateLimitStore.isAnyActive).toBe(false);
    rateLimitStore.applyHeuristicFlags({ something_else: true });
    expect(rateLimitStore.isAnyActive).toBe(false);
  });

  it('rate_limited=true with full payload promotes to applyActive', () => {
    const reset = FUTURE();
    rateLimitStore.applyHeuristicFlags({
      rate_limited: true,
      provider: 'claude_cli',
      reset_at_iso: reset,
      estimated_wait_seconds: 30,
    });
    expect(rateLimitStore.isAnyActive).toBe(true);
    expect(rateLimitStore.activeList[0].reset_at_iso).toBe(reset);
  });

  it('rate_limited=true tolerates non-string provider / non-string reset / non-number wait', () => {
    rateLimitStore.applyHeuristicFlags({
      rate_limited: true,
      provider: 123 as unknown as string,
      reset_at_iso: 999 as unknown as string,
      estimated_wait_seconds: 'huh' as unknown as number,
    });
    // applyActive runs but provider becomes "unknown" because typeof check fails
    expect(rateLimitStore.activeList[0].provider).toBe('unknown');
    expect(rateLimitStore.activeList[0].reset_at_iso).toBeNull();
  });
});

describe('rate-limit store — auto-prune via tick', () => {
  beforeEach(() => {
    rateLimitStore._reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    rateLimitStore._reset();
  });

  it('1-second tick prunes entries whose reset_at has elapsed', async () => {
    rateLimitStore.applyActive({
      provider: 'soon',
      reset_at_iso: new Date(Date.now() + 500).toISOString(),
    });
    expect(rateLimitStore.isAnyActive).toBe(true);
    // Advance past reset_at + a tick boundary
    vi.advanceTimersByTime(2_000);
    expect(rateLimitStore.isAnyActive).toBe(false);
  });
});
