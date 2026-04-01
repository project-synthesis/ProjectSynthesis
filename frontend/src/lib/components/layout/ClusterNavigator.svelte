<script lang="ts">
  import type { ClusterNode } from '$lib/api/clusters';
  import { clustersStore, type StateFilter } from '$lib/stores/clusters.svelte';
  import { editorStore, PROMPT_TAB_ID } from '$lib/stores/editor.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { scoreColor, taxonomyColor, stateColor } from '$lib/utils/colors';
  import { formatScore, parsePrimaryDomain } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';

  const PAGE_SIZE = 50;

  // Pagination state — families are derived from the store's tree (no redundant API calls)
  let pageLimit = $state(PAGE_SIZE);

  // State filter — reads from shared store (drives both navigator tabs and topology graph)
  // Candidate state intentionally excluded — candidates are transient internal nodes
  // not yet promoted to user-visible states by the lifecycle service.
  const stateFilter = $derived(clustersStore.stateFilter);

  // Derive families directly from the store's taxonomy tree (already in memory)
  const allFamilies = $derived(clustersStore.taxonomyTree);
  const families = $derived(allFamilies.slice(0, pageLimit));
  const hasMore = $derived(pageLimit < allFamilies.length);
  const loaded = $derived(!clustersStore.taxonomyLoading || allFamilies.length > 0);
  const error = $derived(clustersStore.taxonomyError);

  // Search state — local filtering from taxonomy tree
  let searchQuery = $state('');
  let searchActive = $derived(searchQuery.trim().length > 0);
  interface LocalSearchResult { id: string; label: string; score: number; state: string; domain: string; }
  let searchResults = $derived.by<LocalSearchResult[]>(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [];
    return allFamilies
      .filter(node => node.state !== 'domain' && (node.label ?? '').toLowerCase().includes(q))
      .slice(0, 10)
      .map(node => ({
        id: node.id,
        label: node.label ?? '',
        score: node.coherence ?? 0,
        state: node.state ?? 'active',
        domain: node.domain ?? 'general',
      }));
  });

  // Expanded family — uses store's clusterDetail state
  let expandedId = $state<string | null>(null);

  // Sync: when Inspector dismisses (selectCluster(null)), collapse here too
  $effect(() => {
    if (clustersStore.selectedClusterId === null) {
      expandedId = null;
    }
  });

  // Proven Templates section — pinned regardless of state filter.
  // Templates are a curated showcase, always visible when they exist.
  let showTemplates = $derived(stateFilter === null || stateFilter === 'active' || stateFilter === 'template');
  let templateClusters = $derived(
    showTemplates
      ? families
          .filter(f => f.state === 'template')
          .sort((a, b) => (b.avg_score ?? 0) - (a.avg_score ?? 0))
      : []
  );

  // Filter families by active state tab.
  // - Domain nodes are excluded: they serve as group headers, not child items
  // - Templates have their own dedicated section — exclude from main list
  let filteredFamilies = $derived(
    families.filter(f => {
      if (f.state === 'domain') return false;
      if (showTemplates && f.state === 'template') return false;
      // Hide orphaned clusters — 0 members means all optimizations were
      // reassigned by cold-path. Keep only if patterns are actively used.
      if (f.member_count === 0 && f.usage_count === 0) return false;
      return !stateFilter || f.state === stateFilter;
    })
  );

  // Badge count reflects the filtered view (what the user actually sees)
  const totalFamilies = $derived(filteredFamilies.length);

  // Group filtered families by primary domain (ignores qualifier from "primary: qualifier")
  let grouped = $derived(
    filteredFamilies.reduce<Record<string, ClusterNode[]>>((acc, f) => {
      const d = parsePrimaryDomain(f.domain);
      if (!acc[d]) acc[d] = [];
      acc[d].push(f);
      return acc;
    }, {})
  );

  // Sort domains by cluster count (descending) — most populated first
  let domains = $derived(
    Object.keys(grouped).sort((a, b) => grouped[b].length - grouped[a].length)
  );

  // Ensure tree is loaded on mount (idempotent — store uses generation counter)
  let _mountLoaded = $state(false);
  $effect(() => {
    if (!_mountLoaded) {
      _mountLoaded = true;
      if (allFamilies.length === 0) {
        clustersStore.loadTree();
      }
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
    // selectCluster triggers _loadClusterDetail — use store's state for detail
    clustersStore.selectCluster(family.id);
  }

  function openMindmap() {
    clustersStore.loadTree();
    editorStore.openMindmap();
  }

  function setStateFilter(f: StateFilter) {
    clustersStore.setStateFilter(f);
  }

  async function useTemplate(clusterId: string) {
    const result = await clustersStore.spawnTemplate(clusterId);
    if (result) {
      forgeStore.prompt = result.prompt;
      if (result.strategy) forgeStore.strategy = result.strategy;
      editorStore.activeTabId = PROMPT_TAB_ID;
      addToast('created', `Template loaded: ${result.label}`);
    } else {
      addToast('deleted', 'Failed to load template');
    }
  }
</script>

<div class="panel">
  <header class="panel-header">
    <span class="section-heading">Clusters</span>
    <span class="badge-solid" title="Total clusters">{totalFamilies}</span>
    <button
      class="mindmap-btn"
      onclick={openMindmap}
      title="Open pattern mindmap"
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

  <!-- State filter tabs -->
  <div class="state-tabs" role="tablist" aria-label="Filter by cluster state">
    {#each ([null, 'active', 'mature', 'template', 'archived'] as StateFilter[]) as tab (tab ?? 'all')}
      <button
        class="state-tab"
        class:state-tab--active={stateFilter === tab}
        onclick={() => setStateFilter(tab)}
        role="tab"
        aria-selected={stateFilter === tab}
      >{tab ?? 'All'}</button>
    {/each}
  </div>

  <!-- Search bar -->
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

  <!-- Column headers — OUTSIDE scrollable area, sticky at top -->
  {#if !searchActive && loaded && totalFamilies > 0}
    <div class="column-headers">
      <span class="col-label col-label--name"></span>
      <span class="col-label col-label--members">mbr</span>
      <span class="col-label col-label--usage">use</span>
      <span class="col-label col-label--score">score</span>
    </div>
  {/if}

  <div class="panel-body">
    {#if searchActive}
      <!-- Search results (local filtering from taxonomy tree) -->
      {#if searchResults.length === 0}
        <p class="empty-note">No matches for "{searchQuery}"</p>
      {:else}
        {#each searchResults as result (result.id)}
          <button
            class="search-result"
            onclick={() => selectSearchResult(result)}
            style="--state-color: {stateColor(result.state)};"
          >
            <span class="domain-dot" style="background: {taxonomyColor(result.domain)};"></span>
            <span class="search-label">{result.label}</span>
            <span class="search-score font-mono" use:tooltip={'Centroid cosine similarity to search text'}>{(result.score * 100).toFixed(0)}%</span>
          </button>
        {/each}
      {/if}
    {:else if error}
      <p class="empty-note" style="color: var(--color-neon-red);">{error}</p>
    {:else if !loaded}
      <p class="empty-note">Loading...</p>
    {:else if totalFamilies === 0 && templateClusters.length === 0}
      <p class="empty-note">Optimize your first prompt to start building your pattern library.</p>
    {:else}
      <!-- Proven Templates section -->
      {#if templateClusters.length > 0}
        <div class="templates-section">
          <div class="templates-heading">PROVEN TEMPLATES</div>
          {#each templateClusters as cluster (cluster.id)}
            <div class="template-row">
              <div class="template-info">
                <span class="template-label">{cluster.label}</span>
                <span class="template-meta">
                  <span class="domain-dot" style="background: {taxonomyColor(cluster.domain)};"></span>
                  <span class="template-domain">{cluster.domain}</span>
                  {#if cluster.avg_score != null}
                    <span class="badge-score font-mono" style="color: {scoreColor(cluster.avg_score)};">{formatScore(cluster.avg_score)}</span>
                  {/if}
                  <span class="badge-neon" title="Members">{cluster.member_count}</span>
                  {#if cluster.preferred_strategy}
                    <span class="template-strategy">{cluster.preferred_strategy}</span>
                  {/if}
                </span>
              </div>
              <button
                class="use-template-btn"
                onclick={() => useTemplate(cluster.id)}
                title="Use this template"
              >Use</button>
            </div>
          {/each}
        </div>
      {/if}

      <!-- Domain groups (filtered) -->
      {#each domains as domain (domain)}
        <div class="domain-group">
          <button
            class="domain-header"
            class:domain-header--highlighted={clustersStore.highlightedDomain === domain}
            onclick={() => clustersStore.toggleHighlightDomain(domain)}
            title="Click to highlight in graph"
          >
            <span class="domain-dot" style="background: {taxonomyColor(domain)};"></span>
            <span class="domain-label">{domain}</span>
            <span class="domain-count">{grouped[domain].length}</span>
          </button>
          {#each grouped[domain] as family (family.id)}
            <button
              class="family-row"
              class:family-row--expanded={expandedId === family.id}
              onclick={() => toggleExpand(family)}
              style="--state-color: {stateColor(family.state)};"
            >
              <span class="family-label">{family.label}</span>
              <span class="family-badges">
                <span class="member-count font-mono" title="{family.member_count} {family.member_count === 1 ? 'member' : 'members'}">{family.member_count}m</span>
                <span
                  class="badge-usage font-mono"
                  class:badge-usage--active={family.usage_count > 0}
                  title="Pattern usage count"
                >{family.usage_count}</span>
                <span
                  class="badge-score font-mono"
                  style="color: {scoreColor(family.avg_score)};"
                  title="Average score"
                >
                  {formatScore(family.avg_score)}
                </span>
              </span>
            </button>
            {#if expandedId === family.id}
              <div class="family-detail">
                {#if clustersStore.clusterDetailLoading}
                  <p class="detail-note">Loading...</p>
                {:else if clustersStore.clusterDetail}
                  <div class="detail-stats">
                    <span style="color: {stateColor(clustersStore.clusterDetail.state)}">{clustersStore.clusterDetail.state}</span>
                    <span>{clustersStore.clusterDetail.member_count} members</span>
                    {#if clustersStore.clusterDetail.preferred_strategy}
                      <span>{clustersStore.clusterDetail.preferred_strategy}</span>
                    {/if}
                  </div>
                  {#if clustersStore.clusterDetail.meta_patterns.length > 0}
                    <div class="meta-list">
                      {#each clustersStore.clusterDetail.meta_patterns as mp (mp.id)}
                        <div class="meta-row">
                          <span class="meta-text">{mp.pattern_text}</span>
                          <span class="meta-count font-mono">{mp.source_count}x</span>
                        </div>
                      {/each}
                    </div>
                  {:else}
                    <p class="detail-note">No meta-patterns extracted yet.</p>
                  {/if}
                {:else if clustersStore.clusterDetailError}
                  <p class="detail-note">Failed to load detail.</p>
                {:else}
                  <div class="skeleton-row">
                    <div class="skeleton-bar skeleton-wide"></div>
                    <div class="skeleton-bar skeleton-narrow"></div>
                  </div>
                {/if}
              </div>
            {/if}
          {/each}
        </div>
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
  /* ---- State filter tabs ---- */
  .state-tabs {
    display: flex;
    align-items: center;
    height: 24px;
    padding: 0 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    gap: 2px;
  }

  .state-tab {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 20px;
    padding: 0 6px;
    border: 1px solid transparent;
    background: transparent;
    color: var(--color-text-dim);
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-sans);
    cursor: pointer;
    text-transform: lowercase;
    border-radius: 0;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .state-tab:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
  }

  .state-tab--active {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 8%, transparent);
  }

  /* ---- Search ---- */
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
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
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
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
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
    border-left: 2px solid var(--state-color, transparent);
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .search-result:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
    border-left-color: var(--state-color, transparent);
  }

  .search-result:active {
    transform: none;
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
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .mindmap-btn:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  /* ---- Proven Templates ---- */
  .templates-section {
    padding: 4px 0;
    border-bottom: 1px solid var(--color-border-subtle);
    margin-bottom: 4px;
  }

  .templates-heading {
    font-size: 11px;
    font-family: var(--font-display, var(--font-sans));
    font-weight: 700;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    padding: 2px 6px 4px;
  }

  .template-row {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 2px 6px;
    min-height: 28px;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .template-row:hover {
    background: var(--color-bg-hover);
  }

  .template-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .template-label {
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .template-meta {
    display: flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
  }

  .template-domain {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .template-strategy {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 80px;
  }

  .use-template-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 20px;
    padding: 0 6px;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    color: var(--color-text-secondary);
    font-size: 10px;
    font-family: var(--font-sans);
    font-weight: 600;
    cursor: pointer;
    border-radius: 0;
    flex-shrink: 0;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .use-template-btn:hover {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 8%, transparent);
  }

  /* ---- Column headers ---- */
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

  /* ---- Usage badge ---- */
  .badge-usage {
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
    width: 14px;
    text-align: right;
  }

  .badge-usage--active {
    color: var(--color-neon-teal);
  }

  /* ---- Domain groups ---- */
  .domain-group {
    margin-bottom: 4px;
  }

  .domain-header {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 20px;
    padding: 0 6px;
    width: 100%;
    border: none;
    background: transparent;
    cursor: pointer;
    text-align: left;
    transition: background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                box-shadow 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .domain-header:hover {
    background: var(--color-bg-hover);
  }

  .domain-header--highlighted {
    box-shadow: inset 0 0 0 1px var(--color-border-accent);
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
  }

  .domain-dot {
    width: 8px;
    height: 8px;
    flex-shrink: 0;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.15);
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

  /* ---- Family rows ---- */
  .family-row {
    display: flex;
    align-items: center;
    gap: 4px;
    height: 22px;
    padding: 0 6px 0 16px;
    background: transparent;
    border: 1px solid transparent;
    border-left: 2px solid var(--state-color, transparent);
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .family-row:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
    border-left-color: var(--state-color, transparent);
  }

  .family-row:active {
    transform: none;
  }

  .family-row--expanded {
    border-color: var(--tier-accent, var(--color-neon-cyan));
    border-left-color: var(--state-color, var(--tier-accent, var(--color-neon-cyan)));
    background: color-mix(in srgb, var(--tier-accent, var(--color-neon-cyan)) 4%, transparent);
  }

  .family-label {
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
    min-width: 0;
  }

  .family-row--expanded .family-label {
    color: var(--tier-accent, var(--color-neon-cyan));
  }

  .family-badges {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }


  .badge-score {
    font-size: 9px;
    width: 24px;
    text-align: right;
  }

  /* ---- Expanded detail ---- */
  .family-detail {
    padding: 2px 6px 4px 16px;
    border: 1px solid var(--color-border-subtle);
    border-top: none;
    background: var(--color-bg-card, rgba(6, 6, 12, 0.5));
  }

  .detail-note {
    font-size: 9px;
    color: var(--color-text-dim);
    padding: 2px 0;
    margin: 0;
  }

  .meta-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .meta-row {
    display: flex;
    align-items: flex-start;
    gap: 4px;
    padding: 2px 0;
  }

  .meta-text {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    line-height: 1.4;
    flex: 1;
    min-width: 0;
  }

  .meta-count {
    font-size: 8px;
    color: var(--color-text-dim);
    flex-shrink: 0;
  }

  .member-count {
    font-size: 9px;
    color: var(--color-text-dim);
    flex-shrink: 0;
    width: 22px;
    text-align: right;
  }

  .detail-stats {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    padding: 2px 0 4px;
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
    margin-bottom: 4px;
  }

  .skeleton-row {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding: 3px 0;
  }

  .skeleton-bar {
    height: 6px;
    background: linear-gradient(90deg, var(--color-bg-card) 25%, var(--color-bg-hover) 50%, var(--color-bg-card) 75%);
    background-size: 200% 100%;
    animation: shimmer 1500ms ease-in-out infinite;
  }

  .skeleton-wide { width: 80%; }
  .skeleton-narrow { width: 50%; }

  @keyframes shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
  }
</style>
