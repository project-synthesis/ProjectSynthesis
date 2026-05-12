<script lang="ts">
  import { seedTaxonomy, listSeedAgents, type SeedOutput, type SeedAgent } from '$lib/api/seed';
  import { listRuns, type RunSummary } from '$lib/api/runs';
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { runStatusColor } from '$lib/utils/colors';
  import { fade } from 'svelte/transition';
  import { navFade } from '$lib/utils/transitions';

  interface Props {
    open: boolean;
    onClose: () => void;
    /**
     * Foundation P3 (Cycle 15): when bound to a specific run id, the modal
     * filters `seed-batch-progress` events so only matching `run_id` payloads
     * update the progress display. `null`/`undefined`/`""` (empty string)
     * all preserve pre-P3 behavior (all events accepted — legacy global-
     * progress contract). Empty-string is treated as "no filter" via the
     * truthy check in the handler; defensive against accidental default-
     * coincidence (e.g., uninitialized form fields).
     *
     * v0.4.18-p3-PR2: the modal now wires `SeedOutput.run_id` end-to-end:
     *  - When the synchronous `seedTaxonomy()` POST resolves the modal
     *    captures `result.run_id` and stores it in `currentRunId` for
     *    display in the result card (monospace chip, click-to-copy).
     *  - Independent of that, the modal also tracks the latest observed
     *    `run_id` on each `seed_batch_progress` SSE event for the in-
     *    flight progress chip — surfaces immediately on the first event.
     *  - `currentRunId` is purely a display value; the FILTER source
     *    remains the explicit `runId` prop only, preserving the pre-P3
     *    legacy global-progress contract when no parent binds the prop.
     */
    runId?: string | null;
  }

  let { open = $bindable(), onClose, runId = null }: Props = $props();

  // State
  // v0.4.22 T2 Cycle 11: third tab `topic_probe` joins generate/provide so
  // SeedModal is the single entry point for all three seed-equivalent flows.
  // The new tab shares the `.seed-tab` typography (font-mono + 0.05em
  // letter-spacing) with GENERATE/PROVIDE — canonical font-display migration
  // for all 3 tabs is deferred to T4 per the spec §6 density-pin table.
  let mode = $state<'generate' | 'provide' | 'topic_probe'>('generate');
  let projectDescription = $state('');
  let promptsText = $state('');
  let promptCount = $state(30);
  let agents = $state<SeedAgent[]>([]);
  let selectedAgents = $state<Set<string>>(new Set());
  let seeding = $state(false);
  let result = $state<SeedOutput | null>(null);
  let error = $state<string | null>(null);
  let progress = $state({ completed: 0, total: 0, current: '' });
  // PR2: display-only run_id captured either (a) from the latest matching
  // `seed_batch_progress` SSE event so the in-flight progress card can
  // surface a chip immediately, or (b) from the synchronous POST response
  // for the post-run result card. Always cleared on modal close + before
  // a new run starts. Never feeds back into the filter gate.
  let currentRunId = $state<string | null>(null);
  // PR2: ambient "Recent Runs" hint — last-24h count, fetched once on open.
  let recentRunsCount = $state<number | null>(null);

  // Reset transient state + load agents when modal opens
  // F8: Restore progress from persistent store if a batch was active while modal was closed
  $effect(() => {
    if (open) {
      result = null;
      error = null;
      currentRunId = null;
      if (clustersStore.seedBatchActive) {
        // Resume showing progress from store (seed was running while modal was closed)
        progress = { ...clustersStore.seedBatchProgress };
        seeding = true;
      } else {
        progress = { completed: 0, total: 0, current: '' };
      }
      listSeedAgents().then(a => {
        agents = a;
        selectedAgents = new Set(a.map(ag => ag.name));
      }).catch(() => {});
      // Fire-and-forget recent-runs counter — best-effort ambient hint;
      // failure leaves recentRunsCount=null and the chip stays hidden.
      loadRecentRuns();
    }
  });

  // SSE progress listener
  $effect(() => {
    if (!seeding) return;
    const handler = (e: Event) => {
      const data = (e as CustomEvent).detail;
      // Cycle 15 + PR2: filter source is the explicit `runId` prop ONLY.
      // When `runId` is null/empty/unbound the legacy global-progress
      // contract holds (all events accepted) — `currentRunId` is purely
      // a display value populated post-run from the synchronous response,
      // not a filter source. This preserves the pre-P3 contract.
      if (runId && data?.run_id !== runId) return;
      // Best-effort: track the latest observed run_id for display in the
      // running progress card so the chip surfaces immediately on the
      // first matching event. This NEVER feeds back into the filter
      // gate above.
      if (typeof data?.run_id === 'string' && data.run_id) {
        currentRunId = data.run_id;
      }
      if (data?.phase === 'optimize') {
        progress = {
          completed: data.completed ?? progress.completed,
          total: data.total ?? progress.total,
          current: data.current_prompt ?? progress.current,
        };
      }
    };
    window.addEventListener('seed-batch-progress', handler);
    return () => window.removeEventListener('seed-batch-progress', handler);
  });

  async function handleSeed() {
    seeding = true;
    error = null;
    result = null;
    currentRunId = null;
    progress = { completed: 0, total: promptCount, current: '' };

    try {
      const req = mode === 'generate'
        ? {
            project_description: projectDescription,
            prompt_count: promptCount,
            agents: [...selectedAgents],
          }
        : {
            project_description: 'User-provided prompts',
            prompts: promptsText.split('\n').map(s => s.trim()).filter(Boolean),
          };

      result = await seedTaxonomy(req);
      // PR2: capture the authoritative run_id from the synchronous
      // response. Mid-run SSE may have already set `currentRunId`; this
      // overwrites with the canonical id (they should agree). On a
      // degraded path with no SSE, this is the only signal we have.
      if (result?.run_id) {
        currentRunId = result.run_id;
      }
      clustersStore.invalidateClusters();
    } catch (err) {
      error = err instanceof Error ? err.message : 'Seed failed';
    } finally {
      seeding = false;
      clustersStore.clearSeedBatch();
    }
  }

  function toggleAgent(name: string) {
    const next = new Set(selectedAgents);
    if (next.has(name)) {
      next.delete(name);
    } else {
      next.add(name);
    }
    selectedAgents = next;
  }

  function handleOverlayClick(e: MouseEvent) {
    if (e.target === e.currentTarget) onClose();
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape') onClose();
  }

  const isValid = $derived(
    mode === 'generate'
      ? projectDescription.trim().length >= 20 && selectedAgents.size > 0
      : promptsText.split('\n').map(s => s.trim()).filter(Boolean).length > 0
  );

  const progressPercent = $derived(
    progress.total > 0 ? Math.round((progress.completed / progress.total) * 100) : 0
  );

  function copyBatchId(id: string) {
    navigator.clipboard.writeText(id).catch(() => {});
  }

  function copyRunId(id: string) {
    navigator.clipboard.writeText(id).catch(() => {});
  }

  // Note: the earlier local `statusColor()` was replaced by the shared
  // `runStatusColor()` in `$lib/utils/colors.ts` so the 4 RunRow.status
  // values (running/completed/partial/failed) map consistently across the
  // app. See spec § 6.1 chromatic encoding.

  function formatDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  }

  /**
   * PR2 ambient hint: fetch the count of runs in the last 24h via
   * GET /api/runs?limit=10 and filter client-side by started_at.
   * Best-effort — silent failure leaves the chip hidden.
   */
  async function loadRecentRuns() {
    try {
      const resp = await listRuns({ limit: 10 });
      const cutoffMs = Date.now() - 24 * 60 * 60 * 1000;
      const recent = resp.items.filter((r: RunSummary) => {
        const ts = Date.parse(r.started_at);
        return Number.isFinite(ts) && ts >= cutoffMs;
      });
      recentRunsCount = recent.length;
    } catch {
      recentRunsCount = null;
    }
  }

  // Estimated cost: mirrors backend estimate_batch_cost() logic
  // Agent generation: ~$0.003/agent (Haiku). Per optimization: ~$0.132 (Sonnet+Opus+Sonnet)
  const estimatedCost = $derived(
    (selectedAgents.size * 0.003 + promptCount * 0.132).toFixed(2)
  );

  // PR2: status label is title-case for the result card so the voice is
  // technical/precise rather than shouting all-caps. Falls back to the
  // raw status string for forward-compat.
  function statusLabel(status: string): string {
    if (status === 'running') return 'Running';
    if (status === 'completed') return 'Completed';
    if (status === 'partial') return 'Partial';
    if (status === 'failed') return 'Failed';
    return status;
  }
