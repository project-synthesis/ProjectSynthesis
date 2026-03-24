<script lang="ts">
  /**
   * Passthrough Protocol guide — thin wrapper around TierGuide.
   *
   * Provides the yellow-themed passthrough workflow content (6 steps) and
   * feature comparison matrix to the generic TierGuide modal shell.
   *
   * Copyright 2025-2026 Project Synthesis contributors.
   */
  import TierGuide from './TierGuide.svelte';
  import { passthroughGuide, STEP_COUNT } from '$lib/stores/passthrough-guide.svelte';
  import type { GuideStep } from '$lib/types/tier-guide';
  import { TIER_COMPARISON } from '$lib/types/tier-guide';

  const STEPS: GuideStep[] = [
    {
      number: 1,
      title: 'System assembles your prompt',
      description:
        'Strategy template, scoring rubric, workspace context, codebase context, applied patterns, and adaptation state are assembled into a single optimized instruction.',
      detail: 'All context enrichment happens server-side. The assembled prompt appears in the editor.',
      accent: 'yellow',
    },
    {
      number: 2,
      title: 'Copy the assembled prompt',
      description: 'Click COPY or select all text from the assembled prompt panel.',
      detail: 'The full prompt is designed to work with any instruction-following LLM.',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Paste into your LLM',
      description:
        'Open ChatGPT, Claude.ai, Gemini, or any LLM interface and submit the assembled prompt.',
      detail: 'Strategy and rubric are embedded — the LLM receives full optimization instructions.',
      accent: 'cyan',
    },
    {
      number: 4,
      title: 'Copy the LLM response',
      description: "Copy the optimized prompt text from your LLM's output.",
      detail: "Only the optimized prompt text — not the LLM's preamble or commentary.",
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Paste result back',
      description: 'Paste into the OPTIMIZED RESULT textarea and click SAVE.',
      detail: 'Optional: add a changes summary to track what the LLM modified.',
      accent: 'cyan',
    },
    {
      number: 6,
      title: 'System scores and persists',
      description:
        'Heuristic scoring evaluates 5 dimensions. Result enters the taxonomy engine and history.',
      detail: 'Hybrid blending applies when historical data exists. Scores feed strategy adaptation.',
      accent: 'green',
    },
  ];

  // empty line removed — COMPARISON extracted to TIER_COMPARISON

  // Dev-mode guard
  if (import.meta.env.DEV && STEPS.length !== STEP_COUNT) {
    console.error(`PassthroughGuide: STEPS.length (${STEPS.length}) !== STEP_COUNT (${STEP_COUNT})`);
  }
</script>

<TierGuide
  title="PASSTHROUGH PROTOCOL"
  ariaLabel="Passthrough workflow guide"
  accentColor="var(--color-neon-yellow)"
  whyTitle="WHY PASSTHROUGH"
  whyText="Zero-dependency fallback. No API key, no CLI, no MCP client required. The system assembles a rich optimization prompt — you run it through whatever LLM you have access to, then paste the result back. Scores, taxonomy, and adaptation all still work."
  steps={STEPS}
  comparison={TIER_COMPARISON}
  highlightColumn="passthrough"
  open={passthroughGuide.open}
  activeStep={passthroughGuide.activeStep}
  onclose={(dismissed) => dismissed ? passthroughGuide.dismiss() : passthroughGuide.close()}
  onsetstep={(i) => passthroughGuide.setStep(i)}
  onnextstep={() => passthroughGuide.nextStep()}
  onprevstep={() => passthroughGuide.prevStep()}
/>
