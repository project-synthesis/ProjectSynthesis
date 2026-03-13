<script lang="ts">
  import { feedback } from '$lib/stores/feedback.svelte';
  import { getScoreColor } from '$lib/utils/colors';

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

  // Map retry threshold (3.0–8.0) to a 0–100% position on bar
  function thresholdPercent(val: number): number {
    return ((val - 3.0) / (8.0 - 3.0)) * 100;
  }

  // Default weight marker position (0–100%) from weight value (0–1)
  function defaultMarkerPercent(dim: string): number {
    return (DEFAULT_WEIGHTS[dim] ?? 0) * 100;
  }

  // Compute actual marker percent from live dimension weights (0–1)
  function liveWeightPercent(dim: string): number {
    const weights = feedback.adaptationState?.dimensionWeights;
    if (!weights || typeof weights[dim] !== 'number') return defaultMarkerPercent(dim);
    return weights[dim] * 100;
  }

  // Format a timestamp to a short relative label
  function formatTs(ts: string | undefined): string {
    if (!ts) return '—';
    const d = new Date(ts);
    if (isNaN(d.getTime())) return ts;
    const now = Date.now();
    const diffMin = Math.round((now - d.getTime()) / 60000);
    if (diffMin < 1) return 'just now';
    if (diffMin < 60) return `${diffMin}m ago`;
    const diffH = Math.round(diffMin / 60);
    if (diffH < 24) return `${diffH}h ago`;
    return `${Math.round(diffH / 24)}d ago`;
  }

  let state = $derived(feedback.adaptationState);
</script>

<div class="space-y-3">
  <h3 class="font-display text-[12px] font-bold uppercase text-text-dim">Adaptation</h3>

  {#if !state}
    <p class="text-xs text-text-dim">No adaptation data.</p>
  {:else}
    <!-- Dimension weight bars -->
    <div class="space-y-2">
      <p class="text-[10px] text-text-dim uppercase font-mono">Dimension Weights</p>
      {#each Object.keys(DEFAULT_WEIGHTS) as dim}
        {@const liveW = state.dimensionWeights?.[dim] ?? DEFAULT_WEIGHTS[dim]}
        {@const livePct = liveWeightPercent(dim)}
        {@const defaultPct = defaultMarkerPercent(dim)}
        {@const dimColor = getScoreColor(liveW * 40)}
        <div class="space-y-0.5">
          <div class="flex justify-between">
            <span class="font-mono text-[10px] text-text-dim capitalize">{DIM_LABELS[dim] ?? dim}</span>
            <span class="font-mono text-[10px] text-text-primary">{(liveW * 100).toFixed(0)}%</span>
          </div>
          <!-- Bar -->
          <div class="relative w-full h-1 bg-bg-primary">
            <!-- Filled portion -->
            <div
              class="absolute top-0 left-0 h-full"
              style="width: {livePct}%; background: {dimColor};"
            ></div>
            <!-- Default weight marker — 1px vertical line -->
            <div
              class="absolute top-0 h-full w-px bg-text-dim/60"
              style="left: {defaultPct}%;"
              title="Default: {(DEFAULT_WEIGHTS[dim] * 100).toFixed(0)}%"
            ></div>
          </div>
        </div>
      {/each}
    </div>

    <!-- Strategy affinities -->
    {#if state.strategyAffinities && Object.keys(state.strategyAffinities).length > 0}
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

    <!-- Retry threshold -->
    <div class="space-y-1.5">
      <p class="text-[10px] text-text-dim uppercase font-mono">Retry Threshold</p>
      <div class="flex items-center gap-2">
        <span class="font-mono text-sm text-text-primary">{state.retryThreshold.toFixed(1)}</span>
        <div class="flex-1 relative h-1 bg-bg-primary">
          <div
            class="absolute top-0 h-full w-px bg-neon-cyan"
            style="left: {thresholdPercent(state.retryThreshold)}%;"
          ></div>
          <!-- Scale end labels -->
        </div>
        <div class="flex justify-between w-full absolute pointer-events-none"></div>
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
        <span class="text-text-primary">{state.feedbackCount}</span>
      </div>
    </div>
  {/if}
</div>
