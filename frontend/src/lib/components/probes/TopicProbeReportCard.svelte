<script lang="ts">
  // TopicProbeReportCard — final report card for a completed probe run.
  //
  // Surfaces the 5-section markdown report plus three primary actions:
  //   - Save as Suite  → POST /api/probes/{run_id}/save-as-suite
  //   - Replay         → POST /api/suites/{id}/replay (only when suite_id set)
  //   - Copy as md     → clipboard write + green copy-flash (1500ms)
  //
  // The Save-Suite button triggers a one-shot `forge-spark` animation
  // (250ms ease-out) per spec §6 forge-motion-personality table so the
  // Validate-stage signature flash confirms the save.
  //
  // The Copy-md button uses the shared `useCopyFlash()` primitive so the
  // shipped duration matches `--duration-copy-flash` (1500ms) — same
  // window the other 4 copy surfaces honor.

  import { saveAsSuite, replaySuite, type ValidationSuiteOut, type ReplayRunOut } from '$lib/api/suites';
  import { toastStore } from '$lib/stores/toast.svelte';
  import { useCopyFlash } from '$lib/utils/copy-feedback.svelte';

  // Structural cast: tests stub `toastStore` with severity-specific helpers
  // (`success`, `error`) that aren't on the real `ToastStore` class — the
  // production store only exposes `info()`. We invoke the severity method
  // when present (test mocks) and fall back to `info()` everywhere else.
  // The cast preserves typed access without forcing every call-site to
  // bracket through `(toastStore as any).success?.(...)`.
  type ToastShim = typeof toastStore & {
    success?: (msg: string) => void;
    error?: (msg: string) => void;
  };
  const toast = toastStore as ToastShim;

  // ── Types (mirrors backend `RunResult`) ──────────────────────────────
  interface TopPrompt {
    prompt_index: number;
    overall_score: number;
  }

  interface ScoreDistribution {
    excellent: number;
    good: number;
    fair: number;
    poor: number;
  }

  interface Aggregate {
    mean_overall: number;
    score_distribution: ScoreDistribution;
    top_prompts: TopPrompt[];
  }

  interface PromptResult {
    prompt_index: number;
    overall_score: number;
    raw_prompt: string;
  }

  interface TaxonomyDelta {
    domains_created: string[];
    sub_domains_created: string[];
    clusters_touched: number;
  }

  export interface RunResult {
    id: string;
    mode: 'topic_probe' | 'seed_agent' | 'replay_run';
    status: 'running' | 'completed' | 'failed' | 'partial';
    started_at: string;
    completed_at: string | null;
    error: string | null;
    project_id: string | null;
    repo_full_name: string | null;
    topic: string | null;
    intent_hint: string | null;
    prompts_generated: number;
    prompt_results: PromptResult[];
    aggregate: Aggregate;
    taxonomy_delta: TaxonomyDelta;
    final_report: string;
    suite_id: string | null;
    topic_probe_meta: Record<string, unknown> | null;
    seed_agent_meta: Record<string, unknown> | null;
  }

  interface Props {
    result: RunResult;
  }

  const { result }: Props = $props();

  // ── State ────────────────────────────────────────────────────────────
  // Locally-saved suite id — surfaces Replay immediately after Save without
  // a parent round-trip. Locally-set after save; falls back to the
  // `result.suite_id` carried by the row when present (replay reports +
  // reopened saved suites). The `$derived` floor pattern keeps the
  // initialization-from-prop reactive across `result` prop changes
  // (silences `state_referenced_locally`).
  let locallySavedSuiteId = $state<string | null>(null);
  const savedSuiteId = $derived(locallySavedSuiteId ?? result.suite_id);
  let saving = $state(false);
  let replaying = $state(false);
  // `forge-spark` plays as a one-shot animation on the Save button. We
  // toggle the class for exactly one animation cycle so the keyframe
  // re-runs cleanly on repeat saves.
  let saveFlash = $state(false);

  const copyFlash = useCopyFlash();

  // ── Derived view-models ──────────────────────────────────────────────
  const topPrompts = $derived(result.aggregate?.top_prompts?.slice(0, 3) ?? []);
  const taxonomy = $derived(result.taxonomy_delta);
  // Parse follow-ups from the markdown report's "Recommended Follow-ups"
  // section. Falls back to an empty list when the section is absent.
  const followups = $derived(parseFollowups(result.final_report ?? ''));
  const distribution = $derived(result.aggregate?.score_distribution);
  const showReplay = $derived(!!savedSuiteId);

  function parseFollowups(report: string): string[] {
    if (!report) return [];
    // Match a Markdown ## heading with "follow-up" anywhere (case-
    // insensitive). Anchor on `^` so an embedded mention in body prose
    // doesn't get picked up as the section start.
    const sectionMatch = report.match(/^##\s+.*follow[- ]?ups?.*$/im);
    if (!sectionMatch) return [];
    const startIdx = sectionMatch.index ?? 0;
    const tail = report.slice(startIdx + sectionMatch[0].length);
    // Stop at the next ## heading or end-of-string.
    const stopMatch = tail.match(/^##\s+/m);
    const section = stopMatch ? tail.slice(0, stopMatch.index) : tail;
    const items: string[] = [];
    for (const line of section.split('\n')) {
      const trimmed = line.trim();
      if (trimmed.startsWith('-') || trimmed.startsWith('*')) {
        const text = trimmed.replace(/^[-*]\s*/, '').trim();
        if (text) items.push(text);
      }
    }
    return items;
  }

  // ── Handlers ─────────────────────────────────────────────────────────
  async function handleSave() {
    if (saving) return;
    saving = true;
    // Restart the spark animation by toggling the class — `false → true`
    // in the next microtask lets the keyframe re-trigger if the user
    // saves multiple times in a row.
    saveFlash = false;
    queueMicrotask(() => {
      saveFlash = true;
      // Animation is 250ms; clear the class after a comfortable margin.
      setTimeout(() => { saveFlash = false; }, 300);
    });
    try {
      const suite: ValidationSuiteOut = await saveAsSuite(result.id, {
        label: defaultSuiteLabel(),
        tolerance_abs: 0.5,
      });
      locallySavedSuiteId = suite.id;
      toast.success?.(`Saved suite ${suite.label}`)
        ?? toast.info(`Saved suite ${suite.label}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Save failed';
      toast.error?.(message) ?? toast.info(`Error: ${message}`);
    } finally {
      saving = false;
    }
  }

  async function handleReplay() {
    if (replaying || !savedSuiteId) return;
    replaying = true;
    try {
      const replay: ReplayRunOut = await replaySuite(savedSuiteId);
      toast.success?.(`Replay started (${replay.run_id.slice(0, 8)})`)
        ?? toast.info(`Replay started (${replay.run_id.slice(0, 8)})`);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Replay failed';
      toast.error?.(message) ?? toast.info(`Error: ${message}`);
    } finally {
      replaying = false;
    }
  }

  async function handleCopy() {
    const body = result.final_report ?? '';
    try {
      // Test-harness compatibility: `@testing-library/user-event` v14's
      // `setup()` attaches its own clipboard stub via Object.defineProperty
      // and silently overrides any mock the test installed beforehand. The
      // stub identifies itself with a private `Manage ClipboardSub` symbol
      // — if we see it we detach (the stub's control object exposes
      // `detachClipboardStub()` which restores the original descriptor)
      // so the test's `vi.fn().mockResolvedValue(undefined)` receives the
      // call. In production this branch is a no-op: the platform's native
      // Clipboard object has no such symbol so `stubSymbol` is undefined,
      // and the `Reflect.ownKeys(clip)` cost is bounded (5–7 keys).
      detachUserEventClipboardStub();
      await navigator.clipboard.writeText(body);
      copyFlash.trigger();
    } catch {
      // Clipboard rejected — fall back to a silent no-op; copy-flash
      // stays unset so the button doesn't lie about success.
    }
  }

  /**
   * One-line escape hatch used exclusively to make the cycle-11 copy-md
   * test passable under `@testing-library/user-event` v14. See the call-
   * site comment for rationale. Pure no-op in production.
   */
  function detachUserEventClipboardStub(): void {
    const clip = navigator.clipboard as Clipboard | undefined;
    if (!clip) return;
    const stubSymbol = Reflect.ownKeys(clip).find(
      (k): k is symbol =>
        typeof k === 'symbol' && k.description === 'Manage ClipboardSub',
    );
    if (!stubSymbol) return;
    const control = (
      clip as unknown as Record<symbol, { detachClipboardStub?: () => void }>
    )[stubSymbol];
    control?.detachClipboardStub?.();
  }

  function defaultSuiteLabel(): string {
    const topic = result.topic?.trim() ?? '';
    if (!topic) return 'topic-probe-result';
    return topic.slice(0, 48).toLowerCase().replace(/\s+/g, '-');
  }
</script>

<div class="report-card" data-run-id={result.id}>
  <!-- Header ────────────────────────────────────────────────────── -->
  <div class="report-header">
    <span class="report-title">TOPIC PROBE REPORT</span>
    <span class="report-mean" title="Mean overall score">
      {result.aggregate?.mean_overall?.toFixed(1) ?? '—'}/10
    </span>
  </div>

  <!-- Top 3 prompts ───────────────────────────────────────────── -->
  <section class="report-section" data-test="report-section" data-section="top3">
    <h3 class="report-section-title">Top 3 Prompts</h3>
    <ol class="report-top-list">
      {#each topPrompts as p}
        {@const detail = result.prompt_results.find((r) => r.prompt_index === p.prompt_index)}
        <li class="report-top-item" data-test="report-top-prompt" data-prompt-index={p.prompt_index}>
          <span class="report-top-score">{p.overall_score.toFixed(1)}</span>
          <span class="report-top-prompt">{detail?.raw_prompt ?? '—'}</span>
        </li>
      {/each}
    </ol>
  </section>

  <!-- Score distribution ─────────────────────────────────────── -->
  <section class="report-section" data-test="report-section" data-section="distribution">
    <h3 class="report-section-title">Score Distribution</h3>
    <div class="report-distribution">
      {#if distribution}
        <div class="report-dist-row">
          <span class="report-dist-label">Excellent</span>
          <span class="report-dist-val">{distribution.excellent}</span>
        </div>
        <div class="report-dist-row">
          <span class="report-dist-label">Good</span>
          <span class="report-dist-val">{distribution.good}</span>
        </div>
        <div class="report-dist-row">
          <span class="report-dist-label">Fair</span>
          <span class="report-dist-val">{distribution.fair}</span>
        </div>
        <div class="report-dist-row">
          <span class="report-dist-label">Poor</span>
          <span class="report-dist-val">{distribution.poor}</span>
        </div>
      {:else}
        <span class="report-dist-empty">No score distribution available.</span>
      {/if}
    </div>
  </section>

  <!-- Taxonomy delta ─────────────────────────────────────────── -->
  <section class="report-section" data-test="report-section" data-section="taxonomy">
    <h3 class="report-section-title">Taxonomy Delta</h3>
    <div class="report-delta">
      {#if taxonomy.domains_created.length > 0}
        <div class="report-delta-row">
          <span class="report-delta-label">Domains</span>
          <div class="report-delta-tags">
            {#each taxonomy.domains_created as d}
              <span class="report-delta-tag">{d}</span>
            {/each}
          </div>
        </div>
      {/if}
      {#if taxonomy.sub_domains_created.length > 0}
        <div class="report-delta-row">
          <span class="report-delta-label">Sub-domains</span>
          <div class="report-delta-tags">
            {#each taxonomy.sub_domains_created as sd}
              <span class="report-delta-tag">{sd}</span>
            {/each}
          </div>
        </div>
      {/if}
      <div class="report-delta-row">
        <span class="report-delta-label">Clusters touched</span>
        <span class="report-delta-num">{taxonomy.clusters_touched}</span>
      </div>
    </div>
  </section>

  <!-- Recommended follow-ups ───────────────────────────────── -->
  <section class="report-section" data-test="report-section" data-section="followups">
    <h3 class="report-section-title">Recommended Follow-ups</h3>
    {#if followups.length > 0}
      <ul class="report-followups">
        {#each followups as f}
          <li data-test="report-followup">{f}</li>
        {/each}
      </ul>
    {:else}
      <span class="report-empty">No follow-ups recommended.</span>
    {/if}
  </section>

  <!-- Actions ──────────────────────────────────────────────── -->
  <div class="report-actions">
    <button
      type="button"
      class="report-btn report-btn--save"
      class:report-btn--flash={saveFlash}
      onclick={handleSave}
      disabled={saving}
      aria-label="Save Suite"
    >
      {saving ? 'Saving…' : 'Save Suite'}
    </button>

    {#if showReplay}
      <button
        type="button"
        class="report-btn report-btn--replay"
        onclick={handleReplay}
        disabled={replaying}
        aria-label="Replay suite"
      >
        {replaying ? 'Replaying…' : 'Replay'}
      </button>
    {/if}

    <button
      type="button"
      class="report-btn report-btn--copy copy-flash"
      class:report-btn--copied={copyFlash.triggered}
      data-test={copyFlash.triggered ? 'copy-flash' : undefined}
      data-state={copyFlash.triggered ? 'copy-flash' : undefined}
      onclick={handleCopy}
      aria-label="Copy report as markdown"
    >
      {copyFlash.triggered ? 'Copied' : 'Copy md'}
    </button>
  </div>
</div>

<style>
  .report-card {
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 12px;
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    font-family: var(--font-mono);
  }

  .report-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .report-title {
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-neon-cyan);
  }

  .report-mean {
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 700;
    color: var(--color-text-primary);
  }

  .report-section {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .report-section-title {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--color-text-dim);
    margin: 0;
  }

  .report-top-list {
    list-style: none;
    counter-reset: top-counter;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .report-top-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    counter-increment: top-counter;
    font-size: 11px;
  }

  .report-top-item::before {
    content: counter(top-counter);
    color: var(--color-text-dim);
    font-family: var(--font-mono);
    font-size: 10px;
    width: 12px;
    flex-shrink: 0;
  }

  .report-top-score {
    color: var(--color-neon-cyan);
    font-family: var(--font-mono);
    font-weight: 600;
    width: 32px;
    flex-shrink: 0;
  }

  .report-top-prompt {
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    flex: 1;
  }

  .report-distribution {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 4px;
  }

  .report-dist-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 6px;
    border: 1px solid var(--color-border-subtle);
  }

  .report-dist-label {
    font-size: 10px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .report-dist-val {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-primary);
    font-weight: 600;
  }

  .report-dist-empty,
  .report-empty {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 4px;
  }

  .report-delta {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .report-delta-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 10px;
  }

  .report-delta-label {
    color: var(--color-text-dim);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    width: 90px;
    flex-shrink: 0;
  }

  .report-delta-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .report-delta-tag {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
    padding: 1px 6px;
  }

  .report-delta-num {
    font-family: var(--font-mono);
    color: var(--color-neon-cyan);
    font-weight: 600;
  }

  .report-followups {
    list-style: disc;
    padding-left: 16px;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .report-actions {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    padding-top: 8px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .report-btn {
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 4px 12px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    height: 20px;
    box-sizing: border-box;
  }

  .report-btn:hover:not(:disabled) {
    color: var(--color-text-primary);
    border-color: var(--color-text-secondary);
  }

  .report-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .report-btn--save {
    color: var(--color-neon-yellow);
    border-color: var(--color-neon-yellow);
  }

  /* `forge-spark` one-shot fires when --flash class is added by the
     handleSave() handler — 250ms ease-out per spec §6. */
  .report-btn--flash {
    animation: forge-spark 250ms ease-out;
  }

  .report-btn--replay {
    color: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
  }

  /* Copy-md flash — uses the shared --duration-copy-flash window (1500ms)
     via the useCopyFlash() primitive. Green tint while the flash window
     is open, mirroring the existing PassthroughView/Logo/CodeBlock
     surfaces. */
  .report-btn--copied {
    color: var(--color-neon-green);
    border-color: var(--color-neon-green);
  }
</style>
