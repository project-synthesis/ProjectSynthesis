<script lang="ts">
  /**
   * Internal Provider guide — thin wrapper around TierGuide.
   *
   * Provides the cyan-themed internal workflow content (5 steps) and
   * feature comparison matrix to the generic TierGuide modal shell.
   * Adapts step content based on the active sub-tier (CLI vs API).
   *
   * Copyright 2025-2026 Project Synthesis contributors.
   */
  import TierGuide from './TierGuide.svelte';
  import { internalGuide, STEP_COUNT } from '$lib/stores/internal-guide.svelte';
  import type { GuideStep } from '$lib/types/tier-guide';
  import { TIER_COMPARISON } from '$lib/types/tier-guide';

  interface Props {
    provider?: string | null;
  }

  let { provider = null }: Props = $props();

  const isCli = $derived(provider?.toLowerCase().includes('cli') ?? false);

  const STEPS: GuideStep[] = $derived.by(() => [
    {
      number: 1,
      title: 'Provider detected',
      description: isCli
        ? 'Claude CLI detected on PATH. Your Max subscription powers the full pipeline at zero marginal cost — no API key or per-token billing required.'
        : 'Anthropic API key configured. Direct API access with prompt caching and streaming. Per-token billing applies.',
      detail: isCli
        ? 'CLI subprocess with OS sandbox. Supports --effort for thinking control.'
        : 'SDK client with automatic retries, prompt caching (cache_control: ephemeral), and streaming via messages.stream().',
      accent: 'cyan',
    },
    {
      number: 2,
      title: 'Full 3-phase pipeline',
      description:
        'Analyze classifies your prompt and detects weaknesses. Optimize rewrites using the selected strategy. Score evaluates 5 dimensions independently. Each phase runs server-side via the detected provider.',
      detail: 'Phases execute sequentially with fresh context windows. Optimizer streams to prevent timeouts on long outputs.',
      accent: 'green',
    },
    {
      number: 3,
      title: 'Streaming with real-time feedback',
      description:
        'SSE events show each phase as it progresses. The model used per phase is captured and displayed live. Analysis, optimization, and scoring results appear incrementally.',
      detail: 'Per-phase effort configurable (low / medium / high / max) in the Navigator panel.',
      accent: 'cyan',
    },
    {
      number: 4,
      title: 'All features enabled',
      description:
        'Hybrid scoring (LLM + heuristic blend), taxonomy clustering, strategy adaptation, refinement sessions, suggestion generation, and intent drift detection — all active in internal mode.',
      detail: 'Lean mode available: disable scoring + explore for 2 LLM calls only.',
      accent: 'green',
    },
    {
      number: 5,
      title: 'Codebase explore + pattern injection',
      description:
        'Link a GitHub repo for semantic codebase context — file outlines, domain boosting, and token-conscious retrieval. Meta-patterns from your prompt library auto-inject into the optimizer.',
      detail: 'Explore uses all-MiniLM-L6-v2 embeddings with SHA-based result caching.',
      accent: 'cyan',
    },
  ]);

  const whyText = $derived(
    isCli
      ? 'Full pipeline powered by your Claude Max subscription at zero marginal cost. The CLI handles authentication, sandboxing, and model selection — just install it and go. All features enabled: scoring, taxonomy, adaptation, refinement, explore, and pattern injection.'
      : 'Full pipeline powered by direct Anthropic API access. Prompt caching reduces cost on repeated patterns. Streaming prevents timeouts on long Opus outputs. All features enabled: scoring, taxonomy, adaptation, refinement, explore, and pattern injection.',
  );

  // Dev-mode guard — inside $effect because STEPS is $derived (reactive)
  $effect(() => {
    if (import.meta.env.DEV && STEPS.length !== STEP_COUNT) {
      console.error(`InternalGuide: STEPS.length (${STEPS.length}) !== STEP_COUNT (${STEP_COUNT})`);
    }
  });
</script>

<TierGuide
  title="INTERNAL PROVIDER"
  ariaLabel="Internal provider workflow guide"
  accentColor="var(--color-neon-cyan)"
  whyTitle="WHY INTERNAL"
  {whyText}
  steps={STEPS}
  comparison={TIER_COMPARISON}
  highlightColumn="internal"
  open={internalGuide.open}
  activeStep={internalGuide.activeStep}
  onclose={(dismissed) => dismissed ? internalGuide.dismiss() : internalGuide.close()}
  onsetstep={(i) => internalGuide.setStep(i)}
  onnextstep={() => internalGuide.nextStep()}
  onprevstep={() => internalGuide.prevStep()}
/>
