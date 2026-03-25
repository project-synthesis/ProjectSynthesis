<script lang="ts">
  import type { RefinementTurn } from '$lib/api/client';
  import { DIMENSION_LABELS } from '$lib/utils/dimensions';
  import { formatScore, formatDelta } from '$lib/utils/formatting';

  interface Props {
    turn: RefinementTurn;
    isExpanded: boolean;
    isSelected: boolean;
    onToggle: () => void;
    onSelect: () => void;
  }

  let { turn, isExpanded, isSelected, onToggle, onSelect }: Props = $props();

  const overallScore = $derived.by(() => {
    if (!turn.scores) return null;
    const vals = Object.values(turn.scores);
    if (vals.length === 0) return null;
    return vals.reduce((a, b) => a + b, 0) / vals.length;
  });

  const topDeltas = $derived.by(() => {
    if (!turn.deltas) return [];
    return Object.entries(turn.deltas)
      .filter(([, v]) => v !== 0)
      .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
      .slice(0, 2);
  });
</script>

<div
  class="turn-card"
  class:selected={isSelected}
  role="button"
  tabindex="0"
  aria-expanded={isExpanded}
  onclick={onSelect}
  ondblclick={onToggle}
  onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); onSelect(); } if (e.key === ' ') { e.preventDefault(); onToggle(); } }}
>
  <!-- Always-visible header -->
  <div class="turn-header">
    <span class="version-badge">v{turn.version}</span>
    <span class="turn-request">
      {turn.refinement_request || 'Initial optimization'}
    </span>
    <span class="turn-spacer"></span>
    {#if overallScore !== null}
      <span class="overall-score">{formatScore(overallScore)}</span>
    {/if}
    {#each topDeltas as [dim, delta]}
      <span
        class="delta-badge"
        class:positive={delta > 0}
        class:negative={delta < 0}
      >{formatDelta(delta)} {(DIMENSION_LABELS[dim] ?? dim).slice(0, 4)}</span>
    {/each}
  </div>

  <!-- Expandable detail -->
  {#if isExpanded}
    <div class="turn-detail">
      {#if turn.scores}
        <ul class="score-list">
          {#each Object.entries(turn.scores) as [dim, value]}
            {@const delta = turn.deltas?.[dim] ?? null}
            <li class="score-item">
              <span class="dim-name">{DIMENSION_LABELS[dim] ?? dim}</span>
              <span class="dim-score">{formatScore(value)}</span>
              {#if delta !== null && delta !== 0}
                <span
                  class="dim-delta"
                  class:positive={delta > 0}
                  class:negative={delta < 0}
                >{formatDelta(delta)}</span>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}

      {#if turn.strategy_used}
        <div class="strategy-row">
          <span class="detail-label">Strategy</span>
          <span class="strategy-value">{turn.strategy_used}</span>
        </div>
      {/if}

      {#if turn.refinement_request && turn.version > 1}
        <div class="changes-row">
          <span class="changes-text">{turn.refinement_request}</span>
        </div>
      {/if}
    </div>
  {/if}
</div>

<style>
  .turn-card {
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-card);
    cursor: pointer;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .turn-card:hover {
    border-color: var(--color-border-accent);
  }

  .turn-card.selected {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.03);
  }

  .turn-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 6px;
    min-height: 24px;
  }

  .version-badge {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
    border: 1px solid var(--tier-accent, var(--color-neon-cyan));
    padding: 0 4px;
    line-height: 16px;
    flex-shrink: 0;
    border-radius: 0;
  }

  .turn-request {
    font-size: 12px;
    font-family: var(--font-sans);
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
  }

  .turn-spacer {
    flex: 1;
  }

  .overall-score {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    flex-shrink: 0;
  }

  .delta-badge {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    flex-shrink: 0;
    white-space: nowrap;
  }

  .delta-badge.positive {
    color: var(--color-neon-green);
  }

  .delta-badge.negative {
    color: var(--color-neon-red);
  }

  /* Expanded detail */
  .turn-detail {
    border-top: 1px solid var(--color-border-subtle);
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .score-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .score-item {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 2px 4px;
  }

  .dim-name {
    flex: 1;
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
  }

  .dim-score {
    font-size: 10px;
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

  .strategy-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 4px;
  }

  .detail-label {
    font-size: 10px;
    font-family: var(--font-sans);
    color: var(--color-text-dim);
  }

  .strategy-value {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .changes-row {
    padding: 0 4px;
  }

  .changes-text {
    font-size: 12px;
    font-family: var(--font-sans);
    color: var(--color-text-secondary);
    line-height: 1.4;
  }
</style>
