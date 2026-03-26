<script lang="ts">
  /**
   * MCP Sampling Protocol guide — thin wrapper around TierGuide.
   *
   * Provides the green-themed MCP sampling workflow content (5 steps) and
   * feature comparison matrix to the generic TierGuide modal shell.
   *
   * Copyright 2025-2026 Project Synthesis contributors.
   */
  import TierGuide from './TierGuide.svelte';
  import { samplingGuide, STEP_COUNT } from '$lib/stores/sampling-guide.svelte';
  import type { GuideStep } from '$lib/types/tier-guide';
  import { TIER_COMPARISON } from '$lib/types/tier-guide';

  const STEPS: GuideStep[] = [
    {
      number: 1,
      title: 'IDE connects',
      description:
        'The MCP bridge detects your IDE and activates the sampling tier automatically. Auto-reconnects on restart.',
      detail: '',
      accent: 'green',
    },
    {
      number: 2,
      title: 'Pipeline runs through your IDE',
      description:
        'All 3 phases (analyze → optimize → score) run through your IDE\'s LLM. No API key needed — uses your IDE subscription.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Full context injected',
      description:
        'Workspace guidance, codebase context, strategy, and patterns are injected into every phase — same enrichment as internal mode.',
      detail: '',
      accent: 'green',
    },
    {
      number: 4,
      title: 'Hybrid scoring',
      description:
        'Your IDE\'s LLM scores the result, then the system blends it with model-independent heuristics for calibrated quality metrics.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Auto-fallback',
      description:
        'If the IDE disconnects, the system falls back to the internal provider without data loss. Sampling restores when the IDE reconnects.',
      detail: '',
      accent: 'green',
    },
  ];

  // COMPARISON extracted to shared TIER_COMPARISON

  // Dev-mode guard
  if (import.meta.env.DEV && STEPS.length !== STEP_COUNT) {
    console.error(`SamplingGuide: STEPS.length (${STEPS.length}) !== STEP_COUNT (${STEP_COUNT})`);
  }
</script>

<TierGuide
  title="MCP SAMPLING PIPELINE"
  ariaLabel="MCP sampling workflow guide"
  accentColor="var(--color-neon-green)"
  whyTitle="WHY SAMPLING"
  whyText="Your IDE's LLM runs the full pipeline via MCP sampling — no API key needed. Requires the MCP Copilot Bridge extension in VS Code."
  steps={STEPS}
  comparison={TIER_COMPARISON}
  highlightColumn="sampling"
  open={samplingGuide.open}
  activeStep={samplingGuide.activeStep}
  onclose={(dismissed) => dismissed ? samplingGuide.dismiss() : samplingGuide.close()}
  onsetstep={(i) => samplingGuide.setStep(i)}
  onnextstep={() => samplingGuide.nextStep()}
  onprevstep={() => samplingGuide.prevStep()}
/>
