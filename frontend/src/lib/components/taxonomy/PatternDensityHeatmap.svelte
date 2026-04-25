<script lang="ts">
  /**
   * PatternDensityHeatmap — observatory data grid.
   *
   * "Heatmap" = grid where row backgrounds are tinted with the domain's
   * taxonomy colour, opacity-scaled to meta_pattern_count. Read-only;
   * rows have no role/tabindex. Loading dims body to 0.5 opacity; error
   * surfaces a retry button. Empty state is factual.
   */
  import { observatoryStore } from '$lib/stores/observatory.svelte';
  import { taxonomyColor } from '$lib/utils/colors';
  import { tooltip } from '$lib/actions/tooltip';
  import type { PatternDensityRow } from '$lib/api/observatory';

  const rows = $derived(observatoryStore.patternDensity ?? []);
  const loading = $derived(observatoryStore.patternDensityLoading);
  const error = $derived(observatoryStore.patternDensityError);

  /**
   * Mount-time backfill — without this, the panel renders the misleading
   * "Pattern library is empty" copy until the user clicks a Timeline period
   * chip (which then debounces a refresh by 1s). Fire once when no data has
   * been observed yet (`patternDensity === null`), no fetch is in-flight,
   * and no error is already surfaced. The local `triggered` flag prevents
   * the effect from re-firing if the gate becomes true again later (e.g.
   * after a manual reset to `null`); subsequent refreshes go through the
   * Retry button or `setPeriod()`.
   */
  let triggered = false;
  $effect(() => {
    if (triggered) return;
    if (
      observatoryStore.patternDensity === null
      && !observatoryStore.patternDensityLoading
      && observatoryStore.patternDensityError === null
    ) {
      triggered = true;
      void observatoryStore.refreshPatternDensity();
    }
  });

  // Brand spec: heatmap row tints must be SUBTLE, not saturated. The neon
  // domain palette (e.g. backend=#b44aff, frontend=#ff4895) renders
  // visually loud even at low percentages when mixed with pure transparent
  // over a dark page background — the eye reads the chromatic shift sharply
  // against near-black. Two combined mitigations:
  //   1. Drop the empirical ceiling from 22% → 14% so even the brightest
  //      row stays a quiet tint rather than a shouting fill.
  //   2. Mix INTO `--color-bg-card` (#11111e) instead of `transparent` —
  //      this composes the row as a tinted card surface (brand hierarchy
  //      tier) rather than a translucent overlay, keeping the row visually
  //      flush with sibling panels and preserving WCAG AA contrast for
  //      the dimmer domain hues (e.g. data=#b49982 warm taupe).
  const HEAT_MAX_PCT = 14;

  const maxCount = $derived(Math.max(1, ...rows.map((r) => r.meta_pattern_count)));

  function heatPct(count: number): number {
    return Math.round((count / maxCount) * HEAT_MAX_PCT);
  }

  function fmt(value: number | null, digits = 2): string {
    return value === null ? '—' : value.toFixed(digits);
  }

  // 0 is a valid count meaning "none observed yet" — render it explicitly
  // rather than collapsing to '—' (which we reserve for "no data" / null).
  function fmtCount(value: number): string {
    return String(value);
  }

  function fmtRate(value: number): string {
    return `${(value * 100).toFixed(0)}%`;
  }

  /**
   * Build the hover tooltip with absolute counts + window endpoints.
   *
   * Spec line 277: "Hover row: 1 px inset cyan contour + tooltip with the
   * absolute counts + timestamp of the last update." The `period_end`
   * field on each row is the canonical window endpoint and acts as the
   * "last update" marker since the aggregator queries live at request
   * time. Format: `YYYY-MM-DD HH:MM UTC`.
   */
  function tooltipFor(row: PatternDensityRow): string {
    const avg = row.meta_pattern_avg_score === null
      ? '—'
      : row.meta_pattern_avg_score.toFixed(2);
    const updated = row.period_end.slice(0, 16).replace('T', ' ');
    return [
      `Domain: ${row.domain_label}`,
      `Clusters: ${row.cluster_count}`,
      `Meta-patterns: ${row.meta_pattern_count} (avg ${avg})`,
      `Global patterns: ${row.global_pattern_count}`,
      `Cross-cluster injection: ${(row.cross_cluster_injection_rate * 100).toFixed(1)}%`,
      `Updated: ${updated} UTC`,
    ].join('\n');
  }
