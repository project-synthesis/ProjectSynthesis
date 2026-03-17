import type { ContentPage } from '../types';

export const integrations: ContentPage = {
  slug: 'integrations',
  title: 'Plug In Anywhere.',
  description: 'MCP server for Claude Code, GitHub OAuth for codebase-aware optimization, Docker for one-command deployment. Project Synthesis meets your workflow.',
  sections: [
    {
      type: 'hero',
      heading: 'PLUG IN ANYWHERE.',
      subheading:
        'MCP server for Claude Code. GitHub OAuth for codebase-aware optimization. Docker for one-command deployment. Project Synthesis meets your workflow where it lives.',
    },
    {
      type: 'card-grid',
      columns: 3,
      cards: [
        {
          color: 'var(--color-neon-cyan)',
          title: 'MCP Server',
          description:
            'Four tools on port 8001 — optimize, analyze, prepare, save. Add one line to .mcp.json and optimize prompts without leaving Claude Code.',
        },
        {
          color: 'var(--color-neon-purple)',
          title: 'GitHub OAuth',
          description:
            'Link repositories for codebase-aware optimization. The explore phase retrieves semantic context from your actual code — not a generic description.',
        },
        {
          color: 'var(--color-neon-green)',
          title: 'Docker',
          description:
            'Single-container deployment bundles backend, frontend, MCP server, and nginx. One command to ship: docker compose up --build -d.',
        },
      ],
    },
    {
      type: 'code-block',
      language: 'json',
      filename: '.mcp.json',
      code: '{\n  "synthesis": {\n    "url": "http://127.0.0.1:8001/mcp"\n  }\n}',
    },
    {
      type: 'code-block',
      language: 'bash',
      filename: '.env',
      code: 'GITHUB_OAUTH_CLIENT_ID=your_id\nGITHUB_OAUTH_CLIENT_SECRET=your_secret',
    },
    {
      type: 'code-block',
      language: 'bash',
      filename: 'terminal',
      code: 'docker compose up --build -d',
    },
  ],
};
