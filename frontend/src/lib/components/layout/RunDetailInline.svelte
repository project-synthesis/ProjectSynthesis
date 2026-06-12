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

  // v0.4.37 §4.3 — replay per-prompt expansion state.
  let expandedPromptIdx = $state<number | null>(null);
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
      {#if (full.prompt_results ?? []).length > 0}
        <!-- v0.4.37 §4.3 — per-prompt list. Pre-v0.4.37 rows lack the
             output keys entirely and degrade: no output section, no dead
             affordances (key-presence discriminator). -->
        <div class="replay-prompts" role="list" data-test="replay-prompt-list">
          {#each full.prompt_results ?? [] as row, i (i)}
            {@const idx = row.raw_prompt_idx ?? row.prompt_index ?? i}
            <div class="replay-prompt-row" role="listitem" data-test="replay-prompt-row">
              <button
                type="button"
                class="replay-prompt-head"
                aria-expanded={expandedPromptIdx === idx}
                onclick={() => { expandedPromptIdx = expandedPromptIdx === idx ? null : idx; }}
              >
                <span class="rp-idx">{idx}</span>
                <span class="rp-intent">{row.intent_label ?? '—'}</span>
                <span class="rp-score">{row.overall_score != null ? row.overall_score.toFixed(1) : '—'}</span>
                <span class="rp-status" data-status={row.status ?? 'completed'}>{row.status ?? '—'}</span>
              </button>
              {#if expandedPromptIdx === idx}
                <div class="replay-prompt-body" data-test="replay-prompt-body">
                  {#if row.raw_prompt}
                    <!-- Backend truncates raw_prompt at 1,000 chars — render
                         as-is with an ellipsis marker at the boundary. -->
                    <pre class="rp-prompt">{row.raw_prompt}{row.raw_prompt.length >= 1000 ? '…' : ''}</pre>
                  {/if}
                  {#if row.dimensions}
                    <div class="rp-dims" data-test="replay-prompt-dims">
                      {#each Object.entries(row.dimensions) as [dim, val] (dim)}
                        <span class="rp-dim-chip">{dim} {val != null ? val.toFixed(1) : '—'}</span>
                      {/each}
                    </div>
                  {/if}
                  {#if row.changes_summary}
                    <pre class="rp-changes" data-test="replay-prompt-changes">{row.changes_summary}</pre>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}
        </div>
      {/if}
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

  /* ── v0.4.37 §4.3 — replay per-prompt list (20px rows, zero-effects) ── */
  .replay-prompts { display: flex; flex-direction: column; gap: 2px; margin-top: 4px; }
  .replay-prompt-row { border: 1px solid var(--color-border-subtle); }
  .replay-prompt-head {
    display: grid;
    grid-template-columns: 24px 1fr 36px 64px;
    gap: 4px;
    align-items: center;
    width: 100%;
    height: 20px;
    padding: 0 4px;
    background: transparent;
    border: none;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 10px;
    text-align: left;
    cursor: pointer;
    transition: background-color 200ms ease;
  }
  .replay-prompt-head:hover { background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent); }
  .replay-prompt-head > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .rp-idx { color: var(--color-text-dim); }
  .rp-status { color: var(--color-text-dim); }
  .rp-status[data-status='completed'] { color: var(--color-neon-green, #22ff88); }
  .rp-status[data-status='failed'] { color: var(--color-neon-red, #ff3366); }
  .replay-prompt-body {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px;
    border-top: 1px solid var(--color-border-subtle);
  }
  .rp-prompt, .rp-changes {
    margin: 0;
    max-height: 140px;
    overflow: auto;
    font-family: var(--font-mono);
    font-size: 10px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--color-text-primary);
  }
  .rp-changes { color: var(--color-text-secondary); }
  .rp-dims { display: flex; flex-wrap: wrap; gap: 4px; }
  /* Dimension-chip recipe — keep visually in lockstep with `.dim-chip`
     in suites/SuiteDetailView.svelte (the suite-detail surface renders
     the same per-prompt dimension data with the same chip treatment). */
  .rp-dim-chip {
    display: inline-flex;
    align-items: center;
    height: 16px;
    padding: 0 4px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
  }
</style>
