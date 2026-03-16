<script lang="ts">
  import { onMount } from 'svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { getSettings, getProviders } from '$lib/api/client';
  import type { SettingsResponse, ProvidersResponse } from '$lib/api/client';

  type Activity = 'editor' | 'history' | 'github' | 'settings';

  let { active }: { active: Activity } = $props();

  // ---- Editor panel state ----
  const strategies: { id: string; label: string; description: string }[] = [
    { id: 'chain_of_thought', label: 'Chain of Thought', description: 'Step-by-step reasoning' },
    { id: 'few_shot', label: 'Few-Shot', description: 'Example-driven prompting' },
    { id: 'role_persona', label: 'Role / Persona', description: 'Expert framing' },
    { id: 'structured_output', label: 'Structured Output', description: 'Schema-constrained response' },
    { id: 'zero_shot', label: 'Zero-Shot', description: 'Direct instruction, no examples' },
    { id: 'react', label: 'ReAct', description: 'Reasoning + acting loop' },
  ];

  // ---- History panel state ----
  // Static mock — will be wired to API in Task 8
  const mockHistory = [
    { id: '1', task_type: 'code', strategy_used: 'chain_of_thought', overall_score: 0.87 },
    { id: '2', task_type: 'analysis', strategy_used: 'few_shot', overall_score: 0.74 },
    { id: '3', task_type: 'creative', strategy_used: 'role_persona', overall_score: 0.92 },
    { id: '4', task_type: 'qa', strategy_used: 'zero_shot', overall_score: 0.65 },
    { id: '5', task_type: 'code', strategy_used: 'structured_output', overall_score: 0.81 },
  ];

  // ---- Settings panel state ----
  let settings = $state<SettingsResponse | null>(null);
  let providers = $state<ProvidersResponse | null>(null);

  onMount(async () => {
    // Pre-fetch for settings panel (best effort)
    try {
      [settings, providers] = await Promise.all([getSettings(), getProviders()]);
    } catch {
      // Silently ignore — backend may not be running
    }
  });

  function scoreColor(score: number): string {
    if (score >= 0.85) return 'var(--color-neon-green)';
    if (score >= 0.65) return 'var(--color-neon-yellow)';
    return 'var(--color-neon-red)';
  }

  function selectStrategy(id: string) {
    forgeStore.strategy = forgeStore.strategy === id ? null : id;
  }
</script>

<aside
  class="navigator"
  style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);"
  aria-label="Navigator"
