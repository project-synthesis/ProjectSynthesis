/**
 * Path-color tokens shared by ActivityPanel + DomainLifecycleTimeline.
 *
 * Single source of truth for the hot/warm/cold neon mapping so both
 * surfaces stay in lockstep without copy-paste.
 *
 * Accepts `string` (not just the strict `ActivityPath` union) so callers
 * threading raw event payloads (e.g. ActivityPanel's `ev.path`) keep the
 * historical fallback semantics for unrecognised values.
 */
export type ActivityPath = 'hot' | 'warm' | 'cold';

export function pathColor(path: ActivityPath | string): string {
  switch (path) {
    case 'hot': return 'var(--color-neon-red)';
    case 'warm': return 'var(--color-neon-yellow)';
    case 'cold': return 'var(--color-neon-cyan)';
    default: return 'var(--color-text-dim)';
  }
}
