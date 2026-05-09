/**
 * useCopyFlash — reactive copy-flash primitive.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  useCopyFlash,
  COPY_FLASH_DURATION_MS,
} from './copy-feedback.svelte';

describe('useCopyFlash', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts in non-triggered state', () => {
    const flash = useCopyFlash();
    expect(flash.triggered).toBe(false);
  });

  it('flips triggered=true on trigger() and resets after the duration', () => {
    const flash = useCopyFlash();
    flash.trigger();
    expect(flash.triggered).toBe(true);
    vi.advanceTimersByTime(COPY_FLASH_DURATION_MS - 1);
    expect(flash.triggered).toBe(true);
    vi.advanceTimersByTime(2);
    expect(flash.triggered).toBe(false);
  });

  it('clears any prior timer on retrigger so the window restarts cleanly', () => {
    // Pre-fix bug surface: if a user mashes "Copy" twice within the
    // window, the FIRST timer was still scheduled and would reset
    // ``triggered`` early — visible UI glitch where "Copied" flashed
    // away before the new flash window completed.
    const flash = useCopyFlash();
    flash.trigger();
    vi.advanceTimersByTime(800); // mid-window
    flash.trigger(); // restart
    vi.advanceTimersByTime(800); // total 1600ms — past first window, mid-second
    expect(flash.triggered).toBe(true);
    vi.advanceTimersByTime(800); // finish second window
    expect(flash.triggered).toBe(false);
  });

  it('invokes onReset callback on window expiry', () => {
    const onReset = vi.fn();
    const flash = useCopyFlash(onReset);
    flash.trigger();
    vi.advanceTimersByTime(COPY_FLASH_DURATION_MS);
    expect(onReset).toHaveBeenCalledOnce();
  });

  it('does NOT invoke onReset on trigger restart (only on expiry)', () => {
    const onReset = vi.fn();
    const flash = useCopyFlash(onReset);
    flash.trigger();
    vi.advanceTimersByTime(500);
    flash.trigger(); // restart before expiry
    expect(onReset).not.toHaveBeenCalled();
    vi.advanceTimersByTime(COPY_FLASH_DURATION_MS);
    expect(onReset).toHaveBeenCalledOnce();
  });

  it('COPY_FLASH_DURATION_MS is 1500 (mirrors --duration-copy-flash)', () => {
    // Pin the contract: if the CSS variable changes, this constant
    // must follow. The audit sweep that introduced this helper
    // unified four hand-rolled timeouts (1200/1500/2000ms) on a
    // single value.
    expect(COPY_FLASH_DURATION_MS).toBe(1500);
  });
});
