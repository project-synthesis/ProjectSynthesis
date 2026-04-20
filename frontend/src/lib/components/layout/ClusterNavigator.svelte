<script lang="ts">
  /**
   * ClusterNavigator — sidebar panel for cluster discovery and navigation.
   *
   * This file is the orchestrator. The heavy lifting lives in extracted
   * sibling components:
   *   - StateFilterTabs   — tablist with sliding 1px indicator.
   *   - TemplatesSection  — Proven Templates showcase.
   *   - DomainGroup       — per-domain hierarchy (direct + sub-domains).
   *   - ClusterRow        — shared row primitive (selection + detail pane).
   *
   * What stays here: panel chrome, search bar, domain-readiness section,
   * hierarchical grouping derivation, cross-tab scroll-into-view effect,
   * open-linked-optimization bridge into the forge/editor stores.
   */
  import { tick } from 'svelte';
  import type { ClusterNode } from '$lib/api/clusters';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { stateColor, taxonomyColor } from '$lib/utils/colors';
  import { parsePrimaryDomain } from '$lib/utils/formatting';
  import { getOptimization } from '$lib/api/client';
  import { CLUSTER_NAV_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import { tooltip } from '$lib/actions/tooltip';
  import DomainReadinessPanel from '$lib/components/taxonomy/DomainReadinessPanel.svelte';
  import { readinessStore } from '$lib/stores/readiness.svelte';
  import CollapsibleSectionHeader from '$lib/components/shared/CollapsibleSectionHeader.svelte';
  import { navCollapse } from '$lib/stores/nav_collapse.svelte';
  import { templatesStore } from '$lib/stores/templates.svelte';
  import { projectStore } from '$lib/stores/project.svelte';
  import { fade, slide } from 'svelte/transition';
  import { navFade, navSlide } from '$lib/utils/transitions';
  import StateFilterTabs from './StateFilterTabs.svelte';
  import TemplatesSection from './TemplatesSection.svelte';
  import DomainGroup from './DomainGroup.svelte';

  function handleReadinessSelect(domainId: string): void {
    clustersStore.selectCluster(domainId);
  }
  function handleReadinessRefresh(e: MouseEvent): void {
    e.stopPropagation();
    void readinessStore.loadAll(true);
  }

  const PAGE_SIZE = 500;

  let pageLimit = $state(PAGE_SIZE);

  const stateFilter = $derived(clustersStore.stateFilter);

  const candidateCount = $derived(
    clustersStore.taxonomyTree.filter((n) => n.state === 'candidate').length,
  );

  const allFamilies = $derived(clustersStore.filteredTaxonomyTree);

  const subDomainParentMap = $derived.by<Map<string, { parentLabel: string; parentId: string }>>(() => {
    const map = new Map<string, { parentLabel: string; parentId: string }>();
    const domainNodesById = new Map<string, ClusterNode>();
    for (const n of allFamilies) {
      if (n.state === 'domain') domainNodesById.set(n.id, n);
    }
    for (const n of allFamilies) {
      if (n.state === 'domain' && n.parent_id) {
        const parent = domainNodesById.get(n.parent_id);
        if (parent && parent.state === 'domain') {
          map.set(n.id, { parentLabel: parent.label, parentId: parent.id });
        }
      }
    }
    return map;
  });

  const families = $derived(allFamilies.slice(0, pageLimit));
  const hasMore = $derived(pageLimit < allFamilies.length);
  const loaded = $derived(!clustersStore.taxonomyLoading || allFamilies.length > 0);
  const error = $derived(clustersStore.taxonomyError);

  let searchQuery = $state('');
  let searchActive = $derived(searchQuery.trim().length > 0);
  interface LocalSearchResult { id: string; label: string; score: number; state: string; domain: string; }
  let searchResults = $derived.by<LocalSearchResult[]>(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [];
    return allFamilies
      .filter((node) => node.state !== 'domain' && (node.label ?? '').toLowerCase().includes(q))
      .slice(0, 10)
      .map((node) => ({
        id: node.id,
        label: node.label ?? '',
        score: node.coherence ?? 0,
        state: node.state ?? 'active',
        domain: node.domain ?? 'general',
      }));
  });

  let expandedId = $state<string | null>(null);

  $effect(() => {
    const id = clustersStore.selectedClusterId;
    expandedId = id;
    if (id) {
      tick().then(() => {
        const el = document.querySelector(`[data-cluster-id="${id}"]`);
        if (el && typeof el.scrollIntoView === 'function') {
          el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
      });
    }
  });

  const hasVisibleTemplates = $derived(
    templatesStore.templates.some((t) => !t.retired_at),
  );

  let filteredFamilies = $derived(
    families.filter((f) => f.state !== 'domain'),
  );

  const totalFamilies = $derived(
    allFamilies.filter((f) => f.state !== 'domain').length,
  );

  interface SubDomainGroupInternal {
    id: string;
    label: string;
    displayLabel: string;
    parentLabel: string;
    clusters: ClusterNode[];
  }
  interface DomainBucket {
    directClusters: ClusterNode[];
    subDomains: SubDomainGroupInternal[];
    totalCount: number;
  }

  let hierarchicalGrouped = $derived.by<Record<string, DomainBucket>>(() => {
    const result: Record<string, DomainBucket> = {};
    const subDomainIds = new Set(subDomainParentMap.keys());

    for (const f of filteredFamilies) {
      if (f.state === 'project') {
        const key = `project:${f.label}`;
        if (!result[key]) result[key] = { directClusters: [], subDomains: [], totalCount: 0 };
        result[key].directClusters.push(f);
        result[key].totalCount++;
        continue;
      }

      const clusterParentId = f.parent_id;

      if (clusterParentId && subDomainIds.has(clusterParentId)) {
        const subInfo = subDomainParentMap.get(clusterParentId)!;
        const topDomain = parsePrimaryDomain(subInfo.parentLabel);
        if (!result[topDomain]) result[topDomain] = { directClusters: [], subDomains: [], totalCount: 0 };

        let subGroup = result[topDomain].subDomains.find((s) => s.id === clusterParentId);
        if (!subGroup) {
          const subNode = allFamilies.find((n) => n.id === clusterParentId);
          const subLabel = subNode?.label ?? clusterParentId;
          subGroup = {
            id: clusterParentId,
            label: subLabel,
            displayLabel: subLabel,
            parentLabel: subInfo.parentLabel,
            clusters: [],
          };
          result[topDomain].subDomains.push(subGroup);
        }
        subGroup.clusters.push(f);
        result[topDomain].totalCount++;
      } else {
        const d = parsePrimaryDomain(f.domain);
        if (!result[d]) result[d] = { directClusters: [], subDomains: [], totalCount: 0 };
        result[d].directClusters.push(f);
        result[d].totalCount++;
      }
    }

    return result;
  });

  let domains = $derived(
    Object.keys(hierarchicalGrouped).sort(
      (a, b) => hierarchicalGrouped[b].totalCount - hierarchicalGrouped[a].totalCount,
    ),
  );

  let _mountLoaded = $state(false);
  $effect(() => {
    if (!_mountLoaded) {
      _mountLoaded = true;
      if (clustersStore.taxonomyTree.length === 0) {
        clustersStore.loadTree();
      }
      templatesStore.load(null);
    }
  });

  function loadMore() {
    pageLimit += PAGE_SIZE;
  }

  function handleSearchInput(e: Event) {
    searchQuery = (e.target as HTMLInputElement).value;
  }

  function clearSearch() {
    searchQuery = '';
  }

  function selectSearchResult(result: LocalSearchResult) {
    expandedId = result.id;
    clustersStore.selectCluster(result.id);
    clearSearch();
  }

  async function toggleExpand(family: ClusterNode) {
    if (expandedId === family.id) {
      expandedId = null;
      clustersStore.selectCluster(null);
      return;
    }
    expandedId = family.id;
    clustersStore.selectCluster(family.id);
  }

  function openMindmap() {
    clustersStore.loadTree();
    editorStore.openMindmap();
  }

  async function openLinkedOpt(traceId: string, optId: string) {
    try {
      const opt = await getOptimization(traceId);
      forgeStore.loadFromRecord(opt);
      editorStore.openResult(optId);
    } catch {
      addToast('deleted', 'Failed to load optimization');
    }
  }
</script>

<div class="panel">
  <header class="panel-header">
    <span class="section-heading">Clusters</span>
    <span class="badge-solid" use:tooltip={CLUSTER_NAV_TOOLTIPS.total_clusters}>{totalFamilies}</span>
    <button
      class="mindmap-btn"
      onclick={openMindmap}
      use:tooltip={CLUSTER_NAV_TOOLTIPS.open_mindmap}
      aria-label="Open pattern mindmap"
    >
      <svg width="12" height="12" viewBox="0 0 18 18" aria-hidden="true">
        <circle cx="9" cy="9" r="2" fill="none" stroke="currentColor" stroke-width="1.5"/>
        <circle cx="3" cy="4" r="1.5" fill="none" stroke="currentColor" stroke-width="1"/>
        <circle cx="15" cy="4" r="1.5" fill="none" stroke="currentColor" stroke-width="1"/>
        <circle cx="15" cy="14" r="1.5" fill="none" stroke="currentColor" stroke-width="1"/>
        <path d="M7.5 7.5L4.5 5.5M10.5 7.5L13.5 5.5M10.5 10.5L13.5 12.5" fill="none" stroke="currentColor" stroke-width="1" stroke-linecap="round"/>
      </svg>
    </button>
  </header>

  <StateFilterTabs
    {stateFilter}
    {candidateCount}
    onChange={(f) => clustersStore.setStateFilter(f)}
  />

  <div class="search-bar">
    <input
      class="search-input"
      type="text"
      placeholder="Search patterns..."
      value={searchQuery}
      oninput={handleSearchInput}
      aria-label="Search patterns"
    />
    {#if searchActive}
      <button class="search-clear" onclick={clearSearch} aria-label="Clear search">×</button>
    {/if}
  </div>

  <div class="panel-body">
    {#if searchActive}
      {#if searchResults.length === 0}
        <p class="empty-note">No matches for "{searchQuery}"</p>
      {:else}
        {#each searchResults as result (result.id)}
          <button
            class="search-result"
            onclick={() => selectSearchResult(result)}
            aria-label={`Open cluster ${result.label}`}
            style="--state-color: {stateColor(result.state)};"
            transition:fade={navFade}
          >
            <span class="domain-dot" style="background: {taxonomyColor(result.domain)};"></span>
            <span class="search-label">{result.label}</span>
            <span class="search-score font-mono" use:tooltip={CLUSTER_NAV_TOOLTIPS.similarity_score}>{(result.score * 100).toFixed(0)}%</span>
          </button>
        {/each}
      {/if}
    {:else if error}
      <p class="empty-note" style="color: var(--color-neon-red);">{error}</p>
    {:else if !loaded}
      <p class="empty-note">Loading...</p>
    {:else if totalFamilies === 0 && !hasVisibleTemplates}
      {#if projectStore.currentProjectId !== null}
        <div class="scoped-empty">
          <p class="empty-note">No prompts in <strong>{projectStore.currentLabel}</strong> yet.</p>
          <p class="empty-note">Try prompting, or</p>
          <button
            type="button"
            class="scoped-empty-cta"
            onclick={() => projectStore.setCurrent(null)}
          >Switch to All projects</button>
        </div>
      {:else}
        <p class="empty-note">Optimize your first prompt to start building your pattern library.</p>
      {/if}
    {:else}
      <div class="section-wrapper">
        <CollapsibleSectionHeader
          open={navCollapse.isOpen('readiness')}
          onToggle={() => navCollapse.toggle('readiness')}
          label="DOMAIN READINESS"
          count={readinessStore.reports.length}
        >
          {#snippet actions()}
            <button
              type="button"
              class="section-action"
              onclick={handleReadinessRefresh}
              disabled={readinessStore.loading}
              use:tooltip={'Force a fresh recomputation (bypasses 30s backend cache).'}
              aria-label="Refresh readiness"
            >{readinessStore.loading ? '···' : 'SYNC'}</button>
          {/snippet}
        </CollapsibleSectionHeader>
        {#if navCollapse.isOpen('readiness')}
          <div transition:slide={navSlide}>
            <DomainReadinessPanel onSelect={handleReadinessSelect} hideHeader={true} />
          </div>
        {/if}
      </div>

      <TemplatesSection />

      {#if totalFamilies > 0}
        <div class="column-headers">
          <span class="col-label col-label--name"></span>
          <span class="col-label col-label--members">mbr</span>
          <span class="col-label col-label--usage">use</span>
          <span class="col-label col-label--score">score</span>
        </div>
      {/if}

      {#each domains as domain (domain)}
        <DomainGroup
          {domain}
          group={hierarchicalGrouped[domain]}
          {expandedId}
          onToggleExpand={toggleExpand}
          onOpenLinkedOpt={openLinkedOpt}
        />
      {/each}
      {#if hasMore}
        <button
          class="action-btn" style="margin-top: 4px; width: 100%;"
          onclick={loadMore}
        >
          Load more
        </button>
      {/if}
    {/if}
  </div>
</div>

<style>
  .scoped-empty {
    display: flex;
    flex-direction: column;
    gap: 6px;
    align-items: flex-start;
    padding: 8px 10px;
  }

  .scoped-empty-cta {
    margin-top: 2px;
    padding: 4px 10px;
    background: transparent;
    border: 1px solid var(--color-neon-cyan);
    color: var(--color-neon-cyan);
    font-family: var(--font-mono);
    font-size: 10px;
    cursor: pointer;
    transition: background var(--duration-hover) var(--ease-spring);
  }

  .scoped-empty-cta:hover {
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
  }

  .search-bar {
    display: flex;
    align-items: center;
    height: 24px;
    padding: 0 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    gap: 2px;
  }

  .search-input {
    flex: 1;
    height: 18px;
    padding: 0 4px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-size: 10px;
    font-family: var(--font-sans);
    outline: none;
    transition: border-color var(--duration-hover) var(--ease-spring);
  }

  .search-input:focus {
    border-color: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 30%, transparent);
  }

  .search-input::placeholder {
    color: var(--color-text-dim);
  }

  .search-clear {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 12px;
    cursor: pointer;
    padding: 0;
    flex-shrink: 0;
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .search-clear:hover {
    color: var(--color-text-primary);
  }

  .search-result {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    padding: 0 6px;
    background: transparent;
    border: 1px solid transparent;
    border-left: 1px solid var(--state-color, transparent);
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: color var(--duration-hover) var(--ease-spring),
                border-color var(--duration-hover) var(--ease-spring),
                background var(--duration-hover) var(--ease-spring);
  }

  .search-result:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
    border-left-color: var(--state-color, transparent);
  }

  .search-result:active {
    background: var(--color-bg-hover);
  }

  .search-label {
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    min-width: 0;
  }

  .search-score {
    font-size: 9px;
    color: var(--tier-accent, var(--color-neon-cyan));
    flex-shrink: 0;
  }

  .mindmap-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 18px;
    height: 18px;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    cursor: pointer;
    padding: 0;
    flex-shrink: 0;
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .mindmap-btn:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .mindmap-btn:active {
    transform: scale(0.92);
  }

  .section-wrapper {
    padding: 0 0 4px;
    margin-bottom: 4px;
  }

  .section-action {
    font-family: var(--font-mono);
    font-size: 9px;
    letter-spacing: 0.05em;
    padding: 0 6px;
    height: 16px;
    display: flex;
    align-items: center;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    cursor: pointer;
    box-sizing: border-box;
    transition: color var(--duration-micro) var(--ease-spring),
      border-color var(--duration-micro) var(--ease-spring);
  }

  .section-action:hover:not(:disabled) {
    color: var(--color-neon-cyan);
    border-color: color-mix(in srgb, var(--color-neon-cyan) 40%, transparent);
  }

  .section-action:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .column-headers {
    display: flex;
    align-items: center;
    height: 16px;
    padding: 0 6px 0 16px;
    gap: 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .col-label {
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    flex-shrink: 0;
    text-align: right;
  }

  .col-label--name {
    flex: 1;
    min-width: 0;
    text-align: left;
  }

  .col-label--members {
    width: 22px;
  }

  .col-label--usage {
    width: 14px;
  }

  .col-label--score {
    width: 24px;
  }

  .domain-dot {
    width: 8px;
    height: 8px;
    flex-shrink: 0;
    outline: 1px solid color-mix(in srgb, var(--color-text-primary) 15%, transparent);
    outline-offset: -1px;
  }
</style>
