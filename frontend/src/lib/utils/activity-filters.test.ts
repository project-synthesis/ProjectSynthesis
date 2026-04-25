/**
 * Shared activity-filter predicates.
 *
 * Both `ActivityPanel.svelte` (topology mission-control terminal) and
 * `DomainLifecycleTimeline.svelte` (Observatory tab) consume
 * `clustersStore.activityEvents`. Before extraction the two surfaces
 * had drifted: ActivityPanel's error predicate caught
 * `seed_failed` + `candidate_rejected`, Timeline's didn't.
 */
import { describe, it, expect } from 'vitest';
import { isErrorEvent, opFamily, type TimelineEvent } from './activity-filters';

function ev(op: string, decision: string, extra: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    ts: '2026-04-25T08:00:00Z',
    path: 'warm',
    op,
    decision,
    cluster_id: null,
    optimization_id: null,
    duration_ms: null,
    context: {},
    ...extra,
  };
}

describe('isErrorEvent', () => {
  it('flags op === "error"', () => {
    expect(isErrorEvent(ev('error', 'whatever'))).toBe(true);
  });

  it('flags decision in canonical error set', () => {
    for (const d of ['rejected', 'failed', 'seed_failed', 'candidate_rejected']) {
      expect(isErrorEvent(ev('whatever_op', d))).toBe(true);
    }
  });

  it('does not flag non-error events', () => {
    expect(isErrorEvent(ev('discover', 'domains_created'))).toBe(false);
    expect(isErrorEvent(ev('split', 'split_complete'))).toBe(false);
    expect(isErrorEvent(ev('readiness', 'sub_domain_readiness_computed'))).toBe(false);
  });

  /**
   * Drift regression: pre-extraction, Timeline's predicate accepted only
   * `rejected | failed`, missing `seed_failed` (batch-level seed failure)
   * and `candidate_rejected` (split-children rejection during Phase 0.5).
   */
  it('catches seed_failed + candidate_rejected (Timeline drift fix)', () => {
    expect(isErrorEvent(ev('seed', 'seed_failed'))).toBe(true);
    expect(isErrorEvent(ev('candidate', 'candidate_rejected'))).toBe(true);
  });
});

describe('opFamily', () => {
  it('maps domain lifecycle ops', () => {
    expect(opFamily('discover')).toBe('domain');
    expect(opFamily('emerge')).toBe('domain');
  });

  it('maps cluster lifecycle ops', () => {
    expect(opFamily('split')).toBe('cluster');
    expect(opFamily('merge')).toBe('cluster');
    expect(opFamily('retire')).toBe('cluster');
    expect(opFamily('archive')).toBe('cluster');
    expect(opFamily('candidate')).toBe('cluster');
    expect(opFamily('state_change')).toBe('cluster');
    expect(opFamily('recovery')).toBe('cluster');
  });

  it('maps pattern lifecycle ops', () => {
    expect(opFamily('global_pattern')).toBe('pattern');
    expect(opFamily('template_lifecycle')).toBe('pattern');
    // extract emits meta-pattern adds; decision contains "pattern"
    expect(opFamily('extract', 'meta_patterns_added')).toBe('pattern');
  });

  it('maps readiness ops', () => {
    expect(opFamily('readiness')).toBe('readiness');
    expect(opFamily('signal_adjuster')).toBe('readiness');
    expect(opFamily('readiness_crossing_suppressed')).toBe('readiness');
  });

  it('handles "readiness/*" sub-path prefix', () => {
    expect(opFamily('readiness/computed')).toBe('readiness');
  });

  it('returns null for ops outside lifecycle scope', () => {
    // Pure infrastructure / measurement ops — engineer-facing only
    expect(opFamily('phase')).toBeNull();
    expect(opFamily('refit')).toBeNull();
    expect(opFamily('umap')).toBeNull();
    expect(opFamily('hdbscan')).toBeNull();
    expect(opFamily('audit')).toBeNull();
    expect(opFamily('reconcile')).toBeNull();
    expect(opFamily('skip')).toBeNull();
    expect(opFamily('refresh')).toBeNull();
    expect(opFamily('maintenance')).toBeNull();
  });

  /**
   * Drift regression: Timeline pre-extraction held 7 dead entries
   * (`reevaluate`, `dissolve`, `promote`, `demote`, `re_promote`,
   * `retired`, `meta_pattern`) — none of these are emitted op names.
   * `opFamily` should NOT match them as if they were canonical;
   * if a future backend emits them, the assertion below will fire
   * and the engineer can decide where they belong.
   */
  it('rejects historically-dead op names (drift guard)', () => {
    for (const op of ['reevaluate', 'dissolve', 'promote', 'demote', 're_promote', 'retired', 'meta_pattern']) {
      expect(opFamily(op)).toBeNull();
    }
  });
});
