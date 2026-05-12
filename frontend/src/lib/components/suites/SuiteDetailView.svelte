<script lang="ts">
  /**
   * SuiteDetailView — full view for a single ValidationSuite.
   *
   * Three stacked panels:
   *   1. **Suite meta header** — surfaces every field of
   *      `ValidationSuiteOut` not already encoded in the panel title: the
   *      source run id, repo + project scope, created timestamp, tolerance
   *      band, baseline distribution (mean / p5 / p50 / p95), task-type
   *      distribution, plus the retired-at + retired-reason fields when
   *      the suite has been retired. Per plan task 12.4 INTEGRATE A3 —
   *      every `ValidationSuiteOut` field is either rendered visibly or
   *      surfaced through tooltips so no canonical truth is hidden.
   *   2. **Replay history** — paginated list of `RunRow` rows where
   *      `source_suite_id = suite.id`. Each row surfaces the run id, mode,
   *      status, started/completed timestamps, and prompts_generated count.
   *      Backs `GET /api/suites/{id}/replays`.
   *   3. **Per-prompt baseline vs latest** — pairs
   *      `ValidationSuiteOut.baseline_scores.per_prompt[i].overall` with the
   *      matching index in `latestReplay.prompt_results[i].overall_score`,
   *      rendering a signed delta column per prompt. Spec § 10 Cycle 12
   *      INTEGRATE focus.
   *
   * Plus a **replay warnings strip** between meta + replay-history —
   * `latestReplay.aggregate.replay_warnings` is rendered as a neon-yellow
   * chromatic row when non-empty. Per spec § 4 `suite_repo_drift`
   * clarification + plan task 12.4: warnings come from the polled
   * `GET /api/runs/{id}` aggregate, NOT from the immediate 202 dispatch
   * response.
   *
   * Both tables use `role="row"` + `data-test` markers — IDE-density canon
   * prefers data-grid markup over native `<table>`, but the a11y semantics
   * surface identically through axe-core.
   */
  import type { RunListResponse, RunResult, RunSummary } from '$lib/api/runs';
  import type { ValidationSuiteOut } from '$lib/api/suites';
  import { replaySuite, retireSuite } from '$lib/api/suites';
  import { suitesStore } from '$lib/stores/suites.svelte';
  import { toastStore } from '$lib/stores/toast.svelte';
  import { formatSignedDelta } from '$lib/utils/formatting';
  import { tooltip } from '$lib/actions/tooltip';
  import DestructiveConfirmModal from '$lib/components/shared/DestructiveConfirmModal.svelte';

  // Test-mock-compat shim: tests stub `toastStore` with severity helpers
  // (`success`, `error`) that aren't on the real `ToastStore` class. We
  // invoke the severity method when present (mocks) and fall back to
  // `info()` everywhere else. Mirrors the TopicProbeReportCard pattern.
  type ToastShim = typeof toastStore & {
    success?: (msg: string) => void;
    error?: (msg: string) => void;
  };
  const toast = toastStore as ToastShim;

  /**
   * Minimal shape of a completed replay run carrying per-prompt results.
   * Pulled out of a `RunRow` row's `prompt_results` payload. Tests
   * exercise this shape directly (Cycle 12 RED test 8); the production
   * call site uses the wider `RunResult` interface for the same purpose.
   */
  export interface SuiteReplayRow extends RunSummary {
    prompt_results?: Array<{
      prompt_index: number;
      overall_score: number;
      raw_prompt?: string;
    }>;
    aggregate?: RunResult['aggregate'];
  }

  interface Props {
    suite: ValidationSuiteOut;
    replays?: RunListResponse | null;
    // Accept either the test fixture shape (`SuiteReplayRow`) or the full
    // production `RunResult`. Both expose `prompt_results` + (optionally)
    // `aggregate.replay_warnings` so the component does not branch on
    // source.
    latestReplay?: SuiteReplayRow | RunResult | null;
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

  // Pull the warning list from `aggregate.replay_warnings`. Both the
  // SuiteReplayRow test fixture and the production RunResult expose the
  // `aggregate` block; falling back to `[]` keeps the strip mount-stable.
  const replayWarnings = $derived<string[]>(
    (latestReplay && 'aggregate' in latestReplay
      ? (latestReplay.aggregate as RunResult['aggregate'] | undefined)?.replay_warnings
      : undefined) ?? [],
  );

  // Task-type distribution → sorted list of `"label: n"` chips for the
  // baseline distribution row. Stable order keeps the header from
  // re-flowing between renders.
  const taskTypeChips = $derived(
    Object.entries(suite.baseline_scores.task_type_distribution ?? {})
      .sort((a, b) => (b[1] as number) - (a[1] as number))
      .map(([label, count]) => `${label}: ${count}`),
  );

  // Headline tooltip — pin every secondary field of `ValidationSuiteOut`
  // that doesn't get its own visible cell. Plan task 12.4 A3: every field
  // should be either rendered or surfaced through a tooltip; this keeps
  // the 20px-density chrome while giving operators a no-click path to the
  // full row.
  const headerTooltip = $derived.by(() => {
    const parts: string[] = [];
    if (suite.source_run_id) parts.push(`source_run: ${suite.source_run_id}`);
    if (suite.repo_full_name) parts.push(`repo: ${suite.repo_full_name}`);
    if (suite.project_id) parts.push(`project: ${suite.project_id}`);
    parts.push(`created: ${fmt(suite.created_at)}`);
    if (suite.retired_at) {
      parts.push(`retired: ${fmt(suite.retired_at)}`);
      if (suite.retired_reason) parts.push(`reason: ${suite.retired_reason}`);
    }
    return parts.join(' · ');
  });

  // ── Lifecycle actions (v0.4.22 T2 Cycle 11/12 follow-up) ─────────────
  //
  // Operators need to drive both lifecycle endpoints from the UI:
  //   - Replay: POST /api/suites/{id}/replay → dispatches a new
  //     ReplayRunGenerator against the frozen baseline; toast + detail
  //     refresh so the new replay row surfaces in the history table.
  //   - Retire: POST /api/suites/{id}/retire → flags the suite (no
  //     destructive delete; replay against retired returns 410 Gone).
  //     Gated by a DestructiveConfirmModal (RETIRE literal + reason
  //     textbox) so a misclick can't freeze a healthy suite.
  let replaying = $state(false);
  let retireModalOpen = $state(false);
  let retireReason = $state('');
  // `retired` is read from the suite prop; computing it as a $derived
  // lets the Retire button hide immediately after a successful retire
  // (the parent loadDetail() refresh re-flows `retired_at`).
  const isRetired = $derived(suite.retired_at != null);

  async function handleReplay(): Promise<void> {
    if (replaying || isRetired) return;
    replaying = true;
    try {
      const replay = await replaySuite(suite.id);
      toast.success?.(`Replay started (${replay.run_id.slice(0, 8)})`)
        ?? toast.info(`Replay started (${replay.run_id.slice(0, 8)})`);
      // Refresh suite detail so the new replay row surfaces in the
      // history table. Best-effort — failures are non-fatal; the toast
      // already confirmed the dispatch.
      try {
        await suitesStore.loadDetail(suite.id);
      } catch {
        /* swallow */
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Replay failed';
      toast.error?.(message) ?? toast.info(`Error: ${message}`);
    } finally {
      replaying = false;
    }
  }

  function openRetireModal(): void {
    if (isRetired) return;
    retireReason = '';
    retireModalOpen = true;
  }

  async function confirmRetire(): Promise<void> {
    const reason = retireReason.trim();
    // The DestructiveConfirmModal already gates on the RETIRE literal —
    // we additionally require a non-empty reason so `retired_reason`
    // carries operator context for audit. Empty reason throws so the
    // modal surfaces the error inline (its catch + errorMessage path).
    if (!reason) {
      throw new Error('Reason required to retire suite');
    }
    try {
      await retireSuite(suite.id, reason);
      toast.success?.('Suite retired') ?? toast.info('Suite retired');
      retireModalOpen = false;
      retireReason = '';
      // Refresh detail so the retired banner + meta-chip appear and the
      // Retire button hides. Best-effort.
      try {
        await suitesStore.loadDetail(suite.id);
      } catch {
        /* swallow */
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Retire failed';
      toast.error?.(message) ?? toast.info(`Error: ${message}`);
      // Re-throw so the modal surfaces the error inline.
      throw e instanceof Error ? e : new Error(message);
    }
  }
</script>

<section
  class="suite-detail-view"
  data-test="suite-detail-view"
  aria-label="Suite detail for {suite.label}"
  data-retired={suite.retired_at != null}
>
  <header class="detail-header" use:tooltip={headerTooltip}>
    <span class="detail-label">{suite.label}</span>
    <span class="detail-meta">
      {suite.prompts_snapshot.length}p · baseline {suite.baseline_scores.mean_overall.toFixed(2)}
      · tolerance ±{suite.tolerance_abs.toFixed(2)}
    </span>
  </header>

  <!-- ── Lifecycle actions row ─────────────────────────────────────
       Replay + Retire. Both are h-5 (20px) per spec § 6 density-pin.
       - Replay: Medium tier (`1px solid neon-blue`, 12% bg-hover tint).
       - Retire: Destructive (`1px solid neon-red`); hidden when already
         retired (idempotent — retiring a retired suite is a noop, but
         hiding the button keeps the affordance honest). Both buttons
         are disabled while a replay is in flight + when retired (so a
         retired suite is read-only end-to-end). -->
  <div class="detail-actions" data-test="suite-detail-actions">
    <button
      type="button"
      class="detail-btn detail-btn--replay"
      onclick={handleReplay}
      disabled={replaying || isRetired}
      aria-label="Replay suite"
      data-test="suite-replay-btn"
    >
      {replaying ? 'Replaying…' : 'Replay'}
    </button>
    {#if !isRetired}
      <button
        type="button"
        class="detail-btn detail-btn--retire"
        onclick={openRetireModal}
        disabled={replaying}
        aria-label="Retire suite"
        data-test="suite-retire-btn"
      >
        Retire
      </button>
    {/if}
  </div>

  <!-- ── Suite meta row — full ValidationSuiteOut field surfacing ─── -->
  <div class="detail-meta-row" data-test="suite-meta">
    {#if suite.source_run_id}
      <span class="meta-chip" data-field="source_run_id">
        <span class="meta-key">run</span><span class="meta-val">{suite.source_run_id}</span>
      </span>
    {/if}
    {#if suite.repo_full_name}
      <span class="meta-chip" data-field="repo_full_name">
        <span class="meta-key">repo</span><span class="meta-val">{suite.repo_full_name}</span>
      </span>
    {/if}
    {#if suite.project_id}
      <span class="meta-chip" data-field="project_id">
        <span class="meta-key">proj</span><span class="meta-val">{suite.project_id}</span>
      </span>
    {/if}
    <span class="meta-chip" data-field="created_at">
      <span class="meta-key">created</span><span class="meta-val">{fmt(suite.created_at)}</span>
    </span>
    <span class="meta-chip" data-field="baseline_distribution"
      use:tooltip={`p5 ${suite.baseline_scores.p5_overall.toFixed(2)} · p50 ${suite.baseline_scores.p50_overall.toFixed(2)} · p95 ${suite.baseline_scores.p95_overall.toFixed(2)}`}>
      <span class="meta-key">p50</span><span class="meta-val">{suite.baseline_scores.p50_overall.toFixed(2)}</span>
    </span>
    {#each taskTypeChips as chip (chip)}
      <span class="meta-chip" data-field="task_type">
        <span class="meta-val">{chip}</span>
      </span>
    {/each}
    {#if suite.retired_at}
      <span class="meta-chip meta-chip--retired" data-field="retired_at"
        use:tooltip={suite.retired_reason ? `Retired: ${suite.retired_reason}` : 'Retired'}>
        <span class="meta-key">retired</span><span class="meta-val">{fmt(suite.retired_at)}</span>
      </span>
    {/if}
  </div>

  <!-- ── Replay warnings strip (latestReplay.aggregate.replay_warnings) ─ -->
  {#if replayWarnings.length > 0}
    <div
      class="warnings-strip"
      data-test="replay-warnings"
      role="status"
      aria-live="polite"
      aria-label={`${replayWarnings.length} replay warning${replayWarnings.length === 1 ? '' : 's'}`}
    >
      {#each replayWarnings as code (code)}
        <span class="warning-chip" data-warning-code={code}>{code}</span>
      {/each}
    </div>
  {/if}

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

<!-- ── Retire confirm modal ─────────────────────────────────────────
     Gated by the canonical DestructiveConfirmModal — operator types
     RETIRE to commit + a non-empty reason captured into `retired_reason`
     for audit. The reason textbox sits inside the snippet body so it
     can bind into the same `retireReason` $state the handler reads. -->
{#snippet retireBody()}
  <div class="retire-body">
    <p class="retire-prose">
      Retire <span class="retire-label">{suite.label}</span>? Replay
      against retired suites returns 410 Gone. The action is reversible
      only via direct DB edit.
    </p>
    <label class="retire-reason-label" for="suite-retire-reason">
      Reason
    </label>
    <input
      id="suite-retire-reason"
      type="text"
      class="retire-reason-input"
      placeholder="e.g. replaced by suite-v2"
      bind:value={retireReason}
      data-test="suite-retire-reason"
    />
  </div>
{/snippet}

<DestructiveConfirmModal
  open={retireModalOpen}
  title="RETIRE SUITE?"
  body={retireBody}
  sideEffectHint="The suite stays in the database but cannot be replayed."
  confirmLiteral="RETIRE"
  confirmLabel="Retire"
  onConfirm={confirmRetire}
  onCancel={() => { retireModalOpen = false; }}
/>

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

  /* ── Suite meta row ─────────────────────────────────────────────
     Chip-style surfacing of every ValidationSuiteOut field that
     doesn't get its own visible row. h-5 chips with 4px gap, mono
     numerics, single-line. Spec § 6 density-pin canon (p-1.5 for
     parent, 1px subtle border on individual chips). */
  .detail-meta-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    padding: 0 4px;
  }

  .meta-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 18px;
    padding: 0 4px;
    border: 1px solid var(--color-border-subtle);
    background: var(--color-bg-secondary, transparent);
    font-size: 9px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    max-width: 280px;
    text-overflow: ellipsis;
  }

  .meta-chip .meta-key {
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .meta-chip .meta-val {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .meta-chip--retired {
    color: var(--color-neon-red, #ff3366);
    border-color: var(--color-neon-red, #ff3366);
  }

  /* When the suite is retired the entire detail surface fades — keeps
     operators from accidentally acting on a frozen-by-policy row. */
  .suite-detail-view[data-retired='true'] {
    opacity: 0.85;
  }

  /* ── Replay warnings strip ──────────────────────────────────────
     Surfaces `aggregate.replay_warnings` from the polled latest-replay
     RunResult. Neon-yellow chromatic encoding pairs with the
     `RegressionBadge` neon-yellow tier so the workbench paints a
     consistent "warn but not fired" palette. */
  .warnings-strip {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px;
    padding: 4px;
    border: 1px solid var(--color-neon-yellow, #ffd166);
    background: color-mix(in srgb, var(--color-neon-yellow, #ffd166) 8%, transparent);
  }

  .warning-chip {
    display: inline-flex;
    align-items: center;
    height: 18px;
    padding: 0 6px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-neon-yellow, #ffd166);
    border: 1px solid var(--color-neon-yellow, #ffd166);
    background: transparent;
    text-transform: uppercase;
    letter-spacing: 0.04em;
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

  /* ── Lifecycle actions row ──────────────────────────────────────
     h-5 (20px) buttons per spec § 6 density-pin canon. Replay is the
     Medium-tier action (1px solid neon-blue, no translateY lift —
     Recipe E lift is reserved for Hero buttons). Retire is destructive
     (1px solid neon-red); confirm gate lives in DestructiveConfirmModal. */
  .detail-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 4px;
  }

  .detail-btn {
    height: 20px;
    padding: 0 8px;
    line-height: 18px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    box-sizing: border-box;
    /* Atomic-event multi-property hover transition — spec § 6 axiom 5. */
    transition:
      background 200ms cubic-bezier(0.16, 1, 0.3, 1),
      border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
      color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .detail-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* Medium tier — Recipe A (border + bg tint, NO translateY lift). */
  .detail-btn--replay {
    color: var(--color-neon-blue, #4d8eff);
  }

  .detail-btn--replay:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-neon-blue, #4d8eff) 12%, transparent);
    color: var(--color-neon-blue, #4d8eff);
    border-color: var(--color-neon-blue, #4d8eff);
  }

  .detail-btn--replay:active:not(:disabled) {
    /* Contraction — inset contour with muted accent. */
    box-shadow: inset 0 0 0 1px rgba(77, 142, 255, 0.4);
  }

  /* Destructive — Recipe A with neon-red chromatic encoding. */
  .detail-btn--retire {
    color: var(--color-neon-red, #ff3366);
    border-color: color-mix(in srgb, var(--color-neon-red, #ff3366) 25%, transparent);
  }

  .detail-btn--retire:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-neon-red, #ff3366) 12%, transparent);
    border-color: var(--color-neon-red, #ff3366);
  }

  .detail-btn--retire:active:not(:disabled) {
    border-color: color-mix(in srgb, var(--color-neon-red, #ff3366) 40%, transparent);
  }

  /* ── Retire modal body ─────────────────────────────────────────── */
  .retire-body {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-family: var(--font-sans);
  }

  .retire-prose {
    margin: 0;
    font-size: 11px;
    color: var(--color-text-primary);
    line-height: 1.4;
  }

  .retire-label {
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
    font-weight: 600;
  }

  .retire-reason-label {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-text-secondary);
  }

  .retire-reason-input {
    width: 100%;
    height: 20px;
    padding: 0 4px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    border-radius: 0;
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 11px;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .retire-reason-input:focus {
    outline: none;
    border-color: color-mix(in srgb, var(--color-neon-cyan) 30%, transparent);
  }
</style>
