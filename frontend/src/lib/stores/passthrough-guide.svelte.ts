/**
 * Passthrough Guide modal state.
 *
 * Module-level reactive store controlling the Passthrough Protocol guide modal.
 * Two trigger contexts: Navigator toggle (respectDismiss=true) and
 * PassthroughView help button (respectDismiss=false, always opens).
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

const STORAGE_KEY = 'synthesis:passthrough_guide_dismissed';

/** Number of steps in the passthrough guide. Single source of truth — imported by the component. */
export const STEP_COUNT = 6;

let _open = $state(false);
let _activeStep = $state(0);

export const passthroughGuide = {
  get open() {
    return _open;
  },

  get activeStep() {
    return _activeStep;
  },

  /**
   * Open the guide.
   * @param respectDismiss When true, check localStorage and skip if dismissed.
   */
  show(respectDismiss = false) {
    if (respectDismiss) {
      try {
        if (localStorage.getItem(STORAGE_KEY) === '1') return;
      } catch {
        /* storage unavailable */
      }
    }
    _activeStep = 0;
    _open = true;
  },

  /** Close without persisting dismissal. */
  close() {
    _open = false;
  },

  /** Close and persist "don't show on toggle" preference. */
  dismiss() {
    _open = false;
    try {
      localStorage.setItem(STORAGE_KEY, '1');
    } catch {
      /* noop */
    }
  },

  /** Clear the dismissal flag (for testing or preference reset). */
  resetDismissal() {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* noop */
    }
  },

  /** Expand a specific step by index (0-based, clamped). */
  setStep(index: number) {
    _activeStep = Math.max(0, Math.min(index, STEP_COUNT - 1));
  },

  /** Advance to the next step. Closes modal on last step. */
  nextStep() {
    if (_activeStep >= STEP_COUNT - 1) {
      _open = false;
    } else {
      _activeStep++;
    }
  },

  /** Go to the previous step (clamps at 0). */
  prevStep() {
    _activeStep = Math.max(0, _activeStep - 1);
  },
};
