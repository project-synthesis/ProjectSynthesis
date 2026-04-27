/**
 * Color tokens shared by ActivityPanel + DomainLifecycleTimeline.
 *
 * Single source of truth for the path (hot/warm/cold) and per-decision
 * neon mapping. Both surfaces stay in lockstep without copy-paste.
 *
 * Pure functions — no DOM, no store. Tested directly.
 */
import type { TaxonomyActivityEvent } from '$lib/api/clusters';

// -- Path colors -----------------------------------------------------------

/**
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

// -- Decision colors -------------------------------------------------------
//
// Five severity buckets resolved to four CSS tokens:
//
//   error   → neon-red    (op === 'error', batch-level seed_failed)
//   warn    → neon-yellow (dissolution, rejection, skip — anything that
//                          requires operator attention but is not a hard error)
//   create  → neon-cyan   (new entities, candidates, operator-triggered actions)
//   success → neon-green  (completion of multi-step operations)
//   info    → text-secondary (algorithm results, computed metrics, telemetry)
//   default → text-dim    (anything unmapped — render but suppress)
//
// Decision strings are grouped into `Set` constants below so adding a new
// decision is a one-line change in the right set, never a new branch in
// `decisionColor` itself. Brand directive: never invent a sixth severity.

const _ERROR_DECISIONS = new Set<string>([
  'seed_failed',
]);

const _WARN_DECISIONS = new Set<string>([
  // Dissolution & rejection — top-level domain dissolution is strictly
  // more severe than sub-domain dissolution (an entire domain disappears,
  // not just a hierarchy level), so it lands in the same warn bucket.
  'dissolved',
  'domain_dissolved',
  'sub_domain_dissolved',
  'rejected',
  'blocked',
  'candidate_rejected',
  'split_fully_reversed',
  // Skip / suppression
  'skipped',
  'sub_domain_skipped',
  'sub_domain_reevaluation_skipped',
  'candidates_filtered',
  // Per-prompt failure (expected; fail-forward)
  'seed_prompt_failed',
]);

const _CREATE_DECISIONS = new Set<string>([
  'create_new',
  'child_created',
  'family_split',
  'candidate_created',
  // R6 operator-triggered rebuild — cyan because it's a deliberate
  // creation pathway (even when dry_run=true, intent is to create).
  'sub_domain_rebuild_invoked',
]);

const _SUCCESS_DECISIONS = new Set<string>([
  'accepted',
  'merged',
  'merge_into',
  'complete',
  'split_complete',
  'archived',
  'domain_created',
  'created',
  'patterns_refreshed',
  'zombies_archived',
  'seed_completed',
  'candidate_promoted',
]);

const _INFO_DECISIONS = new Set<string>([
  'algorithm_result',
  'noise_reassigned',
  'mega_clusters_detected',
  'no_sub_structure',
  'scored',
  'q_computed',
  'repaired',
  'domains_created',
  'sub_domains_created',
  'sub_domain_readiness_computed',
  'domain_stability_computed',
  // R1+R5: re-evaluation that did NOT trigger dissolution — the sub-domain
  // survived the consistency check; this is informational telemetry.
  'sub_domain_reevaluated',
  // R7: vocab regen telemetry — informational by default. The WARNING log
  // line on low-overlap regen is emitted via `logger.warning` separately;
  // operators can layer additional severity in the UI by reading
  // `context.overlap_pct < 50` if they wish.
  'vocab_generated_enriched',
  'vocab_quality_assessed',
  // Seed progress noise (high-volume, intentionally muted)
  'seed_started',
  'seed_explore_complete',
  'seed_agents_complete',
  'seed_persist_complete',
  'seed_taxonomy_complete',
  'seed_prompt_scored',
]);

/**
 * Resolve a decision string to its severity color token.
 *
 * Hard error events (`op === 'error'`) short-circuit to red regardless
 * of decision. Otherwise the five severity sets above determine the
 * token.
 */
export function decisionColor(e: Pick<TaxonomyActivityEvent, 'op' | 'decision'>): string {
  if (e.op === 'error') return 'var(--color-neon-red)';
  const d = e.decision;
  if (_ERROR_DECISIONS.has(d)) return 'var(--color-neon-red)';
  if (_WARN_DECISIONS.has(d)) return 'var(--color-neon-yellow)';
  if (_CREATE_DECISIONS.has(d)) return 'var(--color-neon-cyan)';
  if (_SUCCESS_DECISIONS.has(d)) return 'var(--color-neon-green)';
  if (_INFO_DECISIONS.has(d)) return 'var(--color-text-secondary)';
  return 'var(--color-text-dim)';
}

/**
 * Three-level severity classification for row-density styling.
 *
 * - `error` → red rail, opaque-ish background
 * - `info`  → muted text-secondary or text-dim (low-signal noise)
 * - `normal` → all other severities (warn/create/success — actionable)
 */
export function severityLevel(
  e: Pick<TaxonomyActivityEvent, 'op' | 'decision'>,
): 'error' | 'info' | 'normal' {
  if (e.op === 'error') return 'error';
  const c = decisionColor(e);
  if (c === 'var(--color-text-secondary)' || c === 'var(--color-text-dim)') return 'info';
  return 'normal';
}
