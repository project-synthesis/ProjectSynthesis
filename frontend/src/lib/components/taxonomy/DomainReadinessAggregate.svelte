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
   */
  import { readinessStore } from '$lib/stores/readiness.svelte';

  const TIER_ORDER = { critical: 0, guarded: 1, healthy: 2 } as const;

  const sorted = $derived.by(() => {
    return [...readinessStore.reports].sort((a, b) => {
      const aw = TIER_ORDER[a.stability.tier as keyof typeof TIER_ORDER] ?? 3;
      const bw = TIER_ORDER[b.stability.tier as keyof typeof TIER_ORDER] ?? 3;
      return aw - bw;
    });
  });

  let rootEl: HTMLElement | undefined = $state();

  function handleCardClick(report: typeof readinessStore.reports[number]) {
    // Mid-session dissolution guard: verify the domain still exists.
    const live = readinessStore.byDomain(report.domain_id);
    if (live === null) return;
    rootEl?.dispatchEvent(new CustomEvent('domain:select', {
      detail: { domain_id: report.domain_id },
      bubbles: true,
    }));
  }
</script>

<section class="readiness-aggregate" bind:this={rootEl} aria-label="Domain readiness aggregate">
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
          onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleCardClick(report); } }}
        >
          <header class="card-header">
            <span class="domain-label">{report.domain_label}</span>
          </header>
        </div>
      {/each}
    </div>
  {/if}
</section>

<style>
  .readiness-aggregate { padding: 6px; }
  .empty-copy {
    padding: 6px;
    margin: 0;
    font-size: 11px;
    color: var(--color-text-dim);
  }
  .card-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 6px;
  }
  .readiness-card {
    padding: 6px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    transition: border-color var(--duration-hover) var(--ease-spring);
  }
  .readiness-card:hover { border-color: var(--color-neon-cyan); }
  .readiness-card:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .card-header {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    padding-bottom: 4px;
  }
  @media (prefers-reduced-motion: reduce) {
    .readiness-card { transition-duration: 0.01ms !important; }
  }
</style>
