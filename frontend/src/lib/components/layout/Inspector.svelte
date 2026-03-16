<script lang="ts">
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { refinementStore } from '$lib/stores/refinement.svelte';
  import ScoreCard from '$lib/components/shared/ScoreCard.svelte';
  import ScoreSparkline from '$lib/components/refinement/ScoreSparkline.svelte';

  const DIMENSION_LABELS: Record<string, string> = {
    clarity: 'Clarity',
    specificity: 'Specificity',
    structure: 'Structure',
    faithfulness: 'Faithfulness',
    conciseness: 'Conciseness',
  };

  const PHASE_LABELS: Record<string, string> = {
    analyzing: 'Analyzing',
    optimizing: 'Optimizing',
    scoring: 'Scoring',
  };
</script>

<aside
  class="inspector"
  aria-label="Inspector panel"
  style="background: var(--color-bg-secondary); border-left: 1px solid var(--color-border-subtle);"
>
  <!-- Header -->
  <div class="inspector-header">
    <span class="section-heading">Inspector</span>
  </div>

  <!-- Body -->
  <div class="inspector-body">

    {#if forgeStore.status === 'idle'}
      <!-- Empty state -->
      <div class="empty-state">
        <span class="empty-text">Enter a prompt and forge</span>
      </div>

    {:else if forgeStore.status === 'analyzing' || forgeStore.status === 'optimizing' || forgeStore.status === 'scoring'}
      <!-- Active phase -->
      <div class="phase-state">
        <div class="spinner" aria-label="Processing" role="status"></div>
        <span class="phase-label">
          {PHASE_LABELS[forgeStore.status] ?? forgeStore.status}
        </span>
        {#if forgeStore.currentPhase}
          <span class="phase-detail">{forgeStore.currentPhase}</span>
        {/if}
      </div>

    {:else if forgeStore.status === 'complete'}
      <!-- Complete — scores + strategy -->
      <div class="complete-state">

        {#if forgeStore.scores}
          <div class="scores-section">
            <div class="section-heading" style="margin-bottom: 6px;">Scores</div>
            <ul class="scores-list" aria-label="Dimension scores">
              {#each Object.entries(forgeStore.scores) as [dim, value]}
                <li class="score-row">
                  <span class="score-dim">{DIMENSION_LABELS[dim] ?? dim}</span>
                  <span class="score-value">{typeof value === 'number' ? value.toFixed(1) : value}</span>
                  {#if forgeStore.scoreDeltas && forgeStore.scoreDeltas[dim] !== undefined}
                    {@const delta = forgeStore.scoreDeltas[dim]}
                    <span
                      class="score-delta"
                      class:positive={delta > 0}
                      class:negative={delta < 0}
                    >{delta > 0 ? '+' : ''}{delta.toFixed(1)}</span>
                  {/if}
                </li>
              {/each}
            </ul>
          </div>
        {:else}
          <div class="scoring-disabled">
            <span class="scoring-disabled-label">Scoring</span>
            <span class="scoring-disabled-value">disabled</span>
          </div>
        {/if}

        {#if forgeStore.result?.strategy_used}
          <div class="strategy-section">
            <div class="section-heading" style="margin-bottom: 4px;">Strategy</div>
            <span class="strategy-value">{forgeStore.result.strategy_used}</span>
          </div>
        {/if}

        {#if forgeStore.scores}
          <div class="scorecard-section">
            <div class="section-heading" style="margin-bottom: 6px;">Score Details</div>
            <ScoreCard
              scores={forgeStore.scores}
              originalScores={forgeStore.originalScores}
              deltas={forgeStore.scoreDeltas}
              overallScore={forgeStore.result?.overall_score ?? null}
            />
          </div>
        {/if}

        {#if refinementStore.scoreProgression.length >= 2}
          <div class="sparkline-section">
            <div class="section-heading" style="margin-bottom: 4px;">Score Trend</div>
            <ScoreSparkline scores={refinementStore.scoreProgression} />
            <span class="sparkline-label">{refinementStore.turns.length} versions</span>
          </div>
        {/if}

      </div>

    {:else if forgeStore.status === 'error'}
      <div class="error-state">
        <span class="error-icon" aria-hidden="true">!</span>
        <span class="error-text">{forgeStore.error ?? 'Unknown error'}</span>
      </div>
    {/if}

  </div>
</aside>

<style>
  .inspector {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .inspector-header {
    display: flex;
    align-items: center;
    height: 24px;
    padding: 0 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .inspector-body {
    flex: 1;
    overflow-y: auto;
    padding: 6px;
  }

  /* Empty state */
  .empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 80px;
  }

  .empty-text {
    font-size: 11px;
    color: var(--color-text-dim);
    font-family: var(--font-sans);
    text-align: center;
  }

  /* Phase / spinner state */
  .phase-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 8px 0;
  }

  .spinner {
    width: 20px;
    height: 20px;
    border: 1px solid var(--color-border-subtle);
    border-top-color: var(--color-neon-cyan);
    animation: spin 800ms linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }

  .phase-label {
    font-size: 11px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
  }

  .phase-detail {
    font-size: 10px;
    color: var(--color-text-dim);
    font-family: var(--font-mono);
  }

  /* Complete state */
  .complete-state {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .scores-section,
  .strategy-section {
    display: flex;
    flex-direction: column;
  }

  .scores-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .score-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 6px; /* card interior p-1.5 */
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .score-dim {
    flex: 1;
    font-size: 10px;
    color: var(--color-text-secondary);
    font-family: var(--font-sans);
  }

  .score-value {
    font-size: 10px;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
  }

  .score-delta {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }

  .score-delta.positive {
    color: var(--color-neon-green);
  }

  .score-delta.negative {
    color: var(--color-neon-red);
  }

  .strategy-value {
    font-size: 11px;
    color: var(--color-neon-cyan);
    font-family: var(--font-mono);
  }

  /* Error state */
  .error-state {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 8px 0;
  }

  .error-icon {
    font-size: 16px;
    font-weight: bold;
    color: var(--color-neon-red);
    font-family: var(--font-mono);
  }

  .error-text {
    font-size: 10px;
    color: var(--color-text-dim);
    font-family: var(--font-sans);
    text-align: center;
    word-break: break-word;
  }

  /* Scoring disabled state */
  .scoring-disabled {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
  }

  .scoring-disabled-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .scoring-disabled-value {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-neon-yellow);
  }

  /* Sparkline section */
  .sparkline-section {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .sparkline-label {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }
</style>
