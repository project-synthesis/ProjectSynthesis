<script lang="ts">
  interface Props {
    scores: number[];
  }

  let { scores }: Props = $props();

  const WIDTH = 120;
  const HEIGHT = 24;
  const PADDING = 2;

  const points = $derived.by(() => {
    if (scores.length < 2) return '';
    const min = Math.min(...scores);
    const max = Math.max(...scores);
    const range = max - min || 1;
    const step = (WIDTH - PADDING * 2) / (scores.length - 1);
    return scores
      .map((s, i) => {
        const x = PADDING + i * step;
        const y = HEIGHT - PADDING - ((s - min) / range) * (HEIGHT - PADDING * 2);
        return `${x},${y}`;
      })
      .join(' ');
  });
</script>

{#if scores.length >= 2}
  <svg
    width={WIDTH}
    height={HEIGHT}
    viewBox="0 0 {WIDTH} {HEIGHT}"
    class="sparkline"
    aria-label="Score progression sparkline"
    role="img"
  >
    <polyline
      points={points}
      fill="none"
      stroke="var(--tier-accent, var(--color-neon-cyan))"
      stroke-width="1.5"
      stroke-linejoin="round"
      stroke-linecap="round"
    />
  </svg>
{/if}

<style>
  .sparkline {
    display: block;
    flex-shrink: 0;
  }
</style>
