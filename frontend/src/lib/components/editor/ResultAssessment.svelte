<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';

  let { assessment }: {
    assessment: {
      verdict: string;
      confidence: string;
      headline: string;
      dimension_insights: Array<{
        dimension: string;
        score: number;
        weight: number;
        label: string;
        assessment: string;
        is_weak: boolean;
        is_strong: boolean;
        delta_from_previous: number | null;
        framework_avg: number | null;
        user_priority: string;
      }>;
      trade_offs: Array<{
        gained_dimension: string;
        lost_dimension: string;
        gained_delta: number;
        lost_delta: number;
        is_typical_for_framework: boolean;
        description: string;
      }>;
      retry_journey: {
        total_attempts: number;
        best_attempt: number;
        score_trajectory: number[];
        gate_sequence: string[];
        momentum_trend: string;
        summary: string;
      };
      framework_fit: {
        framework: string;
        task_type: string;
        fit_score: number;
        fit_label: string;
        user_rating_avg: number | null;
        sample_count: number;
        alternatives: string[];
        recommendation: string;
      } | null;
      improvement_signals: Array<{
        dimension: string;
        current_score: number;
        potential_gain: number;
        elasticity: number;
        effort_label: string;
        suggestion: string;
      }>;
      next_actions: Array<{
        action: string;
        rationale: string;
        priority: string;
        category: string;
      }>;
    };
  } = $props();

  // Expand/collapse states for progressive disclosure
  let expandedL1 = $state(false);
  let expandedDimension = $state<string | null>(null);

  // Verdict Tailwind class maps
  const verdictClasses: Record<string, string> = {
    strong: 'text-neon-green',
    solid: 'text-neon-cyan',
    mixed: 'text-neon-yellow',
    weak: 'text-neon-red',
  };

  const verdictBorderColors: Record<string, string> = {
    strong: 'border-neon-green',
    solid: 'border-neon-cyan',
    mixed: 'border-neon-yellow',
    weak: 'border-neon-red',
  };

  const confidenceLabels: Record<string, string> = {
    high: 'HIGH',
    medium: 'MED',
    low: 'LOW',
  };

  // Score-to-Tailwind-class helper
  function getScoreClass(score: number): string {
    if (score >= 9) return 'text-neon-green border-neon-green/20';
    if (score >= 7) return 'text-neon-cyan border-neon-cyan/20';
    if (score >= 4) return 'text-neon-yellow border-neon-yellow/20';
    return 'text-neon-red border-neon-red/20';
  }

  let overallScore = $derived(forge.overallScore ?? 0);
  let verdictClass = $derived(verdictClasses[assessment.verdict] ?? 'text-neon-yellow');
  let verdictBorder = $derived(verdictBorderColors[assessment.verdict] ?? 'border-neon-yellow');

  // Retry sparkline: max score for normalizing bar heights
  let sparklineMax = $derived(
    Math.max(...(assessment.retry_journey.score_trajectory.length > 0
      ? assessment.retry_journey.score_trajectory
      : [10]))
  );

  // Sorted insights by user weight descending
  let sortedInsights = $derived(
    [...assessment.dimension_insights].sort((a, b) => b.weight - a.weight)
  );

  // Dimensions that lost in a trade-off
  let tradeOffLosers = $derived(
    new Set(assessment.trade_offs.map((t) => t.lost_dimension))
  );

  function toggleL1() {
    expandedL1 = !expandedL1;
    if (!expandedL1) expandedDimension = null;
  }

  function toggleDimension(dim: string) {
    expandedDimension = expandedDimension === dim ? null : dim;
  }
</script>

<!--
  ResultAssessment — progressive disclosure verdict engine.
  L0: Verdict bar (always visible)
  L1: Dimension map (click verdict to expand)
  L2: Journey + framework (click dimension row)
