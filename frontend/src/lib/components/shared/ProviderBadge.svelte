<script lang="ts">
  interface Props {
    provider?: string | null;
  }

  let { provider = null }: Props = $props();

  type BadgeVariant = 'cli' | 'api' | 'mcp' | 'none';

  const variant = $derived((): BadgeVariant => {
    if (!provider) return 'none';
    const p = provider.toLowerCase();
    if (p.includes('cli')) return 'cli';
    if (p.includes('mcp')) return 'mcp';
    if (p.includes('api') || p.includes('anthropic')) return 'api';
    return 'none';
  });

  const label = $derived(() => {
    if (!provider) return 'None';
    const p = provider.toLowerCase();
    if (p.includes('cli')) return 'CLI';
    if (p.includes('mcp')) return 'MCP';
    if (p.includes('api') || p.includes('anthropic')) return 'API';
    return provider.toUpperCase().slice(0, 4);
  });
</script>

<span
  class="provider-badge"
  class:variant-cli={variant() === 'cli'}
  class:variant-api={variant() === 'api'}
  class:variant-mcp={variant() === 'mcp'}
  class:variant-none={variant() === 'none'}
  aria-label="Active provider: {label()}"
  title="Provider: {provider ?? 'None'}"
>
  {label()}
</span>

<style>
  .provider-badge {
    display: inline-flex;
    align-items: center;
    font-size: 10px;
    font-family: var(--font-mono);
    padding: 0 6px;   /* px-1.5 */
    height: 16px;
    border: 1px solid var(--color-border-subtle);
    border-radius: 2px; /* rounded-sm */
    color: var(--color-text-dim);
    white-space: nowrap;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .variant-cli {
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  .variant-api {
    border-color: #a855f7; /* purple */
    color: #a855f7;
  }

  .variant-mcp {
    border-color: var(--color-neon-green);
    color: var(--color-neon-green);
  }

  .variant-none {
    border-color: var(--color-neon-red);
    color: var(--color-neon-red);
  }
</style>
