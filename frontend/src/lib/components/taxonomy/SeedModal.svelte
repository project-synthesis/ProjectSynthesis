<script lang="ts">
  import { seedTaxonomy, listSeedAgents, type SeedOutput, type SeedAgent } from '$lib/api/seed';
  import { clustersStore } from '$lib/stores/clusters.svelte';

  interface Props {
    open: boolean;
    onClose: () => void;
  }

  let { open = $bindable(), onClose }: Props = $props();

  // State
  let mode = $state<'generate' | 'provide'>('generate');
  let projectDescription = $state('');
  let promptsText = $state('');
  let promptCount = $state(30);
  let agents = $state<SeedAgent[]>([]);
  let selectedAgents = $state<Set<string>>(new Set());
  let seeding = $state(false);
  let result = $state<SeedOutput | null>(null);
  let error = $state<string | null>(null);
  let progress = $state({ completed: 0, total: 0, current: '' });

  // Load agents on mount
  $effect(() => {
    if (open) {
      listSeedAgents().then(a => {
        agents = a;
        selectedAgents = new Set(a.map(ag => ag.name));
      }).catch(() => {});
    }
  });

  // SSE progress listener
  $effect(() => {
    if (!seeding) return;
    const handler = (e: Event) => {
      const data = (e as CustomEvent).detail;
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
      clustersStore.invalidateClusters();
    } catch (err) {
      error = err instanceof Error ? err.message : 'Seed failed';
    } finally {
      seeding = false;
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

  function statusColor(status: string): string {
    if (status === 'completed') return 'var(--color-neon-green)';
    if (status === 'partial') return 'var(--color-neon-yellow)';
    return 'var(--color-neon-red)';
  }

  function formatDuration(ms: number): string {
    if (ms < 1000) return `${ms}ms`;
    if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
    return `${Math.floor(ms / 60000)}m ${Math.floor((ms % 60000) / 1000)}s`;
  }

  // Estimated cost: mirrors backend estimate_batch_cost() logic
  // Agent generation: ~$0.003/agent (Haiku). Per optimization: ~$0.132 (Sonnet+Opus+Sonnet)
  const estimatedCost = $derived(
    (selectedAgents.size * 0.003 + promptCount * 0.132).toFixed(2)
  );
</script>

<svelte:window onkeydown={handleKeyDown} />

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events a11y_no_static_element_interactions -->
  <div class="seed-overlay" onclick={handleOverlayClick} role="dialog" aria-modal="true" aria-label="Seed Taxonomy">
    <div class="seed-modal">
      <!-- Header -->
      <div class="seed-header">
        <span class="seed-title">SEED TAXONOMY</span>
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
              <label class="seed-label">AGENTS</label>
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

        {:else}
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
        {/if}

        <!-- Progress -->
        {#if seeding}
          <div class="seed-progress">
            <div class="seed-progress-header">
              <span class="seed-progress-label">SEEDING...</span>
              <span class="seed-progress-pct">{progressPercent}%</span>
            </div>
            <div class="seed-progress-track">
              <div class="seed-progress-fill" style="width: {progressPercent}%"></div>
            </div>
            {#if progress.current}
              <div class="seed-progress-current" title={progress.current}>
                {progress.current.length > 60 ? progress.current.slice(0, 60) + '…' : progress.current}
              </div>
            {/if}
            <div class="seed-progress-stats">
              {progress.completed} / {progress.total} completed
            </div>
          </div>
        {/if}

        <!-- Error -->
        {#if error}
          <div class="seed-error">{error}</div>
        {/if}

        <!-- Result card -->
        {#if result}
          <div class="seed-result">
            <div class="seed-result-header">
              <span class="seed-result-status" style="color: {statusColor(result.status)}; border-color: {statusColor(result.status)}">
                {result.status.toUpperCase()}
              </span>
              <button
                class="seed-batch-id"
                onclick={() => copyBatchId(result!.batch_id)}
                title="Click to copy batch ID"
              >
                {result.batch_id}
              </button>
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
              <span class="seed-tier-badge">{result.tier.toUpperCase()}</span>
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
    background: rgba(0, 0, 0, 0.7);
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
    background: rgba(255, 255, 255, 0.03);
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
    background: rgba(255, 255, 255, 0.03);
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
    background: rgba(0, 229, 255, 0.03);
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
    background: rgba(255, 34, 85, 0.06);
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
    background: rgba(0, 229, 255, 0.1);
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
    background: rgba(255, 255, 255, 0.04);
    border-color: var(--color-text-secondary);
  }

  .seed-btn-secondary:disabled {
    opacity: 0.3;
    cursor: not-allowed;
  }
</style>