</script>

<section class="heatmap" aria-label="Pattern density heatmap">
  <header class="heatmap-header">
    <span class="col col-domain">domain</span>
    <span class="col col-n">clusters</span>
    <span class="col col-n">meta</span>
    <span class="col col-n">avg score</span>
    <span class="col col-n">global</span>
    <span class="col col-n">x-cluster inj. rate</span>
  </header>

  {#if error}
    <div class="heatmap-error" data-test="heatmap-error">
      <p>Pattern density could not be loaded.</p>
      <button type="button" onclick={() => observatoryStore.refreshPatternDensity()}>Retry</button>
    </div>
  {:else if rows.length === 0 && !loading}
    <p class="empty-copy">Pattern library is empty. Run <code>POST /api/seed</code> or start optimizing prompts.</p>
  {:else}
    <div class="heatmap-body" data-test="heatmap-body" style="opacity: {loading ? 0.5 : 1};">
      {#each rows as row (row.domain_id)}
        <div
          class="density-row"
          data-test="density-row"
          style="background-color: color-mix(in srgb, {taxonomyColor(row.domain_label)} {heatPct(row.meta_pattern_count)}%, var(--color-bg-card));"
          use:tooltip={tooltipFor(row)}
        >
          <span class="col col-domain">{row.domain_label}</span>
          <span class="col col-n">{fmtCount(row.cluster_count)}</span>
          <span class="col col-n">{fmtCount(row.meta_pattern_count)}</span>
          <span class="col col-n">{fmt(row.meta_pattern_avg_score, 1)}</span>
          <span class="col col-n">{fmtCount(row.global_pattern_count)}</span>
          <span class="col col-n">{fmtRate(row.cross_cluster_injection_rate)}</span>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .heatmap { padding: 6px; }
  .heatmap-header {
    display: grid;
    grid-template-columns: 1.5fr repeat(5, 1fr);
    gap: 4px;
    height: 20px;
    align-items: center;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
    padding: 0 6px;
  }
  .density-row {
    display: grid;
    grid-template-columns: 1.5fr repeat(5, 1fr);
    gap: 4px;
    height: 20px;
    align-items: center;
    padding: 0 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
    border-top: 1px solid var(--color-border-subtle);
    transition: box-shadow var(--duration-hover) var(--ease-spring);
  }
  /*
   * Brand spec (line 277): hover surfaces a 1px inset cyan contour to
   * mark the row as the active read target for the tooltip overlay.
   * Contour is the brand's interactive-state grammar — zero blur, zero
   * spread, single 1px line. Read-only (no role/cursor) is preserved
   * by H5; this is purely a focus-of-attention cue.
   */
  .density-row:hover {
    box-shadow: inset 0 0 0 1px var(--color-neon-cyan);
  }
  .col-domain { font-family: var(--font-sans); font-size: 11px; }
  .col-n { text-align: right; font-variant-numeric: tabular-nums; }
  .empty-copy { padding: 6px; font-size: 11px; color: var(--color-text-dim); margin: 0; }

  .heatmap-error {
    padding: 6px;
    box-shadow: inset 0 0 0 1px var(--color-neon-red);
  }
  .heatmap-error button {
    margin-top: 6px;
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    background: transparent;
    border: 1px solid var(--color-neon-red);
    color: var(--color-neon-red);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    transition: background-color var(--duration-hover) var(--ease-spring);
  }
  .heatmap-error button:hover {
    background: color-mix(in srgb, var(--color-neon-red) 6%, transparent);
  }
  .heatmap-error button:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .heatmap-body {
    transition: opacity var(--duration-hover) var(--ease-spring);
  }

  @media (prefers-reduced-motion: reduce) {
    .heatmap-body,
    .heatmap-error button,
    .density-row { transition-duration: 0.01ms !important; }
  }
</style>
