<script lang="ts">
  /**
   * SuiteDetailView — full view for a single ValidationSuite.
   *
   * Two stacked panels:
   *   1. **Replay history** — paginated list of `RunRow` rows where
   *      `source_suite_id = suite.id`. Each row surfaces the run id, mode,
   *      status, started/completed timestamps, and prompts_generated count.
   *      Backs `GET /api/suites/{id}/replays`.
   *   2. **Per-prompt baseline vs latest** — pairs
   *      `ValidationSuiteOut.baseline_scores.per_prompt[i].overall` with the
   *      matching index in `latestReplay.prompt_results[i].overall_score`,
   *      rendering a signed delta column per prompt. Spec § 10 Cycle 12
   *      INTEGRATE focus.
   *
   * Both tables use `role="row"` + `data-test` markers — IDE-density canon
   * prefers data-grid markup over native `<table>`, but the a11y semantics
   * surface identically through axe-core.
   */
  import type { RunListResponse, RunSummary } from '$lib/api/runs';
  import type { ValidationSuiteOut } from '$lib/api/suites';
  import { formatSignedDelta } from '$lib/utils/formatting';

  /** Minimal shape of a completed replay run carrying per-prompt results.
   *  Pulled out of a `RunRow` row's `aggregate.prompt_results` payload. */
  export interface SuiteReplayRow extends RunSummary {
    prompt_results?: Array<{
      prompt_index: number;
      overall_score: number;
      raw_prompt?: string;
    }>;
  }

  interface Props {
    suite: ValidationSuiteOut;
    replays?: RunListResponse | null;
    latestReplay?: SuiteReplayRow | null;
  }

  let { suite, replays = null, latestReplay = null }: Props = $props();

  // Format an ISO timestamp into a compact `YYYY-MM-DD HH:mm` form. Falls
  // back to the raw string on parse failure.
  function fmt(ts: string | null): string {
    if (!ts) return '—';
    try {
      const d = new Date(ts);
      const pad = (n: number) => n.toString().padStart(2, '0');
      return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
    } catch {
      return ts;
    }
  }

  // Pair baseline + latest per-prompt scores. Index-aligned join — both
  // payloads order by `raw_prompt_idx` / `prompt_index` ascending, so a
  // positional zip is safe. Empty when no replay carries prompt_results.
  const perPromptRows = $derived.by(() => {
    const baseline = suite.baseline_scores.per_prompt ?? [];
    const latest = latestReplay?.prompt_results ?? [];
    return baseline.map((b) => {
      const l = latest.find((x) => x.prompt_index === b.raw_prompt_idx) ?? null;
      const baselineScore = b.overall;
      const latestScore = l?.overall_score ?? null;
      const delta = latestScore != null ? latestScore - baselineScore : null;
      return {
        idx: b.raw_prompt_idx,
        baseline: baselineScore,
        latest: latestScore,
        delta,
      };
    });
  });

  const replayItems = $derived(replays?.items ?? []);
</script>

<section
  class="suite-detail-view"
  data-test="suite-detail-view"
  aria-label="Suite detail for {suite.label}"
>
  <header class="detail-header">
    <span class="detail-label">{suite.label}</span>
    <span class="detail-meta">
      {suite.prompts_snapshot.length}p · baseline {suite.baseline_scores.mean_overall.toFixed(2)}
      · tolerance ±{suite.tolerance_abs.toFixed(2)}
    </span>
  </header>

  <!-- ── Replay history ─────────────────────────────────────────── -->
  <div class="replay-history" data-test="replay-history" role="table" aria-label="Replay history">
    <div class="replay-row replay-row--head" role="row">
      <span role="columnheader" class="col-id">replay</span>
      <span role="columnheader" class="col-status">status</span>
      <span role="columnheader" class="col-started">started</span>
      <span role="columnheader" class="col-completed">completed</span>
      <span role="columnheader" class="col-prompts">prompts</span>
    </div>
    {#if replayItems.length === 0}
      <p class="empty-note">No replays yet. Trigger one from the suite menu.</p>
    {:else}
      {#each replayItems as r (r.id)}
        <div class="replay-row" data-test="replay-row" role="row">
          <span role="cell" class="col-id">{r.id}</span>
          <span role="cell" class="col-status" data-status={r.status}>{r.status}</span>
          <span role="cell" class="col-started">{fmt(r.started_at)}</span>
          <span role="cell" class="col-completed">{fmt(r.completed_at)}</span>
          <span role="cell" class="col-prompts">{r.prompts_generated}</span>
        </div>
      {/each}
    {/if}
  </div>

  <!-- ── Per-prompt baseline vs latest diff ─────────────────────── -->
  <div class="per-prompt" data-test="per-prompt" role="table" aria-label="Per-prompt baseline vs latest">
    <div class="prompt-row prompt-row--head" role="row">
      <span role="columnheader" class="col-idx">#</span>
      <span role="columnheader" class="col-baseline">baseline</span>
      <span role="columnheader" class="col-latest">latest</span>
      <span role="columnheader" class="col-delta">Δ</span>
    </div>
    {#each perPromptRows as row (row.idx)}
      <div
        class="prompt-row"
        data-test="per-prompt-row"
        data-prompt-idx={row.idx}
        role="row"
      >
        <span role="cell" class="col-idx">{row.idx}</span>
        <span role="cell" class="col-baseline">{row.baseline.toFixed(1)}</span>
        <span role="cell" class="col-latest">{row.latest != null ? row.latest.toFixed(1) : '—'}</span>
        <span role="cell" class="col-delta">{formatSignedDelta(row.delta)}</span>
      </div>
    {/each}
  </div>
</section>

<style>
  .suite-detail-view {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
  }

  .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 20px;
    padding: 0 4px;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .detail-label {
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .detail-meta {
    color: var(--color-text-dim);
    white-space: nowrap;
  }

  .replay-history,
  .per-prompt {
    display: flex;
    flex-direction: column;
    border: 1px solid var(--color-border-subtle);
  }

  .replay-row,
  .prompt-row {
    display: grid;
    align-items: center;
    height: 20px;
    padding: 0 4px;
    border-bottom: 1px solid var(--color-border-subtle);
    transition:
      background-color 200ms ease,
      border-color 200ms ease;
  }

  .replay-row {
    grid-template-columns: 1fr 90px 130px 130px 60px;
    gap: 4px;
  }

  .prompt-row {
    grid-template-columns: 40px 80px 80px 80px;
    gap: 4px;
  }

  .replay-row:last-child,
  .prompt-row:last-child {
    border-bottom: none;
  }

  .replay-row--head,
  .prompt-row--head {
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-size: 9px;
  }

  .replay-row[data-test='replay-row']:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 40%, transparent);
  }

  .col-status {
    color: var(--color-text-dim);
  }

  .col-status[data-status='completed'] {
    color: var(--color-neon-green, #22ff88);
  }

  .col-status[data-status='running'] {
    color: var(--color-neon-cyan, #2cd0ff);
  }

  .col-status[data-status='partial'] {
    color: var(--color-neon-yellow, #ffd166);
  }

  .col-status[data-status='failed'] {
    color: var(--color-neon-red, #ff3366);
  }

  .col-delta {
    color: var(--color-text-primary);
    font-variant-numeric: tabular-nums;
  }

  .empty-note {
    padding: 8px;
    color: var(--color-text-dim);
    font-style: italic;
  }
</style>
