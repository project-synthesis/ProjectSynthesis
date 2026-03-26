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
        ? 'Claude CLI detected. Your subscription powers the full pipeline at zero marginal cost — no API key needed.'
        : 'Anthropic API key configured. Direct access with prompt caching and streaming.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 2,
      title: 'Full 3-phase pipeline',
      description:
        'Analyze detects weaknesses. Optimize rewrites using the selected strategy. Score evaluates 5 quality dimensions independently.',
      detail: '',
      accent: 'green',
    },
    {
      number: 3,
      title: 'Real-time progress',
      description:
        'Each phase streams results as it completes. Model selection per phase is configurable in Settings.',
      detail: '',
      accent: 'cyan',
    },
    {
      number: 4,
      title: 'All features enabled',
      description:
        'Hybrid scoring, taxonomy clustering, strategy adaptation, refinement, suggestions, and codebase explore — all active.',
      detail: '',
      accent: 'green',
    },
    {
      number: 5,
      title: 'Codebase context + patterns',
      description:
        'Link a GitHub repo for codebase-aware optimization. Proven patterns from your prompt library auto-inject into the optimizer.',
      detail: '',
      accent: 'cyan',
    },
  ]);

  const whyText = $derived(
    isCli
      ? 'Full pipeline at zero marginal cost via Claude CLI. All features enabled.'
      : 'Full pipeline via Anthropic API with prompt caching. All features enabled.',
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
