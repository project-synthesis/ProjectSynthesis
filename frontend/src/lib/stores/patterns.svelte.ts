/**
 * Pattern store — suggestion state, family data, paste detection.
 *
 * Manages auto-suggestion on paste and pattern graph data for the mindmap.
 */

import { matchPattern, getPatternGraph, type PatternMatch, type PatternGraph } from '$lib/api/patterns';

const PASTE_CHAR_DELTA = 50;
const PASTE_DEBOUNCE_MS = 300;
const SUGGESTION_AUTO_DISMISS_MS = 10_000;

class PatternStore {
  // Suggestion state
  suggestion = $state<PatternMatch | null>(null);
  suggestionVisible = $state(false);

  // Graph data
  graph = $state<PatternGraph | null>(null);
  graphLoaded = $state(false);
  graphError = $state<string | null>(null);

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
   * User clicked [Apply] — returns the meta-pattern texts for pipeline injection.
   */
  applySuggestion(): string[] | null {
    if (!this.suggestion) return null;
    const patterns = this.suggestion.meta_patterns.map(mp => mp.pattern_text);
    this.dismissSuggestion();
    return patterns;
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

  /**
   * Load graph data for the mindmap.
   */
  async loadGraph(familyId?: string): Promise<void> {
    try {
      this.graphError = null;
      this.graph = await getPatternGraph(familyId);
      this.graphLoaded = true;
    } catch (err) {
      this.graphError = err instanceof Error ? err.message : 'Failed to load graph';
      console.error('Graph load failed:', err);
    }
  }

  /**
   * Refresh graph data (called on pattern_updated events).
   */
  invalidateGraph(): void {
    this.graphLoaded = false;
  }

  /**
   * Reset last length tracking (call when prompt is cleared).
   */
  resetTracking(): void {
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
