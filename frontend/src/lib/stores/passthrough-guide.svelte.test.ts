import { describe, it, expect, beforeEach, vi } from 'vitest';
import { passthroughGuide } from './passthrough-guide.svelte';

describe('passthroughGuide store', () => {
  beforeEach(() => {
    passthroughGuide.close();
    passthroughGuide.resetDismissal();
  });

  it('starts closed', () => {
    expect(passthroughGuide.open).toBe(false);
    expect(passthroughGuide.activeStep).toBe(0);
  });

  it('show(false) opens unconditionally', () => {
    passthroughGuide.show(false);
    expect(passthroughGuide.open).toBe(true);
  });

  it('show(false) resets activeStep to 0', () => {
    passthroughGuide.show(false);
    passthroughGuide.setStep(3);
    passthroughGuide.close();
    passthroughGuide.show(false);
    expect(passthroughGuide.activeStep).toBe(0);
  });

  it('show(true) opens when not dismissed', () => {
    passthroughGuide.show(true);
    expect(passthroughGuide.open).toBe(true);
  });

  it('show(true) skips when dismissed', () => {
    passthroughGuide.dismiss();
    passthroughGuide.show(true);
    expect(passthroughGuide.open).toBe(false);
  });

  it('show(false) opens even when dismissed', () => {
    passthroughGuide.dismiss();
    passthroughGuide.show(false);
    expect(passthroughGuide.open).toBe(true);
  });

  it('close() closes without persisting dismissal', () => {
    passthroughGuide.show(false);
    passthroughGuide.close();
    expect(passthroughGuide.open).toBe(false);
    // Should still open with respectDismiss=true
    passthroughGuide.show(true);
    expect(passthroughGuide.open).toBe(true);
  });

  it('dismiss() closes and persists to localStorage', () => {
    passthroughGuide.show(false);
    passthroughGuide.dismiss();
    expect(passthroughGuide.open).toBe(false);
    expect(localStorage.getItem('synthesis:passthrough_guide_dismissed')).toBe('1');
  });

  it('resetDismissal() clears localStorage', () => {
    passthroughGuide.dismiss();
    expect(localStorage.getItem('synthesis:passthrough_guide_dismissed')).toBe('1');
    passthroughGuide.resetDismissal();
    expect(localStorage.getItem('synthesis:passthrough_guide_dismissed')).toBeNull();
  });

  it('setStep() sets activeStep clamped to valid range', () => {
    passthroughGuide.setStep(3);
    expect(passthroughGuide.activeStep).toBe(3);

    passthroughGuide.setStep(-1);
    expect(passthroughGuide.activeStep).toBe(0);

    passthroughGuide.setStep(99);
    expect(passthroughGuide.activeStep).toBe(5); // STEP_COUNT - 1
  });

  it('nextStep() advances activeStep', () => {
    passthroughGuide.show(false);
    expect(passthroughGuide.activeStep).toBe(0);

    passthroughGuide.nextStep();
    expect(passthroughGuide.activeStep).toBe(1);

    passthroughGuide.nextStep();
    expect(passthroughGuide.activeStep).toBe(2);
  });

  it('nextStep() closes modal on last step', () => {
    passthroughGuide.show(false);
    passthroughGuide.setStep(5); // last step
    passthroughGuide.nextStep();
    expect(passthroughGuide.open).toBe(false);
  });

  it('prevStep() decrements activeStep', () => {
    passthroughGuide.show(false);
    passthroughGuide.setStep(3);
    passthroughGuide.prevStep();
    expect(passthroughGuide.activeStep).toBe(2);
  });

  it('prevStep() clamps at 0', () => {
    passthroughGuide.show(false);
    passthroughGuide.prevStep();
    expect(passthroughGuide.activeStep).toBe(0);
  });

  it('handles localStorage errors gracefully', () => {
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });

    // Should not throw
    passthroughGuide.show(true);
    expect(passthroughGuide.open).toBe(true);

    passthroughGuide.dismiss();
    expect(passthroughGuide.open).toBe(false);

    getItemSpy.mockRestore();
    setItemSpy.mockRestore();
  });
});
