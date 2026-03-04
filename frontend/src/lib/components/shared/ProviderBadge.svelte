<script lang="ts">
  let { provider }: { provider: string } = $props();

  let label = $derived(
    provider === 'anthropic' || provider === 'claude_cli' ? 'CLI' :
    provider === 'openai' || provider === 'anthropic_api' ? 'API' :
    'Unknown'
  );

  let color = $derived(
    provider === 'anthropic' || provider === 'claude_cli' ? 'bg-neon-orange/10 text-neon-orange border-neon-orange/20' :
    provider === 'openai' || provider === 'anthropic_api' ? 'bg-neon-green/10 text-neon-green border-neon-green/20' :
    'bg-bg-card text-text-dim border-border-subtle'
  );
</script>

<span class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium border {color}">
  {label}
</span>
