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
      title: 'MCP capability detection',
      description:
        'The bridge extension connects to the MCP server and declares sampling capability. ASGI middleware intercepts the initialize handshake and activates the sampling tier. Auto-reconnects on server restart via 10s health check.',
      detail: 'VS Code bridge: StreamableHTTP transport, roots/list for workspace context',
      accent: 'green',
    },
    {
      number: 2,
      title: 'Prompt enters the pipeline',
      description:
        'The 3-phase pipeline (analyze → optimize → score) runs through the IDE\'s LLM via MCP sampling/createMessage. No backend API key required — uses the IDE\'s model subscription directly.',
      detail: 'Model ID captured per phase and displayed in real time',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Context enrichment',
      description:
        'Workspace guidance (CLAUDE.md, README, entry points), codebase index, adaptation state, taxonomy patterns, and strategy instructions are injected into every phase. Same enrichment as internal mode.',
      detail: 'Deep scanning: README.md + entry points + architecture docs + guidance files',
      accent: 'green',
    },
    {
      number: 4,
      title: 'Structured output fallback',
      description:
        'Each phase requests structured JSON via MCP tool calling. If the IDE rejects tools (McpError), the pipeline injects the JSON schema directly into the user message and parses the response with brace-depth extraction.',
      detail: 'Scoring capped at 1024 tokens with JSON terminal directive',
      accent: 'cyan',
    },
    {
      number: 5,
      title: 'Tier degradation',
      description:
        'If the MCP connection drops or a phase times out, the system falls back to the internal provider (CLI or API) without data loss. Sampling auto-restores when the bridge reconnects.',
      detail: 'Taxonomy, history, adaptation, and feedback persist across tier changes',
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
  whyText="The IDE's LLM runs the full 3-phase pipeline (analyze, optimize, score) via MCP sampling. No backend provider or API key needed — uses your IDE subscription directly. Hybrid scoring blends the IDE LLM's evaluation with model-independent heuristics. Workspace context, taxonomy patterns, and adaptation state are injected identically to internal mode."
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
