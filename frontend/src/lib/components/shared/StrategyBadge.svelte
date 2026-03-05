<script lang="ts">
  let { strategy }: { strategy: string } = $props();

  // Chromatic encoding per spec: strategy → neon color
  const colorMap: Record<string, string> = {
    'auto':                'bg-neon-cyan/10 text-neon-cyan border-neon-cyan/20',
    'chain-of-thought':    'bg-neon-cyan/10 text-neon-cyan border-neon-cyan/20',
    'co-star':             'bg-neon-purple/10 text-neon-purple border-neon-purple/20',
    'CO-STAR':             'bg-neon-purple/10 text-neon-purple border-neon-purple/20',
    'risen':               'bg-neon-green/10 text-neon-green border-neon-green/20',
    'RISEN':               'bg-neon-green/10 text-neon-green border-neon-green/20',
    'role-task-format':    'bg-neon-red/10 text-neon-red border-neon-red/20',
    'few-shot-scaffolding':'bg-neon-yellow/10 text-neon-yellow border-neon-yellow/20',
    'step-by-step':        'bg-neon-orange/10 text-neon-orange border-neon-orange/20',
    'structured-output':   'bg-neon-blue/10 text-neon-blue border-neon-blue/20',
    'constraint-injection':'bg-neon-pink/10 text-neon-pink border-neon-pink/20',
    'context-enrichment':  'bg-neon-teal/10 text-neon-teal border-neon-teal/20',
    'persona-assignment':  'bg-neon-indigo/10 text-neon-indigo border-neon-indigo/20',
  };

  // Normalize strategy name for lookup (lowercase)
  let classes = $derived(
    colorMap[strategy] ||
    colorMap[strategy?.toLowerCase()] ||
    'bg-bg-card text-text-secondary border-border-subtle'
  );
</script>

<span class="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-mono font-medium border {classes}" data-testid="strategy-badge">
  {strategy}
</span>
