import type { ContentPage } from '../types';

export const careers: ContentPage = {
  slug: 'careers',
  title: 'Build the Tools That Build Better Prompts.',
  description: 'Join a small team building the infrastructure layer for prompt engineering. Fully remote, async-first, documentation-heavy.',
  sections: [
    {
      type: 'hero',
      heading: 'BUILD THE TOOLS THAT BUILD BETTER PROMPTS.',
      subheading:
        'A small team with high standards. Fully remote, async-first, specs before code. We measure everything — including ourselves.',
    },
    {
      type: 'prose',
      blocks: [
        {
          heading: 'How We Work',
          content:
            'Fully remote, async-first, documentation-heavy. Every feature starts with a spec. Every decision gets written down. We use trace logs instead of standups, CHANGELOG entries instead of sprint reviews, and code review instead of meetings. Time zones don\'t matter — quality does.',
        },
        {
          heading: 'What Matters',
          content:
            'Precision over speed. Measurement over opinion. We don\'t ship features that can\'t be evaluated. Every change that ships has a test or a metric. We prefer explicit over implicit, typed contracts over runtime duck-typing, and small precise PRs over large exploratory ones. The system prompt is: make it measurable.',
        },
      ],
    },
    {
      type: 'role-list',
      roles: [
        {
          title: 'Senior Backend Engineer',
          description:
            'Own the pipeline, scoring, and persistence layers. Python 3.12, FastAPI, async SQLAlchemy, Pydantic. Experience with LLM API integration and streaming responses preferred. You will write the contracts that define what the optimizer promises and what the scorer measures.',
          type: 'REMOTE',
          department: 'Engineering',
        },
        {
          title: 'Frontend Engineer',
          description:
            'Build the workbench where prompt engineers spend their day. SvelteKit 2, Svelte 5 runes, Tailwind CSS 4. Strong CSS fundamentals — we use scoped styles and CSS custom properties, not utility soup. Experience with SSE, real-time UI, and design systems preferred.',
          type: 'REMOTE',
          department: 'Engineering',
        },
        {
          title: 'ML/Evaluation Engineer',
          description:
            'Own the scoring system: calibrate heuristics, measure bias correction effectiveness, validate z-score normalization, and extend the embedding-based semantic retrieval. Experience with sentence-transformers, evaluation methodology, and statistical analysis. You will be the person who decides when a score is trustworthy.',
          type: 'REMOTE',
          department: 'ML & Data',
        },
      ],
    },
  ],
};
