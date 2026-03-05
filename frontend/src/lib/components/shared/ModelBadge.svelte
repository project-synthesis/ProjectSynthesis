<script lang="ts">
  let { model }: { model: string } = $props();

  // Model → color mapping per spec:
  // haiku=neon-teal, sonnet=neon-indigo, opus=neon-purple
  function getModelColor(m: string): string {
    const lower = m.toLowerCase();
    if (lower.includes('haiku')) return 'bg-neon-teal/10 text-neon-teal border-neon-teal/20';
    if (lower.includes('sonnet')) return 'bg-neon-indigo/10 text-neon-indigo border-neon-indigo/20';
    if (lower.includes('opus')) return 'bg-neon-purple/10 text-neon-purple border-neon-purple/20';
    return 'bg-bg-card text-text-secondary border-border-subtle';
  }

  // Extract short display name (e.g., "claude-haiku-4-5" → "HAIKU")
  function getDisplayName(m: string): string {
    const lower = m.toLowerCase();
    if (lower.includes('haiku')) return 'HAIKU';
    if (lower.includes('sonnet')) return 'SONNET';
    if (lower.includes('opus')) return 'OPUS';
    return m;
  }

  let color = $derived(getModelColor(model));
  let displayName = $derived(getDisplayName(model));
</script>

<span class="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-mono font-medium border {color}" data-testid="model-badge">
  {displayName}
</span>