>
  <!-- ============ EDITOR PANEL ============ -->
  {#if active === 'editor'}
    <div class="panel">
      <header class="panel-header">
        <span class="section-heading">Strategies</span>
      </header>
      <div class="panel-body">
        {#each strategies as strat}
          <button
            class="row-item"
            class:row-item--active={forgeStore.strategy === strat.id}
            onclick={() => selectStrategy(strat.id)}
            title={strat.description}
          >
            <span class="row-label">{strat.label}</span>
            {#if forgeStore.strategy === strat.id}
              <span class="row-badge">active</span>
            {/if}
          </button>
        {/each}
      </div>
    </div>

  <!-- ============ HISTORY PANEL ============ -->
  {:else if active === 'history'}
    <div class="panel">
      <header class="panel-header">
        <span class="section-heading">History</span>
      </header>
      <div class="panel-body">
        {#each mockHistory as item}
          <button class="row-item history-row" onclick={() => {}}>
            <div class="history-meta">
              <span class="row-label">{item.task_type}</span>
              <span
                class="row-score font-mono"
                style="color: {scoreColor(item.overall_score)};"
              >
                {(item.overall_score * 100).toFixed(0)}
              </span>
            </div>
            <span class="row-desc">{item.strategy_used.replace(/_/g, ' ')}</span>
          </button>
        {/each}
        <p class="empty-note">Wired in Task 8 — showing mock data</p>
      </div>
    </div>

  <!-- ============ GITHUB PANEL ============ -->
  {:else if active === 'github'}
    <div class="panel">
      <header class="panel-header">
        <span class="section-heading">GitHub</span>
      </header>
      <div class="panel-body">
        {#if githubStore.linkedRepo}
          <div class="info-block">
            <div class="info-row">
              <span class="info-key">Repo</span>
              <span class="info-val font-mono">{githubStore.linkedRepo.full_name}</span>
            </div>
            <div class="info-row">
              <span class="info-key">Branch</span>
              <span class="info-val font-mono">
                {githubStore.linkedRepo.branch ?? githubStore.linkedRepo.default_branch}
              </span>
            </div>
            {#if githubStore.linkedRepo.language}
              <div class="info-row">
                <span class="info-key">Lang</span>
                <span class="info-val">{githubStore.linkedRepo.language}</span>
              </div>
            {/if}
          </div>
          <button
            class="action-btn"
            onclick={() => githubStore.unlinkRepo()}
          >
            Unlink repo
          </button>
        {:else if githubStore.user}
          <div class="info-block">
            <div class="info-row">
              <span class="info-key">User</span>
              <span class="info-val font-mono">{githubStore.user.login}</span>
            </div>
          </div>
          <p class="empty-note">No repo linked. Use Repo Picker in the editor to link one.</p>
        {:else}
          <p class="empty-note">Sign in to GitHub to link a repository for context-aware optimization.</p>
          <button
            class="action-btn action-btn--primary"
            onclick={() => githubStore.login()}
          >
            Connect GitHub
          </button>
        {/if}
      </div>
    </div>

  <!-- ============ SETTINGS PANEL ============ -->
  {:else if active === 'settings'}
    <div class="panel">
      <header class="panel-header">
        <span class="section-heading">Settings</span>
      </header>
      <div class="panel-body">
        <!-- Provider -->
        <div class="sub-section">
          <span class="sub-heading">Provider</span>
          <div class="info-block">
            <div class="info-row">
              <span class="info-key">Active</span>
              <span class="info-val font-mono" style="color: var(--color-neon-cyan);">
                {providers?.active_provider ?? '—'}
              </span>
            </div>
            {#if providers?.available?.length}
              <div class="info-row">
                <span class="info-key">Available</span>
                <span class="info-val">{providers.available.join(', ')}</span>
              </div>
            {/if}
          </div>
        </div>

        <!-- Config values -->
        {#if settings}
          <div class="sub-section">
            <span class="sub-heading">Config</span>
            <div class="info-block">
              <div class="info-row">
                <span class="info-key">Max chars</span>
                <span class="info-val font-mono">{settings.max_raw_prompt_chars.toLocaleString()}</span>
              </div>
              <div class="info-row">
                <span class="info-key">Model</span>
                <span class="info-val font-mono">{settings.embedding_model}</span>
              </div>
              <div class="info-row">
                <span class="info-key">Rate limit</span>
                <span class="info-val font-mono">{settings.optimize_rate_limit}</span>
              </div>
              <div class="info-row">
                <span class="info-key">Retention</span>
                <span class="info-val font-mono">{settings.trace_retention_days}d</span>
              </div>
            </div>
          </div>
        {:else}
          <p class="empty-note">Backend offline — settings unavailable</p>
        {/if}
      </div>
    </div>
  {/if}
</aside>

<style>
  .navigator {
    height: 100%;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }

  .panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  .panel-header {
    display: flex;
    align-items: center;
    height: 32px;
    padding: 0 8px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .panel-body {
    padding: 8px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 2px;
    flex: 1;
  }

  /* ---- Row items ---- */
  .row-item {
    display: flex;
    align-items: center;
    height: 20px;
    padding: 0 6px;
    border: none;
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    width: 100%;
    text-align: left;
    gap: 6px;
    transition:
      color 200ms cubic-bezier(0.16, 1, 0.3, 1),
      background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .row-item:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: transparent;
  }

  .row-item:active {
    transform: none;
  }

  .row-item--active {
    color: var(--color-neon-cyan);
    background: var(--color-bg-hover);
    border-color: transparent;
  }

  .row-item--active:hover {
    color: var(--color-neon-cyan);
  }

  .row-label {
    font-size: 10px;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .row-desc {
    font-size: 10px;
    color: var(--color-text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .row-badge {
    font-size: 9px;
    font-family: var(--font-mono);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-neon-cyan);
    flex-shrink: 0;
  }

  /* ---- History row ---- */
  .history-row {
    height: auto;
    min-height: 20px;
    padding: 2px 6px;
    flex-direction: column;
    align-items: stretch;
    gap: 1px;
  }

  .history-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
  }

  .row-score {
    font-size: 10px;
    flex-shrink: 0;
  }

  /* ---- Info blocks ---- */
  .info-block {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-bottom: 8px;
  }

  .info-row {
    display: flex;
    align-items: center;
    height: 20px;
    gap: 8px;
    padding: 0 6px;
  }

  .info-key {
    font-size: 10px;
    color: var(--color-text-dim);
    width: 56px;
    flex-shrink: 0;
    text-overflow: ellipsis;
    overflow: hidden;
    white-space: nowrap;
  }

  .info-val {
    font-size: 10px;
    color: var(--color-text-primary);
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* ---- Sub-sections ---- */
  .sub-section {
    margin-bottom: 10px;
  }

  .sub-section > .sub-heading {
    display: block;
    padding: 0 6px;
    margin-bottom: 4px;
  }

  /* ---- Action buttons ---- */
  .action-btn {
    width: calc(100% - 12px);
    margin: 4px 6px 0;
    height: 24px;
    font-size: 10px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    color: var(--color-text-secondary);
    cursor: pointer;
    transition:
      color 200ms cubic-bezier(0.16, 1, 0.3, 1),
      background 200ms cubic-bezier(0.16, 1, 0.3, 1),
      border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .action-btn:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .action-btn:active {
    transform: none;
  }

  .action-btn--primary {
    color: var(--color-neon-cyan);
    border-color: var(--color-border-accent);
  }

  .action-btn--primary:hover {
    color: var(--color-neon-cyan);
    background: rgba(0, 229, 255, 0.05);
  }

  /* ---- Empty state ---- */
  .empty-note {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 4px 6px;
    line-height: 1.5;
    margin: 0 0 6px;
  }
</style>
