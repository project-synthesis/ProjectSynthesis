<script lang="ts">
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    provider?: string | null;
  }

  let { provider = null }: Props = $props();

  type BadgeVariant = 'cli' | 'api' | 'mcp' | 'passthrough' | 'none';

  const variant = $derived.by((): BadgeVariant => {
    if (!provider) return 'passthrough';
    const p = provider.toLowerCase();
    if (p.includes('cli')) return 'cli';
    if (p.includes('mcp')) return 'mcp';
    if (p.includes('api') || p.includes('anthropic')) return 'api';
    return 'none';
  });

  const label = $derived.by(() => {
    if (!provider) return 'PASSTHROUGH';
    const p = provider.toLowerCase();
    if (p.includes('cli')) return 'CLI';
    if (p.includes('mcp')) return 'MCP';
    if (p.includes('api') || p.includes('anthropic')) return 'API';
    return provider.toUpperCase().slice(0, 4);
  });
</script>

<span
  class="provider-badge"
  class:variant-cli={variant === 'cli'}
  class:variant-api={variant === 'api'}
  class:variant-mcp={variant === 'mcp'}
  class:variant-passthrough={variant === 'passthrough'}
  class:variant-none={variant === 'none'}
  aria-label="Active provider: {label}"
  use:tooltip={`Provider: ${provider ?? 'None'}`}
>
  {label}
</span>

<style>
  .provider-badge {
    display: inline-flex;
    align-items: center;
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 1px 6px;
    border: 1px solid var(--color-border-subtle);
    border-radius: 0;
    color: var(--color-text-dim);
    white-space: nowrap;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .variant-cli {
    border-color: rgba(0, 229, 255, 0.3);
    color: var(--color-neon-cyan);
  }

  .variant-api {
    border-color: var(--color-neon-purple); /* purple */
    color: var(--color-neon-purple);
  }

  .variant-mcp {
    border-color: var(--color-neon-green);
    color: var(--color-neon-green);
  }

  .variant-passthrough {
    border-color: var(--color-neon-yellow);
    color: var(--color-neon-yellow);
  }

  .variant-none {
    border-color: var(--color-neon-red);
    color: var(--color-neon-red);
  }
</style>
