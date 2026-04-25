/**
 * Cluster store — suggestion state, cluster detail, paste detection.
 *
 * Manages auto-suggestion on paste and cluster tree data for the topology view.
 * Replaces the former patterns.svelte.ts store.
 */

import {
  matchPattern, getClusterDetail, getClusterTree, getClusterStats,
  getClusterSimilarityEdges, getClusterInjectionEdges,
  getClusterActivity, getClusterActivityHistory,
  type ClusterMatchResponse, type ClusterDetail, type ClusterNode, type ClusterStats,
  type SimilarityEdge, type InjectionEdge, type TaxonomyActivityEvent,
  type MetaPatternItem,
} from '$lib/api/clusters';
import { projectStore } from '$lib/stores/project.svelte';

/** A node is orphaned when it has no members and no usage — its optimizations
 *  were reassigned by cold-path but the cluster wasn't retired yet. */
export function isOrphanNode(node: { member_count: number; usage_count: number }): boolean {
  return node.member_count === 0 && node.usage_count === 0;
}

const PASTE_CHAR_DELTA = 30;         // chars delta to detect paste event
const PASTE_DEBOUNCE_MS = 300;       // fast debounce for paste
const TYPING_DEBOUNCE_MS = 800;      // longer debounce for keystroke typing
const MIN_PROMPT_LENGTH = 30;        // don't match fragments shorter than this

/** Shape of a match result from the cluster match endpoint. */
export type ClusterMatch = NonNullable<ClusterMatchResponse['match']> & {
  // Narrowed from optional to required after store-side defaulting
  cross_cluster_patterns: MetaPatternItem[];
  match_level: 'family' | 'cluster';
};

