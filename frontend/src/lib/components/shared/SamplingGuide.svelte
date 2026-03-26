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
      title: 'IDE configures MCP sampling',
      description:
        'Your IDE interfaces with the Project Synthesis MCP server automatically. The system dynamically detects network capabilities to ensure sampling is supported. (*Note: Currently, only VS Code fully supports this pipeline*)',
      detail: 'Connection detected via ASGI middleware on initialization handshake',
      accent: 'green',
    },
    {
      number: 2,
      title: 'LLM self-enhancement',
      description:
        'You submit a base prompt. Instead of making standard backend API requests, the server commands your IDE\'s LLM to reflect on and rewrite the prompt. The AI essentially refines the instructions it will eventually execute.',
      detail: 'Bypasses external API keys by utilizing your existing IDE subscription (e.g., Copilot)',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Deep context injection',
      description:
        'Despite running remotely through the IDE viewport, the server still meticulously injects structured parameters, taxonomy strategy definitions, and vast workspace context right before the LLM begins rewriting.',
      detail: 'Full feature parity with internal modes: Analyze → Optimize → Score',
      accent: 'green',
    },
    {
      number: 4,
      title: 'Structured validation fallback',
      description:
        'The IDE\'s LLM receives strict tool-calling procedures to ensure generated outputs are wrapped in perfectly typed JSON schemas. If tools are natively unsupported by the IDE, the pipeline engages a bulletproof markdown-parsing fallback constraint.',
      detail: 'Avoids syntax degeneration via schema constraint wrappers',
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Zero-friction capability fallback',
      description:
        'Because IDE LLM instances occasionally time out under heavy processing loads, the system constantly monitors execution. If an operation stalls, it seamlessly drops back down to your internal provider without losing data.',
      detail: 'Automatically restores the pipeline the moment your MCP tunnel clears',
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
  whyTitle="WHY USE THIS MODE?"
  whyText="Harness your IDE's built-in LLM to power the entire 3-phase optimization pipeline completely free of backend API costs. This mode turns the LLM onto itself—having the model analyze, critique, and deeply enhance its own prompt in a closed loop before execution. (Note: Currently, only VS Code fully supports MCP sampling capabilities.)"
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
