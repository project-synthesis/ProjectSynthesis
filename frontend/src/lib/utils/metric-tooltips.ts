/**
 * Centralized tooltip definitions for all user-facing metrics.
 *
 * Brand voice: one technical line per tooltip. Canon terminology.
 * Import from here — never inline tooltip strings in components.
 */

// ---------------------------------------------------------------------------
// Taxonomy health (Inspector, TopologyControls, StatusBar)
// ---------------------------------------------------------------------------

export const TAXONOMY_TOOLTIPS = {
  q_system:
    'Composite quality: tightness \u00d7 separation \u00d7 coverage (0\u20131, higher = healthier)',
  coherence:
    'Intra-cluster similarity. 1.0 = identical patterns, <0.5 = split candidate',
  separation:
    'Inter-cluster distance. Low = overlapping (merge candidate), high = distinct',
  active: 'Active clusters absorbing new optimizations',
  candidate: 'Emerging clusters accumulating members before promotion',
  template: 'Promoted clusters \u2014 reusable prompt patterns',
};

// ---------------------------------------------------------------------------
// Score dimensions (ScoreCard, RefinementTurnCard)
// ---------------------------------------------------------------------------

export const DIMENSION_TOOLTIPS: Record<string, string> = {
  clarity: 'Precision of language \u2014 no ambiguity or vagueness (1\u201310)',
  specificity: 'Task scope and constraint definition (1\u201310)',
  structure: 'Layout and section organization (1\u201310)',
  faithfulness: 'Original intent preservation after optimization (1\u201310)',
  conciseness: 'Signal density \u2014 no unnecessary words (1\u201310)',
  overall: 'Mean of all five dimensions (1\u201310)',
};

export const SCORE_TOOLTIPS = {
  delta: (dim: string) => `Delta: ${dim} score vs. original`,
  original: (dim: string) => `Pre-optimization ${dim} score`,
};

// ---------------------------------------------------------------------------
// Cluster inspector
// ---------------------------------------------------------------------------

export const CLUSTER_TOOLTIPS = {
  member_count: 'Unique optimizations assigned to this cluster',
  usage_count: 'Times this cluster\u2019s patterns were applied',
  avg_score: 'Mean score across scored members (1\u201310)',
  preferred_strategy: 'Most effective strategy for this cluster',
  source_count: 'Independent discoveries of this pattern',
};

// ---------------------------------------------------------------------------
// Statistics (Navigator routing panels)
// ---------------------------------------------------------------------------

export const STAT_TOOLTIPS = {
  mean: 'Score mean \u2014 baseline quality level',
  stddev: 'Score spread \u2014 low = consistent, high = variable',
  duration: (ms: number) =>
    `Pipeline: ${(ms / 1000).toFixed(1)}s (analyze + optimize + score)`,
};

// ---------------------------------------------------------------------------
// Refinement
// ---------------------------------------------------------------------------

export const REFINEMENT_TOOLTIPS = {
  version_count: (n: number) =>
    `${n} refinement turn${n !== 1 ? 's' : ''} applied`,
  turn_delta: 'Delta from previous version',
};
