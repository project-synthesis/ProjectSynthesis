<script lang="ts">
  /**
   * Pattern Graph guide — teaches the diegetic UI interactions.
   *
   * Tier-aware accent color matches the user's current routing tier.
   * Triggered on first Pattern Graph visit (respects dismissal) and
   * via the ? help button in ambient telemetry (always opens).
   *
   * Copyright 2025-2026 Project Synthesis contributors.
   */
  import TierGuide from './TierGuide.svelte';
  import { patternGraphGuide, STEP_COUNT } from '$lib/stores/pattern-graph-guide.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import type { GuideStep } from '$lib/types/tier-guide';
  import { TIER_COMPARISON } from '$lib/types/tier-guide';

  const STEPS: GuideStep[] = [
    {
      number: 1,
      title: 'Navigate the graph',
      description:
        'Click and drag to orbit. Scroll to zoom. Each node is a prompt cluster — size shows member count, color shows domain, wireframe density shows coherence.',
      detail: 'The graph is a 3D UMAP projection of your taxonomy. Similar clusters are positioned near each other. Domain nodes (larger, pentagonal) anchor their child clusters.',
      accent: 'cyan',
    },
    {
      number: 2,
      title: 'Inspect clusters',
      description:
        'Click any node to see its details in the Inspector panel on the right. The sidebar automatically switches to show the selected cluster.',
      detail: 'The state filter tabs (ALL/ACT/CAN/MAT/TPL/ARC) dim non-matching nodes in the graph. Matching nodes glow at full opacity while others fade to 25%.',
      accent: 'cyan',
    },
    {
      number: 3,
      title: 'Access controls',
      description:
        'Move your mouse to the right edge of the graph to reveal the control panel. Toggle similarity/injection edge layers, seed new prompts, recluster, or open the activity feed.',
      detail: 'Controls auto-hide 2 seconds after your mouse leaves. Click the graph background or press Escape to dismiss immediately.',
      accent: 'green',
    },
    {
      number: 4,
      title: 'View metrics & search',
      description:
        'Press Q to toggle the full metrics panel showing taxonomy health, quality scores, and trends. Press Ctrl+F to search for specific clusters.',
      detail: 'The metrics panel shows different information depending on context: system health when nothing is selected, cluster details when a node is clicked, or domain aggregates for domain nodes.',
      accent: 'green',
    },
  ];

  if (import.meta.env.DEV && STEPS.length !== STEP_COUNT) {
    console.error(`PatternGraphGuide: STEPS.length (${STEPS.length}) !== STEP_COUNT (${STEP_COUNT})`);
  }
</script>

<TierGuide
  title="PATTERN GRAPH"
  ariaLabel="Pattern Graph interaction guide"
  accentColor={routing.tierColor}
  whyTitle="IMMERSIVE VISUALIZATION"
  whyText="The graph IS the interface. Nodes encode data through size, color, and wireframe density. Controls appear only when you need them — move to the right edge or press keyboard shortcuts."
  steps={STEPS}
  comparison={TIER_COMPARISON}
  highlightColumn={routing.tier === 'sampling' ? 'sampling' : routing.tier === 'passthrough' ? 'passthrough' : 'internal'}
  open={patternGraphGuide.open}
  activeStep={patternGraphGuide.activeStep}
  onclose={(dismissed) => dismissed ? patternGraphGuide.dismiss() : patternGraphGuide.close()}
  onsetstep={(i) => patternGraphGuide.setStep(i)}
  onnextstep={() => patternGraphGuide.nextStep()}
  onprevstep={() => patternGraphGuide.prevStep()}
/>
