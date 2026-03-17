import type { ContentPage } from '../types';

export const scoring: ContentPage = {
  slug: 'scoring',
  title: 'Five Dimensions. Hybrid Engine. No Self-Rating Bias.',
  description: 'Independent 5-dimension scoring blended with model-independent heuristics. A/B randomization and z-score normalization eliminate position bias and score clustering.',
  sections: [
    {
      type: 'hero',
      heading: 'FIVE DIMENSIONS. HYBRID ENGINE. NO SELF-RATING BIAS.',
      subheading:
        'Independent evaluation across five rubric dimensions, blended with model-independent heuristics. LLMs can\'t grade their own work fairly — so we don\'t let them.',
    },
    {
      type: 'card-grid',
      columns: 5,
      cards: [
        {
          color: 'var(--color-neon-cyan)',
          title: 'Clarity',
          description: 'The prompt is unambiguous and can be interpreted only one way.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Specificity',
          description: 'Sufficient detail is provided — format, constraints, and scope are explicit.',
        },
        {
          color: 'var(--color-neon-green)',
          title: 'Structure',
          description: 'Information is organized logically; related elements are grouped.',
        },
        {
          color: 'var(--color-neon-yellow)',
          title: 'Faithfulness',
          description: 'The optimized prompt preserves the original intent without drift.',
        },
        {
          color: 'var(--color-neon-pink)',
          title: 'Conciseness',
          description: 'No redundant tokens. Every word earns its place.',
        },
      ],
    },
    {
      type: 'metric-bar',
      label: 'Original: write some code to handle user data',
      dimensions: [
        { name: 'Clarity', value: 3.2, color: 'var(--color-neon-cyan)' },
        { name: 'Specificity', value: 2.0, color: 'var(--color-neon-purple)' },
        { name: 'Structure', value: 2.2, color: 'var(--color-neon-green)' },
        { name: 'Faithfulness', value: 5.0, color: 'var(--color-neon-yellow)' },
        { name: 'Conciseness', value: 8.0, color: 'var(--color-neon-pink)' },
      ],
    },
    {
      type: 'metric-bar',
      label: 'Optimized: Write a Python function validate_user(data: dict) → tuple[bool, list[str]]...',
      dimensions: [
        { name: 'Clarity', value: 8.0, color: 'var(--color-neon-cyan)' },
        { name: 'Specificity', value: 9.0, color: 'var(--color-neon-purple)' },
        { name: 'Structure', value: 7.0, color: 'var(--color-neon-green)' },
        { name: 'Faithfulness', value: 8.0, color: 'var(--color-neon-yellow)' },
        { name: 'Conciseness', value: 7.0, color: 'var(--color-neon-pink)' },
      ],
    },
    {
      type: 'prose',
      blocks: [
        {
          heading: 'Hybrid Scoring Methodology',
          content:
            'Final scores blend LLM evaluation with model-independent heuristics using dimension-specific weights: structure 50% heuristic, conciseness and specificity 40%, clarity 30%, faithfulness 20%. Heuristics measure objective signals — sentence length variance, punctuation density, keyword overlap — that a self-rating LLM systematically over- or under-values.',
        },
        {
          heading: 'Z-Score Normalization',
          content:
            'When 10 or more historical samples exist, scores are normalized against the distribution mean and standard deviation. This prevents score clustering — the tendency for scores to converge toward a narrow band after enough evaluations.',
        },
        {
          heading: 'Divergence Detection',
          content:
            'When LLM and heuristic scores disagree by more than 2.5 points on any dimension, a divergence flag is set on the result. Divergence signals ambiguous prompts where human review adds more value than pipeline consensus.',
        },
        {
          heading: 'Passthrough Bias Correction',
          content:
            'MCP passthrough mode applies a 0.85 scaling factor to self-rated scores. When an external LLM optimizes and scores its own output, the result is compressed to account for systematic self-rating inflation.',
        },
      ],
    },
  ],
};
