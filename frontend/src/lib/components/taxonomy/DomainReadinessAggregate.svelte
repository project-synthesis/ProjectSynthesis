<script lang="ts">
  /**
   * DomainReadinessAggregate — observatory panel that surfaces every domain's
   * stability + emergence at a glance, sorted critical → guarded → healthy.
   *
   * Reads readinessStore.reports (already cached + invalidated on
   * taxonomy_changed/domain_created SSE events). Each card composes the
   * existing DomainStabilityMeter + SubDomainEmergenceList rather than
   * re-rendering the underlying primitives — keeps the rings + sparkline
   * coherent across the navigator and observatory surfaces.
   *
   * Card click dispatches a `domain:select` CustomEvent with the
   * domain_id; the parent container can listen and route. Mid-session
   * dissolution (domain dropped between mount and click) is guarded
   * via readinessStore.byDomain(id) — null result short-circuits.
   *
   * Keyboard semantics mirror DomainReadinessPanel: Space/Enter on the card
   * activates select(); nested clicks inside the card body are caught at the
   * grid container's onclick handler — currently no nested interactive
   * controls live inside the card, so no Event.target equality guard is
   * needed (compare against DRP's onRowKey for the multi-control variant).
   */
  import type { DomainReadinessReport, StabilityTier } from '$lib/api/readiness';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import DomainStabilityMeter from './DomainStabilityMeter.svelte';
  import SubDomainEmergenceList from './SubDomainEmergenceList.svelte';

  /**
   * Stability tier weights — critical (most urgent) sorts first. Any unknown
   * tier (defensive: backend contract may evolve) bubbles to the bottom via
   * the `?? 3` fallback in the comparator.
   */
  const TIER_ORDER: Record<StabilityTier, number> = {
    critical: 0,
    guarded: 1,
    healthy: 2,
  };

  const sorted = $derived.by(() => {
    return [...readinessStore.reports].sort((a, b) => {
      const aw = TIER_ORDER[a.stability.tier] ?? 3;
      const bw = TIER_ORDER[b.stability.tier] ?? 3;
      return aw - bw;
    });
  });

  let rootEl: HTMLElement | undefined = $state();

  function handleCardClick(report: DomainReadinessReport) {
    // Mid-session dissolution guard: verify the domain still exists. The
    // store's `byDomain` returns null when the domain has been reaped between
    // mount and click — silently no-op rather than dispatching a stale id.
    const live = readinessStore.byDomain(report.domain_id);
    if (live === null) return;
    rootEl?.dispatchEvent(
      new CustomEvent('domain:select', {
        detail: { domain_id: report.domain_id },
        bubbles: true,
      }),
    );
  }

  function handleCardKey(event: KeyboardEvent, report: DomainReadinessReport) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      handleCardClick(report);
    }
  }
</script>

<section
  class="readiness-aggregate"
  bind:this={rootEl}
  aria-label="Domain readiness aggregate"
>
  {#if sorted.length === 0}
    <p class="empty-copy">No domains yet — the taxonomy is warming up.</p>
  {:else}
    <div class="card-grid">
      {#each sorted as report (report.domain_id)}
        <div
          class="readiness-card"
          data-tier={report.stability.tier}
          onclick={() => handleCardClick(report)}
          role="button"
          tabindex="0"
          aria-label="Open {report.domain_label} readiness"
          onkeydown={(e) => handleCardKey(e, report)}
        >
          <header class="card-header">
            <span class="domain-label">{report.domain_label}</span>
            <span class="card-meta">{report.member_count}m</span>
          </header>
          <DomainStabilityMeter report={report.stability} />
          <SubDomainEmergenceList report={report.emergence} />
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .readiness-aggregate {
    padding: 6px;
  }
  .empty-copy {
    padding: 6px;
    margin: 0;
    font-family: var(--font-sans);
    font-size: 11px;
    color: var(--color-text-dim);
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 6px;
  }
  .readiness-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    transition: border-color var(--duration-hover) var(--ease-spring);
  }
  .readiness-card:hover {
    border-color: var(--color-neon-cyan);
  }
  /*
   * Brand spec: 1px focus contour, no glow. `color-mix` mirrors the focus
   * styling on DomainReadinessPanel rows so keyboard navigation between the
   * sidebar list and the observatory grid feels consistent.
   */
  .readiness-card:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
    outline-offset: -1px;
  }
  .card-header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 4px;
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    padding-bottom: 4px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .card-meta {
    font-family: var(--font-mono);
    font-size: 9px;
    font-weight: 500;
    letter-spacing: 0.05em;
    color: var(--color-text-dim);
  }
  @media (prefers-reduced-motion: reduce) {
    .readiness-card {
      transition: none;
    }
  }
</style>
