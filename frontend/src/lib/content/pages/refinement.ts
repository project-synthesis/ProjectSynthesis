import type { ContentPage } from '../types';

export const refinement: ContentPage = {
  slug: 'refinement',
  title: 'Branch. Refine. Converge.',
  description: 'Iterative prompt refinement with version history, branching, rollback, and smart suggestions. Each turn is a fresh pipeline invocation — not accumulated context.',
  sections: [
    {
      type: 'hero',
      heading: 'BRANCH. REFINE. CONVERGE.',
      subheading:
        'Each turn is a fresh pipeline invocation — not accumulated context. Start a refinement session, branch at any version, roll back to any point, and converge on your best prompt.',
    },
    {
      type: 'step-flow',
      steps: [
        {
          title: 'INITIAL TURN',
          description:
            'Begin a refinement session from any optimization. The first turn establishes the baseline version and opens the timeline.',
        },
        {
          title: 'REFINE',
          description:
            'Apply a natural language instruction or pick from 3 AI-generated suggestions. Each turn runs the full pipeline — analyze, optimize, score — from a clean context.',
        },
        {
          title: 'BRANCH OR ROLLBACK',
          description:
            'Unhappy with a direction? Roll back to any previous version. Rollback creates a branch fork — you never lose history.',
        },
        {
          title: 'CONVERGE',
          description:
            'Track score progression on the sparkline. When dimensions stop improving, your prompt has converged. Export the winning version.',
        },
      ],
    },
    {
      type: 'card-grid',
      columns: 2,
      cards: [
        {
          color: 'var(--color-neon-green)',
          title: 'Version History',
          description:
            'Every turn is stored with its full score breakdown, instruction, and timestamp. Navigate the timeline to compare any two versions side by side.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Smart Suggestions',
          description:
            'Three targeted suggestions generated per turn based on the lowest-scoring dimensions. One click applies the suggestion as the next refinement instruction.',
        },
      ],
    },
  ],
};
