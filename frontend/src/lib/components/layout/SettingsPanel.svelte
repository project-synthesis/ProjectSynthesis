<script lang="ts">
  /**
   * SettingsPanel — sidebar Settings tab.
   *
   * Tier-morphing: same slot renders Context / Models / Effort / Pipeline /
   * Defaults / Provider-Connection-Routing / System — each sub-section
   * branches off `routing.isPassthrough | routing.isSampling | internal`.
   * Keeps effort degradation effect + API-key lifecycle + collapsible
   * Provider/System accordions.
   *
   * Extracted from Navigator.svelte. `strategies` comes from the parent so
   * the Defaults dropdown stays in lockstep with StrategiesPanel.
   */
  import type { SettingsResponse, ProvidersResponse, ApiKeyStatus, StrategyInfo } from '$lib/api/client';
  import { getSettings, getProviders, getApiKey, setApiKey, deleteApiKey } from '$lib/api/client';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { editorStore } from '$lib/stores/editor.svelte';
  import { preferencesStore } from '$lib/stores/preferences.svelte';
  import { githubStore } from '$lib/stores/github.svelte';
  import { passthroughGuide } from '$lib/stores/passthrough-guide.svelte';
  import { samplingGuide } from '$lib/stores/sampling-guide.svelte';
  import { routing } from '$lib/stores/routing.svelte';
  import { rateLimitStore } from '$lib/stores/rate-limit.svelte';
  import { addToast } from '$lib/stores/toast.svelte';
  import { forceSamplingTooltip, forcePassthroughTooltip } from '$lib/utils/mcp-tooltips';
  import { STAT_TOOLTIPS } from '$lib/utils/metric-tooltips';
  import { ROUTING_TOOLTIPS, SCORING_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import { tooltip } from '$lib/actions/tooltip';

  interface Props {
    active: boolean;
    strategies: StrategyInfo[];
  }

  let { active, strategies }: Props = $props();

  const activeResult = $derived(editorStore.activeResult ?? forgeStore.result);
  const settingsModels = $derived(activeResult?.models_by_phase ?? null);
  const settingsModelHeading = $derived(settingsModels ? 'Models' : 'IDE Model');

  let settings = $state<SettingsResponse | null>(null);
  let providers = $state<ProvidersResponse | null>(null);
  let apiKeyStatus = $state<ApiKeyStatus | null>(null);
  let apiKeyInput = $state('');
  let apiKeyError = $state<string | null>(null);
  let apiKeySaving = $state(false);
  let apiKeyDeleting = $state(false);
  let confirmingDelete = $state(false);
  let confirmDeleteTimer: ReturnType<typeof setTimeout> | null = null;

  let showProvider = $state(false);
  let showSystem = $state(false);
  // Rate-limits accordion: auto-opens when a limit becomes active so
  // users can immediately see the detail card without clicking. The
  // accordion stays open across limit-clear so users aren't surprised
  // by it collapsing mid-glance.
  let showRateLimits = $state(false);
  $effect(() => {
    if (rateLimitStore.isAnyActive) showRateLimits = true;
  });
  const rateLimitActiveCount = $derived(rateLimitStore.activeList.length);

  function providerLabel(p: string): string {
    // Plan-agnostic label -- the Claude CLI provider works against any
    // Anthropic plan (Pro / Team / Enterprise / MAX / Bedrock / Vertex).
    // Don't bake a specific plan name into UI labels.
    switch (p) {
      case 'claude_cli':
        return 'Claude CLI';
      case 'anthropic_api':
        return 'Anthropic API';
      default:
        return p;
    }
  }

  function formatRateLimitWait(seconds: number): string {
    if (seconds < 60) return `${seconds}s`;
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    if (m < 60) return `${m}m ${s}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
  }

  // One-time prefetch on mount (best effort)
  let settingsLoaded = false;
  $effect(() => {
    if (settingsLoaded) return;
    settingsLoaded = true;
    Promise.all([getSettings(), getProviders(), getApiKey()])
      .then(([s, p, k]) => {
        settings = s;
        providers = p;
        apiKeyStatus = k;
      })
      .catch(() => {});
  });

  // Lazy GitHub auth check when this panel becomes active
  let githubChecked = false;
  $effect(() => {
    if (active && !githubChecked) {
      githubChecked = true;
      githubStore.checkAuth().catch(() => {});
    }
  });

  const forceSamplingDisabled = $derived(
    !preferencesStore.pipeline.force_sampling &&
      (forgeStore.samplingCapable !== true || preferencesStore.pipeline.force_passthrough),
  );
  const forcePassthroughDisabled = $derived(
    !preferencesStore.pipeline.force_passthrough &&
      (forgeStore.samplingCapable === true || preferencesStore.pipeline.force_sampling),
  );

  // ---- Model catalog helpers ----
  const _FALLBACK_EFFORTS = ['low', 'medium', 'high'];
  function tierFor(phase: 'analyzer' | 'optimizer' | 'scorer'): string {
    return preferencesStore.models[phase];
  }
  function catalogEntry(tier: string) {
    return settings?.model_catalog?.find((t) => t.tier === tier) ?? null;
  }
  function supportedEffortsFor(phase: 'analyzer' | 'optimizer' | 'scorer'): string[] {
    const entry = catalogEntry(tierFor(phase));
    if (!entry) return _FALLBACK_EFFORTS;
    return entry.supported_efforts;
  }

  const _PHASE_TO_EFFORT_KEY = {
    analyzer: 'analyzer_effort',
    optimizer: 'optimizer_effort',
    scorer: 'scorer_effort',
  } as const;
  const _EFFORT_ORDER = ['low', 'medium', 'high', 'xhigh', 'max'];
  function degradeEffort(current: string, supported: string[]): string {
    if (supported.includes(current)) return current;
    const idx = _EFFORT_ORDER.indexOf(current);
    for (let i = idx - 1; i >= 0; i--) {
      if (supported.includes(_EFFORT_ORDER[i])) return _EFFORT_ORDER[i];
    }
    if (supported.includes('high')) return 'high';
    return supported[0] ?? 'high';
  }
  $effect(() => {
    if (!settings?.model_catalog) return;
    for (const phase of ['analyzer', 'optimizer', 'scorer'] as const) {
      const supported = supportedEffortsFor(phase);
      if (supported.length === 0) continue;
      const effortKey = _PHASE_TO_EFFORT_KEY[phase];
      const current = preferencesStore.pipeline[effortKey];
      if (!supported.includes(current)) {
        const next = degradeEffort(current, supported);
        if (next !== current) {
          void preferencesStore.setEffort(effortKey, next);
        }
      }
    }
  });

  // Cleanup confirmation timer on teardown
  $effect(() => {
    return () => {
      if (confirmDeleteTimer) clearTimeout(confirmDeleteTimer);
    };
  });

  async function handleSetApiKey(): Promise<void> {
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
    getProviders()
      .then((p) => {
        providers = p;
      })
      .catch((e) => console.debug('Provider refresh failed:', e));
  }

  async function handleDeleteApiKey(): Promise<void> {
    apiKeyError = null;
    apiKeyDeleting = true;
    if (confirmDeleteTimer) {
      clearTimeout(confirmDeleteTimer);
      confirmDeleteTimer = null;
    }
    try {
      apiKeyStatus = await deleteApiKey();
      addToast('deleted', 'API key removed');
    } catch (err: unknown) {
      apiKeyError = err instanceof Error ? err.message : 'Failed to remove API key';
    } finally {
      apiKeyDeleting = false;
      confirmingDelete = false;
    }
    getProviders()
      .then((p) => {
        providers = p;
      })
      .catch((e) => console.debug('Provider refresh failed:', e));
  }
</script>

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
            <span class="data-label">Strategy Intel</span>
            <button
              class="toggle-track toggle-track--yellow"
              class:toggle-track--on={preferencesStore.pipeline.enable_strategy_intelligence}
              onclick={() => preferencesStore.setPipelineToggle('enable_strategy_intelligence', !preferencesStore.pipeline.enable_strategy_intelligence)}
              role="switch"
              aria-checked={preferencesStore.pipeline.enable_strategy_intelligence}
              aria-label="Toggle Strategy Intelligence"
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
            { label: 'Analyzer', phase: 'analyzer' as const },
            { label: 'Optimizer', phase: 'optimizer' as const },
            { label: 'Scorer', phase: 'scorer' as const },
          ] as { label, phase }}
            <div class="data-row">
              <span class="data-label">{label}</span>
              <select
                class="pref-select"
                aria-label="{label} model"
                value={preferencesStore.models[phase]}
                onchange={(e) => preferencesStore.setModel(phase, (e.target as HTMLSelectElement).value)}
              >
                {#if settings?.model_catalog && settings.model_catalog.length > 0}
                  {#each settings.model_catalog as tierInfo (tierInfo.tier)}
                    <option value={tierInfo.tier}>{tierInfo.label}</option>
                  {/each}
                {:else}
                  <option value="opus">opus</option>
                  <option value="sonnet">sonnet</option>
                  <option value="haiku">haiku</option>
                {/if}
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
        {#if !routing.isPassthrough}
          {#each [
            { label: 'Explore', key: 'enable_explore' },
            { label: 'Scoring', key: 'enable_scoring' },
            { label: 'Strategy Intel', key: 'enable_strategy_intelligence' },
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
            disabled={forcePassthroughDisabled || rateLimitStore.isAnyActive}
            use:tooltip={rateLimitStore.isAnyActive ? 'Passthrough engaged (Rate Limit Active)' : forcePassthroughTooltip(forcePassthroughDisabled)}
            style={(forcePassthroughDisabled || rateLimitStore.isAnyActive) ? 'opacity: 0.4; cursor: not-allowed;' : undefined}
          >
            <span class="toggle-thumb"></span>
          </button>
        </div>
      </div>
    </div>

    <!-- Effort — internal tier only -->
    {#if !routing.isPassthrough && !routing.isSampling}
      <div class="sub-section">
        <span class="sub-heading sub-heading--tier">Effort</span>
        <div class="card-terminal">
          {#each [
            { label: 'Analyzer', phase: 'analyzer' as const, key: 'analyzer_effort' as const },
            { label: 'Optimizer', phase: 'optimizer' as const, key: 'optimizer_effort' as const },
            { label: 'Scorer', phase: 'scorer' as const, key: 'scorer_effort' as const },
          ] as { label, phase, key }}
            {@const efforts = supportedEffortsFor(phase)}
            {@const haikuBacked = efforts.length === 0}
            <div class="data-row">
              <span class="data-label">{label}</span>
              <select
                class="pref-select"
                aria-label="{label} effort"
                value={haikuBacked ? '' : (preferencesStore.pipeline[key] as string)}
                disabled={haikuBacked}
                title={haikuBacked ? 'Haiku ignores the effort parameter' : undefined}
                onchange={(e) => preferencesStore.setEffort(key, (e.target as HTMLSelectElement).value)}
              >
                {#if haikuBacked}
                  <option value="">—</option>
                {:else}
                  {#each efforts as level (level)}
                    <option value={level}>{level}</option>
                  {/each}
                {/if}
              </select>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    <!-- Defaults -->
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
            {#each strategies as strat (strat.name)}
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

    <!-- Provider / Connection / Routing (collapsible) -->
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

    <!-- Rate limits (collapsible) -- always visible so users have a
         single place to check provider rate-limit state, even when no
         limit is currently active. v0.4.12: SSE-driven via
         rate_limit_active / rate_limit_cleared events. When a limit is
         active, the workbench renders a global banner above the editor;
         this card is the canonical detail surface. -->
    <div class="sub-section">
      <button
        class="accordion-heading"
        onclick={() => showRateLimits = !showRateLimits}
        aria-expanded={showRateLimits}
      >
        <span class="accordion-arrow" class:accordion-arrow--open={showRateLimits}>&#x25B8;</span>
        <span class="sub-heading sub-heading--tier">Rate limits</span>
        <span class="accordion-summary">
          {#if rateLimitStore.isAnyActive}
            <span style="color: var(--color-neon-amber, #f59e0b);">{rateLimitActiveCount} active</span>
          {:else}
            none
          {/if}
        </span>
      </button>
      {#if showRateLimits}
        <div class="card-terminal">
          {#if rateLimitStore.isAnyActive}
            {#each rateLimitStore.activeList as entry (entry.provider)}
              <div class="data-row">
                <span class="data-label">{providerLabel(entry.provider)}</span>
                <span class="data-value font-mono" style="color: var(--color-neon-amber, #f59e0b);">
                  {#if entry.seconds_remaining != null}
                    {formatRateLimitWait(entry.seconds_remaining)}
                  {:else}
                    rate-limited
                  {/if}
                </span>
              </div>
              {#if entry.reset_at_iso}
                <div class="data-row">
                  <span class="data-label" style="opacity: 0.6;">↳ resets at</span>
                  <span class="data-value font-mono" style="opacity: 0.8;">
                    {new Date(entry.reset_at_iso).toLocaleString()}
                  </span>
                </div>
              {/if}
            {/each}
            <div class="data-row" style="margin-top: 0.5em;">
              <span class="data-label">Fallback</span>
              <span class="data-value font-mono neon-yellow">passthrough (heuristic-only)</span>
            </div>
          {:else}
            <div class="data-row">
              <span class="data-label">Status</span>
              <span class="data-value font-mono" style="color: var(--color-neon-green);">
                clear
              </span>
            </div>
            <div class="data-row" style="opacity: 0.6;">
              <span class="data-label">Behavior</span>
              <span class="data-value">
                When a provider returns 429 mid-batch, prompts continue in
                passthrough mode (heuristic scoring) until the limit lifts.
                Banner above the editor shows live countdown.
              </span>
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <!-- System (collapsible) -->
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

<style>
  .sub-section {
    margin-bottom: 6px;
  }

  .sub-section > .sub-heading {
    display: block;
    padding: 0 6px;
    margin-bottom: 4px;
  }

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
    transition: border-color var(--duration-hover) var(--ease-spring),
                color var(--duration-hover) var(--ease-spring);
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
    border-color: color-mix(in srgb, var(--color-neon-red) 30%, transparent);
  }

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

  .toggle-track {
    width: 28px;
    height: 14px;
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    position: relative;
    transition: all var(--duration-hover) var(--ease-spring);
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
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .toggle-track--on .toggle-thumb {
    left: 15px;
    background: var(--tier-accent, var(--color-neon-cyan));
  }

  .toggle-track--green.toggle-track--on {
    background: color-mix(in srgb, var(--color-neon-green) 15%, transparent);
    border-color: var(--color-neon-green);
  }

  .toggle-track--green.toggle-track--on .toggle-thumb {
    background: var(--color-neon-green);
  }

  .toggle-track--yellow.toggle-track--on {
    background: color-mix(in srgb, var(--color-neon-yellow) 15%, transparent);
    border-color: var(--color-neon-yellow);
  }

  .toggle-track--yellow.toggle-track--on .toggle-thumb {
    background: var(--color-neon-yellow);
  }

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
    background: color-mix(in srgb, var(--color-neon-orange) 6%, transparent);
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
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .accordion-heading:hover {
    background: color-mix(in srgb, var(--color-bg-hover) 50%, transparent);
    border-color: transparent;
  }

  .accordion-heading:hover .sub-heading {
    color: var(--color-text-primary);
  }

  .accordion-arrow {
    font-size: 10px;
    color: var(--color-text-dim);
    transition: transform var(--duration-hover) var(--ease-spring);
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
