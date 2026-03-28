<script lang="ts">
  import type { DimensionScores } from '$lib/api/client';
  import { DIMENSION_LABELS } from '$lib/utils/dimensions';
  import { formatScore, formatDelta } from '$lib/utils/formatting';

  interface Props {
    scores: DimensionScores;
    originalScores?: DimensionScores | null;
    deltas?: Record<string, number> | null;
    overallScore?: number | null;
    heuristicFlags?: string[];
  }

  let { scores, originalScores = null, deltas = null, overallScore = null, heuristicFlags = [] }: Props = $props();

  const dimensionEntries = $derived(Object.entries(scores) as [string, number][]);
</script>

<div class="scorecard">
  {#if overallScore !== null && overallScore !== undefined}
    <div class="overall-row">
      <span class="overall-label">Overall</span>
      <span class="overall-value">{formatScore(overallScore)}</span>
    </div>
  {/if}

  <ul class="dimensions-list" aria-label="Dimension scores">
    {#each dimensionEntries as [dim, value]}
      {@const delta = deltas?.[dim] ?? null}
      {@const orig = (originalScores as Record<string, number> | null)?.[dim] ?? null}
      <li class="dimension-row">
        <span class="dim-label">{DIMENSION_LABELS[dim] ?? dim}</span>
        <span class="dim-value">{formatScore(value)}</span>
        {#if delta !== null}
          <span
            class="dim-delta"
            class:positive={delta > 0}
            class:negative={delta < 0}
          >{formatDelta(delta)}</span>
        {/if}
        {#if orig !== null}
          <span class="dim-orig">{formatScore(orig)}</span>
        {/if}
      </li>
    {/each}
  </ul>

  {#if heuristicFlags && heuristicFlags.length > 0}
    <div class="divergence-warning" role="alert">
      <span class="divergence-icon" aria-hidden="true">!</span>
      <span class="divergence-text">
        Score divergence: {heuristicFlags.join(', ')}
      </span>
    </div>
  {/if}
</div>

<style>
  .scorecard {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .overall-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding: 4px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    margin-bottom: 4px;
  }

  .overall-label {
    font-size: 10px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .overall-value {
    font-size: 18px; /* text-lg */
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    line-height: 1;
  }

  .dimensions-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .dimension-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .dim-label {
    flex: 1;
    font-size: 10px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
  }

  .dim-value {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
  }

  .dim-delta {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .dim-delta.positive {
    color: var(--color-neon-green);
  }

  .dim-delta.negative {
    color: var(--color-neon-red);
  }

  .dim-orig {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .divergence-warning {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 6px;
    border: 1px solid var(--color-neon-yellow);
    margin-top: 4px;
  }

  .divergence-icon {
    color: var(--color-neon-yellow);
    font-size: 10px;
    font-family: var(--font-mono);
    flex-shrink: 0;
  }

  .divergence-text {
    font-size: 9px;
    color: var(--color-neon-yellow);
    font-family: var(--font-sans);
  }
</style>
