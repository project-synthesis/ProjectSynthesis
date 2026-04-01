/**
 * Cluster store — suggestion state, cluster detail, paste detection.
 *
 * Manages auto-suggestion on paste and cluster tree data for the topology view.
 * Replaces the former patterns.svelte.ts store.
 */

import {
  matchPattern, getClusterDetail, getClusterTree, getClusterStats,
  getClusterTemplates, getClusterSimilarityEdges, getClusterInjectionEdges,
  type ClusterMatchResponse, type ClusterDetail, type ClusterNode, type ClusterStats,
  type SimilarityEdge, type InjectionEdge,
} from '$lib/api/clusters';

const PASTE_CHAR_DELTA = 50;
const PASTE_DEBOUNCE_MS = 300;
const SUGGESTION_AUTO_DISMISS_MS = 10_000;

/** Shape of a match result from the cluster match endpoint. */
export type ClusterMatch = NonNullable<ClusterMatchResponse['match']>;

export type StateFilter = null | 'active' | 'mature' | 'template' | 'archived';

class ClusterStore {
  // Suggestion state
  suggestion = $state<ClusterMatch | null>(null);
  suggestionVisible = $state(false);

  // Cluster detail (Inspector)
  selectedClusterId = $state<string | null>(null);
  clusterDetail = $state<ClusterDetail | null>(null);
  clusterDetailLoading = $state(false);
  clusterDetailError = $state<string | null>(null);

  // Taxonomy tree and stats (unified)
  taxonomyTree = $state<ClusterNode[]>([]);
  taxonomyStats = $state<ClusterStats | null>(null);
  taxonomyLoading = $state(false);
  taxonomyError = $state<string | null>(null);

  // Templates
  templates = $state<ClusterNode[]>([]);

  // Domain highlighting for cross-component filtering
  highlightedDomain = $state<string | null>(null);

  // Similarity edges for topology overlay
  similarityEdges = $state<SimilarityEdge[]>([]);
  showSimilarityEdges = $state(false);

  // Injection provenance edges for topology overlay
  injectionEdges = $state<InjectionEdge[]>([]);
  showInjectionEdges = $state(false);

  // State filter — shared between ClusterNavigator tabs and SemanticTopology.
  // Default to 'active' so the working set is visible on load.
  // Users can switch to 'all' or 'archived' to view history.
  stateFilter = $state<StateFilter>('active');

  filteredTaxonomyTree = $derived.by(() => {
    const filter = this.stateFilter;
    const tree = this.taxonomyTree;

    // First pass: collect non-domain nodes that pass the filter
    const childNodes = tree.filter(node => {
      if (node.state === 'domain') return false;
      if (node.state === 'archived' && node.member_count === 0 && node.usage_count === 0) return false;
      return filter === null || node.state === filter;
    });

    // Second pass: include domain nodes only if they have visible children.
    // Without this, empty domains (e.g. "devops" with 0 active clusters)
    // show as orphan headers in the active tab and topology graph.
    const childDomains = new Set(childNodes.map(n => (n.domain ?? 'general').split(':')[0].trim().toLowerCase()));
    const domainNodes = tree.filter(
      node => node.state === 'domain' && childDomains.has(node.label?.toLowerCase() ?? '')
    );

    return [...domainNodes, ...childNodes];
  });

  // Internal
  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private _dismissTimer: ReturnType<typeof setTimeout> | null = null;
  private _lastLength = 0;
  private _loadGeneration = 0;
  private _clusterGeneration = 0;

  setStateFilter(filter: StateFilter): void {
    this.stateFilter = filter;
    // Clear cluster selection — the previous selection may not exist in
    // the new filtered view (e.g., active cluster selected, switch to archived tab)
    this.selectCluster(null);
  }

  toggleHighlightDomain(domain: string): void {
    this.highlightedDomain = this.highlightedDomain === domain ? null : domain;
  }

  /**
   * Called on paste/input — checks if content delta exceeds threshold,
   * debounces, then calls the match endpoint.
   */
  checkForPatterns(text: string): void {
    const delta = Math.abs(text.length - this._lastLength);
    this._lastLength = text.length;

    if (delta < PASTE_CHAR_DELTA) return;

    // Debounce
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(async () => {
      try {
        const resp = await matchPattern(text);
        if (resp.match) {
          this.suggestion = resp.match;
          this.suggestionVisible = true;
          this._startDismissTimer();
        } else {
          this.suggestion = null;
          this.suggestionVisible = false;
        }
      } catch (err) {
        console.warn('Pattern match failed:', err);
      }
    }, PASTE_DEBOUNCE_MS);
  }

  /**
   * User clicked [Apply] — returns the meta-pattern IDs for pipeline injection.
   */
  applySuggestion(): string[] | null {
    if (!this.suggestion) return null;
    const ids = this.suggestion.meta_patterns.map(mp => mp.id);
    this.dismissSuggestion();
    return ids;
  }

  /**
   * User clicked [Skip] or auto-dismiss timer fired.
   */
  dismissSuggestion(): void {
    this.suggestion = null;
    this.suggestionVisible = false;
    if (this._dismissTimer) {
      clearTimeout(this._dismissTimer);
      this._dismissTimer = null;
    }
  }

