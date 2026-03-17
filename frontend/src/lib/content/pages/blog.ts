import type { ContentPage } from '../types';

export const blog: ContentPage = {
  slug: 'blog',
  title: 'From the Pipeline.',
  description: 'Technical deep-dives, methodology breakdowns, and integration guides from the Project Synthesis team.',
  sections: [
    {
      type: 'hero',
      heading: 'FROM THE PIPELINE.',
      subheading:
        'Technical deep-dives, methodology breakdowns, and integration guides from the team building Project Synthesis.',
    },
    {
      type: 'article-list',
      articles: [
        {
          title: 'Introducing Project Synthesis',
          excerpt:
            'A 3-phase prompt optimization pipeline — analyze, optimize, score — with independent subagent context windows. How the architecture isolates each phase to prevent cross-contamination between analyzer opinions and scorer judgments, and why that matters for scoring integrity.',
          date: '2026-03-15',
          readTime: '8 min read',
        },
        {
          title: 'The Scoring Problem: Why LLMs Can\'t Grade Their Own Work',
          excerpt:
            'Position bias is well-documented in LLM evaluation literature — models prefer options they see first. But verbosity bias, self-congratulation in scoring, and score clustering are less discussed. This post explains the three failure modes we observed in naive LLM scoring and how hybrid blending with z-score normalization addresses each one.',
          date: '2026-03-08',
          readTime: '12 min read',
        },
        {
          title: 'MCP in Practice: Optimizing Prompts Without Leaving Claude Code',
          excerpt:
            'The MCP server gives Claude Code direct access to the full Project Synthesis pipeline via four tools. This walkthrough shows the passthrough workflow: how synthesis_prepare_optimization assembles codebase context, how an external LLM processes it, and how synthesis_save_result applies bias correction before persisting the result.',
          date: '2026-02-28',
          readTime: '6 min read',
        },
      ],
    },
  ],
};
