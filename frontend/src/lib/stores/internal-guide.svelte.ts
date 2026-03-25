/**
 * Internal Provider guide modal state.
 *
 * Thin wrapper around the guide store factory for the internal tier (CLI / API).
 * Triggered at startup when an internal provider is detected, and by the
 * tier onboarding coordinator on runtime tier transitions.
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import { createGuideStore } from './guide-factory.svelte';

/** Number of steps in the internal guide. Single source of truth — imported by the component. */
export const STEP_COUNT = 5;

export const internalGuide = createGuideStore('synthesis:internal_guide_dismissed', STEP_COUNT);
