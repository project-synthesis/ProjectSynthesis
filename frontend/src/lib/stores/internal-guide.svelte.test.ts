import { describe, it, expect, beforeEach, vi } from 'vitest';
import { internalGuide } from './internal-guide.svelte';

describe('internalGuide store', () => {
  beforeEach(() => {
    internalGuide.close();
    internalGuide.resetDismissal();
  });

  it('starts closed', () => {
    expect(internalGuide.open).toBe(false);
    expect(internalGuide.activeStep).toBe(0);
  });

  it('show(false) opens unconditionally', () => {
    internalGuide.show(false);
    expect(internalGuide.open).toBe(true);
  });

  it('show(false) resets activeStep to 0', () => {
    internalGuide.show(false);
    internalGuide.setStep(3);
    internalGuide.close();
    internalGuide.show(false);
    expect(internalGuide.activeStep).toBe(0);
  });

  it('show(true) opens when not dismissed', () => {
    internalGuide.show(true);
    expect(internalGuide.open).toBe(true);
  });

  it('show(true) skips when dismissed', () => {
    internalGuide.dismiss();
    internalGuide.show(true);
    expect(internalGuide.open).toBe(false);
  });

  it('show(false) opens even when dismissed', () => {
    internalGuide.dismiss();
    internalGuide.show(false);
    expect(internalGuide.open).toBe(true);
  });

  it('close() closes without persisting dismissal', () => {
    internalGuide.show(false);
    internalGuide.close();
    expect(internalGuide.open).toBe(false);
    // Should still open with respectDismiss=true
    internalGuide.show(true);
    expect(internalGuide.open).toBe(true);
  });

  it('dismiss() closes and persists to localStorage', () => {
    internalGuide.show(false);
    internalGuide.dismiss();
    expect(internalGuide.open).toBe(false);
    expect(localStorage.getItem('synthesis:internal_guide_dismissed')).toBe('1');
  });

  it('resetDismissal() clears localStorage', () => {
    internalGuide.dismiss();
    expect(localStorage.getItem('synthesis:internal_guide_dismissed')).toBe('1');
    internalGuide.resetDismissal();
    expect(localStorage.getItem('synthesis:internal_guide_dismissed')).toBeNull();
  });

  it('setStep() sets activeStep clamped to valid range', () => {
    internalGuide.setStep(3);
    expect(internalGuide.activeStep).toBe(3);

    internalGuide.setStep(-1);
    expect(internalGuide.activeStep).toBe(0);

    internalGuide.setStep(99);
    expect(internalGuide.activeStep).toBe(4); // STEP_COUNT (5) - 1
  });

  it('nextStep() advances activeStep', () => {
    internalGuide.show(false);
    expect(internalGuide.activeStep).toBe(0);

    internalGuide.nextStep();
    expect(internalGuide.activeStep).toBe(1);

    internalGuide.nextStep();
    expect(internalGuide.activeStep).toBe(2);
  });

  it('nextStep() closes modal on last step', () => {
    internalGuide.show(false);
    internalGuide.setStep(4); // last step (STEP_COUNT - 1)
    internalGuide.nextStep();
    expect(internalGuide.open).toBe(false);
  });

  it('prevStep() decrements activeStep', () => {
    internalGuide.show(false);
    internalGuide.setStep(3);
    internalGuide.prevStep();
    expect(internalGuide.activeStep).toBe(2);
  });

  it('prevStep() clamps at 0', () => {
    internalGuide.show(false);
    internalGuide.prevStep();
    expect(internalGuide.activeStep).toBe(0);
  });

  it('handles localStorage errors gracefully', () => {
    const getItemSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });
    const setItemSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });

    // Should not throw
    internalGuide.show(true);
    expect(internalGuide.open).toBe(true);

    internalGuide.dismiss();
    expect(internalGuide.open).toBe(false);

    getItemSpy.mockRestore();
    setItemSpy.mockRestore();
  });
});
