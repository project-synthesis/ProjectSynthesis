/**
 * Guide store factory.
 *
 * Creates a reactive store for tier-specific workflow guide modals
 * (PassthroughGuide, SamplingGuide).  Each store owns its own localStorage
 * key for the "don't show on toggle" preference and its own step count.
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

export interface GuideStore {
  readonly open: boolean;
  readonly activeStep: number;
  show(respectDismiss?: boolean): void;
  close(): void;
  dismiss(): void;
  resetDismissal(): void;
  setStep(index: number): void;
  nextStep(): void;
  prevStep(): void;
}

export function createGuideStore(storageKey: string, stepCount: number): GuideStore {
  let _open = $state(false);
  let _activeStep = $state(0);

  return {
    get open() {
      return _open;
    },

    get activeStep() {
      return _activeStep;
    },

    show(respectDismiss = false) {
      if (respectDismiss) {
        try {
          if (localStorage.getItem(storageKey) === '1') return;
        } catch {
          /* storage unavailable */
        }
      }
      _activeStep = 0;
      _open = true;
    },

    close() {
      _open = false;
    },

    dismiss() {
      _open = false;
      try {
        localStorage.setItem(storageKey, '1');
      } catch {
        /* noop */
      }
    },

    resetDismissal() {
      try {
        localStorage.removeItem(storageKey);
      } catch {
        /* noop */
      }
    },

    setStep(index: number) {
      _activeStep = Math.max(0, Math.min(index, stepCount - 1));
    },

    nextStep() {
      if (_activeStep >= stepCount - 1) {
        _open = false;
      } else {
        _activeStep++;
      }
    },

    prevStep() {
      _activeStep = Math.max(0, _activeStep - 1);
    },
  };
}
