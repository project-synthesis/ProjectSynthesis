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
      title: 'IDE connects via MCP',
      description:
        'Your IDE (VS Code, Cursor) connects to the MCP server automatically. The system detects sampling capability from the initialize handshake — no manual setup required.',
      detail: 'Connection detected via ASGI middleware on initialize message',
      accent: 'green',
    },
    {
      number: 2,
      title: 'You enter a prompt',
      description:
        'Type or paste your prompt in the editor. The system routes to the IDE\'s LLM instead of the backend provider. Model Hints and Effort Hints steer the IDE\'s model selection.',
      detail: 'Hints are advisory — the IDE has final say on which model to use',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Pipeline runs through IDE',
      description:
        'All three phases (analyze, optimize, score) execute via MCP sampling. Strategy, patterns, adaptation context, and codebase guidance are injected automatically — same enrichment as internal mode.',
      detail: 'Structured output via tool calling ensures typed JSON responses',
      accent: 'green',
    },
    {
      number: 4,
      title: 'Results appear in the UI',
      description:
        'Optimized prompt, scores, and suggestions display identically to internal mode. All results persist to history, taxonomy, and adaptation. The actual model used by the IDE is captured per phase.',
      detail: 'Taxonomy clustering, feedback, and refinement all work the same',
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Auto-fallback if IDE is idle',
      description:
        'If the MCP connection goes idle, the system seamlessly uses the internal provider (CLI or API). Sampling auto-restores when the IDE reconnects — no manual intervention required.',
      detail: 'No data loss — taxonomy, history, and adaptation continue working',
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
  title="MCP SAMPLING PROTOCOL"
  ariaLabel="MCP sampling workflow guide"
  accentColor="var(--color-neon-green)"
  whyTitle="WHY SAMPLING"
  whyText="Your IDE's LLM powers the entire optimization pipeline via MCP sampling. Full 3-phase pipeline (analyze, optimize, score) runs through the IDE — no backend provider or API key needed. Model and effort preferences are transmitted as hints; the IDE has final say on model selection."
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
