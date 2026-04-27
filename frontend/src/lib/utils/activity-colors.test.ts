import { describe, it, expect } from 'vitest';
import { pathColor, decisionColor, severityLevel } from './activity-colors';

describe('pathColor', () => {
  it('returns neon-red token for hot', () => {
    expect(pathColor('hot')).toContain('neon-red');
  });
  it('returns neon-yellow token for warm', () => {
    expect(pathColor('warm')).toContain('neon-yellow');
  });
  it('returns neon-cyan token for cold', () => {
    expect(pathColor('cold')).toContain('neon-cyan');
  });
  it('returns text-dim fallback for unknown path', () => {
    expect(pathColor('mystery')).toContain('text-dim');
  });
});

describe('decisionColor', () => {
  it('hard error op short-circuits to neon-red', () => {
    expect(decisionColor({ op: 'error', decision: 'whatever' })).toContain('neon-red');
  });

  it('seed_failed → neon-red (batch-level error)', () => {
    expect(decisionColor({ op: 'seed', decision: 'seed_failed' })).toContain('neon-red');
  });

  // R3 — sub_domain_reevaluation_skipped routes through the warn bucket
  // because operators want it visually flagged (potentially indicates a
  // domain whose vocabulary regen failed silently).
  it('sub_domain_reevaluation_skipped → neon-yellow (warn)', () => {
    expect(decisionColor({ op: 'discover', decision: 'sub_domain_reevaluation_skipped' }))
      .toContain('neon-yellow');
  });

  // R5 — dissolution remains warn (yellow). The matching_members count
  // does not change severity, only payload richness.
  it('sub_domain_dissolved → neon-yellow (warn)', () => {
    expect(decisionColor({ op: 'discover', decision: 'sub_domain_dissolved' }))
      .toContain('neon-yellow');
  });

  // R6 — operator-triggered rebuild is a deliberate creation pathway.
  it('sub_domain_rebuild_invoked → neon-cyan (create)', () => {
    expect(decisionColor({ op: 'discover', decision: 'sub_domain_rebuild_invoked' }))
      .toContain('neon-cyan');
  });

  // R1+R5 — re-eval that did NOT dissolve is informational telemetry.
  it('sub_domain_reevaluated → text-secondary (info)', () => {
    expect(decisionColor({ op: 'discover', decision: 'sub_domain_reevaluated' }))
      .toContain('text-secondary');
  });

  // R7 — vocab regen is informational by default. The WARNING-level
  // log line on low overlap is emitted server-side via logger.warning.
  it('vocab_generated_enriched → text-secondary (info)', () => {
    expect(decisionColor({ op: 'discover', decision: 'vocab_generated_enriched' }))
      .toContain('text-secondary');
  });

  it('candidate_promoted → neon-green (success)', () => {
    expect(decisionColor({ op: 'candidate', decision: 'candidate_promoted' }))
      .toContain('neon-green');
  });

  it('candidate_rejected → neon-yellow (warn)', () => {
    expect(decisionColor({ op: 'candidate', decision: 'candidate_rejected' }))
      .toContain('neon-yellow');
  });

  it('falls through to text-dim for unmapped decisions', () => {
    expect(decisionColor({ op: 'discover', decision: 'totally_new_thing' }))
      .toContain('text-dim');
  });
});

describe('severityLevel', () => {
  it('maps op=error to error', () => {
    expect(severityLevel({ op: 'error', decision: 'x' })).toBe('error');
  });
  it('maps create/warn/success to normal', () => {
    expect(severityLevel({ op: 'discover', decision: 'sub_domain_rebuild_invoked' })).toBe('normal');
    expect(severityLevel({ op: 'discover', decision: 'sub_domain_dissolved' })).toBe('normal');
    expect(severityLevel({ op: 'discover', decision: 'domain_created' })).toBe('normal');
  });
  it('maps info + dim to info', () => {
    expect(severityLevel({ op: 'discover', decision: 'sub_domain_reevaluated' })).toBe('info');
    expect(severityLevel({ op: 'discover', decision: 'totally_new_thing' })).toBe('info');
  });
});