-->
<div class="border border-border-subtle bg-bg-card">
  <!-- L0 Verdict Bar (always visible) -->
  <button
    class="w-full flex items-center gap-2 px-2 py-1.5 transition-colors duration-200 hover:bg-bg-hover/50"
    onclick={toggleL1}
    aria-expanded={expandedL1}
    aria-label="Toggle result assessment details"
    data-testid="assessment-toggle"
  >
    <!-- Score circle -->
    <ScoreCircle score={overallScore} size={36} />

    <!-- Verdict badge -->
    <span
      class="shrink-0 px-1.5 py-0.5 text-[9px] font-mono font-bold uppercase border {verdictBorder} {verdictClass}"
    >
      {assessment.verdict.toUpperCase()}
    </span>

    <!-- Confidence badge -->
    <span class="shrink-0 px-1 py-0.5 text-[8px] font-mono text-text-dim border border-border-subtle">
      {confidenceLabels[assessment.confidence] ?? 'MED'}
    </span>

    <!-- Headline -->
    <span class="flex-1 text-[10px] font-mono text-text-secondary truncate text-left">
      {assessment.headline}
    </span>

    <!-- Retry sparkline -->
    {#if assessment.retry_journey.score_trajectory.length > 1}
      <div class="shrink-0 flex items-end gap-px h-4" aria-label="Retry score sparkline">
        {#each assessment.retry_journey.score_trajectory as score, i}
          {@const barH = Math.max(2, (score / sparklineMax) * 16)}
          {@const isBest = i + 1 === assessment.retry_journey.best_attempt}
          <div
            class="w-1 {isBest ? 'bg-neon-cyan' : 'bg-text-dim/30'}"
            style="height: {barH}px;"
          ></div>
        {/each}
      </div>
    {/if}

    <!-- Chevron -->
    <svg
      class="w-3 h-3 shrink-0 text-text-dim transition-transform duration-200"
      class:rotate-180={expandedL1}
      fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2"
    >
      <path stroke-linecap="round" stroke-linejoin="round" d="M19 9l-7 7-7-7"></path>
    </svg>
  </button>

  <!-- L1 Dimension Map (expanded) -->
  {#if expandedL1}
    <div class="border-t border-border-subtle">
      {#each sortedInsights as insight, i}
        {@const isTradeOff = tradeOffLosers.has(insight.dimension)}
        {@const isExpanded = expandedDimension === insight.dimension}
        {@const elasticity = assessment.improvement_signals.find(
          (s) => s.dimension === insight.dimension
        )?.elasticity ?? 0}

        <!-- Dimension row -->
        <button
          class="w-full flex items-center gap-2 px-2 py-1 border-b border-border-subtle/50
                 hover:bg-bg-hover/50 transition-colors duration-200 text-left"
          onclick={() => toggleDimension(insight.dimension)}
          aria-expanded={isExpanded}
          data-testid="assessment-dimension-{insight.dimension}"
        >
          <!-- Score -->
          <span
            class="shrink-0 w-9 text-center text-[11px] font-mono font-bold border-l {getScoreClass(insight.score)}"
            style="padding-left: 4px;"
          >
            {insight.score.toFixed(0)}
          </span>

          <!-- Divider -->
          <div class="w-px h-4 bg-border-subtle" aria-hidden="true"></div>

          <!-- Content -->
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-1.5">
              <span class="text-[10px] font-mono text-text-primary">{insight.label}</span>
              {#if insight.user_priority === 'high'}
                <span class="text-[8px] font-mono text-neon-purple px-0.5 border border-neon-purple/40">PRI</span>
              {/if}
              {#if insight.delta_from_previous != null && Math.abs(insight.delta_from_previous) >= 0.5}
                <span class="text-[9px] font-mono {insight.delta_from_previous > 0 ? 'text-neon-green' : 'text-neon-red'}">
                  {insight.delta_from_previous > 0 ? '+' : ''}{insight.delta_from_previous.toFixed(1)}
                </span>
              {/if}
              {#if isTradeOff}
                <span class="text-[8px] font-mono text-neon-yellow px-0.5 border border-neon-yellow/40">TRADE-OFF</span>
              {/if}
            </div>
            <p class="text-[9px] font-mono text-text-dim truncate">{insight.assessment}</p>
          </div>

          <!-- Elasticity bar -->
          <div class="shrink-0 w-8 h-1 bg-bg-primary relative" title="Elasticity: {elasticity.toFixed(1)}">
            <div
              class="absolute top-0 left-0 h-full bg-neon-cyan/40"
              style="width: {Math.min(100, elasticity * 100)}%;"
            ></div>
          </div>
        </button>

        <!-- L2 Journey + Framework (expanded dimension) -->
        {#if isExpanded}
          <div class="px-3 py-2 bg-bg-primary/30 border-b border-border-subtle/50">
            <div class="grid grid-cols-2 gap-3" style="grid-template-columns: 1fr 1fr;">
              <!-- Left: Retry journey -->
              <div class="space-y-1.5">
                <p class="text-[9px] font-display font-bold uppercase tracking-wider text-text-dim">Retry Journey</p>
                {#if assessment.retry_journey.score_trajectory.length > 1}
                  <div class="flex items-end gap-0.5 h-6">
                    {#each assessment.retry_journey.score_trajectory as score, j}
                      {@const h = Math.max(2, (score / sparklineMax) * 24)}
                      {@const best = j + 1 === assessment.retry_journey.best_attempt}
                      <div
                        class="flex-1 {best ? 'bg-neon-cyan' : 'bg-text-dim/30'}"
                        style="height: {h}px; max-width: 12px;"
                      ></div>
                    {/each}
                  </div>
                {/if}
                <p class="text-[9px] font-mono text-text-dim">{assessment.retry_journey.summary}</p>
              </div>

              <!-- Right: Framework fit -->
              <div class="space-y-1.5">
                <p class="text-[9px] font-display font-bold uppercase tracking-wider text-text-dim">Framework Fit</p>
                {#if assessment.framework_fit}
                  <div class="space-y-0.5">
                    <div class="flex items-center gap-1">
                      <span class="text-[10px] font-mono text-text-primary">
                        {assessment.framework_fit.framework}
                      </span>
                      <span class="text-[8px] font-mono text-text-dim border border-border-subtle px-0.5">
                        {assessment.framework_fit.fit_label}
                      </span>
                    </div>
                    {#if assessment.framework_fit.recommendation}
                      <p class="text-[9px] font-mono text-text-dim">
                        {assessment.framework_fit.recommendation}
                      </p>
                    {/if}
                  </div>
                {:else}
                  <p class="text-[9px] font-mono text-text-dim">No framework data</p>
                {/if}

                <!-- Trade-off pattern for this dimension -->
                {#each assessment.trade_offs.filter(
                  (t) => t.gained_dimension === insight.dimension || t.lost_dimension === insight.dimension
                ) as tradeOff}
                  <p class="text-[9px] font-mono text-neon-yellow/70 mt-1">
                    {tradeOff.description}
                  </p>
                {/each}
              </div>
            </div>
          </div>
        {/if}
      {/each}
    </div>
  {/if}

  <!-- Actions bar (always visible) -->
  {#if assessment.next_actions.length > 0}
    <div class="flex items-stretch border-t border-border-subtle">
      {#each assessment.next_actions.slice(0, 2) as action, i}
        <div
          class="flex-1 px-2 py-1.5 {i === 0 ? 'border-r border-border-subtle' : ''}"
          style="{i === 0 ? 'flex: 3;' : 'flex: 2;'}"
        >
          <div class="flex items-center gap-1">
            {#if i === 0}
              <div class="w-px h-3 bg-neon-green" aria-hidden="true"></div>
            {/if}
            <span class="text-[10px] font-mono {i === 0 ? 'text-text-primary' : 'text-text-secondary'}">
              {action.action}
            </span>
          </div>
          <p class="text-[9px] font-mono text-text-dim mt-0.5 line-clamp-1">
            {action.rationale}
          </p>
        </div>
      {/each}
    </div>
  {/if}
</div>
