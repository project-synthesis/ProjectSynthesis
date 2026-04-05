/**
 * Pattern Graph Guide modal state.
 *
 * Shows keyboard shortcuts and interaction hints for the diegetic UI.
 * Triggered on first visit to Pattern Graph tab (respects dismissal)
 * and via the ? help button (always opens).
 *
 * Copyright 2025-2026 Project Synthesis contributors.
 */

import { createGuideStore } from './guide-factory.svelte';

export const STEP_COUNT = 4;

export const patternGraphGuide = createGuideStore('synthesis:pattern_graph_guide_dismissed', STEP_COUNT);
