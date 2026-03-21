<script lang="ts">
  import PatternNavigator from './PatternNavigator.svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { addToast, type ToastAction } from '$lib/stores/toast.svelte';
  import { scoreColor, taxonomyColor } from '$lib/utils/colors';
  import { formatScore } from '$lib/utils/formatting';
  import { forceSamplingTooltip, forcePassthroughTooltip } from '$lib/utils/mcp-tooltips';
  import { getSettings, getProviders, getHistory, getOptimization, getApiKey, setApiKey, deleteApiKey, getStrategies, getStrategy, updateStrategy } from '$lib/api/client';
  import type { SettingsResponse, ProvidersResponse, HistoryItem, ApiKeyStatus, StrategyInfo } from '$lib/api/client';

  type Activity = 'editor' | 'history' | 'patterns' | 'github' | 'settings';

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
      .then((list) => { strategiesList = list; })
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

  // ---- Derived disabled states for force toggles ----
  // A toggle that is already ON must always be interactive so the user can turn it OFF.
  // The disabled condition only prevents turning a toggle ON.
  let forceSamplingDisabled = $derived(
    !preferencesStore.pipeline.force_sampling &&
    (forgeStore.samplingCapable !== true || forgeStore.mcpDisconnected || preferencesStore.pipeline.force_passthrough)
  );
  let forcePassthroughDisabled = $derived(
    !preferencesStore.pipeline.force_passthrough &&
    ((forgeStore.samplingCapable === true && !forgeStore.mcpDisconnected) || preferencesStore.pipeline.force_sampling)
  );

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

      const verb = detail.action === 'created' ? 'added' : detail.action === 'deleted' ? 'removed' : 'updated';
      addToast(detail.action as ToastAction, `Strategy ${verb}: ${detail.name}`);

      // Re-fetch strategies list
      getStrategies()
        .then((list) => { strategiesList = list; })
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
    } catch (err: unknown) {
      apiKeyError = err instanceof Error ? err.message : 'Failed to set API key';
    } finally {
      apiKeySaving = false;
    }
  }

  async function handleDeleteApiKey() {
    apiKeyError = null;
    try {
      apiKeyStatus = await deleteApiKey();
    } catch (err: unknown) {
      apiKeyError = err instanceof Error ? err.message : 'Failed to remove API key';
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
        .catch((err: unknown) => {
          historyError = err instanceof Error ? err.message : 'Failed to load history';
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
      editorStore.openResult(item.id, opt);
      // Stay on the current sidebar panel — don't switch away from history
    } catch {
      forgeStore.prompt = item.raw_prompt;
      forgeStore.status = 'idle';
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
              <span class="row-prompt">{item.intent_label || (item.raw_prompt ? item.raw_prompt.slice(0, 60) + (item.raw_prompt.length > 60 ? '..' : '') : 'Untitled')}</span>
              <div class="history-meta">
                {#if item.domain}
                  <span class="row-domain font-mono" style="color: {taxonomyColor(item.domain)};">{item.domain}</span>
                {/if}
                <span class="row-badge font-mono">{item.strategy_used || 'auto'}</span>
                <span
                  class="row-score font-mono"
                  style="color: {scoreColor(item.overall_score)};"
                >
                  {item.overall_score != null ? formatScore(item.overall_score) : '—'}
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

  <!-- ============ PATTERNS PANEL ============ -->
  {:else if active === 'patterns'}
    <PatternNavigator />

  <!-- ============ GITHUB PANEL ============ -->
  {:else if active === 'github'}
    <div class="panel">
      <header class="panel-header">
        <span class="section-heading">GitHub</span>
      </header>
      <div class="panel-body">
        {#if githubStore.linkedRepo}
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label">Repo</span>
              <span class="data-value font-mono">{githubStore.linkedRepo.full_name}</span>
            </div>
            <div class="data-row">
              <span class="data-label">Branch</span>
              <span class="data-value font-mono">
                {githubStore.linkedRepo.branch ?? githubStore.linkedRepo.default_branch}
              </span>
            </div>
            {#if githubStore.linkedRepo.language}
              <div class="data-row">
                <span class="data-label">Lang</span>
                <span class="data-value">{githubStore.linkedRepo.language}</span>
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
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label">User</span>
              <span class="data-value font-mono">{githubStore.user.login}</span>
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
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', phase: 'analyzer' },
              { label: 'Optimizer', phase: 'optimizer' },
              { label: 'Scorer', phase: 'scorer' },
            ] as { label, phase }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.models[phase as keyof typeof preferencesStore.models]}
                  onchange={(e) => preferencesStore.setModel(phase, (e.target as HTMLSelectElement).value)}
                >
                  <option value="opus">opus</option>
                  <option value="sonnet">sonnet</option>
                  <option value="haiku">haiku</option>
                </select>
              </div>
            {/each}
          </div>
        </div>

        <!-- Pipeline (always visible — primary control) -->
        <div class="sub-section">
          <span class="sub-heading">Pipeline</span>
          <div class="card-terminal">
            {#each [
              { label: 'Explore', key: 'enable_explore' },
              { label: 'Scoring', key: 'enable_scoring' },
              { label: 'Adaptation', key: 'enable_adaptation' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
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
              <div class="data-row">
                <span class="badge-neon">LEAN MODE</span>
              </div>
            {/if}
            <!-- Force sampling — rendered separately for disabled-state support -->
            <div class="data-row">
              <span class="data-label" title="Use IDE's LLM for the 3-phase pipeline via MCP sampling">Force IDE sampling</span>
              <button
                class="toggle-track"
                class:toggle-track--on={preferencesStore.pipeline.force_sampling}
                onclick={() => preferencesStore.setPipelineToggle('force_sampling', !preferencesStore.pipeline.force_sampling)}
                role="switch"
                aria-checked={preferencesStore.pipeline.force_sampling}
                aria-label="Toggle Force IDE sampling"
                disabled={forceSamplingDisabled}
                title={forceSamplingTooltip(forceSamplingDisabled)}
                style={forceSamplingDisabled ? 'opacity: 0.4; cursor: not-allowed;' : undefined}
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
            <!-- Force passthrough — manual override, always available except when sampling works -->
            <div class="data-row">
              <span class="data-label" title="Bypass all pipelines — returns assembled template for manual processing">Force passthrough</span>
              <button
                class="toggle-track"
                class:toggle-track--on={preferencesStore.pipeline.force_passthrough}
                onclick={() => preferencesStore.setPipelineToggle('force_passthrough', !preferencesStore.pipeline.force_passthrough)}
                role="switch"
                aria-checked={preferencesStore.pipeline.force_passthrough}
                aria-label="Toggle Force passthrough"
                disabled={forcePassthroughDisabled}
                title={forcePassthroughTooltip(forcePassthroughDisabled)}
                style={forcePassthroughDisabled ? 'opacity: 0.4; cursor: not-allowed;' : undefined}
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
          </div>
        </div>

        <!-- Defaults (always visible — primary control) -->
        <div class="sub-section">
          <span class="sub-heading">Defaults</span>
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label">Strategy</span>
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
            {#if preferencesStore.pipeline.force_sampling && forgeStore.samplingCapable === true}
              <div class="data-row">
                <span
                  class="badge-neon"
                  style="color: {forgeStore.mcpDisconnected ? 'var(--color-text-dim)' : 'var(--color-accent, #00e5ff)'}; border-color: {forgeStore.mcpDisconnected ? 'var(--color-border-subtle)' : 'var(--color-accent, #00e5ff)'}; {forgeStore.mcpDisconnected ? 'opacity: 0.4;' : ''}"
                >{forgeStore.mcpDisconnected ? 'SAMPLING (disconnected)' : 'SAMPLING'}</span>
              </div>
            {/if}
            {#if preferencesStore.pipeline.force_passthrough}
              <div class="data-row">
                <span class="badge-neon" style="color: var(--color-neon-yellow); border-color: var(--color-neon-yellow);">PASSTHROUGH</span>
              </div>
            {/if}
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
            <div class="card-terminal">
              <div class="data-row">
                <span class="data-label">Active</span>
                <span class="data-value font-mono" style="color: var(--color-neon-cyan);">
                  {providers?.active_provider ?? '—'}
                </span>
              </div>
              {#if providers?.available?.length}
                <div class="data-row">
                  <span class="data-label">Available</span>
                  <span class="data-value">{providers.available.join(', ')}</span>
                </div>
              {/if}
              <div class="data-row">
                <span class="data-label">API key</span>
                <span class="data-value font-mono" style="color: {apiKeyStatus?.configured ? 'var(--color-neon-green)' : 'var(--color-text-dim)'};">
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
                  {apiKeySaving ? 'SAVING...' : 'SET KEY'}
                </button>
                {#if apiKeyStatus?.configured}
                  <button class="action-btn" onclick={handleDeleteApiKey}>
                    REMOVE
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
              <div class="card-terminal">
                <div class="data-row">
                  <span class="data-label">Max chars</span>
                  <span class="data-value font-mono">{settings.max_raw_prompt_chars.toLocaleString()}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Embedding</span>
                  <span class="data-value font-mono">{settings.embedding_model}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Rate limit</span>
                  <span class="data-value font-mono">{settings.optimize_rate_limit}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Retention</span>
                  <span class="data-value font-mono">{settings.trace_retention_days}d</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Scoring</span>
                  <span class="data-value font-mono">hybrid</span>
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

  .row-domain {
    font-size: 9px;
    flex-shrink: 0;
  }

  .row-score {
    font-size: 10px;
    flex-shrink: 0;
  }

  /* ---- Info blocks ---- */

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
