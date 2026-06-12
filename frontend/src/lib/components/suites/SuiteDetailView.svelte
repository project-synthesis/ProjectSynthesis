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
  import { editorStore } from '$lib/stores/editor.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { getOptimization } from '$lib/api/client';
  import { runsPanelStore } from '$lib/stores/runs-panel.svelte';

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
      // Replay rows carry `raw_prompt_idx` (the generator's slot index);
      // `prompt_index` is accepted as a legacy/test alias. At least one
      // must be present for the per-prompt join to pair the row.
      raw_prompt_idx?: number;
      prompt_index?: number;
      overall_score?: number | null;
      raw_prompt?: string;
      [key: string]: unknown;
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

  // Pair baseline + latest per-prompt scores. Keyed join on the row's
  // slot index: replay rows carry `raw_prompt_idx` (the only key the
  // backend emits — see replay_run_generator `_project_pending_to_result`);
  // `prompt_index` is tolerated as a legacy/test alias. Empty when no
  // replay carries prompt_results.
  const perPromptRows = $derived.by(() => {
    const baseline = suite.baseline_scores.per_prompt ?? [];
    const latest = latestReplay?.prompt_results ?? [];
    const snapshot = suite.prompts_snapshot ?? [];
    return baseline.map((b) => {
      const l =
        latest.find(
          (x) => (x.raw_prompt_idx ?? x.prompt_index) === b.raw_prompt_idx,
        ) ?? null;
      const lr = l as Record<string, unknown> | null;
      const snap = snapshot[b.raw_prompt_idx] ?? null;
      const baselineScore = b.overall;
      const latestScore = l?.overall_score ?? null;
      const delta = latestScore != null ? latestScore - baselineScore : null;
      return {
        idx: b.raw_prompt_idx,
        baseline: baselineScore,
        latest: latestScore,
        delta,
        snap,
        latestRow: lr,
        dims: (lr?.dimensions ?? null) as Record<string, number | null> | null,
        status: (lr?.status ?? null) as string | null,
        error: (lr?.error ?? null) as string | null,
      };
    });
  });

  // ── v0.4.37 §4.1 — per-prompt expansion + five-state DIFF machine ──
  let expandedIdx = $state<number | null>(null);

  // Collapse the open row when the SELECTED SUITE changes — suite B must
  // never render suite A's expansion state (row indexes are per-suite).
  // Keyed on `suite.id`, not the object, so in-place detail refreshes
  // (replay/retire → loadDetail re-flows a new object with the same id)
  // do NOT collapse the operator's open row. Per the project's Svelte-5
  // invariant, `suite.id` is read BEFORE the gate so the effect always
  // subscribes to prop swaps.
  let lastSuiteId: string | undefined;
  $effect(() => {
    const id = suite.id;
    if (lastSuiteId !== undefined && id !== lastSuiteId) {
      expandedIdx = null;
    }
    lastSuiteId = id;
  });

  type DiffState =
    | { kind: 'hidden' }
    | { kind: 'output-vs-output'; before: string; after: string }
    | { kind: 'raw-vs-output'; before: string; after: string }
    | { kind: 'disabled'; tooltip: string };

  type PerPromptRow = (typeof perPromptRows)[number];

  // Evaluated per prompt row, in spec §4.1 order.
  function diffStateFor(row: PerPromptRow): DiffState {
    // State 1 — no replay selected/available for the suite: hidden
    // (the diff table already renders its "No replays yet" empty state).
    if (!latestReplay || !row.latestRow) return { kind: 'hidden' };
    // State 5 — pre-v0.4.37 replay row: the key is absent entirely.
    if (!('optimized_prompt' in row.latestRow)) {
      return { kind: 'disabled', tooltip: 'output not captured (pre-v0.4.37 replay)' };
    }
    const optimized = row.latestRow['optimized_prompt'] as string | null;
    // State 4 — post-v0.4.37 score-less row: key present, value null.
    if (optimized == null) {
      return { kind: 'disabled', tooltip: 'no output for this prompt (rate-limited or failed)' };
    }
    const baselineOutput = row.snap?.baseline_optimized_prompt ?? null;
    // State 2 — output-vs-output (the regression view).
    if (baselineOutput != null) {
      return { kind: 'output-vs-output', before: baselineOutput, after: optimized };
    }
    // State 3 — raw → latest output (what the optimizer did this run).
    return { kind: 'raw-vs-output', before: row.snap?.raw_prompt ?? '', after: optimized };
  }

  function openRowDiff(row: PerPromptRow, ds: DiffState): void {
    if (ds.kind !== 'output-vs-output' && ds.kind !== 'raw-vs-output') return;
    editorStore.openInlineDiff({
      key: `${suite.id}:${row.idx}`,
      title: `${suite.label} #${row.idx}`,
      before: ds.before,
      after: ds.after,
      beforeLabel: ds.kind === 'output-vs-output' ? 'BASELINE' : 'RAW',
      afterLabel: 'LATEST',
    });
  }

  // ── v0.4.37 §4.1 — provenance liveness (tombstone + history link) ──
  const aliveIds = $derived(suitesStore.aliveOriginalIds);

  function isDead(id: string | null | undefined): boolean {
    return !!id && aliveIds != null && !aliveIds.has(id);
  }
  function isAlive(id: string | null | undefined): boolean {
    return !!id && aliveIds != null && aliveIds.has(id);
  }

  async function openInHistory(optId: string): Promise<void> {
    const traceId = suitesStore.originalTraceIds[optId];
    if (!traceId) return;
    try {
      const opt = await getOptimization(traceId);
      forgeStore.loadFromRecord(opt);
      editorStore.openResult(opt.id);
    } catch {
      toast.error?.('Failed to load optimization')
        ?? toast.info('Failed to load optimization');
    }
  }

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
      class="btn-outline-secondary detail-btn--replay"
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
        class="btn-outline-danger"
        onclick={openRetireModal}
        disabled={replaying}
        aria-label="Retire suite"
        data-test="suite-retire-btn"
      >
        Retire
      </button>
    {/if}
  </div>

  <!-- ── Suite meta row — full ValidationSuiteOut field surfacing ───
       Long opaque IDs (run, repo, proj) are tooltipped rather than
       visibly rendered — operators can't read a truncated 36-char
       UUID anyway and the borderless key:value rows keep the panel
       quiet. Short, scannable fields (created, p50, task types) stay
       visible. Retired-state still surfaces visibly because the dim
       + neon-red treatment is load-bearing. -->
  <div class="detail-meta-row" data-test="suite-meta">
    {#if suite.source_run_id}
      <button
        type="button"
        class="meta-pair meta-pair--link"
        data-field="source_run_id"
        data-test="suite-run-link"
        use:tooltip={`open run ${suite.source_run_id} in the Runs panel`}
        onclick={() => runsPanelStore.requestSelect(suite.source_run_id!)}
      >
        <span class="meta-key">run</span><span class="meta-val">{suite.source_run_id.slice(0, 8)}…</span>
      </button>
    {/if}
    {#if suite.repo_full_name}
      <span class="meta-pair" data-field="repo_full_name"
        use:tooltip={`repo ${suite.repo_full_name}`}>
        <span class="meta-key">repo</span><span class="meta-val">{suite.repo_full_name}</span>
      </span>
    {/if}
    {#if suite.project_id}
      <span class="meta-pair" data-field="project_id"
        use:tooltip={`proj ${suite.project_id}`}>
        <span class="meta-key">proj</span><span class="meta-val">{suite.project_id.slice(0, 8)}…</span>
      </span>
    {/if}
    <span class="meta-pair" data-field="created_at">
      <span class="meta-key">created</span><span class="meta-val">{fmt(suite.created_at)}</span>
    </span>
    <span class="meta-pair" data-field="baseline_distribution"
      use:tooltip={`p5 ${suite.baseline_scores.p5_overall.toFixed(2)} · p50 ${suite.baseline_scores.p50_overall.toFixed(2)} · p95 ${suite.baseline_scores.p95_overall.toFixed(2)}`}>
      <span class="meta-key">p50</span><span class="meta-val">{suite.baseline_scores.p50_overall.toFixed(2)}</span>
    </span>
    {#each taskTypeChips as chip (chip)}
      <span class="meta-pair meta-pair--tasktype" data-field="task_type">
        <span class="meta-val">{chip}</span>
      </span>
    {/each}
    {#if suite.retired_at}
      <span class="meta-pair meta-pair--retired" data-field="retired_at"
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

  <!-- ── Replay history ─────────────────────────────────────────────
       Shrunk from 5 columns (replay/status/started/completed/prompts)
       to 4 (replay/status/started/prompts) — `completed` was redundant
       with `started` at sidebar widths and pushed the grid past the
       container. Full timestamp pair is still available via tooltip. -->
  <div class="replay-history" data-test="replay-history" role="table" aria-label="Replay history">
    <div class="replay-row replay-row--head" role="row">
      <span role="columnheader" class="col-id">replay</span>
      <span role="columnheader" class="col-status">status</span>
      <span role="columnheader" class="col-started">started</span>
      <span role="columnheader" class="col-prompts">prompts</span>
    </div>
    {#if replayItems.length === 0}
      <p class="empty-note">No replays yet. Trigger one from the suite menu.</p>
    {:else}
      {#each replayItems as r (r.id)}
        <div
          class="replay-row"
          data-test="replay-row"
          role="row"
          use:tooltip={
            r.completed_at
              ? `started ${fmt(r.started_at)} · finished ${fmt(r.completed_at)}`
              : `started ${fmt(r.started_at)} · still running`
          }
        >
          <span role="cell" class="col-id">{r.id}</span>
          <span role="cell" class="col-status" data-status={r.status}>{r.status}</span>
          <span role="cell" class="col-started">{fmt(r.started_at)}</span>
          <span role="cell" class="col-prompts">{r.prompts_generated}</span>
        </div>
      {/each}
    {/if}
  </div>

  <!-- ── Per-prompt baseline vs latest diff (v0.4.37: expandable rows
       + five-state DIFF affordance + tombstone + history link) ─────── -->
  <div class="per-prompt" data-test="per-prompt" role="table" aria-label="Per-prompt baseline vs latest">
    <div class="prompt-row prompt-row--head" role="row">
      <span role="columnheader" class="col-chevron" aria-label="Expand"></span>
      <span role="columnheader" class="col-idx">#</span>
      <span role="columnheader" class="col-baseline">baseline</span>
      <span role="columnheader" class="col-latest">latest</span>
      <span role="columnheader" class="col-delta">Δ</span>
      <span role="columnheader" class="col-diff">diff</span>
    </div>
    {#each perPromptRows as row (row.idx)}
      {@const ds = diffStateFor(row)}
      <div
        class="prompt-row"
        data-test="per-prompt-row"
        data-prompt-idx={row.idx}
        role="row"
      >
        <button
          type="button"
          class="row-chevron col-chevron"
          aria-expanded={expandedIdx === row.idx}
          aria-label="Toggle prompt {row.idx} detail"
          data-test="prompt-expand-btn"
          onclick={() => { expandedIdx = expandedIdx === row.idx ? null : row.idx; }}
        >{expandedIdx === row.idx ? '▾' : '▸'}</button>
        <span role="cell" class="col-idx">{row.idx}</span>
        <span role="cell" class="col-baseline">{row.baseline.toFixed(1)}</span>
        <span role="cell" class="col-latest">{row.latest != null ? row.latest.toFixed(1) : '—'}</span>
        <span role="cell" class="col-delta">{formatSignedDelta(row.delta)}</span>
        <span role="cell" class="col-diff">
          {#if ds.kind === 'output-vs-output' || ds.kind === 'raw-vs-output'}
            <button
              type="button"
              class="diff-btn"
              data-test="prompt-diff-btn"
              data-diff-state={ds.kind}
              use:tooltip={ds.kind === 'output-vs-output'
                ? 'diff baseline output vs latest output'
                : 'diff raw prompt vs latest output'}
              onclick={() => openRowDiff(row, ds)}
            >DIFF</button>
          {:else if ds.kind === 'disabled'}
            <button
              type="button"
              class="diff-btn"
              data-test="prompt-diff-btn"
              data-diff-state="disabled"
              data-diff-tooltip={ds.tooltip}
              use:tooltip={ds.tooltip}
              disabled
            >DIFF</button>
          {/if}
        </span>
      </div>
      {#if expandedIdx === row.idx}
        <div class="prompt-expanded" data-test="prompt-expanded" data-prompt-idx={row.idx}>
          {#if row.snap?.intent_label}
            <div class="expanded-meta">{row.snap.intent_label}</div>
          {/if}
          <pre class="expanded-prompt">{row.snap?.raw_prompt ?? ''}</pre>
          {#if row.dims}
            <div class="expanded-dims" data-test="prompt-dims">
              {#each Object.entries(row.dims) as [dim, val] (dim)}
                <span class="dim-chip">{dim} {val != null ? val.toFixed(1) : '—'}</span>
              {/each}
            </div>
          {/if}
          {#if row.status}
            <div class="expanded-meta">status: {row.status}</div>
          {/if}
          {#if row.error}
            <div class="expanded-error">{row.error}</div>
          {/if}
          {#if isDead(row.snap?.original_optimization_id)}
            <div class="tombstone" data-test="prompt-tombstone">original optimization deleted — content preserved in this snapshot</div>
          {:else if isAlive(row.snap?.original_optimization_id) && row.snap?.original_optimization_id}
            <button
              type="button"
              class="open-history-btn"
              data-test="open-in-history"
              onclick={() => openInHistory(row.snap!.original_optimization_id!)}
            >OPEN IN HISTORY</button>
          {/if}
        </div>
      {/if}
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
    /* Sidebar canon cap is 6px per layout-and-accessibility.md:80
       (`p-1.5` is the ceiling for sidebar/panel content; `space-y-1.5`
       is the ceiling for section gaps). Was 8px on both. */
    gap: 6px;
    padding: 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-primary);
  }

  .detail-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    /* Was a fixed 20px row — the baseline · tolerance line is too long
       for the narrow sidebar at one row, so let the header wrap to two
       lines when needed instead of overflowing horizontally. */
    min-height: 20px;
    padding: 0 4px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-wrap: wrap;
  }

  .detail-label {
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 0;
    flex: 1 1 auto;
  }

  .detail-meta {
    color: var(--color-text-dim);
    /* Was `white-space: nowrap` — that prevented the meta line from
       wrapping at narrow widths and pushed the parent wider. Now wraps
       naturally below the label when the row can't fit both. */
    flex-shrink: 0;
  }

  /* ── Suite meta row ─────────────────────────────────────────────
     Borderless key:value pairs replacing the prior heavy-bordered
     `.meta-chip` cards. Each pair fits on one line as `KEY value`,
     wrapping naturally at narrow sidebar widths. The bordered
     rectangle-per-field treatment turned the meta region into 5+
     visual "data cards" stacked vertically — too much chrome for
     supplemental fields when the primary baseline / tolerance line
     already sits in `.detail-header`. Spec § 6 IDE-density principle:
     data-ink ratio over chrome. */
  .detail-meta-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 4px 8px;        /* row-gap 4px · column-gap 8px */
    padding: 0 4px;
    font-size: 9px;
    color: var(--color-text-primary);
  }

  .meta-pair {
    display: inline-flex;
    align-items: baseline;
    gap: 4px;
    white-space: nowrap;
    max-width: 100%;
    overflow: hidden;
  }

  .meta-pair .meta-key {
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .meta-pair .meta-val {
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Task-type chips remain visibly chipped (they encode discrete
     categorical data — code, summarization, qa, etc. — and reading
     them as labels is the point). Light pill treatment, no border. */
  .meta-pair--tasktype .meta-val {
    padding: 0 6px;
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
    color: var(--color-text-secondary);
  }

  .meta-pair--retired {
    color: var(--color-neon-red, #ff3366);
  }
  .meta-pair--retired .meta-key {
    color: var(--color-neon-red, #ff3366);
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

  /* Tables now use flexible (fr-based) grid columns so they fit the
     ~270px Navigator sidebar without overflow. Each column carries
     `min-width: 0` via the cell rule below so long IDs/timestamps
     truncate with ellipsis instead of pushing the grid wider. */
  .replay-row {
    grid-template-columns: 1.4fr 0.7fr 0.9fr 0.5fr;
    gap: 4px;
  }

  .prompt-row {
    grid-template-columns: 14px 24px 1fr 1fr 1fr 36px;
    gap: 4px;
  }

  /* Every cell truncates rather than overflowing the grid. */
  .replay-row > [role='cell'],
  .replay-row > [role='columnheader'],
  .prompt-row > [role='cell'],
  .prompt-row > [role='columnheader'] {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
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
    /* Inherits `padding: 4px 6px` + `color: text-dim` from the global
       `.empty-note` rule in app.css. Only the italicized prose
       treatment is local — failed-load empty rows aren't italicized
       (they use `.panel-error` styling), so this italic only fires
       inside the per-prompt / replay-history tables when they have
       no rows yet. */
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

  /* Buttons now use canonical `.btn-outline-secondary` (Replay baseline)
     and `.btn-outline-danger` (Retire — full destructive recipe from
     app.css). The local `.detail-btn` baseline (font-mono + uppercase +
     letter-spacing + missing border-radius/weight) was a non-canonical
     reinvention of `.btn-outline-secondary`; removed in favor of the
     global. Only the `.detail-btn--replay` modifier survives — it
     overrides the gray secondary baseline to neon-blue (Replay = the
     "analysis" semantic; there's no global blue-tier button variant).
     Retire uses `.btn-outline-danger` directly with no local modifier.

     Recipe A hover (border + bg tint, no translateY lift) is baked into
     `.btn-outline-secondary` already; the blue-tint override below paints
     the same recipe in neon-blue. */
  .detail-btn--replay {
    color: var(--color-neon-blue, #4d8eff);
    border-color: color-mix(in srgb, var(--color-neon-blue, #4d8eff) 30%, transparent);
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
    /* Canon: 0.1em per SKILL.md typography "Section heading" rule.
       Was 0.08em — minor drift, corrected. */
    letter-spacing: 0.1em;
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

  /* ── v0.4.37 — expandable per-prompt rows + DIFF affordance ────────
     All zero-effects: 1px contours, no glow/shadow, 20px collapsed rows
     preserved; the expanded body is a bordered block below the row. */
  .row-chevron {
    height: 20px;
    padding: 0;
    background: transparent;
    border: none;
    color: var(--color-text-dim);
    font-size: 9px;
    cursor: pointer;
    transition: color 200ms ease;
  }
  .row-chevron:hover { color: var(--color-text-primary); }

  /* Shared chrome for the two row affordances (DIFF / OPEN IN HISTORY):
     identical 1px contour + mono type + cyan hover so they can't drift. */
  .diff-btn,
  .open-history-btn {
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    letter-spacing: 0.06em;
    cursor: pointer;
    transition: border-color 200ms ease, color 200ms ease;
  }
  .diff-btn:hover:not(:disabled),
  .open-history-btn:hover {
    border-color: var(--color-neon-cyan, #00e5ff);
    color: var(--color-neon-cyan, #00e5ff);
  }

  .diff-btn {
    height: 16px;
    padding: 0 4px;
    font-size: 8px;
  }
  .diff-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .prompt-expanded {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 6px 6px 18px;
    border-bottom: 1px solid var(--color-border-subtle);
    background: color-mix(in srgb, var(--color-bg-hover) 25%, transparent);
  }

  .expanded-prompt {
    margin: 0;
    max-height: 160px;
    overflow: auto;
    font-family: var(--font-mono);
    font-size: 10px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--color-text-primary);
    border: 1px solid var(--color-border-subtle);
    padding: 4px;
  }

  .expanded-meta {
    font-size: 9px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .expanded-dims {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  /* Dimension-chip recipe — keep visually in lockstep with `.rp-dim-chip`
     in layout/RunDetailInline.svelte (the replay-detail surface renders
     the same per-prompt dimension data with the same chip treatment). */
  .dim-chip {
    display: inline-flex;
    align-items: center;
    height: 16px;
    padding: 0 4px;
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
  }

  .expanded-error {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-neon-red, #ff3366);
  }

  .tombstone {
    font-size: 9px;
    font-style: italic;
    color: var(--color-text-dim);
  }

  /* Chrome + hover shared with .diff-btn — see the grouped rules above. */
  .open-history-btn {
    align-self: flex-start;
    height: 18px;
    padding: 0 6px;
    font-size: 9px;
    text-transform: uppercase;
  }

  /* RUN meta-link — text-link treatment, no chrome (the meta row is
     deliberately quiet); neon-cyan signals interactivity. */
  .meta-pair--link {
    background: transparent;
    border: none;
    padding: 0;
    cursor: pointer;
    font-family: inherit;
    font-size: inherit;
  }
  .meta-pair--link .meta-val {
    color: var(--color-neon-cyan, #00e5ff);
  }
  .meta-pair--link:hover .meta-val {
    text-decoration: underline;
  }
</style>
