import type { ContentPage } from '../types';

export const security: ContentPage = {
  slug: 'security',
  title: 'Defense in Depth.',
  description: 'Fernet encryption at rest, input validation on all endpoints, workspace isolation, and a clear vulnerability disclosure process.',
  sections: [
    {
      type: 'hero',
      heading: 'DEFENSE IN DEPTH.',
      subheading:
        'Encryption at rest, input validation on every endpoint, workspace isolation in the MCP server, and a responsible disclosure process with a 72-hour acknowledgment SLA.',
    },
    {
      type: 'step-flow',
      steps: [
        {
          title: 'REPORT PRIVATELY',
          description:
            'Use GitHub private vulnerability reporting or email support@zenresources.net. Do not open a public issue — we need time to patch before disclosure.',
        },
        {
          title: 'TEAM ACKNOWLEDGES (72H)',
          description:
            'We acknowledge all valid security reports within 72 hours with an initial assessment and expected timeline for a fix.',
        },
        {
          title: 'FIX IN PRIVATE BRANCH',
          description:
            'The fix is developed and reviewed in a private fork. Coordination with the reporter happens throughout if they wish to be involved.',
        },
        {
          title: 'ADVISORY PUBLISHED',
          description:
            'After the fix ships, a GitHub Security Advisory is published with CVE assignment if applicable, full technical details, and credit to the reporter.',
        },
      ],
    },
    {
      type: 'card-grid',
      columns: 2,
      cards: [
        {
          color: 'var(--color-neon-cyan)',
          title: 'Encryption at Rest',
          description:
            'API keys and GitHub tokens are stored Fernet-encrypted. The SECRET_KEY is auto-generated per deployment and stored at data/.app_secrets with 0o600 permissions.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Input Validation',
          description:
            'All API endpoints validate inputs via Pydantic models. Rate limiting is enforced per endpoint (optimize: 10/min, refine: 10/min, feedback: 30/min, default: 60/min).',
        },
        {
          color: 'var(--color-neon-green)',
          title: 'Workspace Isolation',
          description:
            'The MCP server\'s roots scanner wraps retrieved workspace content in <untrusted-context> tags and enforces per-file character caps (10K chars, 500 lines) before injection.',
        },
        {
          color: 'var(--color-neon-yellow)',
          title: 'No External Dependencies',
          description:
            'No analytics, no error tracking, no CDN. The application\'s only outbound connections are to your configured LLM provider and optionally GitHub OAuth.',
        },
      ],
    },
  ],
};
