<!-- frontend/src/lib/components/layout/RunDetailInline.svelte -->
<script lang="ts">
  import { getRun, type RunSummary } from '$lib/api/runs';
  import { goto } from '$app/navigation';
  import TopicProbeReportCard, { type RunResult as TopicProbeRunResult } from '$lib/components/probes/TopicProbeReportCard.svelte';
  import DrillButton from '$lib/components/probes/DrillButton.svelte';

  interface Props {
    run: RunSummary;
  }
  let { run }: Props = $props();

  // `$derived` wraps the fetch so it re-runs if `run.id` changes (silences
  // `state_referenced_locally`); the closure also keeps the promise reactive
  // to prop swaps when the parent re-uses this component for a different row.
  const fullPromise = $derived(getRun(run.id));
</script>

{#await fullPromise}
  <p class="text-[10px] text-text-dim">Loading detail…</p>
{:then full}
  {#if run.mode === 'topic_probe'}
    <!-- Structural cast: `runs.ts::RunResult.prompt_results[].raw_prompt` is
         `string | undefined` (backend may omit on partial rows), while
         `TopicProbeReportCard` narrows to `string`. The card's `tagRow`
         snippet defends against missing fields, so the runtime contract
         is satisfied; the cast bridges the two type surfaces without
         polluting the api module's permissive shape. -->
    <TopicProbeReportCard result={full as unknown as TopicProbeRunResult} />
  {:else if run.mode === 'seed_agent'}
    {@const clusters = (full.seed_agent_meta as { clusters?: Array<{ id: string; label: string; domain: string; task_type: string }> } | null)?.clusters ?? []}
    {#if clusters.length > 0}
      <div class="seed-clusters-inline">
        {#each clusters as cluster}
          <div class="seed-cluster-row-inline">
            <span class="seed-cluster-label">{cluster.label}</span>
            <span class="seed-cluster-meta text-text-dim">{cluster.domain} · {cluster.task_type}</span>
            <DrillButton {cluster} onDrilled={(rid) => goto(`/probes/${rid}`)} />
          </div>
        {/each}
      </div>
    {:else}
      <p class="text-[10px] text-text-dim">No clusters available for this run.</p>
    {/if}
  {:else if run.mode === 'replay_run'}
    {@const agg = full.aggregate as { mean_overall?: number | null; baseline_mean?: number | null } | null}
    {#if agg?.mean_overall != null}
      <div class="replay-summary">
        <span class="text-[10px]">Suite: <code>{full.suite_id ?? '—'}</code></span>
        <span class="text-[10px]">Latest mean: {agg.mean_overall.toFixed(2)}</span>
        {#if agg.baseline_mean != null}
          <span class="text-[10px]">Baseline: {agg.baseline_mean.toFixed(2)}</span>
        {/if}
      </div>
    {:else}
      <p class="text-[10px] text-text-dim">Replay in progress.</p>
    {/if}
  {/if}
{:catch err}
  <p class="text-[10px] text-neon-red">Failed to load detail: {err.message}</p>
{/await}

<style>
  .seed-clusters-inline { display: flex; flex-direction: column; gap: 4px; }
  .seed-cluster-row-inline {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 6px;
    align-items: center;
    padding: 2px 4px;
    border: 1px solid var(--color-border-subtle);
  }
  .seed-cluster-label { font-size: 11px; }
  .seed-cluster-meta { font-family: var(--font-mono); font-size: 10px; }
  .replay-summary { display: flex; flex-direction: column; gap: 2px; }
</style>
