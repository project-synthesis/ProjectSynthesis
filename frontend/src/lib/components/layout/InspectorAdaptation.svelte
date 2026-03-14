<script lang="ts">
  import { feedback } from '$lib/stores/feedback.svelte';

  // Weight-to-Tailwind class helper for bar fills
  function getScoreBarClass(weight: number): string {
    const score = weight * 40; // normalize weight (0-0.25) to score-like range (0-10)
    if (score >= 9) return 'bg-neon-green/15 border border-neon-green/30';
    if (score >= 7) return 'bg-neon-cyan/15 border border-neon-cyan/30';
    if (score >= 4) return 'bg-neon-yellow/15 border border-neon-yellow/30';
    return 'bg-neon-red/15 border border-neon-red/30';
  }

  // Keys match backend adaptation_engine output: full `_score` suffixed keys
  const DEFAULT_WEIGHTS: Record<string, number> = {
    clarity_score: 0.20,
    specificity_score: 0.20,
    structure_score: 0.15,
    faithfulness_score: 0.25,
    conciseness_score: 0.20,
  };

  // Display-friendly short labels
  const DIM_LABELS: Record<string, string> = {
    clarity_score: 'clarity',
    specificity_score: 'specificity',
    structure_score: 'structure',
    faithfulness_score: 'faithfulness',
    conciseness_score: 'conciseness',
  };

  // Issue short labels
  const ISSUE_SHORT: Record<string, string> = {
    lost_key_terms: 'Term preservation',
    changed_meaning: 'Meaning fidelity',
    hallucinated_content: 'Addition prevention',
    lost_examples: 'Example preservation',
    too_verbose: 'Conciseness enforcement',
    too_vague: 'Specificity protection',
    wrong_tone: 'Tone matching',
    broken_structure: 'Structure preservation',
  };

  let showTechnicalDetails = $state(false);

  let state = $derived(feedback.adaptationState);
  let summary = $derived(feedback.adaptationSummary);

  // Load adaptation data on mount
  $effect(() => {
    feedback.loadAdaptationSummary();
    feedback.loadAdaptationState();
  });

  // Priority bar max: use the highest weight to normalize bars
  let maxWeight = $derived.by(() => {
    if (!summary?.priorities?.length) return 0.3;
    return Math.max(...summary.priorities.map((p) => p.weight), 0.3);
  });
</script>

