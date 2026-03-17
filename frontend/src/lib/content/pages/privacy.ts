import type { ContentPage } from '../types';

export const privacy: ContentPage = {
  slug: 'privacy',
  title: 'Your Prompts. Your Infrastructure. Your Data.',
  description: 'Project Synthesis is self-hosted. Your prompts stay on your machine. No telemetry, no SaaS dependency, no external data collection.',
  sections: [
    {
      type: 'hero',
      heading: 'YOUR PROMPTS. YOUR INFRASTRUCTURE. YOUR DATA.',
      subheading:
        'Project Synthesis runs entirely on your machine. No SaaS dependency. No telemetry. The only data that leaves is what you send to your configured LLM provider.',
    },
    {
      type: 'prose',
      blocks: [
        {
          heading: 'Data Processing',
          content:
            'All optimization data — prompts, results, scores, feedback, refinement sessions — is stored in a local SQLite database at data/synthesis.db. No data is sent to any Project Synthesis service. The application has no network dependencies beyond your configured LLM provider (Anthropic API or Claude CLI) and optionally GitHub OAuth.',
        },
        {
          heading: 'LLM Provider Communication',
          content:
            'When you submit a prompt for optimization, the text is sent directly to your configured LLM provider — either via the Anthropic API using your own key, or via the Claude CLI using your own account. Project Synthesis acts as an orchestrator, not a proxy. It does not intercept, log, or store provider responses beyond the structured pipeline outputs that it writes to your local database.',
        },
        {
          heading: 'GitHub Integration',
          content:
            'GitHub OAuth tokens are encrypted at rest using Fernet symmetric encryption. The SECRET_KEY is auto-generated on first startup and persisted to data/.app_secrets with 0o600 permissions. Tokens are decrypted in memory only for the duration of an API call. Repository content retrieved during the explore phase is stored only in the local in-memory TTL cache.',
        },
        {
          heading: 'Secrets Management',
          content:
            'The Anthropic API key, if configured via the UI, is stored Fernet-encrypted in data/.api_credentials. The auto-generated SECRET_KEY is stored in data/.app_secrets. Both files are created with restrictive permissions and should never be committed to version control. The .gitignore excludes the entire data/ directory by default.',
        },
        {
          heading: 'No Cloud Services',
          content:
            'Project Synthesis does not use any analytics service, error tracking service, CDN, or external authentication provider beyond GitHub OAuth (which is optional and only active if you configure the OAuth credentials). There is no telemetry endpoint, no crash reporting, and no usage metering.',
        },
        {
          heading: 'Data Retention',
          content:
            'Optimization history is retained indefinitely in the local database unless you delete it. Trace logs in data/traces/ rotate daily and are pruned according to the TRACE_RETENTION_DAYS configuration (default: 30 days). The in-memory explore cache uses LRU eviction and a configurable TTL. You control all retention policies.',
        },
      ],
    },
  ],
};
