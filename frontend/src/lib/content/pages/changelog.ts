import type { ContentPage } from '../types';

export const changelog: ContentPage = {
  slug: 'changelog',
  title: 'What Changed and When.',
  description: 'Release history for Project Synthesis. All notable changes, additions, and fixes.',
  sections: [
    {
      type: 'hero',
      heading: 'WHAT CHANGED AND WHEN.',
      subheading: 'All notable changes to Project Synthesis, in reverse chronological order.',
    },
    {
      type: 'timeline',
      versions: [
        {
          version: 'v2.0.0',
          date: 'Unreleased',
          categories: [
            {
              label: 'ADDED',
              color: 'var(--color-neon-green)',
              items: [
                'Ground-up redesign — clean-slate rebuild of prompt optimization platform',
                '3-phase pipeline orchestrator (analyze → optimize → score) with independent subagent context windows',
                'Hybrid scoring engine — blends LLM scores with model-independent heuristics via score_blender.py',
                'Z-score normalization against historical distribution to prevent score clustering',
                'MCP server with 4 tools: synthesis_optimize, synthesis_analyze, synthesis_prepare_optimization, synthesis_save_result',
                'GitHub OAuth integration with Fernet-encrypted token storage and codebase-aware optimization',
                'Conversational refinement with version history, branching/rollback, and 3 suggestions per turn',
                'Real-time event bus — SSE event stream with optimization, feedback, refinement, and strategy events',
                'In-process pub/sub with cross-process notify via HTTP POST to /api/events/_publish',
                'Scorer A/B randomization to prevent position and verbosity bias',
                'Persistent user preferences (model selection, pipeline toggles, default strategy)',
                '6 optimization strategies with YAML frontmatter (tagline, description) for adaptive discovery',
                'Inline strategy template editor with live disk save and hot-reload via watchfiles.awatch()',
                'Toast notification system with chromatic action encoding',
                'Session persistence via localStorage — page refresh restores last optimization from DB',
                'SHA-based explore caching with TTL and LRU eviction',
                'Background repo file indexing with SHA-based staleness detection',
                'Sentence-transformers embedding service (all-MiniLM-L6-v2, 384-dim) with async wrappers',
                'Passthrough bias correction (default 15% discount) for MCP self-rated scores',
                'Per-phase JSONL trace logs to data/traces/ with daily rotation',
                'In-memory rate limiting (optimize 10/min, refine 10/min, feedback 30/min)',
                'SvelteKit 2 frontend with VS Code workbench layout and industrial cyberpunk design system',
                'Docker single-container deployment with nginx, healthcheck validation, and custom 503 page',
                'init.sh service manager with PID tracking, process group kill, preflight checks, and log rotation',
              ],
            },
            {
              label: 'FIXED',
              color: 'var(--color-neon-cyan)',
              items: [
                'Docker: switched SvelteKit from adapter-auto to adapter-static for nginx static serving',
                'Docker: nginx listens on unprivileged port 8080, removed NET_BIND_SERVICE capability',
                'Docker: Alembic migration errors now fail hard instead of being silently ignored',
                'Docker: entrypoint cleanup propagates actual exit code instead of always returning 0',
                'Docker: healthcheck validates full stack via nginx (port 8080) not just backend directly',
                'Docker: removed text/event-stream from nginx gzip (breaks SSE chunked encoding)',
                'Docker: added .env files to .dockerignore to prevent secret leakage into images',
              ],
            },
          ],
        },
        {
          version: 'v0.7.0',
          date: '2026-02-15',
          categories: [
            {
              label: 'ADDED',
              color: 'var(--color-neon-green)',
              items: [
                'Initial release of Project Synthesis prompt optimization platform',
                'FastAPI backend with SQLite via SQLAlchemy async',
                'Basic optimize endpoint with LLM-based rewriting',
                'SvelteKit frontend with prompt editor and result viewer',
                'Strategy selection with chain-of-thought, few-shot, role-playing, structured-output',
              ],
            },
          ],
        },
      ],
    },
  ],
};