  async loadTree(): Promise<void> {
    const gen = ++this._loadGeneration;
    this.taxonomyLoading = true;
    this.taxonomyError = null;
    try {
      const [tree, stats, simEdges, injEdges] = await Promise.all([
        getClusterTree(),
        getClusterStats(),
        getClusterSimilarityEdges().catch(() => [] as SimilarityEdge[]),
        getClusterInjectionEdges().catch(() => [] as InjectionEdge[]),
      ]);
      if (gen !== this._loadGeneration) return; // stale response
      this.taxonomyTree = tree;
      this.taxonomyStats = stats;
      this.similarityEdges = simEdges;
      this.injectionEdges = injEdges;
    } catch (err) {
      if (gen !== this._loadGeneration) return;
      this.taxonomyError = (err instanceof Error && err.message) ? err.message : 'Failed to load clusters';
      console.warn('Cluster tree load failed:', err);
    } finally {
      this.taxonomyLoading = false;
    }
  }

  /** Called by SSE handler when taxonomy_changed fires. */
  invalidateClusters(): void {
    this.loadTree();
    // Refresh the currently selected cluster detail (if any) so the
    // Inspector never shows stale data after a warm/cold path mutation.
    if (this.selectedClusterId) {
      this._loadClusterDetail(this.selectedClusterId);
    }
  }


  /**
   * Select a cluster for Inspector display. Pass null to deselect.
   */
  selectCluster(id: string | null): void {
    this.selectedClusterId = id;
    if (!id) {
      // Increment generation to invalidate any in-flight detail loads
      ++this._clusterGeneration;
      this.clusterDetail = null;
      this.clusterDetailError = null;
      this.clusterDetailLoading = false;
      return;
    }
    this._loadClusterDetail(id);
  }

  private async _loadClusterDetail(id: string): Promise<void> {
    const gen = ++this._clusterGeneration;
    this.clusterDetailLoading = true;
    this.clusterDetailError = null;
    try {
      const detail = await getClusterDetail(id);
      if (gen !== this._clusterGeneration) return; // stale — newer load in flight
      this.clusterDetail = detail;
    } catch (err) {
      if (gen !== this._clusterGeneration) return;
      this.clusterDetailError = (err instanceof Error && err.message) ? err.message : 'Failed to load cluster';
      this.clusterDetail = null;
    } finally {
      // Always clear loading. If a newer call is in flight, it will have
      // already set clusterDetailLoading=true synchronously before its
      // first await, so there's no visible flash of false.
      this.clusterDetailLoading = false;
    }
  }

  /** Load template clusters. */
  async loadTemplates(): Promise<void> {
    try {
      const resp = await getClusterTemplates({ limit: 100 });
      this.templates = resp.items;
    } catch (err) {
      console.warn('Template load failed:', err);
    }
  }

  /** Spawn a new optimization from a template cluster.
   *  Returns the prompt, strategy, and label so the caller (ClusterNavigator)
   *  can write them to forgeStore/editorStore without a circular import.
   *  Returns null on empty optimizations or API failure. */
  async spawnTemplate(clusterId: string): Promise<{ prompt: string; strategy: string | null; label: string } | null> {
    try {
      const detail = await getClusterDetail(clusterId);
      if (!detail?.optimizations?.length) return null;

      // Find highest-scoring member
      const best = detail.optimizations.reduce((a, b) =>
        (b.overall_score ?? 0) > (a.overall_score ?? 0) ? b : a
      );

      return {
        prompt: best.raw_prompt ?? '',
        strategy: detail.preferred_strategy ?? null,
        label: detail.label,
      };
    } catch (err) {
      console.warn('spawnTemplate failed:', err);
      return null;
    }
  }

  /**
   * Reset last length tracking (call when prompt is cleared).
   */
  resetTracking(): void {
    this._lastLength = 0;
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.suggestion = null;
    this.suggestionVisible = false;
    this.selectedClusterId = null;
    this.clusterDetail = null;
    this.clusterDetailLoading = false;
    this.clusterDetailError = null;
    this.taxonomyTree = [];
    this.taxonomyStats = null;
    this.taxonomyLoading = false;
    this.taxonomyError = null;
    this.templates = [];
    this.similarityEdges = [];
    this.showSimilarityEdges = false;
    this.injectionEdges = [];
    this.showInjectionEdges = false;
    this.highlightedDomain = null;
    this.stateFilter = 'active';
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    if (this._dismissTimer) clearTimeout(this._dismissTimer);
    this._debounceTimer = null;
    this._dismissTimer = null;
    this._lastLength = 0;
    this._loadGeneration = 0;
    this._clusterGeneration = 0;
  }

  private _startDismissTimer(): void {
    if (this._dismissTimer) clearTimeout(this._dismissTimer);
    this._dismissTimer = setTimeout(() => {
      this.dismissSuggestion();
    }, SUGGESTION_AUTO_DISMISS_MS);
  }
}

export const clustersStore = new ClusterStore();
