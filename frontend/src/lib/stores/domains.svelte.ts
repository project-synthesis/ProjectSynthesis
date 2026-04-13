/**
 * Domain store — reactive API-driven domain color resolution.
 *
 * Replaces the hardcoded DOMAIN_COLORS map in colors.ts. Fetches domain
 * nodes from GET /api/domains and provides color resolution with keyword
 * fallback for free-form domain strings.
 */

import { getDomains, type DomainInfo } from '$lib/api/domains';

/** Default color for unknown domains (dim gray). */
const FALLBACK_COLOR = '#7a7a9e';

class DomainStore {
  domains = $state<DomainInfo[]>([]);
  loaded = $state(false);

  /** Generation counter — prevents stale responses from overwriting fresh data. */
  private _loadGeneration = 0;

  /** label → color_hex lookup (derived from domains). */
  colors = $derived(
    this.domains.reduce<Record<string, string>>((acc, d) => {
      acc[d.label] = d.color_hex;
      return acc;
    }, {}),
  );

  /** Sorted list of domain labels (derived from domains). */
  labels = $derived(this.domains.map((d) => d.label));

  /** Fetch domains from the backend. */
  async load(): Promise<void> {
    const gen = ++this._loadGeneration;
    try {
      const result = await getDomains();
      if (gen !== this._loadGeneration) return; // stale response
      this.domains = result;
      this.loaded = true;
    } catch (err) {
      if (gen !== this._loadGeneration) return;
      console.warn('Domain store load failed:', err);
      // Keep stale data if we had a previous load
    }
  }

  /**
   * Resolve a domain identifier to a hex color.
   *
   * Accepts:
   * - A hex color string (returned as-is)
   * - A known domain label (exact match from API data)
   * - A "primary: qualifier" format (uses primary for lookup)
   * - A free-form string (keyword match against known domain labels)
   * - null/undefined (returns FALLBACK_COLOR)
   */
  colorFor(domain: string | null | undefined): string {
    if (!domain) return FALLBACK_COLOR;
    // Hex pass-through
    if (domain.startsWith('#')) return domain;
    // Parse "primary: qualifier" format
    const primary = domain.includes(':') ? domain.split(':')[0].trim() : domain;
    // Exact match
    if (primary in this.colors) return this.colors[primary];
    // Keyword fallback for free-form strings (e.g. "frontend CSS architecture")
    const lower = primary.toLowerCase();
    for (const [label, hex] of Object.entries(this.colors)) {
      if (label !== 'general' && lower.includes(label)) return hex;
    }
    // Sub-domain parent-prefix fallback: "backend-auth" → try "backend"
    const lowerDomain = domain.toLowerCase();
    for (const d of this.domains) {
      if (lowerDomain.startsWith(d.label.toLowerCase() + '-')) {
        return d.color_hex ?? FALLBACK_COLOR;
      }
    }
    return FALLBACK_COLOR;
  }

  /** Called by SSE handler when domain_created or taxonomy_changed fires. */
  invalidate(): void {
    this.load();
  }

  /** @internal Test-only: restore initial state */
  _reset(): void {
    this.domains = [];
    this.loaded = false;
    this._loadGeneration = 0;
  }
}

export const domainStore = new DomainStore();
