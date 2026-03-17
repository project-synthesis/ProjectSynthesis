<script lang="ts">
  import type { MetricBarSection } from '$lib/content/types';

  interface Props {
    dimensions: MetricBarSection['dimensions'];
    label?: MetricBarSection['label'];
  }

  let { dimensions, label }: Props = $props();
</script>

<div class="metric-bar" data-reveal>
  {#if label}
    <div class="metric-bar__label">{label}</div>
  {/if}
  <div class="metric-bar__rows">
    {#each dimensions as dim}
      <div class="metric-bar__row">
        <span class="metric-bar__name">{dim.name}</span>
        <div class="metric-bar__track">
          <div
            class="metric-bar__fill"
            style="width:{dim.value * 10}%;background:{dim.color}"
          ></div>
        </div>
        <span class="metric-bar__value font-mono">{dim.value}</span>
      </div>
    {/each}
  </div>
</div>

<style>
  .metric-bar {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .metric-bar__label {
    font-size: 10px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-bottom: 2px;
  }

  .metric-bar__rows {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .metric-bar__row {
    display: grid;
    grid-template-columns: 70px 1fr 30px;
    align-items: center;
    gap: 8px;
  }

  .metric-bar__name {
    font-size: 10px;
    color: var(--color-text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .metric-bar__track {
    height: 3px;
    background: var(--color-border-subtle);
    position: relative;
    overflow: hidden;
  }

  .metric-bar__fill {
    height: 100%;
    transition: width var(--duration-progress) var(--ease-spring);
  }

  .metric-bar__value {
    font-size: 10px;
    color: var(--color-text-dim);
    text-align: right;
  }
</style>
