<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import { getStrategyHex } from '$lib/utils/strategy';
  import { getScoreColor } from '$lib/utils/colors';
  import StageCard from './StageCard.svelte';
  import StageExplore from './StageExplore.svelte';
  import StageAnalyze from './StageAnalyze.svelte';
  import StageStrategy from './StageStrategy.svelte';
  import StageOptimize from './StageOptimize.svelte';
  import StageValidate from './StageValidate.svelte';

  const stageLabels: Record<string, string> = {
    explore: 'Explore',
    analyze: 'Analyze',
    strategy: 'Strategy',
    optimize: 'Optimize',
    validate: 'Validate'
  };

  const stageIcons: Record<string, string> = {
    explore: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z',
    analyze: 'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
    strategy: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2',
    optimize: 'M13 10V3L4 14h7v7l9-11h-7z',
    validate: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z'
  };

  // Stage-specific left accent colors per spec
  const stageBaseColors: Record<string, string> = {
    explore: '#a855f7',   // neon-purple
    analyze: '#4d8eff',   // neon-blue
    strategy: '#00e5ff',  // default neon-cyan; replaced by strategy chromatic color at runtime
    optimize: '#00e5ff',  // neon-cyan (primary brand = transformation)
    validate: '#00e5ff'   // will be overridden by score-mapped color
  };

  function getStageColor(stage: string): string {
    if (stage === 'strategy') {
      return getStrategyHex(forge.stageResults?.strategy?.data?.primary_framework as string | undefined);
    }
    if (stage === 'validate') {
      const score = forge.overallScore;
      if (score != null) return getScoreColor(score);
      return '#00e5ff';
    }
    return stageBaseColors[stage] || '#00e5ff';
  }

  let visibleStages = $derived(forge.visibleStages);

  // Pre-initialize all stage keys to false so bind:expanded never receives
  // undefined on first render. The $effect below overrides these values once
  // it runs (after first paint), but the binding must be valid from the start.
  let expandedStages = $state<Record<string, boolean>>(
    Object.fromEntries(forge.stages.map(s => [s, false]))
  );

  $effect(() => {
    const current = forge.currentStage;
    const statuses = forge.stageStatuses;

    for (const stage of forge.stages) {
      const st = statuses[stage];
      if (stage === current && (st === 'running' || st === 'idle')) {
        // Auto-expand the active/running stage
        expandedStages[stage] = true;
      } else if (st === 'done' && expandedStages[stage] === undefined) {
        // Collapse completed stages on first encounter (user hasn't touched it yet)
        expandedStages[stage] = false;
      } else if (st === 'idle' && stage !== current) {
        // Collapse idle non-current stages — handles forge reset between runs
        expandedStages[stage] = false;
      }
    }
  });
</script>

<div class="space-y-1" aria-live="polite" aria-label="Pipeline stages">
  {#each visibleStages as stage, i (stage)}
    {@const status = forge.stageStatuses[stage]}
    {@const result = forge.stageResults[stage]}
    <StageCard
      name={stageLabels[stage]}
      icon={stageIcons[stage]}
      {status}
      index={i}
      isActive={forge.currentStage === stage}
      duration={result?.duration}
      model={result?.data?.model as string | undefined}
      tokenCount={result?.tokenCount}
      stageColor={getStageColor(stage)}
      bind:expanded={expandedStages[stage]}
    >
      {#if stage === 'explore'}
        <StageExplore />
      {:else if stage === 'analyze'}
        <StageAnalyze />
      {:else if stage === 'strategy'}
        <StageStrategy />
      {:else if stage === 'optimize'}
        <StageOptimize />
      {:else if stage === 'validate'}
        <StageValidate />
      {/if}
    </StageCard>
  {/each}
</div>
