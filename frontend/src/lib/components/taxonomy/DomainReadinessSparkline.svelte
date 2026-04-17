<script lang="ts">
  /**
   * DomainReadinessSparkline — fetches 24h readiness history for a single
   * domain and renders a peer sparkline (consistency or gap) via
   * `ScoreSparkline`. Sits alongside `DomainStabilityMeter` /
   * `SubDomainEmergenceList` in `TopologyInfoPanel.svelte` so those
   * components keep their slim `report: DomainStabilityReport` /
   * `report: SubDomainEmergenceReport` Props contracts untouched.
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

  // Oldest → newest, drop nulls (gap can be null when no candidates)
  const scores = $derived(
    [...historyPoints]
      .reverse()
      .map((p) => (metric === 'consistency' ? p.consistency : p.top_candidate_gap))
      .filter((v): v is number => v != null),
  );

  const ariaLabel = $derived(
    metric === 'consistency'
      ? `${domainLabel} consistency 24h sparkline`
      : `${domainLabel} gap to threshold 24h trendline`,
  );
</script>

{#if scores.length >= 2}
  <span class="drs" aria-label={ariaLabel}>
    <ScoreSparkline
      scores={scores}
      width={120}
      height={20}
      baseline={baseline}
      minRange={0.10}
    />
  </span>
{/if}

<style>
  .drs {
    display: inline-flex;
    align-items: center;
  }
</style>