<div class="space-y-3">
  <h3 class="font-display text-[12px] font-bold uppercase text-text-dim">Adaptation</h3>

  {#if !state && !summary}
    <p class="text-xs text-text-dim">No adaptation data.</p>
  {:else}
    <!-- Priority bar chart: 5-column grid showing relative dimension weights -->
    <div class="space-y-2">
      <p class="text-[10px] text-text-dim uppercase font-mono">Dimension Priorities</p>
      <div class="grid grid-cols-5 gap-1">
        {#each Object.keys(DEFAULT_WEIGHTS) as dim}
          {@const liveW = state?.dimensionWeights?.[dim] ?? DEFAULT_WEIGHTS[dim]}
          {@const pct = Math.round((liveW / maxWeight) * 100)}
          {@const shift = summary?.priorities?.find((p) => p.dimension === dim)}
          <div class="flex flex-col items-center gap-0.5">
            <div class="w-full bg-bg-primary relative" style="height: 40px;">
              <div
                class="absolute bottom-0 w-full transition-all {getScoreBarClass(liveW)}"
                style="height: {pct}%;"
              ></div>
            </div>
            <span class="text-[8px] font-mono text-text-dim text-center leading-tight">
              {(DIM_LABELS[dim] ?? dim).slice(0, 5)}
            </span>
            {#if shift}
              <span class="text-[8px] font-mono {shift.direction === 'up' ? 'text-neon-green' : 'text-neon-red'}">
                {shift.direction === 'up' ? '+' : ''}{(shift.shift * 100).toFixed(0)}%
              </span>
            {/if}
          </div>
        {/each}
      </div>
    </div>

    <!-- Active guardrails -->
    {#if summary && summary.activeGuardrails.length > 0}
      <div class="space-y-1.5">
        <p class="text-[10px] text-text-dim uppercase font-mono">Active Guardrails</p>
        <div class="space-y-0.5">
          {#each summary.activeGuardrails as guardrailId}
            {@const label = ISSUE_SHORT[guardrailId] ?? guardrailId}
            {@const count = summary.issueResolution[guardrailId] ?? 0}
            <div class="flex items-center justify-between p-1 bg-bg-card border border-border-subtle">
              <span class="text-[10px] font-mono text-neon-yellow/80">{label}</span>
              <span class="text-[9px] font-mono text-text-dim">{count}x</span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Issue resolution tracking -->
    {#if summary && Object.keys(summary.issueResolution).length > 0}
      <div class="space-y-1.5">
        <p class="text-[10px] text-text-dim uppercase font-mono">Issue History</p>
        <div class="space-y-0.5">
          {#each Object.entries(summary.issueResolution) as [issueId, count]}
            {@const label = ISSUE_SHORT[issueId] ?? issueId}
            {@const isActive = summary.activeGuardrails.includes(issueId)}
            <div class="flex items-center justify-between px-1 py-0.5">
              <span class="text-[9px] font-mono {isActive ? 'text-neon-yellow/70' : 'text-text-dim'}">
                {label}
              </span>
              <span class="text-[9px] font-mono text-text-dim">
                {isActive ? 'monitoring' : 'resolved'} ({count})
              </span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Framework intelligence -->
    {#if summary && summary.topFrameworks.length > 0}
      <div class="space-y-1.5">
        <p class="text-[10px] text-text-dim uppercase font-mono">Framework Preferences</p>
        <div class="space-y-0.5">
          {#each summary.topFrameworks as fw, i}
            {@const ratio = summary.frameworkPreferences[fw] ?? 0}
            <div class="grid grid-cols-[16px_1fr_auto] items-center gap-1 p-1 bg-bg-card border border-border-subtle">
              <span class="text-[9px] font-mono text-neon-green">
                {i === 0 ? '\u2191' : '\u2192'}
              </span>
              <span class="text-[10px] font-mono text-text-primary truncate">{fw}</span>
              <span class="text-[9px] font-mono text-text-dim">
                {ratio > 0 ? '+' : ''}{ratio.toFixed(0)}
              </span>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Strategy affinities (existing, more detailed) -->
    {#if state?.strategyAffinities && Object.keys(state.strategyAffinities).length > 0}
      <div class="space-y-1.5">
        <p class="text-[10px] text-text-dim uppercase font-mono">Strategy Affinities</p>
        {#each Object.entries(state.strategyAffinities) as [taskType, affinity]}
          {@const aff = affinity as { preferred?: string[]; avoid?: string[] } | null}
          <div class="p-1.5 bg-bg-card border border-border-subtle space-y-1">
            <span class="font-mono text-[10px] text-text-secondary capitalize">{taskType}</span>
            {#if aff?.preferred && aff.preferred.length > 0}
              <div class="flex flex-wrap gap-1">
                {#each aff.preferred as fw}
                  <span class="border border-neon-green/50 text-neon-green text-[9px] font-mono px-1 py-0">{fw}</span>
                {/each}
              </div>
            {/if}
            {#if aff?.avoid && aff.avoid.length > 0}
              <div class="flex flex-wrap gap-1">
                {#each aff.avoid as fw}
                  <span class="border border-neon-red/50 text-neon-red text-[9px] font-mono px-1 py-0 line-through">{fw}</span>
                {/each}
              </div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}

    <!-- Quality threshold -->
    <div class="space-y-1.5">
      <p class="text-[10px] text-text-dim uppercase font-mono">Quality Threshold</p>
      <div class="flex items-center gap-2">
        <span class="font-mono text-sm text-text-primary">
          {(summary?.retryThreshold ?? state?.retryThreshold ?? 5.0).toFixed(1)}
        </span>
        <div class="flex-1 relative h-1 bg-bg-primary">
          <div
            class="absolute top-0 h-full w-px bg-neon-cyan"
            style="left: {(((summary?.retryThreshold ?? state?.retryThreshold ?? 5.0) - 3.0) / 5.0) * 100}%;"
          ></div>
        </div>
      </div>
      <div class="flex justify-between text-[9px] font-mono text-text-dim">
        <span>3.0</span>
        <span>8.0</span>
      </div>
    </div>

    <!-- Meta -->
    <div class="space-y-1 text-[10px] font-mono">
      <div class="flex justify-between">
        <span class="text-text-dim">Feedback count</span>
        <span class="text-text-primary">
          {summary?.feedbackCount ?? state?.feedbackCount ?? 0}
        </span>
      </div>
    </div>

    <!-- L3 Technical Details -->
    <button
      class="w-full text-left text-[10px] font-mono text-text-dim hover:text-neon-cyan/70
             border border-border-subtle p-1.5 transition-colors"
      onclick={() => { showTechnicalDetails = !showTechnicalDetails; }}
    >
      {showTechnicalDetails ? '\u25B4' : '\u25BE'} Technical Details
    </button>
    {#if showTechnicalDetails && state}
      <div class="p-1.5 bg-bg-primary border border-border-subtle text-[9px] font-mono text-text-dim space-y-1">
        <div>Retry threshold: {state.retryThreshold.toFixed(2)}</div>
        {#if state.dimensionWeights}
          <div>Weights: {JSON.stringify(
            Object.fromEntries(
              Object.entries(state.dimensionWeights).map(([k, v]) => [k.replace('_score', ''), (v as number).toFixed(3)])
            )
          )}</div>
        {/if}
        <div>Feedback count: {state.feedbackCount}</div>
      </div>
    {/if}
  {/if}
</div>
