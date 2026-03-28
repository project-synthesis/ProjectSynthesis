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
      title: 'Prompt assembled',
      description:
        'Strategy, workspace context, patterns, and scoring rubric are assembled into one instruction.',
      detail: '',
      accent: 'yellow',
    },
    {
      number: 2,
      title: 'Copy the assembled prompt',
      description: 'Click COPY to grab the full assembled prompt.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Paste into any LLM',
      description:
        'ChatGPT, Claude.ai, Gemini, or any LLM — paste and submit.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 4,
      title: 'Copy the response',
      description: "Copy the optimized prompt from your LLM's output.",
      detail: '',
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Paste result back',
      description: 'Paste into the result area and click SAVE.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 6,
      title: 'Scored and saved',
      description:
        'The system scores 5 quality dimensions, generates improvement suggestions, and saves to history and taxonomy.',
      detail: '',
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
  whyText="No API key or CLI needed. The system assembles the prompt — you run it through any LLM and paste the result back."
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
