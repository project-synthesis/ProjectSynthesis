/**
 * Taxonomy health synthesizer — produces plain-language status from raw metrics.
 *
 * Reads all available quality signals and generates a headline + detail
 * that anyone can understand without statistics knowledge.
 */

import type { ClusterStats } from '$lib/api/clusters';

export interface HealthAssessment {
  /** Primary status line — the headline */
  headline: string;
  /** Plain-language explanation + what to do next */
  detail: string;
  /** Severity level */
  severity: 'good' | 'warning' | 'critical' | 'info';
  /** CSS color for the headline */
  color: string;
}

// -- Thresholds --

const Q_GOOD = 0.7;
const Q_WARNING = 0.45;

const COHERENCE_GOOD = 0.7;
const COHERENCE_LOW = 0.5;

const SEPARATION_GOOD = 0.5;
const SEPARATION_LOW = 0.3;

const TREND_IMPROVING = 0.1;
const TREND_DECLINING = -0.1;

/**
 * Synthesize a health assessment from cluster stats.
 * Returns null if insufficient data.
 */
export function assessTaxonomyHealth(stats: ClusterStats | null): HealthAssessment | null {
  if (!stats || stats.q_system == null) return null;

  const q = stats.q_system;
  const coh = stats.q_coherence ?? 0;
  const sep = stats.q_separation ?? 0;
  const trend = stats.q_trend;
  const active = stats.nodes?.active ?? 0;
  const candidate = stats.nodes?.candidate ?? 0;
  const template = stats.nodes?.template ?? 0;
  const total = active + candidate + template;
  const hasTrend = (stats.q_point_count ?? 0) >= 3;

  const improving = hasTrend && trend > TREND_IMPROVING;
  const declining = hasTrend && trend < TREND_DECLINING;

  // -- Early states --

  if (total === 0) {
    return {
      headline: 'No patterns yet',
      detail: 'Start optimizing prompts and the system will automatically discover patterns in your work.',
      severity: 'info',
      color: 'var(--color-text-dim)',
    };
  }

  if (total > 0 && total <= 3) {
    return {
      headline: 'Just getting started',
      detail: `${total} group${total > 1 ? 's' : ''} forming. Keep optimizing to help the system find more patterns.`,
      severity: 'info',
      color: 'var(--color-neon-blue)',
    };
  }

  // -- Diagnose issues in plain language --
  const issues: string[] = [];

  if (coh < COHERENCE_LOW) {
    issues.push('some groups contain prompts that don\'t really belong together');
  } else if (coh < COHERENCE_GOOD) {
    issues.push('some groups could be more focused');
  }

  if (sep < SEPARATION_LOW) {
    issues.push('groups are too similar to each other \u2014 try running a recluster');
  } else if (sep < SEPARATION_GOOD) {
    issues.push('a few groups overlap and could be better separated');
  }

  // -- Headline --
  let headline: string;
  let severity: HealthAssessment['severity'];
  let color: string;

  if (q >= Q_GOOD) {
    if (improving) {
      headline = 'Looking great, getting better';
      severity = 'good';
      color = 'var(--color-neon-green)';
    } else if (declining) {
      headline = 'Good, but slipping';
      severity = 'warning';
      color = 'var(--color-neon-yellow)';
    } else {
      headline = 'Well organized';
      severity = 'good';
      color = 'var(--color-neon-green)';
    }
  } else if (q >= Q_WARNING) {
    if (improving) {
      headline = 'Getting better';
      severity = 'warning';
      color = 'var(--color-neon-yellow)';
    } else if (declining) {
      headline = 'Losing organization';
      severity = 'warning';
      color = 'var(--color-neon-orange)';
    } else {
      headline = 'Could be better';
      severity = 'warning';
      color = 'var(--color-neon-yellow)';
    }
  } else {
    if (improving) {
      headline = 'Rebuilding';
      severity = 'critical';
      color = 'var(--color-neon-orange)';
    } else {
      headline = 'Needs a recluster';
      severity = 'critical';
      color = 'var(--color-neon-red)';
    }
  }

  // -- Detail --
  const parts: string[] = [];

  // What's in the taxonomy
  const counts: string[] = [];
  if (active > 0) counts.push(`${active} active`);
  if (candidate > 0) counts.push(`${candidate} forming`);
  if (template > 0) counts.push(`${template} reusable`);
  parts.push(`${counts.join(', ')} group${total !== 1 ? 's' : ''}`);

  // Quality insight
  if (coh >= COHERENCE_GOOD && sep >= SEPARATION_GOOD) {
    parts.push('patterns are well-grouped and clearly distinct');
  } else if (issues.length > 0) {
    parts.push(issues[0]);
  }

  // What to do next
  if (sep < SEPARATION_LOW && active >= 5) {
    parts.push('hit Recluster to reorganize the groups');
  } else if (candidate > active && candidate >= 3) {
    parts.push('new groups are forming \u2014 they\'ll be confirmed automatically');
  } else if (template === 0 && active >= 5 && q >= Q_GOOD) {
    parts.push('promote your best groups to reusable templates');
  } else if (improving) {
    parts.push('keep going \u2014 each optimization makes the patterns sharper');
  }

  return {
    headline,
    detail: capitalize(parts.join('. ')) + '.',
    severity,
    color,
  };
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
