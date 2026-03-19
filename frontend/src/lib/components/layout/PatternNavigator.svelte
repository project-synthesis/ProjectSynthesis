<script lang="ts">
  import { listFamilies, getFamilyDetail, searchPatterns, type PatternFamily, type FamilyDetail, type SearchResult } from '$lib/api/patterns';
  import { patternsStore } from '$lib/stores/patterns.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { domainColor, scoreColor } from '$lib/constants/patterns';

  const PAGE_SIZE = 50;

  let families = $state<PatternFamily[]>([]);
  let loaded = $state(false);
  let error = $state<string | null>(null);
  let hasMore = $state(false);
  let nextOffset = $state<number | null>(null);
  let loadingMore = $state(false);
  let totalFamilies = $state(0);

  // Search state
  let searchQuery = $state('');
  let searchResults = $state<SearchResult[]>([]);
  let searching = $state(false);
  let searchActive = $derived(searchQuery.trim().length > 0);
  let searchTimer: ReturnType<typeof setTimeout> | null = null;

  // Expanded family — inline detail
  let expandedId = $state<string | null>(null);
  let expandedDetail = $state<FamilyDetail | null>(null);
  let expandedLoading = $state(false);

  // Group families by domain
  let grouped = $derived(
    families.reduce<Record<string, PatternFamily[]>>((acc, f) => {
      const d = f.domain || 'general';
      if (!acc[d]) acc[d] = [];
      acc[d].push(f);
      return acc;
    }, {})
  );

  let domains = $derived(Object.keys(grouped).sort());

  // Load families on mount
  let _mountLoaded = $state(false);
  $effect(() => {
    if (!_mountLoaded) {
      _mountLoaded = true;
      loadFamilies();
    }
  });

  // Reload when graph is invalidated (pattern_updated event)
  let _lastGraphLoaded = $state(true);
  $effect(() => {
    const gl = patternsStore.graphLoaded;
    if (_lastGraphLoaded && !gl) {
      // Graph was invalidated — refresh family list
      loadFamilies();
    }
    _lastGraphLoaded = gl;
  });

  async function loadFamilies() {
    try {
      error = null;
      const resp = await listFamilies({ limit: PAGE_SIZE });
      families = resp.items;
      hasMore = resp.has_more;
      nextOffset = resp.next_offset;
      totalFamilies = resp.total;
      loaded = true;
    } catch (err) {
      error = err instanceof Error ? err.message : 'Failed to load patterns';
      loaded = true;
    }
  }

  async function loadMore() {
    if (!hasMore || nextOffset === null || loadingMore) return;
    loadingMore = true;
    try {
      const resp = await listFamilies({ offset: nextOffset, limit: PAGE_SIZE });
      families = [...families, ...resp.items];
      hasMore = resp.has_more;
      nextOffset = resp.next_offset;
    } catch {
      // Silently fail — user can retry
    }
    loadingMore = false;
  }

  function handleSearchInput(e: Event) {
    const q = (e.target as HTMLInputElement).value;
    searchQuery = q;

    if (searchTimer) clearTimeout(searchTimer);

    if (!q.trim()) {
      searchResults = [];
      searching = false;
      return;
    }

    searching = true;
    searchTimer = setTimeout(async () => {
      try {
        searchResults = await searchPatterns(q.trim(), 10);
      } catch {
        searchResults = [];
      }
      searching = false;
    }, 300);
  }

  function clearSearch() {
    searchQuery = '';
    searchResults = [];
    searching = false;
    if (searchTimer) clearTimeout(searchTimer);
  }

  function selectSearchResult(result: SearchResult) {
    const familyId = result.type === 'family' ? result.id : result.family_id;
    if (familyId) {
      patternsStore.selectFamily(familyId);
      expandedId = familyId;
      expandedDetail = null;
      expandedLoading = true;
      getFamilyDetail(familyId)
        .then((d) => { expandedDetail = d; })
        .catch(() => { expandedDetail = null; })
        .finally(() => { expandedLoading = false; });
    }
    clearSearch();
  }

  async function toggleExpand(family: PatternFamily) {
    if (expandedId === family.id) {
      expandedId = null;
      expandedDetail = null;
      patternsStore.selectFamily(null);
      return;
    }
    expandedId = family.id;
    expandedDetail = null;
    expandedLoading = true;
    // Also select in patterns store so Inspector shows detail
    patternsStore.selectFamily(family.id);
    try {
      expandedDetail = await getFamilyDetail(family.id);
    } catch {
      expandedDetail = null;
    }
    expandedLoading = false;
  }

  function openMindmap() {
    patternsStore.loadGraph();
    editorStore.openMindmap();
  }
</script>

