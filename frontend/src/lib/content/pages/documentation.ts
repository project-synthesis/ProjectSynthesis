import type { ContentPage } from '../types';

export const documentation: ContentPage = {
  slug: 'documentation',
  title: 'Everything You Need to Ship.',
  description: 'Quickstart, architecture deep-dives, configuration reference, and deployment guides. Everything to go from clone to production.',
  sections: [
    {
      type: 'hero',
      heading: 'EVERYTHING YOU NEED TO SHIP.',
      subheading:
        'From first clone to production deployment. Quickstarts, architecture references, configuration guides, and prompt template documentation.',
    },
    {
      type: 'card-grid',
      columns: 3,
      cards: [
        {
          color: 'var(--color-neon-cyan)',
          title: 'Quickstart',
          description:
            'Clone the repo, run ./init.sh, open the app. Three services start automatically: FastAPI on 8000, SvelteKit on 5199, MCP on 8001.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'Architecture',
          description:
            'Layer rules, provider injection, pipeline phases, scorer bias mitigation, event bus, workspace intelligence — the full system design.',
        },
        {
          color: 'var(--color-neon-yellow)',
          title: 'Configuration',
          description:
            'Environment variables, model selection per phase, pipeline toggles, rate limits, trace retention, and service ports — all configurable.',
        },
        {
          color: 'var(--color-neon-green)',
          title: 'Contributing',
          description:
            'Layer rules enforced by code review. Strategy files are the lowest-friction contribution — drop a Markdown file in prompts/strategies/ to add a new strategy.',
        },
        {
          color: 'var(--color-neon-pink)',
          title: 'Prompt Templates',
          description:
            'All prompts live in prompts/ with {{variable}} substitution. Validated at startup against manifest.json. Hot-reloaded on every call — edit without restart.',
        },
        {
          color: 'var(--color-neon-teal)',
          title: 'Deployment',
          description:
            'Docker single-container deployment with nginx, adapter-static SvelteKit, and healthcheck validation. Supports Bedrock and Vertex AI providers.',
        },
      ],
    },
    {
      type: 'code-block',
      language: 'bash',
      filename: 'terminal',
      code: 'git clone https://github.com/your-org/PromptForge_v2.git\ncd PromptForge_v2\n./init.sh',
    },
  ],
};
