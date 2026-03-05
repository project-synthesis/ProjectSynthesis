<script lang="ts">
  let { score, max = 10 }: { score: number; max?: number } = $props();

  let pct = $derived(Math.min(100, Math.max(0, (score / max) * 100)));

  // Score → color per spec: 1-3=red, 4-6=yellow, 7-8=cyan, 9-10=green
  function getScoreColor(s: number): string {
    if (s >= 9) return '#22ff88';
    if (s >= 7) return '#00e5ff';
    if (s >= 4) return '#fbbf24';
    return '#ff3366';
  }

  let barColor = $derived(getScoreColor(score));
</script>

<div class="w-full h-1.5 bg-bg-primary rounded-full overflow-hidden" data-testid="score-bar">
  <div
    class="h-full rounded-full"
    style="width: {pct}%; background-color: {barColor}; transition: width 500ms ease;"
  ></div>
</div>