<div class="panel">
  <header class="panel-header">
    <span class="section-heading">Patterns</span>
    <span class="total-badge font-mono" title="Total pattern families">{totalFamilies}</span>
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

  <div class="panel-body">
    {#if searchActive}
      <!-- Search results -->
      {#if searching}
        <p class="empty-note">Searching...</p>
      {:else if searchResults.length === 0}
        <p class="empty-note">No matches for "{searchQuery}"</p>
      {:else}
        {#each searchResults as result (result.id)}
          <button
            class="search-result"
            onclick={() => selectSearchResult(result)}
          >
            <span class="search-type font-mono">{result.type === 'family' ? 'FAM' : 'PAT'}</span>
            <span class="search-label">{result.label}</span>
            <span class="search-score font-mono">{(result.score * 100).toFixed(0)}%</span>
          </button>
        {/each}
      {/if}
    {:else if error}
      <p class="empty-note" style="color: var(--color-neon-red);">{error}</p>
    {:else if !loaded}
      <p class="empty-note">Loading...</p>
    {:else if families.length === 0}
      <p class="empty-note">Optimize your first prompt to start building your pattern library.</p>
    {:else}
      {#each domains as domain (domain)}
        <div class="domain-group">
          <div class="domain-header">
            <span class="domain-dot" style="background: {domainColor(domain)};"></span>
            <span class="domain-label">{domain}</span>
            <span class="domain-count">{grouped[domain].length}</span>
          </div>
          {#each grouped[domain] as family (family.id)}
            <button
              class="family-row"
              class:family-row--expanded={expandedId === family.id}
              onclick={() => toggleExpand(family)}
            >
              <span class="family-label">{family.intent_label}</span>
              <span class="family-badges">
                <span class="badge-count font-mono" title="Usage count">{family.usage_count}</span>
                <span
                  class="badge-score font-mono"
                  style="color: {scoreColor(family.avg_score)};"
                  title="Average score"
                >
                  {family.avg_score != null ? family.avg_score.toFixed(1) : '--'}
                </span>
              </span>
            </button>
            {#if expandedId === family.id}
              <div class="family-detail">
                {#if expandedLoading}
                  <p class="detail-note">Loading...</p>
                {:else if expandedDetail}
                  {#if expandedDetail.meta_patterns.length > 0}
                    <div class="meta-list">
                      {#each expandedDetail.meta_patterns as mp (mp.id)}
                        <div class="meta-row">
                          <span class="meta-text">{mp.pattern_text}</span>
                          <span class="meta-count font-mono">{mp.source_count}x</span>
                        </div>
                      {/each}
                    </div>
                  {:else}
                    <p class="detail-note">No meta-patterns extracted yet.</p>
                  {/if}
                {:else}
                  <p class="detail-note">Failed to load detail.</p>
                {/if}
              </div>
            {/if}
          {/each}
        </div>
      {/each}
      {#if hasMore}
        <button
          class="load-more-btn"
          onclick={loadMore}
          disabled={loadingMore}
        >
          {loadingMore ? 'Loading...' : 'Load more'}
        </button>
      {/if}
    {/if}
  </div>
</div>

<style>
  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: center;
    height: 24px;
    padding: 0 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
    gap: 4px;
  }

  .panel-header .section-heading {
    flex: 1;
  }

  .total-badge {
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .panel-body {
    padding: 6px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
    min-height: 0;
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
    border-color: rgba(0, 229, 255, 0.3);
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
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .search-result:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .search-result:active {
    transform: none;
  }

  .search-type {
    font-size: 8px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    width: 24px;
    flex-shrink: 0;
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
    color: var(--color-neon-cyan);
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
    color: var(--color-neon-cyan);
  }

  .empty-note {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 4px 6px;
    line-height: 1.5;
    margin: 0 0 6px;
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
  }

  .domain-dot {
    width: 6px;
    height: 6px;
    flex-shrink: 0;
  }

  .domain-label {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.08em;
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
    cursor: pointer;
    width: 100%;
    text-align: left;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .family-row:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .family-row:active {
    transform: none;
  }

  .family-row--expanded {
    border-color: var(--color-neon-cyan);
    background: rgba(0, 229, 255, 0.04);
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
    color: var(--color-neon-cyan);
  }

  .family-badges {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }

  .badge-count {
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .badge-score {
    font-size: 9px;
    min-width: 20px;
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

  /* ---- Load more ---- */
  .load-more-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 20px;
    margin-top: 4px;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    color: var(--color-text-secondary);
    font-size: 10px;
    font-family: var(--font-sans);
    cursor: pointer;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .load-more-btn:hover {
    border-color: var(--color-border-accent);
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
  }

  .load-more-btn:active {
    transform: none;
  }

  .load-more-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
