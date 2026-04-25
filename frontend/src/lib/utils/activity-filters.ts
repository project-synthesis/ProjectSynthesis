/**
 * Shared activity-filter predicates.
 *
 * Two consumers read `clustersStore.activityEvents`:
 *   - `ActivityPanel.svelte` — high-fidelity topology terminal (16 op chips,
 *     full keyMetric, cluster + optimization deep-links).
 *   - `DomainLifecycleTimeline.svelte` — Observatory's lifecycle-grouped
 *     timeline (4 op-family chips, period-scoped JSONL backfill).
 *
 * Both surfaces filter by error severity and op family. Before this
 * module the predicates were inlined in each component and had drifted:
 * ActivityPanel caught `seed_failed` + `candidate_rejected`, Timeline
 * didn't. This module is the single source of truth; both surfaces
 * import.
 *
 * Coverage: the canonical backend op vocabulary (33 distinct ops) is
 * mapped here in `opFamily()`. Pure infrastructure ops (phase, refit,
 * umap, hdbscan, audit, reconcile, skip, refresh, maintenance) return
 * `null` — they belong in ActivityPanel's terminal feed but NOT in the
 * Observatory's lifecycle timeline.
 */
import type { TaxonomyActivityEvent } from '$lib/api/clusters';

export type TimelineEvent = TaxonomyActivityEvent;
export type OpFamily = 'domain' | 'cluster' | 'pattern' | 'readiness';

const _ERROR_DECISIONS = new Set([
  'rejected',
  'failed',
  // Batch-level seed failure (entire seed batch aborted).
  'seed_failed',
  // Split-children rejection during warm Phase 0.5 candidate evaluation.
  'candidate_rejected',
]);

/**
 * Canonical "is this an error/failure event?" predicate.
 *
 * Both ActivityPanel and DomainLifecycleTimeline use this for their
 * `errors-only` filter chip. Pre-extraction Timeline only checked
 * `rejected | failed`; this is the unified behaviour.
 */
export function isErrorEvent(e: Pick<TimelineEvent, 'op' | 'decision'>): boolean {
  if (e.op === 'error') return true;
  return _ERROR_DECISIONS.has(e.decision);
}

/**
 * Backend op-name → lifecycle family. Returns `null` for ops that are
 * pure infrastructure / measurement (audit, reconcile, refit, phase,
 * skip, refresh, maintenance, umap, hdbscan) — those belong in
 * ActivityPanel's terminal feed but not in the lifecycle Timeline.
 *
 * Decision-disambiguated where the same op spans multiple families
 * (`extract` emits `meta_patterns_added` for pattern lifecycle but
 * also non-pattern bookkeeping decisions).
 */
export function opFamily(op: string, decision?: string): OpFamily | null {
  // Domain lifecycle
  if (op === 'discover' || op === 'emerge') return 'domain';

  // Cluster lifecycle
  if (
    op === 'split'
    || op === 'merge'
    || op === 'retire'
    || op === 'archive'
    || op === 'candidate'
    || op === 'state_change'
    || op === 'recovery'
  ) {
    return 'cluster';
  }

  // Pattern lifecycle
  if (op === 'global_pattern' || op === 'template_lifecycle') return 'pattern';
  if (op === 'extract' && (decision?.includes('pattern') ?? false)) return 'pattern';

  // Readiness signals
  if (
    op === 'readiness'
    || op === 'signal_adjuster'
    || op === 'readiness_crossing_suppressed'
    || op.startsWith('readiness/')
  ) {
    return 'readiness';
  }

  return null;
}
