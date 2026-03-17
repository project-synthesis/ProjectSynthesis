import type { ContentPage } from '../types';

export const about: ContentPage = {
  slug: 'about',
  title: 'Built by Engineers Who Got Tired of Vague Prompts.',
  description: 'Project Synthesis started as a frustration with unmeasured prompts. Now it\'s an open-source platform for treating prompt engineering as a discipline with real metrics.',
  sections: [
    {
      type: 'hero',
      heading: 'BUILT BY ENGINEERS WHO GOT TIRED OF VAGUE PROMPTS.',
      subheading:
        'Prompt quality was always a gut feeling. We wanted a number. So we built the system to produce one.',
    },
    {
      type: 'prose',
      blocks: [
        {
          heading: 'The Problem',
          content:
            'Every team using LLMs has the same silent crisis: prompts that nobody measures. Engineers iterate by feel, ship by intuition, and debug by comparison. There\'s no compile step, no test runner, no diff that tells you if version 12 is better than version 11. The quality bar is "it felt smarter this time."',
        },
        {
          heading: 'The Approach',
          content:
            'We treat prompt optimization as compilation. Source code goes in, optimized code comes out, and the transformation is auditable. The analyzer classifies what you\'re trying to do. The optimizer rewrites for the right strategy. The scorer evaluates independently — it never sees the optimizer\'s reasoning, and it evaluates both prompts in random order to prevent position bias. Every result is stored, diffable, and feedbackable.',
        },
        {
          heading: 'Open Source',
          content:
            'Project Synthesis is Apache 2.0 licensed. It runs entirely on your infrastructure — local SQLite, your API key or Claude CLI, no telemetry, no SaaS dependency. The only data that leaves your machine is the prompts you send to your configured LLM provider, which you already control. Self-hosted by design.',
        },
      ],
    },
    {
      type: 'card-grid',
      columns: 3,
      cards: [
        {
          color: 'var(--color-neon-cyan)',
          title: 'Measure Everything',
          description:
            'Five dimensions, scored independently, blended with heuristics. Every optimization produces a number you can track over time.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Zero Trust in Self-Rating',
          description:
            'LLMs systematically over-rate their own output. We randomize, blend, normalize, and flag divergence. The score you see is defended.',
        },
        {
          color: 'var(--color-neon-green)',
          title: 'Your Infrastructure',
          description:
            'Local SQLite. Your API key. No telemetry. The codebase is the product — you can read every decision made on your behalf.',
        },
      ],
    },
  ],
};
