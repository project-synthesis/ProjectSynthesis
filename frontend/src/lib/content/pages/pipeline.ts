import type { ContentPage } from '../types';

export const pipeline: ContentPage = {
  slug: 'pipeline',
  title: 'Three Phases. One Pipeline. Zero Guesswork.',
  description: 'Analyze, optimize, and score prompts through three independent LLM subagents — each with its own context window, rubric, and output contract.',
  sections: [
    {
      type: 'hero',
      heading: 'THREE PHASES. ONE PIPELINE. ZERO GUESSWORK.',
      subheading:
        'Each optimization runs through three independent LLM subagents — analyzer, optimizer, scorer — each with its own context window, rubric, and output contract.',
      cta: { label: 'Open the App', href: '/' },
    },
    {
      type: 'step-flow',
      steps: [
        {
          title: 'ANALYZE',
          description:
            'Classify task type, detect weaknesses, and select the best optimization strategy. The analyzer produces a structured contract — no natural language drift between phases.',
        },
        {
          title: 'OPTIMIZE',
          description:
            'Rewrite the prompt using the selected strategy with codebase context injection. Strategy templates are file-driven and hot-reloaded — swap them without restarting.',
        },
        {
          title: 'SCORE',
          description:
            'Blind A/B evaluation with randomized presentation order. Hybrid blending fuses LLM scores with model-independent heuristics. Intent drift detection flags divergence > 2.5 points.',
        },
      ],
    },
    {
      type: 'card-grid',
      columns: 3,
      cards: [
        {
          color: 'var(--color-neon-cyan)',
          title: 'Isolated Context',
          description:
            'Each subagent runs in a fresh context window. No cross-contamination between phases — the scorer never sees the optimizer\'s reasoning.',
        },
        {
          color: 'var(--color-neon-green)',
          title: 'Bias Mitigation',
          description:
            'A/B randomized presentation order eliminates position bias. Z-score normalization prevents score clustering against historical distributions.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Strategy Adaptive',
          description:
            'Six built-in strategies (chain-of-thought, few-shot, role-playing, structured-output, meta-prompting, auto). Fully file-driven — add a Markdown file to add a strategy.',
        },
      ],
    },
    {
      type: 'code-block',
      language: 'python',
      filename: 'mcp',
      code: 'synthesis_optimize(prompt="your prompt here", strategy="chain-of-thought")',
    },
  ],
};
