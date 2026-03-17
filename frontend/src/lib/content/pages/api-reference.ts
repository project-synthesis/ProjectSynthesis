import type { ContentPage } from '../types';

export const apiReference: ContentPage = {
  slug: 'api-reference',
  title: 'Every Endpoint. Every Parameter.',
  description: 'Complete REST API reference for Project Synthesis. All endpoints on port 8000, grouped by router.',
  sections: [
    {
      type: 'hero',
      heading: 'EVERY ENDPOINT. EVERY PARAMETER.',
      subheading:
        'REST API on port 8000. All responses follow the pagination envelope for list endpoints. SSE streams use text/event-stream with JSON event payloads.',
    },
    {
      type: 'endpoint-list',
      groups: [
        {
          name: 'Pipeline',
          endpoints: [
            {
              method: 'SSE',
              path: 'POST /api/optimize',
              description: 'Run the full 3-phase pipeline (analyze → optimize → score). Streams phase events via SSE.',
              details: 'Body: { prompt, strategy?, context? }. Events: analyzed, optimized, scored, done, error.',
            },
            {
              method: 'GET',
              path: 'GET /api/optimize/{trace_id}',
              description: 'Retrieve a completed optimization by trace ID.',
              details: 'Returns full OptimizationRecord with all phase outputs and scores.',
            },
            {
              method: 'POST',
              path: 'POST /api/optimize/passthrough',
              description: 'Assemble the full prompt + context for external LLM processing (MCP passthrough prepare step).',
              details: 'Body: { prompt, strategy?, workspace_path? }. Returns assembled prompt string.',
            },
            {
              method: 'POST',
              path: 'POST /api/optimize/passthrough/save',
              description: 'Persist an externally optimized result with heuristic bias correction.',
              details: 'Body: { original_prompt, optimized_prompt, self_scores }. Applies 0.85 passthrough discount.',
            },
          ],
        },
        {
          name: 'Refinement',
          endpoints: [
            {
              method: 'SSE',
              path: 'POST /api/refine',
              description: 'Start or continue a refinement session. Streams the same phase events as /api/optimize.',
              details: 'Body: { session_id?, optimization_id, instruction }. Returns session_id in done event.',
            },
            {
              method: 'GET',
              path: 'GET /api/refine/{id}/versions',
              description: 'List all versions in a refinement session.',
              details: 'Returns array of RefinementVersion with scores, instructions, and branch metadata.',
            },
            {
              method: 'POST',
              path: 'POST /api/refine/{id}/rollback',
              description: 'Roll back to a previous version, creating a branch fork.',
              details: 'Body: { version_id }. Returns new session continuing from the rollback point.',
            },
          ],
        },
        {
          name: 'History',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/history',
              description: 'List optimizations with sort, filter, and pagination.',
              details: 'Query: sort_by, sort_dir, strategy, task_type, offset, limit. Returns pagination envelope.',
            },
          ],
        },
        {
          name: 'Feedback',
          endpoints: [
            {
              method: 'POST',
              path: 'POST /api/feedback',
              description: 'Submit thumbs up/down feedback for an optimization.',
              details: 'Body: { optimization_id, rating: "up" | "down" }. Updates strategy affinity tracker.',
            },
            {
              method: 'GET',
              path: 'GET /api/feedback',
              description: 'Retrieve feedback for an optimization.',
              details: 'Query: optimization_id. Returns rating if exists.',
            },
          ],
        },
        {
          name: 'Providers',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/providers',
              description: 'List available LLM providers and active provider.',
            },
            {
              method: 'GET',
              path: 'GET /api/provider/api-key',
              description: 'Check whether an API key is configured (masked).',
            },
            {
              method: 'PATCH',
              path: 'PATCH /api/provider/api-key',
              description: 'Set or update the Anthropic API key. Triggers provider hot-reload.',
              details: 'Body: { api_key }. Key stored Fernet-encrypted in data/.api_credentials.',
            },
            {
              method: 'DELETE',
              path: 'DELETE /api/provider/api-key',
              description: 'Remove the stored API key and revert to CLI provider.',
            },
          ],
        },
        {
          name: 'Preferences',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/preferences',
              description: 'Read persistent user preferences (model selection, pipeline toggles, default strategy).',
            },
            {
              method: 'PATCH',
              path: 'PATCH /api/preferences',
              description: 'Update one or more preference fields. Persisted to data/preferences.json.',
              details: 'Body: partial Preferences object. Returns updated preferences.',
            },
          ],
        },
        {
          name: 'Strategies',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/strategies',
              description: 'List all available strategy files with name, tagline, and description.',
            },
            {
              method: 'GET',
              path: 'GET /api/strategies/{name}',
              description: 'Retrieve a single strategy template including frontmatter and body.',
            },
            {
              method: 'PUT',
              path: 'PUT /api/strategies/{name}',
              description: 'Save a strategy template to disk. Triggers file watcher event.',
              details: 'Body: { content } — full Markdown content including YAML frontmatter.',
            },
          ],
        },
        {
          name: 'Settings',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/settings',
              description: 'Read-only server configuration (model IDs, rate limits, feature flags).',
            },
          ],
        },
        {
          name: 'GitHub Auth',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/github/auth/login',
              description: 'Redirect to GitHub OAuth authorization page.',
            },
            {
              method: 'GET',
              path: 'GET /api/github/auth/callback',
              description: 'Handle OAuth callback, exchange code for token, set session cookie.',
            },
            {
              method: 'GET',
              path: 'GET /api/github/auth/me',
              description: 'Return current authenticated GitHub user or 401.',
            },
            {
              method: 'POST',
              path: 'POST /api/github/auth/logout',
              description: 'Clear GitHub session and revoke stored token.',
            },
          ],
        },
        {
          name: 'GitHub Repos',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/github/repos',
              description: 'List repositories accessible to the authenticated user.',
            },
            {
              method: 'POST',
              path: 'POST /api/github/repos/link',
              description: 'Link a repository for codebase-aware optimization.',
              details: 'Body: { owner, repo }. Triggers background indexing.',
            },
            {
              method: 'GET',
              path: 'GET /api/github/repos/linked',
              description: 'Return the currently linked repository.',
            },
            {
              method: 'DELETE',
              path: 'DELETE /api/github/repos/unlink',
              description: 'Unlink the current repository and clear the index.',
            },
          ],
        },
        {
          name: 'Health',
          endpoints: [
            {
              method: 'GET',
              path: 'GET /api/health',
              description: 'Service health check with provider status, score health, recent error counts, and avg phase durations.',
            },
          ],
        },
        {
          name: 'Events',
          endpoints: [
            {
              method: 'SSE',
              path: 'GET /api/events',
              description: 'Subscribe to real-time events via SSE. Receives optimization, feedback, refinement, and strategy events.',
              details: 'Event types: optimization_created, optimization_analyzed, optimization_failed, feedback_submitted, refinement_turn, strategy_changed.',
            },
            {
              method: 'POST',
              path: 'POST /api/events/_publish',
              description: 'Internal cross-process event publish endpoint (used by MCP server).',
              details: 'Body: { event_type, payload }.',
            },
          ],
        },
      ],
    },
  ],
};
