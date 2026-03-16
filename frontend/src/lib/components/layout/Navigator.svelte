<script lang="ts">
  import { onMount } from 'svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { getSettings, getProviders, getHistory, getOptimization, getApiKey, setApiKey, deleteApiKey } from '$lib/api/client';
  import type { SettingsResponse, ProvidersResponse, HistoryItem, ApiKeyStatus } from '$lib/api/client';

  type Activity = 'editor' | 'history' | 'github' | 'settings';

  let { active }: { active: Activity } = $props();

  // ---- Editor panel state ----
  const strategies: { id: string; label: string; description: string }[] = [
    { id: 'chain-of-thought', label: 'Chain of Thought', description: 'Step-by-step reasoning' },
    { id: 'few-shot', label: 'Few-Shot', description: 'Example-driven prompting' },
    { id: 'role-playing', label: 'Role-Playing', description: 'Expert persona framing' },
    { id: 'structured-output', label: 'Structured Output', description: 'Format + constraints' },
    { id: 'meta-prompting', label: 'Meta-Prompting', description: 'Structural improvement' },
    { id: 'auto', label: 'Auto', description: 'Let the optimizer decide' },
  ];

  // ---- History panel state ----
  let historyItems = $state<HistoryItem[]>([]);
  let historyError = $state<string | null>(null);
  let historyLoaded = $state(false);

  // ---- Settings panel state ----
  let settings = $state<SettingsResponse | null>(null);
  let providers = $state<ProvidersResponse | null>(null);

  // ---- API Key state ----
  let apiKeyStatus = $state<ApiKeyStatus | null>(null);
  let apiKeyInput = $state('');
  let apiKeyError = $state<string | null>(null);
  let apiKeySaving = $state(false);

  onMount(async () => {
    // Pre-fetch for settings panel (best effort)
    try {
      [settings, providers, apiKeyStatus] = await Promise.all([
        getSettings(), getProviders(), getApiKey(),
      ]);
    } catch {
      // Silently ignore — backend may not be running
    }
  });

  // Auto-refresh history when real-time events arrive from any source
  onMount(() => {
    const handler = () => { historyLoaded = false; };
    window.addEventListener('optimization-event', handler);
    return () => window.removeEventListener('optimization-event', handler);
  });

  async function handleSetApiKey() {
    if (!apiKeyInput.trim()) return;
    apiKeySaving = true;
    apiKeyError = null;
    try {
      apiKeyStatus = await setApiKey(apiKeyInput.trim());
      apiKeyInput = '';
    } catch (err: any) {
      apiKeyError = err?.message || 'Failed to set API key';
    } finally {
      apiKeySaving = false;
    }
  }

  async function handleDeleteApiKey() {
    apiKeyError = null;
    try {
      apiKeyStatus = await deleteApiKey();
    } catch (err: any) {
      apiKeyError = err?.message || 'Failed to remove API key';
    }
  }

  // Fetch history when the history panel becomes active
  $effect(() => {
    if (active === 'history' && !historyLoaded) {
      getHistory({ limit: 50, sort_by: 'created_at', sort_order: 'desc' })
        .then((resp) => {
          historyItems = resp.items;
          historyError = null;
          historyLoaded = true;
        })
        .catch((err: any) => {
          historyError = err?.message || 'Failed to load history';
          historyLoaded = true;
        });
    }
  });

  function scoreColor(score: number | null): string {
    if (score == null || score <= 0) return 'var(--color-text-dim)';
    if (score >= 7.5) return 'var(--color-neon-green)';
    if (score >= 5.0) return 'var(--color-neon-yellow)';
    return 'var(--color-neon-red)';
  }

  // Fix 7: Reset historyLoaded when a new optimization completes
  $effect(() => {
    if (forgeStore.status === 'complete') {
      historyLoaded = false;
    }
  });

  async function loadHistoryItem(item: HistoryItem) {
    // Cancel any in-flight optimization first
    if (forgeStore.status !== 'idle' && forgeStore.status !== 'complete' && forgeStore.status !== 'error') {
      forgeStore.cancel();
    }
    try {
      const opt = await getOptimization(item.trace_id);
      forgeStore.result = opt;
      forgeStore.status = 'complete';
      forgeStore.prompt = opt.raw_prompt;
      // Populate score fields so Inspector/ScoreCard render correctly
      if (opt.scores) forgeStore.scores = opt.scores;
      if (opt.original_scores) forgeStore.originalScores = opt.original_scores;
      if (opt.score_deltas) forgeStore.scoreDeltas = opt.score_deltas;
      editorStore.openResult(item.id);
      // Switch to editor activity
      window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
    } catch {
      // Fallback: populate from the history item directly
      forgeStore.prompt = item.raw_prompt;
      forgeStore.status = 'idle';
      window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
    }
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
        {#if historyError}
          <p class="empty-note">{historyError}</p>
        {:else if !historyLoaded}
          <p class="empty-note">Loading…</p>
        {:else if historyItems.length === 0}
          <p class="empty-note">No optimizations yet.</p>
        {:else}
          {#each historyItems.filter(i => i.status === 'completed') as item (item.id)}
            <button class="row-item history-row" onclick={() => loadHistoryItem(item)}>
              <span class="row-prompt">{item.raw_prompt || 'Untitled'}</span>
              <div class="history-meta">
                <span class="row-badge font-mono">{item.strategy_used || 'auto'}</span>
                <span
                  class="row-score font-mono"
                  style="color: {scoreColor(item.overall_score)};"
                >
                  {item.overall_score != null ? item.overall_score.toFixed(1) : '—'}
                </span>
              </div>
            </button>
          {/each}
          {#if historyItems.filter(i => i.status === 'completed').length === 0}
            <p class="empty-note">No completed optimizations yet.</p>
          {/if}
        {/if}
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

        <!-- API Key -->
        <div class="sub-section">
          <span class="sub-heading">API Key</span>
          <div class="info-block">
            <div class="info-row">
              <span class="info-key">Status</span>
              <span class="info-val font-mono" style="color: {apiKeyStatus?.configured ? 'var(--color-neon-green)' : 'var(--color-text-dim)'};">
                {apiKeyStatus?.configured ? 'configured' : 'not set'}
              </span>
            </div>
            {#if apiKeyStatus?.masked_key}
              <div class="info-row">
                <span class="info-key">Key</span>
                <span class="info-val font-mono">{apiKeyStatus.masked_key}</span>
              </div>
            {/if}
          </div>
          <div class="api-key-form">
            <input
              class="api-key-input"
              type="password"
              placeholder="sk-..."
              bind:value={apiKeyInput}
              onkeydown={(e: KeyboardEvent) => { if (e.key === 'Enter') handleSetApiKey(); }}
            />
            <div class="api-key-actions">
              <button
                class="action-btn"
                onclick={handleSetApiKey}
                disabled={apiKeySaving || !apiKeyInput.trim()}
              >
                {apiKeySaving ? 'Saving...' : 'Set key'}
              </button>
              {#if apiKeyStatus?.configured}
                <button class="action-btn" onclick={handleDeleteApiKey}>
                  Remove
                </button>
              {/if}
            </div>
          </div>
          {#if apiKeyError}
            <p class="empty-note" style="color: var(--color-neon-red);">{apiKeyError}</p>
          {/if}
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
    height: 24px;
    padding: 0 6px;
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .panel-body {
    padding: 6px;
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
    padding: 2px 4px;
    flex-direction: column;
    align-items: stretch;
    gap: 1px;
  }

  .row-prompt {
    font-size: 10px;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: left;
    width: 100%;
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
    border: 1px solid rgba(0, 229, 255, 0.2);
    background: rgba(0, 229, 255, 0.05);
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 0 8px;
    height: 20px;
    line-height: 18px;
    width: calc(100% - 12px);
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .action-btn--primary:hover {
    color: var(--color-neon-cyan);
    background: rgba(0, 229, 255, 0.1);
    border-color: var(--color-neon-cyan);
  }

  /* ---- Empty state ---- */
  .empty-note {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 4px 6px;
    line-height: 1.5;
    margin: 0 0 6px;
  }

  /* ---- API Key form ---- */
  .api-key-form {
    padding: 0 6px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .api-key-input {
    width: 100%;
    height: 22px;
    padding: 0 6px;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-primary);
    background: var(--color-bg-primary);
    border: 1px solid var(--color-border-subtle);
    outline: none;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .api-key-input:focus {
    border-color: var(--color-border-accent);
  }

  .api-key-input::placeholder {
    color: var(--color-text-dim);
  }

  .api-key-actions {
    display: flex;
    gap: 4px;
  }

  .api-key-actions .action-btn {
    flex: 1;
    margin: 0;
    width: auto;
  }
</style>
