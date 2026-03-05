<script lang="ts">
  let { score, size = 20 }: { score: number; size?: number } = $props();

  // Score → color mapping per spec:
  // 1-3=neon-red, 4-6=neon-yellow, 7-8=neon-cyan, 9-10=neon-green
  function getScoreColor(s: number): string {
    if (s >= 9) return '#22ff88';  // neon-green
    if (s >= 7) return '#00e5ff';  // neon-cyan
    if (s >= 4) return '#fbbf24';  // neon-yellow
    return '#ff3366';              // neon-red
  }

  let scoreColor = $derived(getScoreColor(score));
  let fontSize = $derived(size <= 24 ? 10 : size <= 36 ? 12 : 14);
</script>

<div
  class="score-circle inline-flex items-center justify-center rounded-full font-mono font-bold shrink-0"
  style="width: {size}px; height: {size}px; color: {scoreColor}; font-size: {fontSize}px; box-shadow: inset 0 0 0 1.5px {scoreColor}; background: color-mix(in srgb, {scoreColor} 8%, transparent);"
  title="Score: {score}/10"
  aria-label="Score: {score} out of 10"
  role="img"
  data-testid="score-circle"
>
  {score.toFixed(score % 1 === 0 ? 0 : 1)}
</div>
