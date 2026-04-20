<script lang="ts">
  /**
   * DomainGroup — renders one top-level domain in the ClusterNavigator.
   *
   * Owns the domain header (with highlight-graph toggle) and the sub-domain
   * hierarchy beneath it. Direct clusters render first, then each sub-domain
   * group with its nested clusters. Uses `ClusterRow` as the shared row
   * primitive (single source of truth for selection + expansion).
   *
   * Extracted from ClusterNavigator to flatten the 1.4k-line monolith
   * while keeping every visual and interaction contract identical.
   */
  import type { ClusterNode } from '$lib/api/clusters';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { taxonomyColor } from '$lib/utils/colors';
  import { tooltip } from '$lib/actions/tooltip';
  import { CLUSTER_NAV_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import CollapsibleSectionHeader from '$lib/components/shared/CollapsibleSectionHeader.svelte';
  import { navCollapse } from '$lib/stores/nav_collapse.svelte';
  import { slide } from 'svelte/transition';
  import { navSlide } from '$lib/utils/transitions';
  import ClusterRow from './ClusterRow.svelte';

  interface SubDomainGroup {
    id: string;
    label: string;
    displayLabel: string;
    parentLabel: string;
    clusters: ClusterNode[];
  }
  interface Group {
    directClusters: ClusterNode[];
    subDomains: SubDomainGroup[];
    totalCount: number;
  }

  interface Props {
    domain: string;
    group: Group;
    expandedId: string | null;
    onToggleExpand: (family: ClusterNode) => void;
    onOpenLinkedOpt: (traceId: string, optId: string) => void;
  }

  let { domain, group, expandedId, onToggleExpand, onOpenLinkedOpt }: Props = $props();

  const domainDisplay = $derived(
    domain.startsWith('project:') ? domain.slice(8) : domain,
  );
  const domainKey = $derived(`domain:${domain}`);
  const domainDotColor = $derived(
    taxonomyColor(domain.startsWith('project:') ? 'general' : domain),
  );
  const domainOpen = $derived(navCollapse.isOpen(domainKey));

  function toggleSubDomain(subDomainId: string) {
    navCollapse.toggle(`subdomain:${subDomainId}`);
  }
</script>

<div class="domain-group">
  <CollapsibleSectionHeader
    open={domainOpen}
    onToggle={() => navCollapse.toggle(domainKey)}
    ariaLabel={`Toggle ${domainDisplay} domain`}
  >
    {#snippet header()}
      <button
        type="button"
        class="domain-label-btn"
        class:domain-label-btn--highlighted={clustersStore.highlightedDomain === domain}
        onclick={(e) => {
          e.stopPropagation();
          clustersStore.toggleHighlightDomain(domain);
        }}
        use:tooltip={CLUSTER_NAV_TOOLTIPS.highlight_graph}
      >
        <span class="domain-dot" style="background: {domainDotColor};"></span>
        <span class="domain-label">{domainDisplay}</span>
        <span class="domain-count">{group.totalCount}</span>
      </button>
    {/snippet}
  </CollapsibleSectionHeader>
  {#if domainOpen}
    <div transition:slide={navSlide}>
      {#each group.directClusters as family (family.id)}
        <ClusterRow
          {family}
          nested={false}
          {expandedId}
          {onToggleExpand}
          {onOpenLinkedOpt}
        />
      {/each}
      {#each group.subDomains as sub (sub.id)}
        {@const subKey = `subdomain:${sub.id}`}
        {@const subOpen = !navCollapse.isCollapsed(subKey)}
        <div class="subdomain-wrapper" class:subdomain-wrapper--collapsed={!subOpen}>
          <CollapsibleSectionHeader
            open={subOpen}
            onToggle={() => toggleSubDomain(sub.id)}
            ariaLabel={`Toggle ${sub.displayLabel} sub-domain`}
          >
            {#snippet header()}
              <div class="subdomain-label-row">
                <span class="domain-dot" style="background: {taxonomyColor(sub.parentLabel)};"></span>
                <span class="subdomain-label">{sub.displayLabel}</span>
                <span class="subdomain-count">{sub.clusters.length}</span>
              </div>
            {/snippet}
          </CollapsibleSectionHeader>
        </div>
        {#if subOpen}
          <div transition:slide={navSlide}>
            {#each sub.clusters as family (family.id)}
              <ClusterRow
                {family}
                nested={true}
                {expandedId}
                {onToggleExpand}
                {onOpenLinkedOpt}
              />
            {/each}
          </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .domain-group {
    margin-bottom: 4px;
  }

  .domain-label-btn {
    all: unset;
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    height: 100%;
    cursor: pointer;
    box-sizing: border-box;
    outline: 1px solid transparent;
    outline-offset: -1px;
    transition: background var(--duration-hover) var(--ease-spring),
                outline-color var(--duration-hover) var(--ease-spring);
  }

  .domain-label-btn:hover {
    background: var(--color-bg-hover);
  }

  .domain-label-btn--highlighted {
    outline-color: var(--color-border-accent);
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
  }

  .domain-label-btn:focus-visible {
    outline: 1px solid color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
    outline-offset: -1px;
  }

  .domain-dot {
    width: 8px;
    height: 8px;
    flex-shrink: 0;
    outline: 1px solid color-mix(in srgb, var(--color-text-primary) 15%, transparent);
    outline-offset: -1px;
  }

  .domain-label {
    font-size: 10px;
    font-family: var(--font-display, var(--font-sans));
    font-weight: 700;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    flex: 1;
  }

  .domain-count {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .subdomain-wrapper {
    border-left: 1px solid color-mix(in srgb, var(--color-text-dim) 30%, transparent);
    margin-top: 2px;
  }

  .subdomain-wrapper--collapsed {
    opacity: 0.6;
  }

  .subdomain-wrapper--collapsed:hover {
    opacity: 1;
  }

  .subdomain-label-row {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 1;
    min-width: 0;
    padding-left: 4px;
  }

  .subdomain-label {
    flex: 1;
    min-width: 0;
    font-size: 10px;
    font-weight: 700;
    color: var(--color-text-dim);
    text-overflow: ellipsis;
    overflow: hidden;
    white-space: nowrap;
    text-align: left;
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }

  .subdomain-count {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
  }
</style>
