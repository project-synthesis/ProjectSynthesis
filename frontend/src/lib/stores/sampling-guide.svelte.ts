/**
 * Sampling Guide modal state.
 *
 * Thin wrapper around the guide store factory for the MCP sampling tier.
 * Triggered by the Navigator "Force IDE sampling" toggle (respectDismiss=true).
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import { createGuideStore } from './guide-factory.svelte';

/** Number of steps in the sampling guide. Single source of truth — imported by the component. */
export const STEP_COUNT = 5;

export const samplingGuide = createGuideStore('synthesis:sampling_guide_dismissed', STEP_COUNT);
