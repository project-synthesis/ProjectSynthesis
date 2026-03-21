/**
 * Pattern store — suggestion state, family data, paste detection.
 *
 * Manages auto-suggestion on paste and pattern graph data for the mindmap.
 */

import { matchPattern, getFamilyDetail, type PatternMatch, type FamilyDetail } from '$lib/api/patterns';
import { getTaxonomyTree, getTaxonomyStats, type TaxonomyNode, type TaxonomyStats } from '$lib/api/taxonomy';

const PASTE_CHAR_DELTA = 50;
const PASTE_DEBOUNCE_MS = 300;
const SUGGESTION_AUTO_DISMISS_MS = 10_000;

class PatternStore {
  // Suggestion state
  suggestion = $state<PatternMatch | null>(null);
  suggestionVisible = $state(false);

  // Family detail (Inspector)
  selectedFamilyId = $state<string | null>(null);
  familyDetail = $state<FamilyDetail | null>(null);
  familyDetailLoading = $state(false);
  familyDetailError = $state<string | null>(null);

  // Taxonomy tree and stats
  taxonomyTree = $state<TaxonomyNode[]>([]);
  taxonomyStats = $state<TaxonomyStats | null>(null);
  taxonomyLoading = $state(false);
  taxonomyError = $state<string | null>(null);

  // Internal
  private _debounceTimer: ReturnType<typeof setTimeout> | null = null;
  private _dismissTimer: ReturnType<typeof setTimeout> | null = null;
  private _lastLength = 0;

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
    this.taxonomyLoading = true;
    this.taxonomyError = null;
    try {
      this.taxonomyTree = await getTaxonomyTree();
      this.taxonomyStats = await getTaxonomyStats();
    } catch (err) {
      this.taxonomyError = err instanceof Error ? err.message : 'Failed to load taxonomy';
      console.warn('Taxonomy load failed:', err);
    } finally {
      this.taxonomyLoading = false;
    }
  }

  /** Called by SSE handler when taxonomy_changed fires. */
  invalidateTaxonomy(): void {
    this.loadTree();
  }

  /**
   * Select a family for Inspector display. Pass null to deselect.
   */
  selectFamily(id: string | null): void {
    this.selectedFamilyId = id;
    if (!id) {
      this.familyDetail = null;
      this.familyDetailError = null;
      return;
    }
    this._loadFamilyDetail(id);
  }

  private async _loadFamilyDetail(id: string): Promise<void> {
    this.familyDetailLoading = true;
    this.familyDetailError = null;
    try {
      this.familyDetail = await getFamilyDetail(id);
    } catch (err) {
      this.familyDetailError = err instanceof Error ? err.message : 'Failed to load family';
      this.familyDetail = null;
    } finally {
      this.familyDetailLoading = false;
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
    this.selectedFamilyId = null;
    this.familyDetail = null;
    this.familyDetailLoading = false;
    this.familyDetailError = null;
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    if (this._dismissTimer) clearTimeout(this._dismissTimer);
    this._debounceTimer = null;
    this._dismissTimer = null;
    this._lastLength = 0;
  }

  private _startDismissTimer(): void {
    if (this._dismissTimer) clearTimeout(this._dismissTimer);
    this._dismissTimer = setTimeout(() => {
      this.dismissSuggestion();
    }, SUGGESTION_AUTO_DISMISS_MS);
  }
}

export const patternsStore = new PatternStore();
