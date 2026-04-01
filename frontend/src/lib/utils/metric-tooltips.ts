/**
 * Centralized tooltip definitions for all user-facing metrics.
 *
 * Plain language — no statistics jargon. Written for someone who has
 * never seen a scatter plot. Import from here, never inline in components.
 */

// ---------------------------------------------------------------------------
// Taxonomy health (Inspector, TopologyControls, StatusBar)
// ---------------------------------------------------------------------------

export const TAXONOMY_TOOLTIPS = {
  q_system:
    'How well-organized your prompt patterns are overall. Higher = better. Scale: 0 to 1',
  coherence:
    'Are prompts inside each group actually similar to each other? Closer to 1 = yes, they belong together',
  separation:
    'Are the groups clearly different from each other? Closer to 1 = yes, no confusing overlap',
  active: 'Groups that are currently collecting new prompts',
  candidate: 'New groups still forming — not yet confirmed as useful',
  template: 'Best groups — promoted as reusable patterns you can apply',
};

// ---------------------------------------------------------------------------
// Score dimensions (ScoreCard, RefinementTurnCard)
// ---------------------------------------------------------------------------

export const DIMENSION_TOOLTIPS: Record<string, string> = {
  clarity: 'Is the prompt easy to understand? No vague or confusing language. Scale: 1 to 10',
  specificity: 'Does the prompt clearly define what it wants? Scale: 1 to 10',
  structure: 'Is the prompt well-organized with clear sections? Scale: 1 to 10',
  faithfulness: 'Does the optimized version still mean the same thing as the original? Scale: 1 to 10',
  conciseness: 'Does the prompt say what it needs to without extra fluff? Scale: 1 to 10',
  overall: 'Average of all five scores above. Scale: 1 to 10',
};

export const SCORE_TOOLTIPS = {
  delta: (dim: string) => `How much the ${dim} score changed after optimization`,
  original: (dim: string) => `The ${dim} score before optimization`,
};

// ---------------------------------------------------------------------------
// Cluster inspector
// ---------------------------------------------------------------------------

export const CLUSTER_TOOLTIPS = {
  member_count: 'How many prompts belong to this group',
  usage_count: 'How many times this group\'s patterns were reused',
  avg_score: 'Average quality score of prompts in this group. Scale: 1 to 10',
  preferred_strategy: 'The optimization strategy that works best for this group',
  source_count: 'How many prompts independently showed this same pattern',
};

// ---------------------------------------------------------------------------
// Statistics (Navigator routing panels)
// ---------------------------------------------------------------------------

export const STAT_TOOLTIPS = {
  mean: 'Your average prompt quality score',
  stddev: 'How much your scores vary — lower means more consistent results',
  duration: (ms: number) =>
    `Took ${(ms / 1000).toFixed(1)} seconds to analyze, optimize, and score`,
};

// ---------------------------------------------------------------------------
// Refinement
// ---------------------------------------------------------------------------

export const REFINEMENT_TOOLTIPS = {
  version_count: (n: number) =>
    `Refined ${n} time${n !== 1 ? 's' : ''}`,
  turn_delta: 'How much this score changed from the previous version',
};
