<script lang="ts">
  /**
   * DomainStabilityMeter — dissolution-pressure gauge for a top-level domain.
   *
   * Visualizes the Source-1 (`domain_raw`) consistency against the dissolution
   * floor (default 0.15) and creation hysteresis threshold (0.60). Pure 1px
   * contour — zero glow, zero shadow, chromatic encoding only.
   */
  import type { DomainStabilityReport } from '$lib/api/readiness';
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    report: DomainStabilityReport;
  }

  let { report }: Props = $props();

  const consistencyPct = $derived(Math.round(report.consistency * 100));
  const floorPct = $derived(Math.round(report.dissolution_floor * 100));
  const hysteresisPct = $derived(Math.round(report.hysteresis_creation_threshold * 100));
  const dissolutionRiskPct = $derived(Math.round(report.dissolution_risk * 100));

  const tierColor = $derived.by(() => {
    switch (report.tier) {
      case 'healthy':
        return 'var(--color-neon-green)';
      case 'guarded':
        return 'var(--color-neon-yellow)';
      case 'critical':
        return 'var(--color-neon-red)';
    }
  });

  const tierLabel = $derived(report.tier.toUpperCase());

  const tipText = $derived(
    `Consistency ${consistencyPct}% vs dissolution floor ${floorPct}% ` +
      `(creation threshold ${hysteresisPct}%). Tier: ${report.tier}.`,
  );

  const failingGuards = $derived(
    (() => {
      const g = report.guards;
      const failed: string[] = [];
      if (!g.general_protected && !g.has_sub_domain_anchor) failed.push('no anchor');
      if (g.age_eligible) failed.push('age ≥48h');
      if (!g.above_member_ceiling) failed.push(`≤${report.member_ceiling}m`);
      if (!g.consistency_above_floor) failed.push('below floor');
      return failed;
    })(),
  );
</script>

<div class="dsm">
  <div class="dsm-header">
    <span class="dsm-title">STABILITY</span>
    <span class="dsm-tier" style="color: {tierColor}">{tierLabel}</span>
  </div>

  <div
    class="dsm-meter"
    role="meter"
    aria-valuemin="0"
    aria-valuemax="100"
    aria-valuenow={consistencyPct}
    aria-label="Consistency {consistencyPct}% vs dissolution floor {floorPct}%"
    use:tooltip={tipText}
  >
    <div class="dsm-fill" style="width: {consistencyPct}%; background: {tierColor}"></div>
    <!-- 1px contour marker for dissolution floor — never a glow -->
    <div class="dsm-marker dsm-floor" style="left: {floorPct}%" aria-hidden="true"></div>
    <!-- 1px contour marker for creation hysteresis threshold -->
    <div
      class="dsm-marker dsm-hysteresis"
      style="left: {hysteresisPct}%"
      aria-hidden="true"
    ></div>
  </div>

  <div class="dsm-numerics">
    <span class="dsm-value" aria-label="Current consistency">{consistencyPct}%</span>
    <span class="dsm-divider" aria-hidden="true">/</span>
    <span class="dsm-floor-label">floor {floorPct}%</span>
    {#if report.would_dissolve}
      <span class="dsm-risk" use:tooltip={'All dissolution guards fail — domain will dissolve next Phase 5 cycle.'}>
        risk {dissolutionRiskPct}%
      </span>
    {/if}
  </div>

  {#if failingGuards.length > 0}
    <div class="dsm-guards">
      {#each failingGuards as reason}
        <span class="dsm-guard-chip">{reason}</span>
      {/each}
    </div>
  {/if}
</div>

<style>
  .dsm {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .dsm-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
  }

  .dsm-title {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .dsm-tier {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .dsm-meter {
    position: relative;
    height: 4px;
    width: 100%;
    background: var(--color-bg-input);
    box-shadow: inset 0 0 0 1px var(--color-border-subtle);
    overflow: hidden;
  }

  .dsm-fill {
    position: absolute;
    left: 0;
    top: 0;
    height: 100%;
    transition: width 500ms cubic-bezier(0.16, 1, 0.3, 1),
      background-color 500ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .dsm-marker {
    position: absolute;
    top: 0;
    height: 100%;
    width: 1px;
    pointer-events: none;
  }

  .dsm-floor {
    background: var(--color-text-primary);
    opacity: 0.6;
  }

  .dsm-hysteresis {
    background: var(--color-text-dim);
    opacity: 0.4;
  }

  .dsm-numerics {
    display: flex;
    align-items: baseline;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .dsm-value {
    font-weight: 700;
    color: var(--color-text-primary);
  }

  .dsm-divider {
    color: var(--color-text-dim);
  }

  .dsm-floor-label {
    color: var(--color-text-dim);
  }

  .dsm-risk {
    margin-left: auto;
    color: var(--color-neon-red);
    font-size: 9px;
    font-weight: 500;
  }

  .dsm-guards {
    display: flex;
    flex-wrap: wrap;
    gap: 3px;
    margin-top: 1px;
  }

  .dsm-guard-chip {
    font-family: var(--font-mono);
    font-size: 8px;
    padding: 0 3px;
    color: var(--color-neon-yellow);
    border: 1px solid
      color-mix(in srgb, var(--color-neon-yellow) 30%, transparent);
    letter-spacing: 0.03em;
  }

  @media (prefers-reduced-motion: reduce) {
    .dsm-fill {
      transition: none;
    }
  }
</style>
