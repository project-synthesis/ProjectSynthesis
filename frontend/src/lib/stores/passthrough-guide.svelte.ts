/**
 * Passthrough Guide modal state.
 *
 * Thin wrapper around the guide store factory for the passthrough tier.
 * Two trigger contexts: Navigator toggle (respectDismiss=true) and
 * PassthroughView help button (respectDismiss=false, always opens).
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import { createGuideStore } from './guide-factory.svelte';

/** Number of steps in the passthrough guide. Single source of truth — imported by the component. */
export const STEP_COUNT = 6;

export const passthroughGuide = createGuideStore('synthesis:passthrough_guide_dismissed', STEP_COUNT);
