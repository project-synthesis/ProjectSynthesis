<script lang="ts">
  import type { DimensionScores } from '$lib/api/client';
  import { DIMENSION_LABELS } from '$lib/utils/dimensions';
  import { formatScore, formatDelta } from '$lib/utils/formatting';
  import { DIMENSION_TOOLTIPS, SCORE_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    scores: DimensionScores;
    originalScores?: DimensionScores | null;
    deltas?: Record<string, number> | null;
    overallScore?: number | null;
    heuristicFlags?: string[];
  }

  let { scores, originalScores = null, deltas = null, overallScore = null, heuristicFlags = [] }: Props = $props();

  const dimensionEntries = $derived(Object.entries(scores) as [string, number][]);
  const hasDelta = $derived(deltas != null);
  const hasOrig = $derived(originalScores != null);
</script>

<div class="scorecard">
  {#if overallScore !== null && overallScore !== undefined}
    <div class="overall-row">
      <span class="overall-label">Overall</span>
      <span class="overall-value" use:tooltip={DIMENSION_TOOLTIPS.overall}>{formatScore(overallScore)}</span>
    </div>
  {/if}

  <div
    class="dimensions-grid"
    class:dimensions-grid--3col={hasDelta && !hasOrig}
    class:dimensions-grid--4col={hasDelta && hasOrig}
    role="list"
    aria-label="Dimension scores"
  >
    {#if hasDelta || hasOrig}
      <span class="dim-label"></span>
      <span class="dim-cell dim-header">score</span>
      {#if hasDelta}<span class="dim-cell dim-header">&Delta;</span>{/if}
      {#if hasOrig}<span class="dim-cell dim-header">orig</span>{/if}
    {/if}
    {#each dimensionEntries as [dim, value]}
      {@const delta = deltas?.[dim] ?? null}
      {@const orig = (originalScores as Record<string, number> | null)?.[dim] ?? null}
      <span class="dim-label">{DIMENSION_LABELS[dim] ?? dim}</span>
      <span class="dim-cell dim-value" use:tooltip={DIMENSION_TOOLTIPS[dim] ?? ''}>{formatScore(value)}</span>
      {#if hasDelta}
        <span
          class="dim-cell dim-delta"
          class:positive={delta != null && delta > 0}
          class:negative={delta != null && delta < 0}
          use:tooltip={delta != null ? SCORE_TOOLTIPS.delta(DIMENSION_LABELS[dim] ?? dim) : ''}
        >{delta != null ? formatDelta(delta) : ''}</span>
      {/if}
      {#if hasOrig}
        <span class="dim-cell dim-orig" use:tooltip={orig != null ? SCORE_TOOLTIPS.original(DIMENSION_LABELS[dim] ?? dim) : ''}>{orig != null ? formatScore(orig) : ''}</span>
      {/if}
    {/each}
  </div>

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

  /* Grid ensures header and data columns share the same track widths */
  .dimensions-grid {
    display: grid;
    grid-template-columns: 1fr auto;
    column-gap: 6px;
    row-gap: 0;
    padding: 0 6px;
  }

  .dimensions-grid--3col {
    grid-template-columns: 1fr auto auto;
  }

  .dimensions-grid--4col {
    grid-template-columns: 1fr auto auto auto;
  }

  .dim-label {
    font-size: 10px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
    padding: 3px 0;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .dim-cell {
    font-family: var(--font-mono);
    text-align: right;
    padding: 3px 0;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .dim-header {
    font-size: 8px;
    color: var(--color-text-dim);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    border-bottom: 1px solid color-mix(in srgb, var(--color-border-subtle) 50%, transparent);
    padding: 0 0 2px;
  }

  .dim-value {
    font-size: 11px;
    color: var(--color-text-primary);
  }

  .dim-delta {
    font-size: 10px;
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
