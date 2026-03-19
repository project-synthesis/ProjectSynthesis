/**
 * Shared constants for the pattern knowledge graph.
 *
 * Domain color palette used by RadialMindmap, PatternNavigator, and Inspector.
 */

export const DOMAIN_COLORS: Record<string, string> = {
  backend: '#a855f7',
  frontend: '#f59e0b',
  database: '#10b981',
  security: '#ef4444',
  devops: '#3b82f6',
  fullstack: '#00e5ff',
  general: '#6b7280',
};

/** Get the color for a domain, falling back to 'general'. */
export function domainColor(domain: string): string {
  return DOMAIN_COLORS[domain] ?? DOMAIN_COLORS.general;
}

/** Score-to-color mapping using design system tokens. */
export function scoreColor(score: number | null): string {
  if (score == null || score <= 0) return 'var(--color-text-dim)';
  if (score >= 7.5) return 'var(--color-neon-green)';
  if (score >= 5.0) return 'var(--color-neon-yellow)';
  return 'var(--color-neon-red)';
}
