<script lang="ts">
  let { provider }: { provider: string } = $props();

  let label = $derived(
    provider === 'anthropic' || provider === 'claude_cli' ? 'CLI' :
    provider === 'openai' || provider === 'anthropic_api' ? 'API' :
    'Unknown'
  );

  // Provider → color per spec: CLI=neon-green, API=neon-yellow
  let color = $derived(
    provider === 'anthropic' || provider === 'claude_cli'
      ? 'bg-neon-green/10 text-neon-green border-neon-green/20'
      : provider === 'openai' || provider === 'anthropic_api'
      ? 'bg-neon-yellow/10 text-neon-yellow border-neon-yellow/20'
      : 'bg-neon-red/10 text-neon-red border-neon-red/20'
  );

  // Provider dot color
  let dotColor = $derived(
    provider === 'anthropic' || provider === 'claude_cli'
      ? '#22ff88'
      : provider === 'openai' || provider === 'anthropic_api'
      ? '#fbbf24'
      : '#ff3366'
  );
</script>

<span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-mono font-medium border {color}" data-testid="provider-badge">
  {label}
  <span class="w-1.5 h-1.5 rounded-full" style="background-color: {dotColor};"></span>
</span>