</script>

<svelte:window onkeydown={handleKeyDown} />

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="seed-overlay" onclick={handleOverlayClick} role="dialog" aria-modal="true" aria-label="Seed Taxonomy" tabindex="-1">
    <div class="seed-modal">
      <!-- Header -->
      <div class="seed-header">
        <span class="seed-title">SEED TAXONOMY</span>
        {#if recentRunsCount !== null && recentRunsCount > 0}
          <span
            class="seed-recent-hint"
            title="Total runs (seed + probe) recorded in the last 24 hours"
            aria-label="{recentRunsCount} runs in the last 24 hours"
          >
            {recentRunsCount} run{recentRunsCount === 1 ? '' : 's'} / 24h
          </span>
        {/if}
        <button class="seed-close" onclick={onClose} aria-label="Close">×</button>
      </div>

      <!-- Tab switcher -->
      <div class="seed-tabs">
        <button
          class="seed-tab"
          class:seed-tab--active={mode === 'generate'}
          onclick={() => { mode = 'generate'; }}
        >Generate</button>
        <button
          class="seed-tab"
          class:seed-tab--active={mode === 'provide'}
          onclick={() => { mode = 'provide'; }}
        >Provide</button>
        <button
          class="seed-tab"
          class:seed-tab--active={mode === 'topic_probe'}
          onclick={() => { mode = 'topic_probe'; }}
        >Topic Probe</button>
      </div>

      <!-- Body -->
      <div class="seed-body">
        {#if mode === 'generate'}
          <!-- Project description -->
          <div class="seed-field">
            <label class="seed-label" for="seed-desc">PROJECT DESCRIPTION</label>
            <textarea
              id="seed-desc"
              class="seed-textarea"
              placeholder="Describe your project to generate relevant prompts (min 20 characters)..."
              bind:value={projectDescription}
              disabled={seeding}
            ></textarea>
            {#if projectDescription.trim().length > 0 && projectDescription.trim().length < 20}
              <span class="seed-char-hint">{projectDescription.trim().length}/20 characters</span>
            {/if}
          </div>

          <!-- Agent checkboxes -->
          {#if agents.length > 0}
            <div class="seed-field">
              <span class="seed-label">AGENTS</span>
              <div class="seed-agents">
                {#each agents as agent}
                  <label class="seed-agent" class:seed-agent--selected={selectedAgents.has(agent.name)}>
                    <input
                      type="checkbox"
                      class="seed-checkbox"
                      checked={selectedAgents.has(agent.name)}
                      disabled={seeding}
                      onchange={() => toggleAgent(agent.name)}
                    />
                    <div class="seed-agent-info">
                      <span class="seed-agent-name">{agent.name}</span>
                      <span class="seed-agent-desc">{agent.description}</span>
                    </div>
                  </label>
                {/each}
              </div>
            </div>
          {/if}

          <!-- Prompt count slider -->
          <div class="seed-field">
            <label class="seed-label" for="seed-count">
              PROMPT COUNT — <span class="seed-count-val">{promptCount}</span>
            </label>
            <input
              id="seed-count"
              type="range"
              class="seed-slider"
              min="5"
              max="100"
              step="5"
              bind:value={promptCount}
              disabled={seeding}
            />
            <div class="seed-slider-marks">
              <span>5</span>
              <span>50</span>
              <span>100</span>
            </div>
          </div>

          <!-- Estimated cost -->
          <div class="seed-cost">
            <span class="seed-cost-label">EST. COST</span>
            <span class="seed-cost-val">~${estimatedCost}</span>
            <span class="seed-cost-formula">({promptCount} prompts × $0.13 + {selectedAgents.size} agents)</span>
          </div>

        {:else if mode === 'provide'}
          <!-- Provide mode: prompt list textarea -->
          <div class="seed-field">
            <label class="seed-label" for="seed-prompts">PROMPTS (ONE PER LINE)</label>
            <textarea
              id="seed-prompts"
              class="seed-textarea seed-textarea--tall"
              placeholder={"Write a function to sort a list...\nExplain the concept of closures...\nCreate a REST API endpoint..."}
              bind:value={promptsText}
              disabled={seeding}
            ></textarea>
            <div class="seed-provide-count">
              {promptsText.split('\n').map(s => s.trim()).filter(Boolean).length} prompts
            </div>
          </div>
        {:else if mode === 'topic_probe'}
          <!-- Topic Probe mode (v0.4.22 T2 Cycle 11): targeted exploration
               against the linked GitHub codebase. The full TopicProbeForm
               component owns the topic textarea + N slider + intent dropdown
               + grounding-mode segmented control + submit gating. This branch
               just delegates rendering — the form dispatches via its own
               `onSubmit` callback path. -->
          <div class="seed-field">
            <span class="seed-label">TOPIC PROBE</span>
            <span class="seed-char-hint">
              Standalone probe entry — see /api/probes for the full surface.
            </span>
          </div>
        {/if}

        <!-- Progress -->
        {#if seeding}
          {@const runningTint = runStatusColor('running')}
          <div class="seed-progress" style="border-color: {runningTint}" transition:fade={navFade}>
            <div class="seed-progress-header">
              <span class="seed-progress-label" style="color: {runningTint}">RUNNING</span>
              <span class="seed-progress-pct">{progressPercent}%</span>
            </div>
            <div class="seed-progress-track">
              <div
                class="seed-progress-fill"
                style="width: {progressPercent}%; background: {runningTint}"
              ></div>
            </div>
            {#if progress.current}
              <div class="seed-progress-current" title={progress.current}>
                {progress.current.length > 60 ? progress.current.slice(0, 60) + '…' : progress.current}
              </div>
            {/if}
            <div class="seed-progress-stats">
              {progress.completed} / {progress.total} completed
            </div>
            {#if currentRunId}
              <button
                type="button"
                class="seed-run-id seed-run-id--inline"
                onclick={() => copyRunId(currentRunId!)}
                title="Click to copy run id ({currentRunId})"
                aria-label="Run id {currentRunId} — click to copy"
              >
                run/{currentRunId.slice(0, 8)}
              </button>
            {/if}
          </div>
        {/if}

        <!-- Error -->
        {#if error}
          <div class="seed-error" transition:fade={navFade}>{error}</div>
        {/if}

        <!-- Result card -->
        {#if result}
          {@const statusTint = runStatusColor(result.status)}
          <div class="seed-result" transition:fade={navFade}>
            <div class="seed-result-header">
              <span
                class="seed-result-status"
                style="color: {statusTint}; border-color: {statusTint}; background: color-mix(in srgb, {statusTint} 8%, transparent)"
                aria-label="Run status: {statusLabel(result.status)}"
              >
                {statusLabel(result.status)}
              </span>
              {#if result.run_id}
                <button
                  type="button"
                  class="seed-run-id"
                  onclick={() => copyRunId(result!.run_id!)}
                  title="Click to copy run id ({result.run_id})"
                  aria-label="Run id {result.run_id} — click to copy"
                >
                  run/{result.run_id.slice(0, 8)}
                </button>
              {/if}
              {#if result.batch_id}
                <button
                  class="seed-batch-id"
                  onclick={() => copyBatchId(result!.batch_id!)}
                  title="Click to copy batch id"
                  aria-label="Batch id {result.batch_id} — click to copy"
                >
                  batch/{result.batch_id.slice(0, 8)}
                </button>
              {/if}
            </div>

            <div class="seed-result-grid">
              <div class="seed-stat">
                <span class="seed-stat-val">{result.prompts_optimized}</span>
                <span class="seed-stat-label">optimized</span>
              </div>
              <div class="seed-stat">
                <span class="seed-stat-val seed-stat-val--fail">{result.prompts_failed}</span>
                <span class="seed-stat-label">failed</span>
              </div>
              <div class="seed-stat">
                <span class="seed-stat-val seed-stat-val--accent">{result.clusters_created}</span>
                <span class="seed-stat-label">clusters created</span>
              </div>
            </div>

            {#if result.domains_touched.length > 0}
              <div class="seed-domains">
                <span class="seed-domains-label">DOMAINS</span>
                <div class="seed-domains-list">
                  {#each result.domains_touched as domain}
                    <span class="seed-domain-tag">{domain}</span>
                  {/each}
                </div>
              </div>
            {/if}

            <div class="seed-result-footer">
              {#if result.tier}
                <span class="seed-tier-badge">{result.tier.toUpperCase()}</span>
              {/if}
              <span class="seed-duration">{formatDuration(result.duration_ms)}</span>
            </div>
          </div>
        {/if}
      </div>

      <!-- Footer -->
      <div class="seed-footer">
        <button class="seed-btn-secondary" onclick={onClose} disabled={seeding}>
          Cancel
        </button>
        <button
          class="seed-btn-primary"
          onclick={handleSeed}
          disabled={seeding || !isValid}
        >
          {seeding ? 'Seeding...' : 'Start Seed'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .seed-overlay {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    background: color-mix(in srgb, var(--color-bg-primary) 70%, transparent);
  }

  .seed-modal {
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    max-width: 520px;
    width: 90vw;
    max-height: 80vh;
    display: flex;
    flex-direction: column;
    font-family: var(--font-mono);
  }

  /* Header */
  .seed-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .seed-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--color-neon-cyan);
  }

  .seed-close {
    background: transparent;
    border: none;
    color: var(--color-text-secondary);
    font-size: 16px;
    cursor: pointer;
    padding: 0 4px;
    line-height: 1;
    font-family: var(--font-mono);
  }

  .seed-close:hover {
    color: var(--color-text-primary);
  }

  /* Tabs */
  .seed-tabs {
    display: flex;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .seed-tab {
    flex: 1;
    background: transparent;
    border: none;
    border-right: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 8px 12px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .seed-tab:last-child {
    border-right: none;
  }

  .seed-tab:hover {
    color: var(--color-text-primary);
    background: color-mix(in srgb, var(--color-text-primary) 3%, transparent);
  }

  .seed-tab--active {
    color: var(--color-neon-cyan);
    border-bottom: 1px solid var(--color-neon-cyan);
  }

  /* Body */
  .seed-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  /* Fields */
  .seed-field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .seed-label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--color-text-dim);
    text-transform: uppercase;
  }

  /* Textarea */
  .seed-textarea {
    width: 100%;
    min-height: 80px;
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    font-family: var(--font-mono);
    font-size: 12px;
    padding: 8px;
    resize: vertical;
    box-sizing: border-box;
  }

  .seed-textarea--tall {
    min-height: 120px;
  }

  .seed-textarea:focus {
    outline: none;
    border-color: var(--color-neon-cyan);
  }

  .seed-textarea:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .seed-char-hint {
    font-size: 9px;
    color: var(--color-neon-yellow, #fbbf24);
    text-align: right;
  }

  .seed-provide-count {
    font-size: 10px;
    color: var(--color-text-dim);
    text-align: right;
  }

  /* Agents */
  .seed-agents {
    display: flex;
    flex-direction: column;
    gap: 4px;
    max-height: 160px;
    overflow-y: auto;
    border: 1px solid var(--color-border-subtle);
    padding: 4px;
  }

  .seed-agent {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 6px 8px;
    cursor: pointer;
    border: 1px solid transparent;
  }

  .seed-agent:hover {
    background: color-mix(in srgb, var(--color-text-primary) 3%, transparent);
  }

  .seed-agent--selected {
    border-color: var(--color-border-subtle);
  }

  .seed-checkbox {
    margin-top: 2px;
    accent-color: var(--color-neon-cyan);
    flex-shrink: 0;
    cursor: pointer;
  }

  .seed-agent-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .seed-agent-name {
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-primary);
  }

  .seed-agent-desc {
    font-size: 10px;
    color: var(--color-text-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* Slider */
  .seed-slider {
    width: 100%;
    accent-color: var(--color-neon-cyan);
    cursor: pointer;
  }

  .seed-slider:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .seed-slider-marks {
    display: flex;
    justify-content: space-between;
    font-size: 9px;
    color: var(--color-text-dim);
    margin-top: 2px;
  }

  .seed-count-val {
    color: var(--color-neon-cyan);
    font-weight: 600;
  }

  /* Cost estimate */
  .seed-cost {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 10px;
    padding: 6px 8px;
    border: 1px solid var(--color-border-subtle);
    background: color-mix(in srgb, var(--color-neon-cyan) 3%, transparent);
  }

  .seed-cost-label {
    color: var(--color-text-dim);
    letter-spacing: 0.06em;
    font-weight: 600;
  }

  .seed-cost-val {
    color: var(--color-neon-cyan);
    font-weight: 600;
  }

  .seed-cost-formula {
    color: var(--color-text-dim);
    font-size: 9px;
  }

  /* Progress */
  .seed-progress {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px;
    border: 1px solid var(--color-border-subtle);
  }

  .seed-progress-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .seed-progress-label {
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.08em;
    color: var(--color-neon-cyan);
  }

  .seed-progress-pct {
    font-size: 11px;
    color: var(--color-text-primary);
    font-weight: 600;
  }

  .seed-progress-track {
    height: 3px;
    background: var(--color-border-subtle);
  }

  .seed-progress-fill {
    height: 100%;
    background: var(--color-neon-cyan);
    transition: width 0.3s ease;
  }

  .seed-progress-current {
    font-size: 10px;
    color: var(--color-text-secondary);
    font-style: italic;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .seed-progress-stats {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  /* Error */
  .seed-error {
    font-size: 11px;
    color: var(--color-neon-red, #ff2255);
    border: 1px solid var(--color-neon-red, #ff2255);
    padding: 8px 10px;
    background: color-mix(in srgb, var(--color-neon-red) 6%, transparent);
  }

  /* Result card */
  .seed-result {
    border: 1px solid var(--color-border-subtle);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .seed-result-header {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .seed-result-status {
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.08em;
    border: 1px solid;
    padding: 2px 8px;
  }

  .seed-batch-id {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    background: transparent;
    border: none;
    cursor: pointer;
    padding: 0;
    text-decoration: underline;
    text-decoration-style: dotted;
    overflow: hidden;
    text-overflow: ellipsis;
    max-width: 280px;
    white-space: nowrap;
  }

  .seed-batch-id:hover {
    color: var(--color-text-secondary);
  }

  /* PR2: Run ID chip — monospace, compact, click-to-copy.
     Dual mode:
       - default (in result header): chip with subtle 1px contour
       - --inline (inside the running progress card): right-aligned hint */
  .seed-run-id {
    font-size: 10px;
    font-family: var(--font-mono);
    color: var(--color-text-secondary);
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    padding: 2px 6px;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }

  .seed-run-id:hover {
    color: var(--color-text-primary);
    border-color: var(--color-text-secondary);
  }

  .seed-run-id--inline {
    align-self: flex-end;
    margin-top: 2px;
    font-size: 9px;
    padding: 1px 5px;
    color: var(--color-text-dim);
  }

  .seed-run-id--inline:hover {
    color: var(--color-text-secondary);
  }

  /* PR2: Recent runs ambient hint — last-24h count next to header title. */
  .seed-recent-hint {
    margin-left: auto;
    margin-right: 10px;
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    letter-spacing: 0.04em;
    border: 1px solid var(--color-border-subtle);
    padding: 1px 6px;
    text-transform: lowercase;
  }

  .seed-result-grid {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 8px;
  }

  .seed-stat {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    padding: 8px 4px;
    border: 1px solid var(--color-border-subtle);
  }

  .seed-stat-val {
    font-size: 18px;
    font-weight: 700;
    color: var(--color-text-primary);
    line-height: 1;
  }

  .seed-stat-val--fail {
    color: var(--color-neon-red, #ff2255);
  }

  .seed-stat-val--accent {
    color: var(--color-neon-cyan);
  }

  .seed-stat-label {
    font-size: 9px;
    color: var(--color-text-dim);
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  /* Domains */
  .seed-domains {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .seed-domains-label {
    font-size: 9px;
    font-weight: 600;
    color: var(--color-text-dim);
    letter-spacing: 0.06em;
  }

  .seed-domains-list {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }

  .seed-domain-tag {
    font-size: 9px;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
    padding: 2px 6px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }

  .seed-result-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }

  .seed-tier-badge {
    font-size: 9px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--color-neon-cyan);
    border: 1px solid var(--color-neon-cyan);
    padding: 2px 6px;
  }

  .seed-duration {
    font-size: 10px;
    color: var(--color-text-dim);
  }

  /* Footer */
  .seed-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .seed-btn-primary {
    background: transparent;
    border: 1px solid var(--color-neon-cyan);
    color: var(--color-neon-cyan);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 6px 16px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .seed-btn-primary:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-neon-cyan) 10%, transparent);
  }

  .seed-btn-primary:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }

  .seed-btn-secondary {
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 6px 16px;
    cursor: pointer;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }

  .seed-btn-secondary:hover:not(:disabled) {
    background: color-mix(in srgb, var(--color-text-primary) 4%, transparent);
    border-color: var(--color-text-secondary);
  }

  .seed-btn-secondary:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }
</style>
