<script lang="ts">
  import ClusterNavigator from './ClusterNavigator.svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { addToast, type ToastAction } from '$lib/stores/toast.svelte';
  import { scoreColor, taxonomyColor } from '$lib/utils/colors';
  import { formatScore, formatRelativeTime } from '$lib/utils/formatting';
  import { forceSamplingTooltip, forcePassthroughTooltip } from '$lib/utils/mcp-tooltips';
  import { passthroughGuide } from '$lib/stores/passthrough-guide.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { getSettings, getProviders, getHistory, getOptimization, getApiKey, setApiKey, deleteApiKey, getStrategies, getStrategy, updateStrategy } from '$lib/api/client';
  import type { SettingsResponse, ProvidersResponse, HistoryItem, ApiKeyStatus, StrategyInfo } from '$lib/api/client';

  const TASK_TYPE_ABBREV: Record<string, string> = {
    coding: 'COD', writing: 'WRT', analysis: 'ANL',
    creative: 'CRE', data: 'DAT', system: 'SYS',
  };

  type Activity = 'editor' | 'history' | 'clusters' | 'github' | 'settings';

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
      addToast('deleted', 'Failed to load strategy template');
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
    } catch { addToast('deleted', 'Strategy save failed'); }
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
  let completedItems = $derived(historyItems.filter(i => i.status === 'completed'));

  // ---- Settings panel state ----
  let settings = $state<SettingsResponse | null>(null);
  let providers = $state<ProvidersResponse | null>(null);

  // ---- API Key state ----
  let apiKeyStatus = $state<ApiKeyStatus | null>(null);
  let apiKeyInput = $state('');
  let apiKeyError = $state<string | null>(null);
  let apiKeySaving = $state(false);
  let apiKeyDeleting = $state(false);
  let confirmingDelete = $state(false);
  let confirmDeleteTimer: ReturnType<typeof setTimeout> | null = null;

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
      addToast('created', 'API key saved');
    } catch (err: unknown) {
      apiKeyError = err instanceof Error ? err.message : 'Failed to set API key';
    } finally {
      apiKeySaving = false;
    }
    // Re-fetch available providers list non-blocking (key already saved above)
    getProviders().then((p) => { providers = p; }).catch((e) => console.debug('Provider refresh failed:', e));
  }

  async function handleDeleteApiKey() {
    apiKeyError = null;
    apiKeyDeleting = true;
    if (confirmDeleteTimer) { clearTimeout(confirmDeleteTimer); confirmDeleteTimer = null; }
    try {
      apiKeyStatus = await deleteApiKey();
      addToast('deleted', 'API key removed');
    } catch (err: unknown) {
      apiKeyError = err instanceof Error ? err.message : 'Failed to remove API key';
    } finally {
      apiKeyDeleting = false;
      confirmingDelete = false;
    }
    // Re-fetch available providers list non-blocking (key already deleted above)
    getProviders().then((p) => { providers = p; }).catch((e) => console.debug('Provider refresh failed:', e));
  }

  // Cleanup confirmation timer on component teardown
  $effect(() => {
    return () => { if (confirmDeleteTimer) clearTimeout(confirmDeleteTimer); };
  });

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
      forgeStore.loadFromRecord(opt); // caches result via editorStore.cacheResult internally
      editorStore.openResult(opt.id); // open tab — data already cached by loadFromRecord
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
          {#each {length: 4} as _}
            <div class="skeleton-row">
              <div class="skeleton-bar skeleton-wide"></div>
              <div class="skeleton-bar skeleton-narrow"></div>
            </div>
          {/each}
        {:else if historyItems.length === 0}
          <p class="empty-note">No optimizations yet.</p>
        {:else}
          {#each completedItems as item (item.id)}
            <button class="row-item history-row" style="--accent: {taxonomyColor(item.domain)};" onclick={() => loadHistoryItem(item)}>
              <span class="row-prompt-line">
                {#if item.task_type && item.task_type !== 'general' && TASK_TYPE_ABBREV[item.task_type]}
                  <span class="row-type">{TASK_TYPE_ABBREV[item.task_type]}</span>
                {/if}
                <span class="row-prompt">{item.intent_label || (item.raw_prompt ? item.raw_prompt.slice(0, 60) + (item.raw_prompt.length > 60 ? '..' : '') : 'Untitled')}</span>
                <span class="row-time">{formatRelativeTime(item.created_at)}</span>
              </span>
              <div class="history-meta">
                <span class="row-badge font-mono">{item.strategy_used || 'auto'}</span>
                <span
                  class="row-score font-mono"
                  style="color: {scoreColor(item.overall_score)};"
                >
                  {item.overall_score != null ? formatScore(item.overall_score) : '—'}
                </span>
                {#if item.feedback_rating}
                  <span
                    class="row-feedback font-mono"
                    style="color: {item.feedback_rating === 'thumbs_up' ? 'var(--color-neon-cyan)' : 'var(--color-neon-red)'};"
                    title={item.feedback_rating === 'thumbs_up' ? 'Positive' : 'Negative'}
                  >{item.feedback_rating === 'thumbs_up' ? '\u2191' : '\u2193'}</span>
                {/if}
              </div>
            </button>
          {/each}
          {#if completedItems.length === 0}
            <p class="empty-note">No completed optimizations yet.</p>
          {/if}
        {/if}
      </div>
    </div>

  <!-- ============ PATTERNS PANEL ============ -->
  {:else if active === 'clusters'}
    <ClusterNavigator />

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
        <!-- Models / Context — morphs by tier -->
        {#if routing.isPassthrough}
        <div class="sub-section">
          <span class="sub-heading sub-heading--passthrough">Context</span>
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label">Analysis</span>
              <span class="data-value neon-yellow">heuristic</span>
            </div>
            <div class="data-row">
              <span class="data-label">Codebase</span>
              <span class="data-value neon-yellow" class:data-value--dim={!githubStore.linkedRepo}>
                {githubStore.linkedRepo ? 'via index' : 'no repo'}
              </span>
            </div>
            <div class="data-row">
              <span class="data-label">Patterns</span>
              <span class="data-value neon-yellow">auto-injected</span>
            </div>
            <div class="data-row">
              <span class="data-label">Adaptation</span>
              <button
                class="toggle-track toggle-track--yellow"
                class:toggle-track--on={preferencesStore.pipeline.enable_adaptation}
                onclick={() => preferencesStore.setPipelineToggle('enable_adaptation', !preferencesStore.pipeline.enable_adaptation)}
                role="switch"
                aria-checked={preferencesStore.pipeline.enable_adaptation}
                aria-label="Toggle Adaptation"
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
          </div>
        </div>
        {:else}
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
        {/if}

        <!-- Pipeline (always visible — primary control) -->
        <div class="sub-section">
          <span class="sub-heading">Pipeline</span>
          <div class="card-terminal">
            <!-- Feature toggles — hidden in passthrough (no separate phases) -->
            {#if !routing.isPassthrough}
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
                  aria-checked={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline] as boolean}
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
                onclick={() => {
                  const newVal = !preferencesStore.pipeline.force_passthrough;
                  preferencesStore.setPipelineToggle('force_passthrough', newVal);
                  if (newVal) passthroughGuide.show(true);
                }}
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

        <!-- Effort / Scoring — morphs by tier -->
        {#if routing.isPassthrough}
        <div class="sub-section">
          <span class="sub-heading sub-heading--passthrough">Scoring</span>
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label">Mode</span>
              <span class="data-value neon-yellow">heuristic</span>
            </div>
          </div>
        </div>
        {:else}
        <div class="sub-section">
          <span class="sub-heading">Effort</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', key: 'analyzer_effort' },
              { label: 'Optimizer', key: 'optimizer_effort' },
              { label: 'Scorer', key: 'scorer_effort' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline] as string}
                  onchange={(e) => preferencesStore.setEffort(key, (e.target as HTMLSelectElement).value)}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="max">max</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}

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
                  style="color: {forgeStore.mcpDisconnected ? 'var(--color-text-dim)' : 'var(--color-neon-cyan)'}; border-color: {forgeStore.mcpDisconnected ? 'var(--color-border-subtle)' : 'var(--color-neon-cyan)'}; {forgeStore.mcpDisconnected ? 'opacity: 0.4;' : ''}"
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
              {forgeStore.provider ?? '—'}
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
                  {forgeStore.provider ?? '—'}
                </span>
              </div>
              {#if providers?.available?.length}
                <div class="data-row">
                  <span class="data-label">Available</span>
                  <span class="data-value">{providers.available.join(', ')}</span>
                </div>
              {/if}
              {#if providers?.routing_tiers?.length}
                <div class="data-row">
                  <span class="data-label">Tiers</span>
                  <span class="data-value">{providers.routing_tiers.join(', ')}</span>
                </div>
              {/if}
              <div class="data-row">
                <span class="data-label">API key</span>
                <span class="data-value font-mono" style="color: {apiKeyStatus?.configured ? 'var(--color-neon-green)' : 'var(--color-text-dim)'};">
                  {apiKeyStatus?.configured ? apiKeyStatus.masked_key || 'configured' : 'not set'}
                </span>
              </div>
              {#if forgeStore.avgDurationMs != null}
                <div class="data-row">
                  <span class="data-label">Avg latency</span>
                  <span class="data-value font-mono">{forgeStore.avgDurationMs}ms</span>
                </div>
              {/if}
              {#if forgeStore.recentErrors?.last_hour}
                <div class="data-row">
                  <span class="data-label">Errors (1h)</span>
                  <span class="data-value font-mono" style="color: var(--color-neon-red);">
                    {forgeStore.recentErrors.last_hour}
                  </span>
                </div>
              {/if}
            </div>
            <form class="api-key-form" onsubmit={(e: Event) => { e.preventDefault(); handleSetApiKey(); }} autocomplete="off">
              <input type="text" name="username" value="anthropic-api-key" autocomplete="username" class="sr-only" tabindex="-1" aria-hidden="true" />
              <label for="api-key-input" class="sr-only">Anthropic API key</label>
              <input
                id="api-key-input"
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
                  <button
                    class="action-btn"
                    disabled={apiKeyDeleting}
                    style={confirmingDelete ? 'color: var(--color-neon-red); border-color: var(--color-neon-red);' : ''}
                    onclick={() => {
                      if (confirmingDelete) {
                        handleDeleteApiKey();
                      } else {
                        confirmingDelete = true;
                        confirmDeleteTimer = setTimeout(() => { confirmingDelete = false; confirmDeleteTimer = null; }, 3000);
                      }
                    }}
                  >
                    {apiKeyDeleting ? 'REMOVING...' : confirmingDelete ? 'CONFIRM?' : 'REMOVE'}
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
            {#if settings}
              <div class="card-terminal">
                <div class="data-row">
                  <span class="data-label">Version</span>
                  <span class="data-value font-mono">{forgeStore.version ?? '—'}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Max chars</span>
                  <span class="data-value font-mono">{settings.max_raw_prompt_chars.toLocaleString()}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Context budget</span>
                  <span class="data-value font-mono">{settings.max_context_tokens.toLocaleString()} tokens</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Embedding</span>
                  <span class="data-value font-mono">{settings.embedding_model}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Database</span>
                  <span class="data-value font-mono">{settings.database_engine}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Optimize rate</span>
                  <span class="data-value font-mono">{settings.optimize_rate_limit}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Feedback rate</span>
                  <span class="data-value font-mono">{settings.feedback_rate_limit}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Refine rate</span>
                  <span class="data-value font-mono">{settings.refine_rate_limit}</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Retention</span>
                  <span class="data-value font-mono">{settings.trace_retention_days}d</span>
                </div>
                {#if forgeStore.phaseDurations && !routing.isPassthrough}
                  {#each Object.entries(forgeStore.phaseDurations) as [phase, ms]}
                    <div class="data-row">
                      <span class="data-label">{phase}</span>
                      <span class="data-value font-mono">{ms.toLocaleString()}ms</span>
                    </div>
                  {/each}
                {/if}
                <div class="data-row">
                  <span class="data-label">Scoring</span>
                  <span class="data-value font-mono"
                    title={routing.isPassthrough
                      ? 'Heuristic-only scoring (no LLM scorer in passthrough mode)'
                      : 'LLM + heuristic blended scores'}>
                    {routing.isPassthrough ? 'heuristic' : 'hybrid'}
                  </span>
                </div>
                {#if forgeStore.scoreHealth}
                  <div class="data-row">
                    <span class="data-label">Score mean</span>
                    <span class="data-value font-mono">{forgeStore.scoreHealth.last_n_mean.toFixed(1)}</span>
                  </div>
                  <div class="data-row">
                    <span class="data-label">Score stddev</span>
                    <span class="data-value font-mono"
                      style={forgeStore.scoreHealth.clustering_warning ? 'color: var(--color-neon-red)' : ''}>
                      {forgeStore.scoreHealth.last_n_stddev.toFixed(2)}
                    </span>
                  </div>
                {/if}
              </div>
            {:else}
              <p class="empty-note">Backend unavailable</p>
            {/if}
          {/if}
        </div>
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
    padding: 2px 6px 2px 8px;
    flex-direction: column;
    align-items: stretch;
    gap: 1px;
    border-left: 2px solid var(--accent, transparent);
  }

  .history-row:hover {
    border-left-color: var(--accent, transparent);
  }

  .row-prompt-line {
    display: flex;
    align-items: center;
    gap: 4px;
    width: 100%;
  }

  .row-type {
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    letter-spacing: 0.04em;
    flex-shrink: 0;
  }

  .row-time {
    font-size: 8px;
    font-family: var(--font-mono);
    color: var(--color-text-dim);
    margin-left: auto;
    flex-shrink: 0;
  }

  .row-prompt {
    font-size: 10px;
    font-weight: 500;
    color: var(--color-text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: left;
    flex: 1;
    min-width: 0;
  }

  .history-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
  }

  .row-score {
    font-size: 10px;
    flex-shrink: 0;
  }

  .row-feedback {
    font-size: 10px;
    flex-shrink: 0;
  }

  .skeleton-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 4px 6px 4px 10px;
    border-left: 2px solid var(--color-border-subtle);
  }

  .skeleton-bar {
    height: 8px;
    background: linear-gradient(90deg, var(--color-bg-card) 25%, var(--color-bg-hover) 50%, var(--color-bg-card) 75%);
    background-size: 200% 100%;
    animation: shimmer 1500ms ease-in-out infinite;
  }

  .skeleton-wide { width: 85%; }
  .skeleton-narrow { width: 55%; }

  @keyframes shimmer {
    0% { background-position: -200% 0; }
    100% { background-position: 200% 0; }
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

  /* Yellow toggle variant — passthrough tier accent */
  .toggle-track--yellow.toggle-track--on {
    background: rgba(251, 191, 36, 0.15);
    border-color: var(--color-neon-yellow);
  }

  .toggle-track--yellow.toggle-track--on .toggle-thumb {
    background: var(--color-neon-yellow);
  }

  /* ---- Passthrough tier utilities ---- */
  .sub-heading--passthrough {
    color: var(--color-neon-yellow);
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
