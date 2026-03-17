<script lang="ts">
  import { githubStore } from '$lib/stores/github.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { getSettings, getProviders, getHistory, getOptimization, getApiKey, setApiKey, deleteApiKey, getStrategies, getStrategy, updateStrategy } from '$lib/api/client';
  import type { SettingsResponse, ProvidersResponse, HistoryItem, ApiKeyStatus, StrategyInfo } from '$lib/api/client';

  type Activity = 'editor' | 'history' | 'github' | 'settings';

  let { active }: { active: Activity } = $props();

  // ---- Editor panel state ----
  let strategiesList = $state<StrategyInfo[]>([]);
  let editingStrategy = $state<string | null>(null);
  let editContent = $state('');
  let editSaving = $state(false);
  let editDirty = $state(false);
  let suppressedNames = $state<Set<string>>(new Set());

  // Load strategies from backend on mount
  let strategiesLoaded = false;
  $effect(() => {
    if (strategiesLoaded) return;
    strategiesLoaded = true;
    getStrategies()
      .then((list: any[]) => { strategiesList = list; })
      .catch(() => {});
  });

  // No label transformation — filename IS the identity

  async function openStrategyEditor(name: string) {
    if (editingStrategy === name) {
      editingStrategy = null;
      return;
    }
    try {
      const detail = await getStrategy(name);
      editContent = detail.content;
      editingStrategy = name;
      editDirty = false;
    } catch {
      editingStrategy = null;
    }
  }

  async function saveStrategyEdit() {
    if (!editingStrategy || !editDirty) return;
    editSaving = true;
    try {
      await updateStrategy(editingStrategy, editContent);
      editDirty = false;

      // Suppress watcher toast for this name (avoid double notification)
      const savedName = editingStrategy;
      suppressedNames = new Set([...suppressedNames, savedName!]);
      setTimeout(() => {
        suppressedNames = new Set([...suppressedNames].filter(n => n !== savedName));
      }, 2000);

      // Refresh descriptions
      const list = await getStrategies();
      strategiesList = list;
    } catch { /* save failed */ }
    editSaving = false;
  }

  function discardStrategyEdit() {
    editingStrategy = null;
    editDirty = false;
  }

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

  // ---- Settings accordion state ----
  let showProvider = $state(false);
  let showSystem = $state(false);

  // Pre-fetch for settings panel (one-time on mount, best effort)
  let settingsLoaded = false;
  $effect(() => {
    if (settingsLoaded) return;
    settingsLoaded = true;
    Promise.all([getSettings(), getProviders(), getApiKey()])
      .then(([s, p, k]) => { settings = s; providers = p; apiKeyStatus = k; })
      .catch(() => {});
    // preferencesStore.init() is called from +layout.svelte — no duplicate here
  });

  // Auto-refresh history when real-time events arrive from any source
  $effect(() => {
    const handler = () => { historyLoaded = false; };
    window.addEventListener('optimization-event', handler);
    return () => window.removeEventListener('optimization-event', handler);
  });

  // React to strategy file changes (created/modified/deleted on disk)
  $effect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (!detail?.name) return;

      // Suppress toasts for names we just saved via UI
      if (suppressedNames.has(detail.name)) return;

      addToast(detail.action, detail.name);

      // Re-fetch strategies list
      getStrategies()
        .then((list: any[]) => { strategiesList = list; })
        .catch(() => {});
    };
    window.addEventListener('strategy-changed', handler);
    return () => window.removeEventListener('strategy-changed', handler);
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

  // Lazy GitHub auth check — when user navigates to GitHub or Settings panel
  let githubChecked = $state(false);
  $effect(() => {
    if ((active === 'github' || active === 'settings') && !githubChecked) {
      githubChecked = true;
      githubStore.checkAuth().catch(() => {});
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
    if (forgeStore.status !== 'idle' && forgeStore.status !== 'complete' && forgeStore.status !== 'error') {
      forgeStore.cancel();
    }
    try {
      const opt = await getOptimization(item.trace_id);
      forgeStore.loadFromRecord(opt);
      editorStore.openResult(item.id);
      window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
    } catch {
      forgeStore.prompt = item.raw_prompt;
      forgeStore.status = 'idle';
      window.dispatchEvent(new CustomEvent('switch-activity', { detail: 'editor' }));
    }
  }

  function selectStrategy(id: string) {
    if (id === 'auto') {
      // "auto" = null (let analyzer decide). Toggle off → null, toggle on → null.
      forgeStore.strategy = null;
    } else {
      // Toggle: click same strategy again → deselect back to auto (null)
      forgeStore.strategy = forgeStore.strategy === id ? null : id;
    }
  }

  // Check if a strategy row should be highlighted as active
  function isStrategyActive(name: string): boolean {
    if (name === 'auto') return forgeStore.strategy === null;
    return forgeStore.strategy === name;
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
        {#if strategiesList.length === 0}
          <p class="empty-note">No strategy files found. Add .md files to prompts/strategies/ to define optimization strategies.</p>
        {/if}
        {#each strategiesList as strat (strat.name)}
          <!-- Single-line strategy row -->
          <div
            class="strat-row"
            class:strat-row--active={isStrategyActive(strat.name)}
            role="button"
            tabindex="0"
            onclick={() => selectStrategy(strat.name)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectStrategy(strat.name); } }}
            title={strat.description}
          >
            <span class="strat-name">{strat.name}</span>
            <span class="strat-tag">{strat.tagline ?? ''}</span>
            <button
              class="strat-edit"
              onclick={(e: MouseEvent) => { e.stopPropagation(); openStrategyEditor(strat.name); }}
              title="Edit template"
            >
              {editingStrategy === strat.name ? '×' : '⋮'}
            </button>
          </div>

          <!-- Inline editor (expands below the row) -->
          {#if editingStrategy === strat.name}
            <div class="strategy-editor">
              <span class="strategy-file-path">prompts/strategies/{strat.name}.md</span>
              <textarea
                class="strategy-textarea"
                value={editContent}
                oninput={(e) => { editContent = (e.target as HTMLTextAreaElement).value; editDirty = true; }}
                spellcheck="false"
              ></textarea>
              <div class="strategy-editor-actions">
                <button
                  class="action-btn action-btn--primary"
                  onclick={saveStrategyEdit}
                  disabled={editSaving || !editDirty}
                >
                  {editSaving ? 'Saving...' : 'SAVE'}
                </button>
                <button class="action-btn" onclick={discardStrategyEdit}>
                  DISCARD
                </button>
              </div>
            </div>
          {/if}
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
        <!-- Models (always visible — primary control) -->
        <div class="sub-section">
          <span class="sub-heading">Models</span>
          <div class="info-block">
            {#each [
              { label: 'Analyzer', phase: 'analyzer' },
              { label: 'Optimizer', phase: 'optimizer' },
              { label: 'Scorer', phase: 'scorer' },
            ] as { label, phase }}
              <div class="info-row">
                <span class="info-key">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.models[phase as keyof typeof preferencesStore.models]}
                  onchange={(e) => preferencesStore.setModel(phase, (e.target as HTMLSelectElement).value)}
                >
                  <option value="opus">Opus</option>
                  <option value="sonnet">Sonnet</option>
                  <option value="haiku">Haiku</option>
                </select>
              </div>
            {/each}
          </div>
        </div>

        <!-- Pipeline (always visible — primary control) -->
        <div class="sub-section">
          <span class="sub-heading">Pipeline</span>
          <div class="info-block">
            {#each [
              { label: 'Codebase explore', key: 'enable_explore' },
              { label: 'Quality scoring', key: 'enable_scoring' },
              { label: 'Adaptation state', key: 'enable_adaptation' },
            ] as { label, key }}
              <div class="info-row">
                <span class="info-key">{label}</span>
                <button
                  class="toggle-track"
                  class:toggle-track--on={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline]}
                  onclick={() => preferencesStore.setPipelineToggle(key, !preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline])}
                  role="switch"
                  aria-checked={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline]}
                  aria-label="Toggle {label}"
                >
                  <span class="toggle-thumb"></span>
                </button>
              </div>
            {/each}
            {#if preferencesStore.isLeanMode}
              <div class="info-row">
                <span class="lean-badge">LEAN MODE</span>
              </div>
            {/if}
          </div>
        </div>

        <!-- Defaults (always visible — primary control) -->
        <div class="sub-section">
          <span class="sub-heading">Defaults</span>
          <div class="info-block">
            <div class="info-row">
              <span class="info-key">Strategy</span>
              <select
                class="pref-select"
                value={preferencesStore.defaultStrategy}
                onchange={(e) => preferencesStore.setDefaultStrategy((e.target as HTMLSelectElement).value)}
              >
                {#each strategiesList as strat (strat.name)}
                  <option value={strat.name}>{strat.tagline ? `${strat.name} — ${strat.tagline}` : strat.name}</option>
                {/each}
              </select>
            </div>
          </div>
        </div>

        <!-- Provider + API Key (collapsible — secondary) -->
        <div class="sub-section">
          <button
            class="accordion-heading"
            onclick={() => showProvider = !showProvider}
            aria-expanded={showProvider}
          >
            <span class="accordion-arrow" class:accordion-arrow--open={showProvider}>&#x25B8;</span>
            <span class="sub-heading">Provider</span>
            <span class="accordion-summary">
              {providers?.active_provider ?? '—'}
              {#if apiKeyStatus?.configured}
                <span style="color: var(--color-neon-green);">&#x2713;</span>
              {/if}
            </span>
          </button>
          {#if showProvider}
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
              <div class="info-row">
                <span class="info-key">API key</span>
                <span class="info-val font-mono" style="color: {apiKeyStatus?.configured ? 'var(--color-neon-green)' : 'var(--color-text-dim)'};">
                  {apiKeyStatus?.configured ? apiKeyStatus.masked_key || 'configured' : 'not set'}
                </span>
              </div>
            </div>
            <form class="api-key-form" onsubmit={(e: Event) => { e.preventDefault(); handleSetApiKey(); }} autocomplete="off">
              <input type="text" name="username" value="anthropic-api-key" autocomplete="username" class="sr-only" tabindex="-1" aria-hidden="true" />
              <input
                class="api-key-input"
                type="password"
                name="password"
                placeholder="sk-..."
                autocomplete="new-password"
                bind:value={apiKeyInput}
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
            </form>
            {#if apiKeyError}
              <p class="empty-note" style="color: var(--color-neon-red);">{apiKeyError}</p>
            {/if}
          {/if}
        </div>

        <!-- System config (collapsible — tertiary) -->
        {#if settings}
          <div class="sub-section">
            <button
              class="accordion-heading"
              onclick={() => showSystem = !showSystem}
              aria-expanded={showSystem}
            >
              <span class="accordion-arrow" class:accordion-arrow--open={showSystem}>&#x25B8;</span>
              <span class="sub-heading">System</span>
            </button>
            {#if showSystem}
              <div class="info-block">
                <div class="info-row">
                  <span class="info-key">Max chars</span>
                  <span class="info-val font-mono">{settings.max_raw_prompt_chars.toLocaleString()}</span>
                </div>
                <div class="info-row">
                  <span class="info-key">Embedding</span>
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
                <div class="info-row">
                  <span class="info-key">Scoring</span>
                  <span class="info-val font-mono">hybrid</span>
                </div>
              </div>
            {/if}
          </div>
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
    min-height: 0;
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
    min-height: 0;
  }

  /* ---- Strategy rows (single-line, compact) ---- */
  .strat-row {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 22px;
    padding: 0 6px;
    background: transparent;
    border: 1px solid transparent;
    cursor: pointer;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .strat-row:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .strat-row--active {
    border-color: var(--color-neon-cyan);
    background: rgba(0, 229, 255, 0.04);
  }

  .strat-name {
    font-size: 11px;
    font-family: var(--font-sans);
    font-weight: 400;
    color: var(--color-text-primary);
    white-space: nowrap;
    flex-shrink: 0;
  }

  .strat-row--active .strat-name {
    color: var(--color-neon-cyan);
  }

  .strat-tag {
    font-size: 9px;
    font-family: var(--font-mono);
    color: rgba(122, 122, 158, 0.6);
    white-space: nowrap;
    flex: 1;
    min-width: 0;
  }

  .strat-edit {
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    background: transparent;
    border: none;
    padding: 0 2px;
    height: 16px;
    line-height: 16px;
    cursor: pointer;
    opacity: 0;
    flex-shrink: 0;
    transition: opacity 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .strat-row:hover .strat-edit {
    opacity: 1;
  }

  .strat-edit:hover {
    color: var(--color-neon-cyan);
  }

  .strategy-editor {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 6px 6px;
    border: 1px solid var(--color-border-subtle);
    border-top: none;
    background: var(--color-bg-card);
  }

  .strategy-file-path {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    padding: 2px 0;
  }

  .strategy-textarea {
    width: 100%;
    min-height: 200px;
    max-height: 400px;
    resize: vertical;
    font-family: var(--font-mono);
    font-size: 10px;
    line-height: 1.5;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    padding: 6px;
    tab-size: 2;
  }

  .strategy-textarea:focus {
    border-color: rgba(0, 229, 255, 0.3);
    outline: none;
  }

  .strategy-editor-actions {
    display: flex;
    align-items: center;
    gap: 4px;
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

  .row-badge {
    font-size: 9px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
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
    margin-bottom: 6px;
  }

  .info-row {
    display: flex;
    align-items: center;
    height: 20px;
    gap: 6px;
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
    margin-bottom: 6px;
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
    height: 20px;
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
    height: 20px;
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

  /* ---- Preference selects ---- */
  .pref-select {
    height: 20px;
    padding: 0 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    cursor: pointer;
    appearance: none;
    -webkit-appearance: none;
    min-width: 80px;
  }

  .pref-select:focus {
    border-color: rgba(0, 229, 255, 0.3);
    outline: none;
  }

  /* ---- Toggle switches ---- */
  .toggle-track {
    width: 28px;
    height: 14px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    position: relative;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
    flex-shrink: 0;
    padding: 0;
  }

  .toggle-track--on {
    background: rgba(0, 229, 255, 0.15);
    border-color: var(--color-neon-cyan);
  }

  .toggle-thumb {
    width: 10px;
    height: 10px;
    background: var(--color-text-dim);
    position: absolute;
    top: 1px;
    left: 1px;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .toggle-track--on .toggle-thumb {
    left: 15px;
    background: var(--color-neon-cyan);
  }

  /* ---- Lean mode badge ---- */
  .lean-badge {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-neon-yellow);
    border: 1px solid var(--color-neon-yellow);
    padding: 0 4px;
    line-height: 16px;
    letter-spacing: 0.08em;
  }

  /* ---- Accordion headings (progressive disclosure) ---- */
  .accordion-heading {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
    height: 20px;
    padding: 0;
    background: transparent;
    border: none;
    cursor: pointer;
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .accordion-heading:hover {
    background: transparent;
    border-color: transparent;
  }

  .accordion-heading:hover .sub-heading {
    color: var(--color-text-primary);
  }

  .accordion-arrow {
    font-size: 10px;
    color: var(--color-text-dim);
    transition: transform 200ms cubic-bezier(0.16, 1, 0.3, 1);
    flex-shrink: 0;
    width: 10px;
    text-align: center;
  }

  .accordion-arrow--open {
    transform: rotate(90deg);
  }

  .accordion-summary {
    margin-left: auto;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }
</style>
