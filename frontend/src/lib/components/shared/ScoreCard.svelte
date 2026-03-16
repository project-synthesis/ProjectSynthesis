<script lang="ts">
  import type { DimensionScores } from '$lib/api/client';

  interface Props {
    scores: DimensionScores;
    originalScores?: DimensionScores | null;
    deltas?: Record<string, number> | null;
    overallScore?: number | null;
  }

  let { scores, originalScores = null, deltas = null, overallScore = null }: Props = $props();

  const DIMENSION_LABELS: Record<string, string> = {
    clarity: 'Clarity',
    specificity: 'Specificity',
    structure: 'Structure',
    faithfulness: 'Faithfulness',
    conciseness: 'Conciseness',
  };

  const dimensionEntries = $derived(Object.entries(scores) as [string, number][]);
</script>

<div class="scorecard">
  {#if overallScore !== null && overallScore !== undefined}
    <div class="overall-row">
      <span class="overall-label">Overall</span>
      <span class="overall-value">{overallScore.toFixed(1)}</span>
    </div>
  {/if}

  <ul class="dimensions-list" aria-label="Dimension scores">
    {#each dimensionEntries as [dim, value]}
      {@const delta = deltas?.[dim] ?? null}
      {@const orig = (originalScores as Record<string, number> | null)?.[dim] ?? null}
      <li class="dimension-row">
        <span class="dim-label">{DIMENSION_LABELS[dim] ?? dim}</span>
        <span class="dim-value">{typeof value === 'number' ? value.toFixed(1) : value}</span>
        {#if delta !== null}
          <span
            class="dim-delta"
            class:positive={delta > 0}
            class:negative={delta < 0}
          >{delta > 0 ? '+' : ''}{delta.toFixed(1)}</span>
        {/if}
        {#if orig !== null}
          <span class="dim-orig">{typeof orig === 'number' ? orig.toFixed(1) : orig}</span>
        {/if}
      </li>
    {/each}
  </ul>
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
</style>
