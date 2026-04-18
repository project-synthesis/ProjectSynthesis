<script lang="ts">
  /**
   * DomainReadinessSparkline — 24h trend peer component for the readiness row.
   *
   * Fetches a single domain's readiness history and renders either the
   * consistency series or the top-candidate gap series via `ScoreSparkline`.
   * Sits in `TopologyInfoPanel.ip-readiness` alongside `DomainStabilityMeter`
   * / `SubDomainEmergenceList` so those components keep their slim
   * `report: DomainStabilityReport` / `report: SubDomainEmergenceReport`
   * Props contracts untouched.
   *
   * Plan: docs/superpowers/plans/2026-04-17-readiness-time-series.md (Task 13).
   */
  import ScoreSparkline from '$lib/components/shared/ScoreSparkline.svelte';
  import {
    getDomainReadinessHistory,
    type ReadinessHistoryPoint,
  } from '$lib/api/readiness';

  type Metric = 'consistency' | 'gap';

  interface Props {
    domainId: string;
    domainLabel: string;
    metric: Metric;
    /** Dissolution floor (consistency) or 0 (gap). Drawn as a dashed line. */
    baseline?: number | null;
  }

  let { domainId, domainLabel, metric, baseline = null }: Props = $props();

  let historyPoints = $state<ReadinessHistoryPoint[]>([]);

  $effect(() => {
    let cancelled = false;
    getDomainReadinessHistory(domainId, '24h')
      .then((res) => {
        if (!cancelled) historyPoints = res.points;
      })
      .catch(() => {
        /* non-fatal — sparkline is optional observability */
      });
    return () => {
      cancelled = true;
    };
  });

  // API returns newest-first; sparkline expects oldest → newest.
  // `gap` can be null when no candidates exist — filter those out.
  const scores = $derived.by(() => {
    const ordered = [...historyPoints].reverse();
    const raw = ordered.map((p) =>
      metric === 'consistency' ? p.consistency : p.top_candidate_gap,
    );
    return raw.filter((v): v is number => v != null);
  });

  const ariaLabel = $derived(
    metric === 'consistency'
      ? `${domainLabel} consistency 24h sparkline`
      : `${domainLabel} gap to threshold 24h trendline`,
  );
</script>

{#if scores.length >= 2}
  <span class="drs" aria-label={ariaLabel}>
    <ScoreSparkline
      {scores}
      width={120}
      height={20}
      {baseline}
      minRange={0.1}
    />
    <!-- minRange=0.1 prevents a zero-scale flatline when consistency/gap
         values are tightly clustered across the 24h window. -->
  </span>
{/if}

<style>
  .drs {
    display: inline-flex;
    align-items: center;
  }
</style>
