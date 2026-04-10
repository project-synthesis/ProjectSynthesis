<script lang="ts">
  import ClusterNavigator from './ClusterNavigator.svelte';
  import { githubStore, type TreeNode } from '$lib/stores/github.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { addToast, type ToastAction } from '$lib/stores/toast.svelte';
  import { scoreColor, taxonomyColor } from '$lib/utils/colors';
  import { formatScore, formatRelativeTime } from '$lib/utils/formatting';
  import { forceSamplingTooltip, forcePassthroughTooltip } from '$lib/utils/mcp-tooltips';
  import { STAT_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { ROUTING_TOOLTIPS, SCORING_TOOLTIPS, STRATEGY_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import { tooltip } from '$lib/actions/tooltip';
  import { passthroughGuide } from '$lib/stores/passthrough-guide.svelte';
  import { samplingGuide } from '$lib/stores/sampling-guide.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { getSettings, getProviders, getHistory, getOptimization, updateOptimization, getApiKey, setApiKey, deleteApiKey, getStrategies, getStrategy, updateStrategy, listProjects } from '$lib/api/client';
  import type { SettingsResponse, ProvidersResponse, HistoryItem, ApiKeyStatus, StrategyInfo, ProjectInfo } from '$lib/api/client';

  // Tab-aware active result for showing per-optimization models in Settings
  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);
  // Active trace for history row highlighting
  const activeTraceId = $derived(activeResult?.trace_id ?? forgeStore.traceId ?? null);
  // When viewing a completed optimization, show its persisted models instead of live phaseModels
  const settingsModels = $derived(activeResult?.models_by_phase ?? null);
  const settingsModelHeading = $derived(settingsModels ? 'Models' : 'IDE Model');

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

  // ---- History rename state ----
  let renamingOptId = $state<string | null>(null);
  let renameOptValue = $state('');
  let renameOptSaving = $state(false);

  function startOptRename(e: MouseEvent, item: HistoryItem): void {
    e.stopPropagation();
    renamingOptId = item.id;
    renameOptValue = item.intent_label || '';
  }

  function cancelOptRename(): void {
    renamingOptId = null;
    renameOptValue = '';
  }

  async function submitOptRename(): Promise<void> {
    const id = renamingOptId;
    const trimmed = renameOptValue.trim();
    if (!id || !trimmed || renameOptSaving) return;
    renameOptSaving = true;
    try {
      await updateOptimization(id, { intent_label: trimmed });
      // Update local state
      historyItems = historyItems.map(h =>
        h.id === id ? { ...h, intent_label: trimmed } : h
      );
      // Update editor tab titles if this optimization is open
      editorStore.updateTabTitle(id, trimmed);
      renamingOptId = null;
      renameOptValue = '';
    } catch {
      // Keep rename input open on error so user can retry
      addToast('deleted', 'Rename failed');
    }
    renameOptSaving = false;
  }

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

  // ---- GitHub repo picker state ----
  let githubTab = $state<'info' | 'files'>('info');
  let repoPickerOpen = $state(false);
  let repoSearch = $state('');
  let projects = $state<ProjectInfo[]>([]);
  const projectLabelMap = $derived<Record<string, string>>(
    Object.fromEntries(projects.map(p => [p.id, p.label]))
  );
  let selectedProjectId = $state<string | null>(null);
  let linkingRepo = $state<string | null>(null);

  let filteredRepos = $derived(
    githubStore.repos.filter(r =>
      !repoSearch || r.full_name.toLowerCase().includes(repoSearch.toLowerCase())
    ).slice(0, 20)
  );

  async function openRepoPicker() {
    repoPickerOpen = true;
    repoSearch = '';
    selectedProjectId = null;
    linkingRepo = null;
    githubStore.loadRepos();
    try {
      projects = await listProjects();
    } catch {
      projects = [];
    }
  }

  async function confirmLinkRepo(fullName: string) {
    // If there are existing non-Legacy projects, show project selection
    const nonLegacy = projects.filter(p => p.label !== 'Legacy');
    if (nonLegacy.length > 0 && !linkingRepo) {
      linkingRepo = fullName;
      return;
    }
    // Link with selected project (or null for auto-create)
    await githubStore.linkRepo(fullName, selectedProjectId || undefined);
    repoPickerOpen = false;
    linkingRepo = null;
    selectedProjectId = null;
    if (githubStore.linkedRepo) {
      addToast('created', `Linked ${githubStore.linkedRepo.full_name}`);
    }
  }

  // ---- Settings accordion state ----
  let showProvider = $state(false);
  let showSystem = $state(false);

  // ---- Derived disabled states for force toggles ----
  // A toggle that is already ON must always be interactive so the user can turn it OFF.
  // The disabled condition only prevents turning a toggle ON.
  //
  // mcpDisconnected is NOT a gate for Force IDE sampling — the toggle expresses
  // user *intent*.  The auto-fallback mechanism uses the internal provider while
  // MCP is idle and auto-restores when it reconnects.  The only hard gate is
  // samplingCapable (was the MCP client ever detected with sampling support?).
  let forceSamplingDisabled = $derived(
    !preferencesStore.pipeline.force_sampling &&
    (forgeStore.samplingCapable !== true || preferencesStore.pipeline.force_passthrough)
  );
  // No pending state needed — toggle auto-syncs with sampling capability
  let forcePassthroughDisabled = $derived(
    !preferencesStore.pipeline.force_passthrough &&
    (forgeStore.samplingCapable === true || preferencesStore.pipeline.force_sampling)
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

  // Load project labels for history row badges (one-time, matches settingsLoaded pattern)
  let projectsLoaded = false;
  $effect(() => {
    if (projectsLoaded) return;
    projectsLoaded = true;
    listProjects().then(p => { projects = p; }).catch(() => {});
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
      addToast('deleted', 'Failed to load optimization');
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

{#snippet treeNode(node: TreeNode, depth: number)}
  {#if node.type === 'dir'}
    <button
      class="tree-item tree-item--dir"
      style="padding-left: {8 + depth * 12}px"
      onclick={() => githubStore.toggleTreeNode(node.path)}
    >
      <span class="tree-arrow">{node.expanded ? '▾' : '▸'}</span>
      <span class="tree-name">{node.name}</span>
    </button>
    {#if node.expanded && node.children}
      {#each node.children as child}
        {@render treeNode(child, depth + 1)}
      {/each}
    {/if}
  {:else}
    <button
      class="tree-item tree-item--file"
      class:tree-item--active={githubStore.selectedFile === node.path}
      style="padding-left: {8 + depth * 12}px"
      onclick={() => githubStore.loadFileContent(node.path)}
    >
      <span class="tree-name">{node.name}</span>
      {#if node.size}
        <span class="tree-size">{node.size > 1024 ? `${(node.size / 1024).toFixed(0)}K` : `${node.size}B`}</span>
      {/if}
    </button>
  {/if}
{/snippet}

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
            use:tooltip={strat.description}
          >
            <span class="strat-name">{strat.name}</span>
            <span class="strat-tag">{strat.tagline ?? ''}</span>
            <button
              class="strat-edit"
              onclick={(e: MouseEvent) => { e.stopPropagation(); openStrategyEditor(strat.name); }}
              use:tooltip={STRATEGY_TOOLTIPS.edit_template}
              aria-label="Edit template"
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
            {#if renamingOptId === item.id}
              <div class="row-item history-row" style="--accent: {taxonomyColor(item.domain)};">
                <span class="row-prompt-line">
                  {#if item.task_type && item.task_type !== 'general' && TASK_TYPE_ABBREV[item.task_type]}
                    <span class="row-type">{TASK_TYPE_ABBREV[item.task_type]}</span>
                  {/if}
                  <!-- svelte-ignore a11y_autofocus -->
                  <form class="rename-form-inline" onsubmit={(e) => { e.preventDefault(); submitOptRename(); }}>
                    <input
                      class="rename-input-inline"
                      type="text"
                      bind:value={renameOptValue}
                      onkeydown={(e) => { if (e.key === 'Escape') cancelOptRename(); }}
                      autofocus
                      aria-label="Rename optimization"
                    />
                    <button
                      class="rename-btn-inline save"
                      type="submit"
                      disabled={renameOptSaving || !renameOptValue.trim()}
                      use:tooltip={'Save'}
                      aria-label="Save"
                    >&#x2713;</button>
                    <button
                      class="rename-btn-inline cancel"
                      type="button"
                      onclick={() => cancelOptRename()}
                      use:tooltip={'Cancel'}
                      aria-label="Cancel"
                    >×</button>
                  </form>
                  <span class="row-time">{formatRelativeTime(item.created_at)}</span>
                </span>
              </div>
            {:else}
              <button
                class="row-item history-row"
                class:history-row--active={activeTraceId === item.trace_id}
                style="--accent: {taxonomyColor(item.domain)};"
                onclick={() => loadHistoryItem(item)}
              >
                <span class="row-prompt-line">
                  {#if item.task_type && item.task_type !== 'general' && TASK_TYPE_ABBREV[item.task_type]}
                    <span class="row-type">{TASK_TYPE_ABBREV[item.task_type]}</span>
                  {/if}
                  <span
                    class="row-prompt"
                    role="textbox"
                    tabindex="-1"
                    ondblclick={(e) => startOptRename(e, item)}
                    use:tooltip={'Double-click to rename'}
                  >{item.intent_label || (item.raw_prompt ? item.raw_prompt.slice(0, 60) + (item.raw_prompt.length > 60 ? '..' : '') : 'Untitled')}</span>
                  <span class="row-time">{formatRelativeTime(item.created_at)}</span>
                </span>
                <div class="history-meta">
                  {#if item.project_id && projectLabelMap[item.project_id]}
                    <span class="row-project font-mono" use:tooltip={`Project: ${projectLabelMap[item.project_id]}`}>
                      {projectLabelMap[item.project_id].slice(0, 2).toUpperCase()}
                    </span>
                  {/if}
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
                      use:tooltip={item.feedback_rating === 'thumbs_up' ? STRATEGY_TOOLTIPS.feedback_positive : STRATEGY_TOOLTIPS.feedback_negative}
                    >{item.feedback_rating === 'thumbs_up' ? '\u2191' : '\u2193'}</span>
                  {/if}
                </div>
              </button>
            {/if}
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
        {#if githubStore.connectionState === 'ready'}
          <span class="connection-badge" style="color: var(--color-text-dim)">connected</span>
        {:else if githubStore.connectionState === 'linked'}
          <span class="connection-badge" style="color: var(--color-neon-cyan)">indexing</span>
        {:else if githubStore.connectionState === 'expired'}
          <span class="connection-badge" style="color: var(--color-neon-red)">expired</span>
        {:else if githubStore.connectionState === 'authenticated'}
          <span class="connection-badge" style="color: var(--color-neon-yellow)">no repo</span>
        {/if}
      </header>
      <div class="panel-body">
        {#if githubStore.linkedRepo}
          <!-- Tab selector: Info / Files -->
          <div class="github-tabs">
            <button
              class="github-tab"
              class:github-tab--active={githubTab === 'info'}
              onclick={() => { githubTab = 'info'; }}
            >Info</button>
            <button
              class="github-tab"
              class:github-tab--active={githubTab === 'files'}
              onclick={() => { githubTab = 'files'; if (githubStore.fileTree.length === 0) githubStore.loadFileTree(); githubStore.loadIndexStatus(); }}
            >Files
              {#if githubStore.indexStatus?.status === 'building'}
                <span class="index-badge index-badge--building">...</span>
              {:else if githubStore.indexStatus?.file_count}
                <span class="index-badge">{githubStore.indexStatus.file_count}</span>
              {/if}
            </button>
          </div>

          {#if githubTab === 'info'}
            <!-- Auth-expired banner with reconnect (inside linkedRepo branch) -->
            {#if githubStore.connectionState === 'expired'}
              <div class="auth-expired-banner">
                <span class="error-note" style="margin: 0;">GitHub session expired</span>
                <button
                  class="action-btn action-btn--primary"
                  onclick={() => githubStore.reconnect()}
                >Reconnect</button>
              </div>
            {/if}
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
              <div class="data-row">
                <span class="data-label">Project</span>
                <span class="data-value font-mono">{githubStore.linkedRepo.project_label ?? '(pending)'}</span>
              </div>
              {#if githubStore.indexStatus}
                <div class="data-row">
                  <span class="data-label">Index</span>
                  <span class="data-value" class:data-value--cyan={githubStore.indexStatus.status === 'ready'}>
                    {githubStore.indexStatus.status} ({githubStore.indexStatus.file_count} files)
                  </span>
                </div>
              {/if}
            </div>
            <div class="picker-actions">
              <button class="action-btn" onclick={() => githubStore.unlinkRepo()}>Unlink</button>
              <button class="action-btn" onclick={() => githubStore.reindex()}>Reindex</button>
            </div>

          {:else}
            <!-- Files tab -->
            {#if githubStore.selectedFile}
              <!-- File content viewer -->
              <div class="file-viewer">
                <div class="file-viewer-header">
                  <span class="file-viewer-path font-mono">{githubStore.selectedFile}</span>
                  <button class="file-viewer-close" onclick={() => githubStore.closeFile()}>x</button>
                </div>
                {#if githubStore.fileLoading}
                  <p class="empty-note">Loading...</p>
                {:else if githubStore.fileContent !== null}
                  <pre class="file-viewer-content"><code>{githubStore.fileContent}</code></pre>
                {:else}
                  <p class="empty-note">Failed to load file.</p>
                {/if}
              </div>
            {:else}
              <!-- File tree browser -->
              {#if githubStore.treeLoading}
                <p class="empty-note">Loading file tree...</p>
              {:else if githubStore.fileTree.length === 0}
                <p class="empty-note">No files found.</p>
              {:else}
                <div class="file-tree">
                  {#each githubStore.fileTree as node}
                    {@render treeNode(node, 0)}
                  {/each}
                </div>
              {/if}
            {/if}
          {/if}
        {:else if githubStore.user}
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label">User</span>
              <span class="data-value font-mono">{githubStore.user.login}</span>
            </div>
          </div>

          {#if !repoPickerOpen}
            <button
              class="action-btn action-btn--primary"
              onclick={openRepoPicker}
            >
              Link a repository
            </button>
          {:else}
            <!-- Repo search -->
            <input
              class="search-input"
              type="text"
              placeholder="Search repos..."
              bind:value={repoSearch}
            />

            {#if linkingRepo}
              <!-- Project selection step -->
              <div class="repo-picker-project">
                <p class="picker-heading">Link <span class="font-mono">{linkingRepo}</span> to:</p>
                <label class="radio-row">
                  <input type="radio" name="project" value="" bind:group={selectedProjectId} checked />
                  <span>New project</span>
                </label>
                {#each projects.filter(p => p.label !== 'Legacy') as proj}
                  <label class="radio-row">
                    <input type="radio" name="project" value={proj.id} bind:group={selectedProjectId} />
                    <span class="font-mono">{proj.label}</span>
                    <span class="repo-meta">({proj.member_count} clusters)</span>
                  </label>
                {/each}
                <div class="picker-actions">
                  <button class="action-btn action-btn--primary" onclick={() => confirmLinkRepo(linkingRepo!)}>
                    Link
                  </button>
                  <button class="action-btn" onclick={() => { linkingRepo = null; selectedProjectId = null; }}>
                    Back
                  </button>
                </div>
              </div>
            {:else if githubStore.loading}
              <p class="empty-note">Loading repositories...</p>
            {:else if filteredRepos.length === 0}
              <p class="empty-note">{repoSearch ? 'No matching repos' : 'No repos found'}</p>
            {:else}
              <!-- Repo list -->
              <div class="repo-list">
                {#each filteredRepos as repo}
                  <button
                    class="repo-item"
                    onclick={() => confirmLinkRepo(repo.full_name)}
                  >
                    <span class="repo-name font-mono">{repo.full_name}</span>
                    {#if repo.language}
                      <span class="repo-meta">{repo.language}</span>
                    {/if}
                  </button>
                {/each}
              </div>
            {/if}

            <button class="action-btn" onclick={() => { repoPickerOpen = false; linkingRepo = null; }}>
              Cancel
            </button>
          {/if}
        {:else}
          {#if githubStore.userCode}
            <!-- Device flow: gated handoff -->
            <div class="device-flow">
              <p class="device-heading">Your authorization code:</p>
              <div class="device-code">
                <span class="device-code-text">{githubStore.userCode}</span>
              </div>
              <p class="device-instructions">
                Copy this code and enter it on GitHub to authorize access to your repositories.
              </p>
              <button
                class="action-btn action-btn--primary"
                onclick={() => {
                  navigator.clipboard.writeText(githubStore.userCode ?? '');
                  window.open(
                    githubStore.verificationUri ?? 'https://github.com/login/device',
                    '_blank',
                  );
                }}
              >
                Copy code &amp; open GitHub
              </button>
              {#if githubStore.polling}
                <p class="device-status">Waiting for authorization...</p>
              {/if}
              {#if githubStore.error}
                <p class="error-note">{githubStore.error}</p>
              {/if}
              <button class="action-btn" onclick={() => githubStore.cancelLogin()}>
                Cancel
              </button>
            </div>
          {:else}
            <p class="empty-note">Connect GitHub to link repositories for context-aware optimization.</p>
            {#if githubStore.error}
              <p class="error-note">{githubStore.error}</p>
            {/if}
            <button
              class="action-btn action-btn--primary"
              onclick={() => githubStore.login()}
            >
              Connect GitHub
            </button>
          {/if}
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
          <span class="sub-heading sub-heading--tier">Context</span>
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
        {:else if routing.isSampling}
        <div class="sub-section">
          <span class="sub-heading sub-heading--tier">{settingsModelHeading}</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', key: 'analyze' },
              { label: 'Optimizer', key: 'optimize' },
              { label: 'Scorer', key: 'score' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <span class="data-value neon-green" class:data-value--dim={!(settingsModels?.[key] ?? forgeStore.phaseModels[key])}>
                  {settingsModels?.[key] ?? forgeStore.phaseModels[key] ?? 'pending'}
                </span>
              </div>
            {/each}
          </div>
        </div>
        {:else}
        <div class="sub-section">
          <span class="sub-heading sub-heading--tier">Models</span>
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
          <span class="sub-heading sub-heading--tier">Pipeline</span>
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
                  class:toggle-track--green={routing.isSampling}
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
              <span class="data-label" use:tooltip={ROUTING_TOOLTIPS.force_sampling_label}>Force IDE sampling</span>
              <button
                class="toggle-track toggle-track--green"
                class:toggle-track--on={preferencesStore.pipeline.force_sampling}
                onclick={() => {
                  const newVal = !preferencesStore.pipeline.force_sampling;
                  preferencesStore.setPipelineToggle('force_sampling', newVal);
                  if (newVal) samplingGuide.show(true);
                }}
                role="switch"
                aria-checked={preferencesStore.pipeline.force_sampling}
                aria-label="Toggle Force IDE sampling"
                disabled={forceSamplingDisabled}
                use:tooltip={forceSamplingTooltip(forceSamplingDisabled)}
                style={forceSamplingDisabled ? 'opacity: 0.4; cursor: not-allowed;' : undefined}
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
            {#if routing.isAutoFallback}
            <div class="autofallback-notice" role="status">
              {routing.autoFallbackMessage}
            </div>
            {:else if routing.isDegraded && routing.requestedTier === 'sampling'}
            <div class="degradation-notice" role="alert">
              {routing.degradationMessage}
            </div>
            {/if}
            <!-- Force passthrough — manual override, always available except when sampling works -->
            <div class="data-row">
              <span class="data-label" use:tooltip={ROUTING_TOOLTIPS.force_passthrough_label}>Force passthrough</span>
              <button
                class="toggle-track toggle-track--yellow"
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
                use:tooltip={forcePassthroughTooltip(forcePassthroughDisabled)}
                style={forcePassthroughDisabled ? 'opacity: 0.4; cursor: not-allowed;' : undefined}
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
          </div>
        </div>

        <!-- Effort — internal tier only (passthrough + sampling handled by Routing/Connection + System) -->
        {#if !routing.isPassthrough && !routing.isSampling}
        <div class="sub-section">
          <span class="sub-heading sub-heading--tier">Effort</span>
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
            {#if routing.isSampling}
              <div class="data-row">
                <span
                  class="badge-neon"
                  style="color: var(--color-neon-green); border-color: var(--color-neon-green);"
                >VIA MCP SAMPLING</span>
              </div>
            {:else if routing.isPassthrough}
              <div class="data-row">
                <span class="badge-neon" style="color: var(--color-neon-yellow); border-color: var(--color-neon-yellow);">PASSTHROUGH</span>
              </div>
            {/if}
          </div>
        </div>

        <!-- Provider / Connection / Routing (collapsible — tier-adaptive) -->
        <div class="sub-section">
          <button
            class="accordion-heading"
            onclick={() => showProvider = !showProvider}
            aria-expanded={showProvider}
          >
            <span class="accordion-arrow" class:accordion-arrow--open={showProvider}>&#x25B8;</span>
            <span class="sub-heading sub-heading--tier"
            >{routing.isPassthrough ? 'Routing' : routing.isSampling ? 'Connection' : 'Provider'}</span>
            <span class="accordion-summary">
              {#if routing.isPassthrough}
                manual
              {:else if routing.isSampling}
                MCP {forgeStore.mcpDisconnected ? 'idle' : 'active'}
              {:else}
                {forgeStore.provider ?? '—'}
                {#if apiKeyStatus?.configured}
                  <span style="color: var(--color-neon-green);">&#x2713;</span>
                {/if}
              {/if}
            </span>
          </button>
          {#if showProvider}
            {#if routing.isPassthrough}
              <!-- PASSTHROUGH: routing overview — no LLM provider involved -->
              <div class="card-terminal">
                <div class="data-row">
                  <span class="data-label">Execution</span>
                  <span class="data-value neon-yellow">manual</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Analysis</span>
                  <span class="data-value neon-yellow">heuristic</span>
                </div>
                <div class="data-row">
                  <span class="data-label">Scoring</span>
                  <span class="data-value neon-yellow">heuristic</span>
                </div>
                {#if providers?.routing_tiers?.length}
                  <div class="data-row">
                    <span class="data-label">Tiers</span>
                    <span class="data-value">{providers.routing_tiers.join(', ')}</span>
                  </div>
                {/if}
              </div>
            {:else if routing.isSampling}
              <!-- SAMPLING: MCP connection health — IDE is the LLM provider -->
              <div class="card-terminal">
                <div class="data-row">
                  <span class="data-label">MCP status</span>
                  <span class="data-value font-mono" style="color: {forgeStore.mcpDisconnected ? 'var(--color-neon-red)' : 'var(--color-neon-green)'};">
                    {forgeStore.mcpDisconnected ? 'disconnected' : 'connected'}
                  </span>
                </div>
                <div class="data-row">
                  <span class="data-label">Sampling</span>
                  <span class="data-value font-mono" style="color: {forgeStore.samplingCapable === true ? 'var(--color-neon-green)' : 'var(--color-text-dim)'};">
                    {forgeStore.samplingCapable === true ? 'supported' : forgeStore.samplingCapable === false ? 'not supported' : 'not detected'}
                  </span>
                </div>
                <div class="data-row">
                  <span class="data-label">Fallback</span>
                  <span class="data-value font-mono" style="color: {forgeStore.provider ? 'var(--color-neon-cyan)' : 'var(--color-text-dim)'};">
                    {forgeStore.provider ?? 'none'}
                  </span>
                </div>
                {#if providers?.routing_tiers?.length}
                  <div class="data-row">
                    <span class="data-label">Tiers</span>
                    <span class="data-value">{providers.routing_tiers.join(', ')}</span>
                  </div>
                {/if}
              </div>
            {:else}
              <!-- INTERNAL: full provider instrumentation -->
              <div class="card-terminal">
                <div class="data-row">
                  <span class="data-label">Active</span>
                  <span class="data-value font-mono neon-cyan">
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
                <form class="data-row" onsubmit={(e: Event) => { e.preventDefault(); handleSetApiKey(); }} autocomplete="off">
                  <input type="text" name="username" value="anthropic-api-key" autocomplete="username" class="sr-only" tabindex="-1" aria-hidden="true" />
                  <label for="api-key-input" class="sr-only">Anthropic API key</label>
                  <input
                    id="api-key-input"
                    class="pref-input"
                    type="password"
                    name="password"
                    placeholder="sk-..."
                    autocomplete="new-password"
                    bind:value={apiKeyInput}
                  />
                  <button
                    class="pref-btn"
                    onclick={handleSetApiKey}
                    disabled={apiKeySaving || !apiKeyInput.trim()}
                    type="button"
                  >{apiKeySaving ? '...' : 'SET'}</button>
                  {#if apiKeyStatus?.configured}
                    <button
                      class="pref-btn"
                      class:pref-btn--danger={confirmingDelete}
                      disabled={apiKeyDeleting}
                      type="button"
                      onclick={() => {
                        if (confirmingDelete) {
                          handleDeleteApiKey();
                        } else {
                          confirmingDelete = true;
                          confirmDeleteTimer = setTimeout(() => { confirmingDelete = false; confirmDeleteTimer = null; }, 3000);
                        }
                      }}
                    >{apiKeyDeleting ? '...' : confirmingDelete ? 'OK?' : 'DEL'}</button>
                  {/if}
                </form>
                {#if apiKeyError}
                  <p class="empty-note" style="color: var(--color-neon-red); padding: 0 4px;">{apiKeyError}</p>
                {/if}
              </div>
            {/if}
          {/if}
        </div>

        <!-- System (collapsible — tier-adaptive) -->
        <div class="sub-section">
          <button
            class="accordion-heading"
            onclick={() => showSystem = !showSystem}
            aria-expanded={showSystem}
          >
            <span class="accordion-arrow" class:accordion-arrow--open={showSystem}>&#x25B8;</span>
            <span class="sub-heading sub-heading--tier">System</span>
            <span class="accordion-summary">v{forgeStore.version ?? '?'}</span>
          </button>
          {#if showSystem}
            {#if settings}
              {#if routing.isPassthrough}
                <!-- PASSTHROUGH: minimal system info — no LLM phases -->
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
                    <span class="data-label">Embedding</span>
                    <span class="data-value font-mono">{settings.embedding_model}</span>
                  </div>
                  <div class="data-row">
                    <span class="data-label">Database</span>
                    <span class="data-value font-mono">{settings.database_engine}</span>
                  </div>
                  <div class="data-row">
                    <span class="data-label">Scoring</span>
                    <span class="data-value neon-yellow" use:tooltip={SCORING_TOOLTIPS.heuristic}>heuristic</span>
                  </div>
                  {#if forgeStore.scoreHealth}
                    <div class="data-row">
                      <span class="data-label">Score mean</span>
                      <span class="data-value font-mono" use:tooltip={STAT_TOOLTIPS.mean}>{forgeStore.scoreHealth.last_n_mean.toFixed(1)}</span>
                    </div>
                    <div class="data-row">
                      <span class="data-label" use:tooltip={STAT_TOOLTIPS.stddev}>Score stddev</span>
                      <span class="data-value font-mono"
                        style={forgeStore.scoreHealth.clustering_warning ? 'color: var(--color-neon-red)' : ''}>
                        {forgeStore.scoreHealth.last_n_stddev.toFixed(2)}
                      </span>
                    </div>
                  {/if}
                </div>
              {:else if routing.isSampling}
                <!-- SAMPLING: system info + IDE-driven scoring -->
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
                  {#if forgeStore.phaseDurations}
                    {#each Object.entries(forgeStore.phaseDurations) as [phase, ms]}
                      <div class="data-row">
                        <span class="data-label">{phase}</span>
                        <span class="data-value font-mono">{ms.toLocaleString()}ms</span>
                      </div>
                    {/each}
                  {/if}
                  <div class="data-row">
                    <span class="data-label">Scoring</span>
                    <span
                      class="data-value neon-green"
                      class:data-value--dim={!forgeStore.phaseModels['score'] && !forgeStore.result?.scoring_mode}
                      use:tooltip={forgeStore.result?.scoring_mode || 'pending'}
                    >
                      {forgeStore.phaseModels['score'] || forgeStore.result?.scoring_mode || 'pending'}
                    </span>
                  </div>
                  {#if forgeStore.scoreHealth}
                    <div class="data-row">
                      <span class="data-label">Score mean</span>
                      <span class="data-value font-mono" use:tooltip={STAT_TOOLTIPS.mean}>{forgeStore.scoreHealth.last_n_mean.toFixed(1)}</span>
                    </div>
                    <div class="data-row">
                      <span class="data-label" use:tooltip={STAT_TOOLTIPS.stddev}>Score stddev</span>
                      <span class="data-value font-mono"
                        style={forgeStore.scoreHealth.clustering_warning ? 'color: var(--color-neon-red)' : ''}>
                        {forgeStore.scoreHealth.last_n_stddev.toFixed(2)}
                      </span>
                    </div>
                  {/if}
                </div>
              {:else}
                <!-- INTERNAL: full system instrumentation -->
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
                  {#if forgeStore.phaseDurations}
                    {#each Object.entries(forgeStore.phaseDurations) as [phase, ms]}
                      <div class="data-row">
                        <span class="data-label">{phase}</span>
                        <span class="data-value font-mono">{ms.toLocaleString()}ms</span>
                      </div>
                    {/each}
                  {/if}
                  <div class="data-row">
                    <span class="data-label">Scoring</span>
                    <span class="data-value font-mono" use:tooltip={SCORING_TOOLTIPS.hybrid}>hybrid</span>
                  </div>
                  {#if forgeStore.scoreHealth}
                    <div class="data-row">
                      <span class="data-label">Score mean</span>
                      <span class="data-value font-mono" use:tooltip={STAT_TOOLTIPS.mean}>{forgeStore.scoreHealth.last_n_mean.toFixed(1)}</span>
                    </div>
                    <div class="data-row">
                      <span class="data-label" use:tooltip={STAT_TOOLTIPS.stddev}>Score stddev</span>
                      <span class="data-value font-mono"
                        style={forgeStore.scoreHealth.clustering_warning ? 'color: var(--color-neon-red)' : ''}>
                        {forgeStore.scoreHealth.last_n_stddev.toFixed(2)}
                      </span>
                    </div>
                  {/if}
                </div>
              {/if}
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
    border-color: var(--tier-accent, var(--color-neon-cyan));
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.04);
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
    color: var(--tier-accent, var(--color-neon-cyan));
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
    color: var(--tier-accent, var(--color-neon-cyan));
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
    border-color: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.3);
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
    border: 1px solid transparent;
    border-left: 1px solid var(--accent, transparent);
    transition: color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                background 200ms cubic-bezier(0.16, 1, 0.3, 1),
                box-shadow 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .history-row:hover {
    border-color: var(--color-border-accent);
    border-left-color: var(--accent, transparent);
  }

  .history-row:active {
    background: var(--color-bg-hover);
  }

  .history-row--active {
    box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--accent, var(--color-neon-cyan)) 40%, transparent);
    border-color: transparent;
    border-left-color: var(--accent, var(--color-neon-cyan));
    background: color-mix(in srgb, var(--accent, var(--color-neon-cyan)) 4%, transparent);
  }

  .history-row--active .row-prompt {
    color: var(--accent, var(--color-neon-cyan));
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

  .rename-form-inline {
    display: flex;
    align-items: center;
    gap: 2px;
    flex: 1;
    min-width: 0;
  }

  .rename-input-inline {
    flex: 1;
    min-width: 0;
    height: 18px;
    font-size: 10px;
    font-family: inherit;
    background: var(--color-bg-secondary);
    border: 1px solid var(--accent, var(--color-border));
    color: var(--color-text-primary);
    padding: 0 4px;
    outline: none;
  }

  .rename-btn-inline {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 18px;
    font-size: 10px;
    border: none;
    background: transparent;
    cursor: pointer;
    padding: 0;
    transition: color 200ms;
  }

  .rename-btn-inline.save {
    color: var(--color-neon-green);
  }

  .rename-btn-inline.save:hover {
    color: var(--accent, var(--color-neon-cyan));
  }

  .rename-btn-inline.save:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .rename-btn-inline.cancel {
    color: var(--color-text-dim);
  }

  .rename-btn-inline.cancel:hover {
    color: var(--color-text-primary);
  }

  .history-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    width: 100%;
  }

  .auth-expired-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 8px;
    margin-bottom: 6px;
    border: 1px solid var(--color-neon-red);
    background: transparent;
  }
  .connection-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    margin-left: auto;
  }
  .row-project {
    font-size: 9px;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    padding: 0 3px;
    white-space: nowrap;
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
    border-left: 1px solid var(--color-border-subtle);
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





  /* ---- Inline input (API key) — matches pref-select exactly ---- */
  .pref-input {
    flex: 1;
    min-width: 0;
    height: 20px;
    padding: 0 4px;
    font-family: var(--font-mono);
    font-size: 11px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-primary);
    outline: none;
    appearance: none;
    -webkit-appearance: none;
  }

  .pref-input:focus {
    border-color: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.3);
  }

  .pref-input::placeholder {
    color: var(--color-text-dim);
  }

  /* ---- Inline button (SET/DEL) — matches pref-select density ---- */
  .pref-btn {
    height: 20px;
    padding: 0 6px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-secondary);
    font-family: var(--font-mono);
    font-size: 10px;
    line-height: 18px;
    cursor: pointer;
    white-space: nowrap;
    transition: border-color 200ms cubic-bezier(0.16, 1, 0.3, 1),
                color 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .pref-btn:hover {
    border-color: var(--color-border-accent);
    color: var(--color-text-primary);
  }

  .pref-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .pref-btn--danger {
    color: var(--color-neon-red);
    border-color: rgba(255, 51, 102, 0.3);
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
    border-color: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.3);
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
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.15);
    border-color: var(--tier-accent, var(--color-neon-cyan));
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
    background: var(--tier-accent, var(--color-neon-cyan));
  }

  /* Green toggle variant — sampling tier accent */
  .toggle-track--green.toggle-track--on {
    background: rgba(34, 255, 136, 0.15);
    border-color: var(--color-neon-green);
  }

  .toggle-track--green.toggle-track--on .toggle-thumb {
    background: var(--color-neon-green);
  }

  /* Yellow toggle variant — passthrough tier accent */
  .toggle-track--yellow.toggle-track--on {
    background: rgba(251, 191, 36, 0.15);
    border-color: var(--color-neon-yellow);
  }

  .toggle-track--yellow.toggle-track--on .toggle-thumb {
    background: var(--color-neon-yellow);
  }

  /* ---- Tier-adaptive utilities ---- */
  .sub-heading--tier {
    color: var(--tier-accent, var(--color-neon-cyan));
  }


  .degradation-notice {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-neon-orange);
    padding: 3px 6px;
    margin: 2px 0;
    border-left: 1px solid var(--color-neon-orange);
    background: rgba(255, 140, 0, 0.06);
    line-height: 1.4;
  }

  .autofallback-notice {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--tier-accent, var(--color-neon-cyan));
    padding: 3px 6px;
    margin: 2px 0;
    border-left: 1px solid rgba(var(--tier-accent-rgb, 0, 229, 255), 0.4);
    background: rgba(var(--tier-accent-rgb, 0, 229, 255), 0.04);
    line-height: 1.4;
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

  /* Repo picker */
  .search-input {
    width: 100%;
    padding: 4px 8px;
    background: transparent;
    border: 1px solid var(--color-border);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: 11px;
    margin-bottom: 6px;
    outline: none;
  }
  .search-input:focus {
    border-color: var(--tier-accent, var(--color-neon-cyan));
  }
  .repo-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
    max-height: 240px;
    overflow-y: auto;
    margin-bottom: 6px;
  }
  .repo-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 6px;
    background: transparent;
    border: none;
    color: var(--color-text);
    cursor: pointer;
    text-align: left;
    font-size: 11px;
  }
  .repo-item:hover {
    background: var(--color-surface-hover);
  }
  .repo-name {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .repo-meta {
    font-size: 10px;
    color: var(--color-text-dim);
    margin-left: 6px;
    flex-shrink: 0;
  }
  .repo-picker-project {
    margin-bottom: 6px;
  }
  .picker-heading {
    font-size: 11px;
    color: var(--color-text);
    margin-bottom: 4px;
  }
  .radio-row {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 0;
    font-size: 11px;
    color: var(--color-text);
    cursor: pointer;
  }
  .radio-row input[type="radio"] {
    accent-color: var(--tier-accent, var(--color-neon-cyan));
  }
  .picker-actions {
    display: flex;
    gap: 6px;
    margin-top: 6px;
  }

  /* Device flow */
  .device-flow {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 12px 0;
  }
  .device-heading {
    font-size: 11px;
    color: var(--color-text-dim);
    margin: 0;
  }
  .device-code {
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .device-code-text {
    font-family: var(--font-mono);
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 4px;
    color: var(--tier-accent, var(--color-neon-cyan));
    padding: 6px 12px;
    border: 1px solid var(--color-border);
  }
  .device-instructions {
    font-size: 10px;
    color: var(--color-text-dim);
    text-align: center;
    margin: 0;
    line-height: 1.4;
  }
  .device-status {
    font-size: 10px;
    color: var(--color-text-dim);
    margin: 0;
  }
  .error-note {
    font-size: 10px;
    color: var(--color-neon-red);
    margin: 0 0 4px;
  }

  /* GitHub tabs */
  .github-tabs {
    display: flex;
    gap: 0;
    margin-bottom: 8px;
    border-bottom: 1px solid var(--color-border);
  }
  .github-tab {
    flex: 1;
    padding: 4px 0;
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--color-text-dim);
    font-size: 10px;
    font-family: var(--font-mono);
    cursor: pointer;
    text-align: center;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 4px;
  }
  .github-tab:hover { color: var(--color-text); }
  .github-tab--active {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-bottom-color: var(--tier-accent, var(--color-neon-cyan));
  }
  .index-badge {
    font-size: 9px;
    padding: 0 3px;
    border: 1px solid var(--color-border);
    color: var(--color-text-dim);
  }
  .index-badge--building {
    color: var(--color-neon-yellow);
    border-color: var(--color-neon-yellow);
  }
  .data-value--cyan { color: var(--tier-accent, var(--color-neon-cyan)); }

  /* File tree */
  .file-tree {
    flex: 1;
    overflow-y: auto;
    min-height: 0;
  }
  .tree-item {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    font-size: 11px;
    font-family: var(--font-mono);
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    width: 100%;
    text-align: left;
  }
  .tree-item--dir {
    background: transparent;
    border: none;
    cursor: pointer;
    color: var(--tier-accent, var(--color-neon-cyan));
  }
  .tree-item--dir:hover { background: var(--color-surface-hover); }
  .tree-item--file {
    color: var(--color-text-dim);
    background: transparent;
    border: none;
    cursor: pointer;
  }
  .tree-item--file:hover { background: var(--color-surface-hover); }
  .tree-item--active { background: var(--color-surface-hover); color: var(--color-text); }
  .tree-arrow { font-size: 8px; width: 8px; flex-shrink: 0; }
  .tree-name { overflow: hidden; text-overflow: ellipsis; }
  .tree-size {
    font-size: 9px;
    color: var(--color-text-dim);
    margin-left: auto;
    flex-shrink: 0;
  }

  /* File viewer */
  .file-viewer { display: flex; flex-direction: column; height: 100%; }
  .file-viewer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 8px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .file-viewer-path {
    font-size: 10px;
    color: var(--tier-accent, var(--color-neon-cyan));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .file-viewer-close {
    background: transparent;
    border: none;
    color: var(--color-text-dim);
    cursor: pointer;
    font-size: 11px;
    padding: 0 4px;
  }
  .file-viewer-close:hover { color: var(--color-text); }
  .file-viewer-content {
    overflow: auto;
    flex: 1;
    min-height: 0;
    font-size: 10px;
    line-height: 1.5;
    padding: 8px;
    margin: 0;
    color: var(--color-text-dim);
    white-space: pre;
    tab-size: 2;
  }
</style>
