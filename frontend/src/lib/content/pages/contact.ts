import type { ContentPage } from '../types';

export const contact: ContentPage = {
  slug: 'contact',
  title: 'Signal, Not Noise.',
  description: 'Purpose-built channels for bugs, features, security, and general inquiries. Use the right channel and get a faster response.',
  sections: [
    {
      type: 'hero',
      heading: 'SIGNAL, NOT NOISE.',
      subheading:
        'Use the right channel. Bug reports get triaged faster with reproduction steps. Security issues stay private until patched.',
    },
    {
      type: 'card-grid',
      columns: 2,
      cards: [
        {
          color: 'var(--color-neon-red)',
          title: 'Bug Reports',
          description:
            'Open a GitHub Issue with reproduction steps, expected vs actual behavior, and the relevant logs from data/backend.log or data/frontend.log.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Feature Requests',
          description:
            'Start a GitHub Discussion. Describe the use case, not the implementation. The best feature requests include a failing scenario.',
        },
        {
          color: 'var(--color-neon-orange)',
          title: 'Security',
          description:
            'Do not open a public issue. Report privately via GitHub private vulnerability reporting or email support@zenresources.net. We acknowledge within 72 hours.',
        },
        {
          color: 'var(--color-neon-cyan)',
          title: 'General',
          description:
            'For everything else — integrations, deployment questions, partnership inquiries — use the form below or email support@zenresources.net.',
        },
      ],
    },
    {
      type: 'contact-form',
      categories: ['Bug Report', 'Feature Request', 'Security', 'General'],
      successMessage: 'Demo form — for real inquiries, contact support@zenresources.net',
    },
  ],
};
