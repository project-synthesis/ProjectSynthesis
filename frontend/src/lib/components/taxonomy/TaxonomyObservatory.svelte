<script lang="ts">
  /**
   * TaxonomyObservatory — three-panel shell composing Timeline + Readiness + Heatmap.
   *
   * Period selector lives inside Timeline + Heatmap panel headers (NOT in this
   * shell header), because Readiness is current-state data with no period
   * applied. The shell legend explains the asymmetry.
   */
  import DomainLifecycleTimeline from './DomainLifecycleTimeline.svelte';
  import DomainReadinessAggregate from './DomainReadinessAggregate.svelte';
  import PatternDensityHeatmap from './PatternDensityHeatmap.svelte';
  import { observatoryStore } from '$lib/stores/observatory.svelte';
</script>

<div class="observatory" data-test="taxonomy-observatory" role="tabpanel">
  <header class="observatory-shell-header" data-test="observatory-shell-header">
    <h2 class="shell-title">OBSERVATORY</h2>
    <p class="observatory-legend" data-test="observatory-legend">
      Readiness reflects current state — the period selector applies to Timeline and Pattern Density only.
    </p>
  </header>

  <div class="panel-grid">
    <div
      class="panel panel--timeline"
      data-test="observatory-timeline-slot"
      data-period={observatoryStore.period}
    >
      <DomainLifecycleTimeline />
    </div>
    <div class="panel panel--readiness" data-test="observatory-readiness-slot">
      <DomainReadinessAggregate />
    </div>
    <div class="panel panel--heatmap" data-test="observatory-heatmap-slot">
      <PatternDensityHeatmap />
    </div>
  </div>
</div>

<style>
  .observatory {
    display: flex;
    flex-direction: column;
    height: 100%;
    padding: 6px;
    gap: 6px;
  }
  .observatory-shell-header {
    height: 28px;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }
  .shell-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    margin: 0;
  }
  .observatory-legend {
    font-size: 10px;
    color: var(--color-text-dim);
    margin: 0;
  }
  .panel-grid {
    flex: 1;
    display: grid;
    grid-template-columns: 3fr 2fr;
    grid-template-rows: 1fr auto;
    gap: 6px;
    min-height: 0;
  }
  .panel {
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    overflow: auto;
  }
  .panel--timeline { grid-column: 1; grid-row: 1; }
  .panel--readiness { grid-column: 2; grid-row: 1; }
  .panel--heatmap { grid-column: 1 / span 2; grid-row: 2; }
</style>