export type StateFilter = null | 'active' | 'candidate' | 'mature' | 'archived';

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

    // First pass: collect non-domain nodes that pass the filter.
    // 'active' filter shows ALL living states (active + mature + candidate) —
    // it represents the working taxonomy, not just the literal "active"
    // lifecycle state.  Other filters are exact-match.
    const _LIVING_STATES = new Set(['active', 'mature', 'candidate']);
    const childNodes = tree.filter(node => {
      if (node.state === 'domain') return false;
      if (isOrphanNode(node)) return false;
      if (filter === null) return true;
      if (filter === 'active') return _LIVING_STATES.has(node.state);
      return node.state === filter;
    });

    // Second pass: include domain nodes only if they have visible children.
    // Without this, empty domains (e.g. "devops" with 0 active clusters)
    // show as orphan headers in the active tab and topology graph.
    //
    // Two inclusion paths:
    //   1. Top-level domains: their label matches a child cluster's domain field
    //   2. Sub-domains: any visible child cluster has parent_id pointing to them
    const childDomains = new Set(childNodes.map(n => (n.domain ?? 'general').split(':')[0].trim().toLowerCase()));
    const childParentIds = new Set(childNodes.map(n => n.parent_id).filter(Boolean));
    const domainNodes = tree.filter(
      node => node.state === 'domain' && (
        childDomains.has(node.label?.toLowerCase() ?? '') ||
        childParentIds.has(node.id)
      )
    );

    return [...domainNodes, ...childNodes];
  });

  /** Global count of non-orphan active clusters — independent of state filter tab.
   *  Used by StatusBar as the canonical cluster indicator. */
  liveClusterCount = $derived(
    this.taxonomyTree.filter(n =>
      n.state === 'active' && !isOrphanNode(n)
    ).length
  );

  /** Canonical state breakdown from the filtered tree — excludes orphans and domains.
   *  Shared by TopologyControls footer, Inspector health counts, and any future consumer. */
  clusterCounts = $derived.by(() => {
    let active = 0, candidate = 0;
    for (const n of this.filteredTaxonomyTree) {
      if (n.state === 'active') active++;
      else if (n.state === 'candidate') candidate++;
    }
    return { active, candidate };
  });

  // Internal — pattern detection
  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private _lastLength = 0;
  // _lastMatchedText was private until Tier 1 — lifted to public so
  // ContextPanel can gate its 'no match' empty-state copy on whether
  // the store has actually attempted a match for the current text.
  _lastMatchedText = $state('');
  // Transient fetch-state flags surfaced for ContextPanel rendering:
  //   _matchInFlight: true while a match request is awaiting response.
  //   _matchError: 'network' if the last match request rejected.
  _matchInFlight = $state(false);
  _matchError: 'network' | null = $state(null);
  private _matchAbort: AbortController | null = null;  // cancel in-flight match requests
  private _loadGeneration = 0;
  private _clusterGeneration = 0;

  setStateFilter(filter: StateFilter): void {
    this.stateFilter = filter;
    // Only clear selection if the selected cluster wouldn't be visible in the
    // new filter. Switching to "all" (null) or to the same state as the selected
    // cluster preserves the selection. Switching to a different state clears it.
    if (this.selectedClusterId && filter !== null) {
      const selected = this.taxonomyTree.find(n => n.id === this.selectedClusterId);
      if (!selected || (selected.state !== filter && selected.state !== 'domain')) {
        this.selectCluster(null);
      }
    }
  }

  toggleHighlightDomain(domain: string): void {
    this.highlightedDomain = this.highlightedDomain === domain ? null : domain;
  }

  /**
   * Called on every input event — two-path detection:
   * - Path A (paste): delta >= 30 chars → fast 300ms debounce
   * - Path B (typing): prompt >= 30 chars → slower 800ms debounce
   * Aborts in-flight requests when new input arrives.
   */
  checkForPatterns(text: string): void {
    const delta = Math.abs(text.length - this._lastLength);
    this._lastLength = text.length;

    // Don't match fragments
    if (text.length < MIN_PROMPT_LENGTH) return;

    // Skip if content hasn't meaningfully changed from last match
    const trimmed = text.trim();
    if (trimmed === this._lastMatchedText) return;

    // Determine debounce: fast for paste, slow for typing
    const isPaste = delta >= PASTE_CHAR_DELTA;
    const debounceMs = isPaste ? PASTE_DEBOUNCE_MS : TYPING_DEBOUNCE_MS;

    // Clear existing debounce timer
    if (this._debounceTimer) clearTimeout(this._debounceTimer);

    this._debounceTimer = setTimeout(async () => {
      // Abort any in-flight match request
      if (this._matchAbort) this._matchAbort.abort();
      this._matchAbort = new AbortController();
      this._matchInFlight = true;
      this._matchError = null;

      const signal = this._matchAbort.signal;
      try {
        // ADR-005 F3 — scope pattern matching to the current project so
        // cross-project patterns don't surface suggestions for unrelated
        // work.  null ("All projects") keeps legacy global behaviour.
        const resp = await matchPattern(
          trimmed,
          signal,
          projectStore.currentProjectId,
        );
        this._lastMatchedText = trimmed;

        if (resp.match && resp.match.meta_patterns.length > 0) {
          // Defensive defaults for legacy responses (backwards compat).
          this.suggestion = {
            ...resp.match,
            cross_cluster_patterns: resp.match.cross_cluster_patterns ?? [],
            match_level: resp.match.match_level ?? 'cluster',
          };
          this.suggestionVisible = true;
        } else {
          this.suggestion = null;
          this.suggestionVisible = false;
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        this._matchError = 'network';
        console.warn('Pattern match failed:', err);
      } finally {
        this._matchInFlight = false;
      }
    }, debounceMs);
  }

  /**
   * User clicked [Apply] — returns the meta-pattern IDs + cluster label
   * for pipeline injection and UI confirmation chip.
   *
   * Tier 1: panel stays visible after apply; selection state is owned
   * by ContextPanel via forgeStore.appliedPatternIds. The store no
   * longer dismisses or hides the suggestion.
   */
  applySuggestion(): { ids: string[]; clusterLabel: string } | null {
    if (!this.suggestion) return null;
    const ids = this.suggestion.meta_patterns.map(mp => mp.id);
    const clusterLabel = this.suggestion.cluster.label;
    return { ids, clusterLabel };
  }

  async loadTree(): Promise<void> {
    const gen = ++this._loadGeneration;
    this.taxonomyLoading = true;
    this.taxonomyError = null;
    try {
      // ADR-005 F4 — tree/stats scoped to current project.  "All projects"
      // (null) keeps the panoramic view; scoped mode uses the B6
      // dominant_project_id filter that keeps structural skeleton visible.
      const pid = projectStore.currentProjectId;
      const [tree, stats, simEdges, injEdges] = await Promise.all([
        getClusterTree(undefined, pid),
        getClusterStats(pid),
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
  async invalidateClusters(): Promise<void> {
    await this.loadTree();
    // Refresh the currently selected cluster detail (if any) so the
    // Inspector never shows stale data after a warm/cold path mutation.
    // Check existence in the new tree first to avoid ghost selections.
    if (this.selectedClusterId) {
      const exists = Array.isArray(this.taxonomyTree) && this.taxonomyTree.some(n => n.id === this.selectedClusterId);
      if (exists) {
        this._loadClusterDetail(this.selectedClusterId);
      } else {
        this.selectCluster(null);
      }
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
      // Auto-switch state filter if the selected cluster would be hidden
      // by the current filter (e.g., clicking a candidate while on "active" tab).
      // null = "all" which shows everything, so no switch needed.
      // Don't switch if the cluster is an orphan (0 members, 0 usage) — it won't
      // appear in the navigator even after switching tabs.
      const isOrphan = (detail.member_count ?? 0) === 0 && (detail.usage_count ?? 0) === 0;
      if (
        this.stateFilter !== null
        && detail.state !== 'domain'
        && detail.state !== this.stateFilter
        && !isOrphan
      ) {
        this.stateFilter = (detail.state as StateFilter) ?? null;
      }
    } catch (err) {
      if (gen !== this._clusterGeneration) return;
      // Cluster no longer exists (404) or load failed — clear the selection
      // entirely so the Inspector doesn't show a stale "Cluster not found"
      // error and the topology doesn't keep trying to focus on a ghost node.
      this.selectedClusterId = null;
      this.clusterDetail = null;
      this.clusterDetailError = null;
    } finally {
      // Always clear loading. If a newer call is in flight, it will have
      // already set clusterDetailLoading=true synchronously before its
      // first await, so there's no visible flash of false.
      this.clusterDetailLoading = false;
    }
  }

  /**
   * Reset last length tracking (call when prompt is cleared).
   */
  resetTracking(): void {
    this._lastLength = 0;
  }

  // Activity panel state
  activityEvents = $state<TaxonomyActivityEvent[]>([]);
  activityOpen = $state(false);
  activityLoading = $state(false);

  // Seed batch progress (persistent across modal open/close)
  seedBatchActive = $state(false);
  seedBatchProgress = $state<{ completed: number; total: number; current: string }>({ completed: 0, total: 0, current: '' });

  updateSeedProgress(data: { phase?: string; completed?: number; total?: number; current_prompt?: string }): void {
    if (data.phase === 'optimize') {
      this.seedBatchActive = true;
      this.seedBatchProgress = {
        completed: data.completed ?? this.seedBatchProgress.completed,
        total: data.total ?? this.seedBatchProgress.total,
        current: data.current_prompt ?? this.seedBatchProgress.current,
      };
    }
  }

  clearSeedBatch(): void {
    this.seedBatchActive = false;
    this.seedBatchProgress = { completed: 0, total: 0, current: '' };
  }

  pushActivityEvent(event: TaxonomyActivityEvent): void {
    this.activityEvents = [event, ...this.activityEvents].slice(0, 200);
  }

  toggleActivity(): void {
    this.activityOpen = !this.activityOpen;
    if (this.activityOpen) {
      this.loadActivity();
    }
  }

  async loadActivity(params?: { path?: string; op?: string }): Promise<void> {
    this.activityLoading = true;
    try {
      const resp = await getClusterActivity({ limit: 200, ...params });
      if (resp.events.length >= 20) {
        // Ring buffer has enough events — use as-is
        this.activityEvents = resp.events;
      } else {
        // Ring buffer sparse (after restart only a few warm-path events
        // exist) — merge with today's JSONL for meaningful context.
        const today = new Date().toISOString().slice(0, 10);
        const hist = await getClusterActivityHistory({ date: today, limit: 200 });
        // Backend now emits the range/date response newest-first within
        // each day; no client-side reverse required.
        const jsonlEvents = hist.events;

        // Merge: ring buffer is authoritative for recent, dedupe by key
        const seen = new Set(resp.events.map(
          (e: TaxonomyActivityEvent) => `${e.ts}|${e.op}|${e.decision}`
        ));
        const merged: TaxonomyActivityEvent[] = [...resp.events];
        for (const e of jsonlEvents) {
          const key = `${e.ts}|${e.op}|${e.decision}`;
          if (!seen.has(key)) {
            seen.add(key);
            merged.push(e);
          }
        }
        merged.sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''));
        this.activityEvents = merged.slice(0, 200);
      }
    } catch (err) {
      console.warn('Activity load failed:', err);
    } finally {
      this.activityLoading = false;
    }
  }

  /**
   * Hydrate `activityEvents` for an Observatory time window.
   *
   * Calls the JSONL `since`/`until` range variant and merges the response
   * with the in-memory ring buffer. Live SSE events continue to prepend
   * via `pushActivityEvent`, so the timeline stays current without losing
   * the historical baseline.
   *
   * `period` accepts the Observatory canonical values (`24h | 7d | 30d`).
   * Caps at 200 events to keep render cheap; the Timeline shows
   * newest-first which matches user intent for an overview surface.
   */
  async loadActivityForPeriod(period: '24h' | '7d' | '30d'): Promise<void> {
    const PERIOD_DAYS: Record<typeof period, number> = { '24h': 1, '7d': 7, '30d': 30 };
    const today = new Date();
    const until = today.toISOString().slice(0, 10);
    const sinceDate = new Date(today.getTime() - (PERIOD_DAYS[period] - 1) * 86_400_000);
    const since = sinceDate.toISOString().slice(0, 10);

    this.activityLoading = true;
    try {
      const [ring, hist] = await Promise.all([
        getClusterActivity({ limit: 200 }),
        getClusterActivityHistory({ since, until, limit: 200 }),
      ]);

      const seen = new Set(ring.events.map(
        (e: TaxonomyActivityEvent) => `${e.ts}|${e.op}|${e.decision}`
      ));
      const merged: TaxonomyActivityEvent[] = [...ring.events];
      for (const e of hist.events) {
        const key = `${e.ts}|${e.op}|${e.decision}`;
        if (!seen.has(key)) {
          seen.add(key);
          merged.push(e);
        }
      }
      merged.sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''));
      this.activityEvents = merged.slice(0, 200);
    } catch (err) {
      console.warn('Activity period load failed:', err);
    } finally {
      this.activityLoading = false;
    }
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
    this.similarityEdges = [];
    this.showSimilarityEdges = false;
    this.injectionEdges = [];
    this.showInjectionEdges = false;
    this.highlightedDomain = null;
    this.stateFilter = 'active';
    this.activityEvents = [];
    this.activityOpen = false;
    this.activityLoading = false;
    this.seedBatchActive = false;
    this.seedBatchProgress = { completed: 0, total: 0, current: '' };
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    if (this._matchAbort) this._matchAbort.abort();
    this._debounceTimer = null;
    this._matchAbort = null;
    this._lastLength = 0;
    this._lastMatchedText = '';
    this._matchInFlight = false;
    this._matchError = null;
    this._loadGeneration = 0;
    this._clusterGeneration = 0;
  }
}

export const clustersStore = new ClusterStore();
