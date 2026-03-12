<script lang="ts">
  import { onDestroy } from 'svelte';
  import { slide } from 'svelte/transition';
  import { cubicOut } from 'svelte/easing';
  import { fetchSettings, updateSettings, fetchProviderStatus, fetchProviderDetect, disconnectGitHub, unlinkRepo, getGitHubLoginUrl, logoutAllDevices, logoutDevice, fetchGitHubAppConfig, saveGitHubAppConfig, fetchAuthMe, patchAuthMe, refreshGitHubToken, getProviderConfig, saveApiKey, deleteApiKey, type AppSettings, type GitHubAppConfig, type ProviderDetectResponse, type ProviderStatusResponse, type ProviderConfigResponse } from '$lib/api/client';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { auth } from '$lib/stores/auth.svelte';
  import { toast } from '$lib/stores/toast.svelte';
  import { user } from '$lib/stores/user.svelte';

  let settings = $state<AppSettings | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let saving = $state(false);

  // ── GitHub App credential management ─────────────────────────────────────
  let appConfig = $state<GitHubAppConfig | null>(null);
  let expandConfig = $state(false);
  let showReconnect = $state(false);
  let configClientId = $state('');
  let configSecret = $state('');
  let showConfigSecret = $state(false);
  let configSaving = $state(false);
  let configError = $state('');

  function cancelConfig() {
    expandConfig = false;
    showReconnect = false;
    configError = '';
    configClientId = '';
    configSecret = '';
  }

  // ── LLM Provider API key management ───────────────────────────────────────
  let providerCfg = $state<ProviderConfigResponse | null>(null);
  let expandApiKey = $state(false);
  let apiKeyInput = $state('');
  let showApiKeyInput = $state(false);
  let savingApiKey = $state(false);
  let apiKeyError = $state('');
  let deletingApiKey = $state(false);

  function cancelApiKeyEdit() {
    expandApiKey = false;
    apiKeyError = '';
    apiKeyInput = '';
    showApiKeyInput = false;
  }

  async function handleSaveApiKey() {
    apiKeyError = '';
    savingApiKey = true;
    try {
      const result = await saveApiKey(apiKeyInput.trim());
      providerCfg = {
        provider_active: result.provider_active,
        provider_available: result.provider_available,
        api_key: result.api_key,
        bootstrap_mode: false,
      };
      if (result.provider_available) {
        expandApiKey = false;
        apiKeyInput = '';
        showApiKeyInput = false;
        workbench.provider = result.provider_active as typeof workbench.provider;
        workbench.isConnected = true;
        if (result.validation_warning) {
          toast.warning(result.validation_warning);
        } else {
          toast.success('API key saved');
        }
      } else {
        apiKeyError = result.validation_warning || 'Key saved but provider could not be initialized. Check the key.';
      }
    } catch (err) {
      apiKeyError = (err as Error).message;
    } finally {
      savingApiKey = false;
    }
  }

  async function handleDeleteApiKey() {
    if (!confirm('Remove saved API key? The pipeline will stop working if no other provider is available.')) return;
    deletingApiKey = true;
    try {
      const result = await deleteApiKey();
      providerCfg = {
        provider_active: result.provider_active,
        provider_available: result.provider_available,
        api_key: result.api_key,
        bootstrap_mode: false,
      };
      toast.success('API key removed');
      if (!result.provider_available) {
        workbench.provider = 'unknown';
        workbench.isConnected = false;
      }
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      deletingApiKey = false;
    }
  }

  async function loadSettings() {
    loading = true;
    error = null;
    try {
      const [s, status, cfg, pcfg] = await Promise.all([
        fetchSettings(),
        fetchProviderStatus().catch(() => null),
        fetchGitHubAppConfig().catch(() => null),
        getProviderConfig().catch(() => null),
      ]);
      settings = s;
      if (status) workbench.isConnected = status.healthy;
      if (cfg) appConfig = cfg;
      if (pcfg) providerCfg = pcfg;
    } catch (err) {
      error = (err as Error).message;
    } finally {
      loading = false;
    }
  }

  async function handleSaveAppConfig() {
    configError = '';
    configSaving = true;
    try {
      const result = await saveGitHubAppConfig(configClientId.trim(), configSecret.trim());
      appConfig = { configured: result.configured, client_id_masked: result.client_id_masked, has_secret: result.has_secret };
      expandConfig = false;
      configClientId = '';
      configSecret = '';
      workbench.setGithubOAuthEnabled(true);
      toast.success('GitHub credentials updated');
      if (github.isConnected) {
        showReconnect = true;
      } else {
        window.location.href = getGitHubLoginUrl();
      }
    } catch (err) {
      configError = (err as Error).message;
    } finally {
      configSaving = false;
    }
  }

  async function handleToggle(key: keyof AppSettings, value: boolean) {
    if (!settings) return;
    saving = true;
    try {
      settings = await updateSettings({ [key]: value });
      toast.success('Settings saved');
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  async function handleDisconnectGitHub() {
    try {
      await disconnectGitHub();
      await unlinkRepo().catch(() => {});
      toast.success('GitHub disconnected');
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      github.disconnect();
    }
  }

  let testingProvider = $state(false);
  let providerTestResult = $state<{ healthy: boolean; message: string; providers?: string } | null>(null);

  async function handleTestConnection() {
    testingProvider = true;
    providerTestResult = null;
    try {
      const [status, detect] = await Promise.all([
        fetchProviderStatus(),
        fetchProviderDetect().catch(() => null),
      ]);
      const providerNames = detect
        ? Object.entries(detect.providers)
            .filter(([, info]) => info.available)
            .map(([name]) => name)
            .join(' + ')
        : null;
      providerTestResult = {
        healthy: status.healthy,
        message: status.message,
        providers: providerNames ?? undefined,
      };
    } catch (e) {
      providerTestResult = { healthy: false, message: (e as Error).message };
    } finally {
      testingProvider = false;
    }
  }

  let loggingOutAll = $state(false);
  let loggingOut = $state(false);
  let reconnecting = $state(false);
  let reconnectError = $state('');
  let reconnectInfo = $state('');

  const _reconnectInfoReasons = new Set(['not_expiring_soon']);
  const _reconnectReasonMessages: Record<string, string> = {
    not_expiring_soon: 'Token is still valid — no refresh needed',
    not_a_github_app_token: 'Manual refresh is only available for GitHub App tokens',
  };

  async function handleReconnectGitHub() {
    reconnecting = true;
    reconnectError = '';
    reconnectInfo = '';
    try {
      const result = await refreshGitHubToken();
      if (result.refreshed) {
        toast.success('GitHub token refreshed');
      } else {
        const reason = result.reason ?? '';
        const msg = _reconnectReasonMessages[reason] ?? 'Token refresh skipped';
        if (_reconnectInfoReasons.has(reason)) {
          reconnectInfo = msg;
        } else {
          reconnectError = msg;
        }
      }
    } catch (e) {
      reconnectError = (e as Error).message;
    } finally {
      reconnecting = false;
    }
  }

  async function handleLogoutDevice() {
    if (loggingOut) return;
    loggingOut = true;
    try {
      const result = await logoutDevice();
      toast.success(`Signed out (${result.revoked_count} session${result.revoked_count !== 1 ? 's' : ''} revoked)`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      loggingOut = false;
    }
  }

  async function handleLogoutAllDevices() {
    if (loggingOutAll) return;
    loggingOutAll = true;
    try {
      const result = await logoutAllDevices();
      toast.success(`Logged out of ${result.revoked_sessions} device${result.revoked_sessions !== 1 ? 's' : ''}`);
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      loggingOutAll = false;
    }
  }

  async function handleNumberChange(key: keyof AppSettings, value: number) {
    if (!settings) return;
    saving = true;
    try {
      settings = await updateSettings({ [key]: value });
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  // ── Profile editing ───────────────────────────────────────────────────────
  let editField = $state<'display_name' | 'email' | null>(null);
  let editValue = $state('');
  let editOriginal = $state('');
  let savingField = $state(false);
  let savedField = $state<'display_name' | 'email' | null>(null);
  let savedFieldTimer: ReturnType<typeof setTimeout> | null = null;

  onDestroy(() => {
    if (savedFieldTimer) clearTimeout(savedFieldTimer);
  });

  function focusEl(node: HTMLElement) {
    node.focus();
  }

  function startEdit(field: 'display_name' | 'email') {
    editField = field;
    const val = field === 'display_name' ? (user.displayName ?? '') : (user.email ?? '');
    editValue = val;
    editOriginal = val;
  }

  function cancelEdit() {
    editField = null;
    editValue = '';
    editOriginal = '';
  }

  async function saveField(field: 'display_name' | 'email') {
    const trimmed = editValue.trim();
    if (!trimmed || trimmed === editOriginal) {
      cancelEdit();
      return;
    }
    savingField = true;
    try {
      await patchAuthMe({ [field]: trimmed });
      if (field === 'display_name') {
        user.displayName = trimmed;
      } else {
        user.email = trimmed;
      }
      editField = null;
      editValue = '';
      editOriginal = '';
      savedField = field;
      if (savedFieldTimer) clearTimeout(savedFieldTimer);
      savedFieldTimer = setTimeout(() => { savedField = null; }, 1500);
    } catch (e) {
      editValue = editOriginal;
      cancelEdit();
      toast.error(field === 'display_name' ? 'Failed to save display name' : 'Failed to save email');
    } finally {
      savingField = false;
    }
  }

  async function handleFieldBlur(field: 'display_name' | 'email') {
    if (editField !== field) return;
    await saveField(field);
  }

  // ── Accordion state ────────────────────────────────────────────────────────
  type SectionId = 'provider' | 'apiKey' | 'github' | 'githubApp' | 'onboarding' | 'session';

  let openSections = $state<Record<SectionId, boolean>>({
    provider: false,
    apiKey: false,
    github: false,
    githubApp: false,
    onboarding: false,
    session: false,
  });

  function toggleSection(id: SectionId) {
    openSections[id] = !openSections[id];
    if (!openSections[id]) {
      if (id === 'apiKey') cancelApiKeyEdit();
      if (id === 'githubApp') cancelConfig();
    }
  }

  // Slide transition config
  const slideIn = { duration: 200, easing: cubicOut };

  $effect(() => {
    loadSettings();
  });
</script>

<div class="p-2">
  {#if saving}
    <div class="flex justify-end px-1 pb-1">
      <span class="font-mono text-[9px] text-neon-cyan">saving</span>
    </div>
  {/if}

  {#if loading}
    <div class="text-xs text-text-secondary px-2 py-8 text-center">Loading...</div>
  {:else if error}
    <div class="text-xs text-neon-red bg-neon-red/10 px-2 py-1.5 border border-neon-red/20">{error}</div>
  {:else if settings}

    <!-- ═══ PROFILE ═══════════════════════════════════════════════════════ -->
    {#if auth.isAuthenticated}
      <div class="flex items-start gap-2.5 px-2 py-2 bg-bg-card border border-border-subtle">
        <div class="w-9 h-9 border border-neon-cyan/20 overflow-hidden shrink-0 bg-bg-input">
          {#if user.avatarUrl}
            <img src={user.avatarUrl} class="w-full h-full object-cover" alt="" />
          {:else}
            <div class="w-full h-full flex items-center justify-center text-text-dim/20">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1">
                <path stroke-linecap="square" stroke-linejoin="miter" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/>
              </svg>
            </div>
          {/if}
        </div>
        <div class="flex-1 min-w-0 -mt-0.5 space-y-0.5">
          <!-- Display name -->
          {#if editField === 'display_name'}
            <input
              type="text" bind:value={editValue} maxlength="128" placeholder="Display name"
              use:focusEl
              onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveField('display_name'); } if (e.key === 'Escape') cancelEdit(); }}
              onblur={() => handleFieldBlur('display_name')}
              class="w-full bg-bg-input border border-neon-cyan/60 px-1.5 py-0.5 font-mono text-[10px] text-text-primary focus:outline-none focus:border-neon-cyan"
            />
          {:else}
            <div class="flex items-center gap-1 group">
              <button
                onclick={() => startEdit('display_name')}
                class="text-[11px] text-text-primary hover:text-neon-cyan text-left truncate flex-1 font-medium leading-tight"
              >{user.displayName || user.githubLogin || 'Set name'}</button>
              {#if savedField === 'display_name'}
                <span class="font-mono text-[8px] text-neon-green shrink-0">saved</span>
              {:else}
                <button
                  onclick={() => startEdit('display_name')}
                  class="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-text-dim hover:text-neon-cyan"
                  aria-label="Edit display name"
                >
                  <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                    <path stroke-linecap="square" stroke-linejoin="miter" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"/>
                  </svg>
                </button>
              {/if}
            </div>
          {/if}
          {#if user.githubLogin}
            <div class="font-mono text-[9px] text-text-dim/50 truncate leading-tight">@{user.githubLogin}</div>
          {/if}
          <!-- Email -->
          {#if editField === 'email'}
            <input
              type="email" bind:value={editValue} maxlength="255" placeholder="email@example.com"
              use:focusEl
              onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); saveField('email'); } if (e.key === 'Escape') cancelEdit(); }}
              onblur={() => handleFieldBlur('email')}
              class="w-full bg-bg-input border border-neon-cyan/60 px-1.5 py-0.5 font-mono text-[10px] text-text-primary focus:outline-none focus:border-neon-cyan"
            />
          {:else}
            <div class="flex items-center gap-1 group">
              <button
                onclick={() => startEdit('email')}
                class="font-mono text-[9px] text-text-dim/60 hover:text-text-secondary text-left truncate flex-1 leading-tight"
              >{user.email || 'Set email'}</button>
              {#if savedField === 'email'}
                <span class="font-mono text-[8px] text-neon-green shrink-0">saved</span>
              {:else}
                <button
                  onclick={() => startEdit('email')}
                  class="opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-text-dim hover:text-neon-cyan"
                  aria-label="Edit email"
                >
                  <svg class="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                    <path stroke-linecap="square" stroke-linejoin="miter" d="M16.862 4.487l1.687-1.688a1.875 1.875 0 112.652 2.652L10.582 16.07a4.5 4.5 0 01-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 011.13-1.897l8.932-8.931zm0 0L19.5 7.125"/>
                  </svg>
                </button>
              {/if}
            </div>
          {/if}
        </div>
      </div>
    {/if}

    <!-- ═══ CONNECTIONS ═══════════════════════════════════════════════════ -->
    <div class="flex items-center gap-2 px-1 pt-3 pb-1">
      <span class="font-display text-[9px] font-bold uppercase tracking-[0.12em] text-text-dim/30">Connections</span>
      <div class="flex-1 h-px bg-border-subtle/30"></div>
    </div>

    <!-- ── Provider ──────────────────────────────────────────────────── -->
    <button
      class="w-full flex items-center gap-2 px-2 py-1.5
             hover:bg-bg-hover/30 transition-colors duration-150"
      onclick={() => toggleSection('provider')}
      aria-expanded={openSections.provider}
    >
      <span class="w-1.5 h-1.5 shrink-0 {workbench.provider === 'anthropic' || workbench.provider === 'claude_cli' ? 'bg-neon-green' : workbench.provider === 'openai' || workbench.provider === 'anthropic_api' ? 'bg-neon-yellow' : 'bg-neon-red'}"></span>
      <span class="font-display text-[10px] font-bold uppercase tracking-[0.08em] text-text-dim">Provider</span>
      <span class="flex-1 text-right font-mono text-[9px] text-text-dim/40 truncate ml-1">
        {workbench.provider === 'anthropic' || workbench.provider === 'claude_cli' ? 'CLI (Claude)' : workbench.provider === 'openai' || workbench.provider === 'anthropic_api' ? 'API (Paid)' : 'Not detected'}
      </span>
      <svg class="w-3 h-3 text-text-dim/30 shrink-0 transition-transform duration-200" class:rotate-90={openSections.provider} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
    </button>

    {#if openSections.provider}
      <div class="px-4 pb-2 pt-0.5 space-y-1" transition:slide={slideIn}>
        {#if workbench.providerModel}
          <div class="text-[10px] text-text-dim font-mono">{workbench.providerModel}</div>
        {/if}
        <div class="flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 {workbench.isConnected ? 'bg-neon-green' : 'bg-neon-red'}"></span>
          <span class="text-[10px] text-text-dim">{workbench.isConnected ? 'Backend connected' : 'Backend disconnected'}</span>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="w-1.5 h-1.5 {workbench.mcpConnected ? 'bg-neon-cyan' : 'bg-neon-red/70'}"></span>
          <span class="text-[10px] text-text-dim">MCP {workbench.mcpConnected ? 'online' : 'offline'}</span>
        </div>
        <button
          onclick={handleTestConnection}
          disabled={testingProvider}
          class="font-mono text-[10px] text-neon-cyan/60 hover:text-neon-cyan uppercase mt-1
                 disabled:opacity-40 transition-colors"
        >{testingProvider ? '...' : 'Test connection'}</button>
        {#if providerTestResult}
          <div class="space-y-0.5">
            <div class="flex items-center gap-1.5">
              <span class="w-1.5 h-1.5 {providerTestResult.healthy ? 'bg-neon-green' : 'bg-neon-red'}"></span>
              <span class="font-mono text-[9px] {providerTestResult.healthy ? 'text-neon-green' : 'text-neon-red'} leading-snug">{providerTestResult.message}</span>
            </div>
            {#if providerTestResult.providers}
              <div class="font-mono text-[9px] text-text-dim ml-3">detected: {providerTestResult.providers}</div>
            {/if}
          </div>
        {/if}
      </div>
    {/if}

    <!-- ── API Key ───────────────────────────────────────────────────── -->
    <button
      class="w-full flex items-center gap-2 px-2 py-1.5
             hover:bg-bg-hover/30 transition-colors duration-150"
      onclick={() => toggleSection('apiKey')}
      aria-expanded={openSections.apiKey}
    >
      <span class="w-1.5 h-1.5 shrink-0 {providerCfg?.api_key.configured ? 'bg-neon-green' : 'bg-neon-red'}"></span>
      <span class="font-display text-[10px] font-bold uppercase tracking-[0.08em] text-text-dim">API Key</span>
      <span class="flex-1 text-right font-mono text-[9px] text-text-dim/40 truncate ml-1">
        {providerCfg ? (providerCfg.api_key.configured ? (providerCfg.api_key.source === 'environment' ? 'via env' : providerCfg.api_key.masked) : 'Not configured') : ''}
      </span>
      <svg class="w-3 h-3 text-text-dim/30 shrink-0 transition-transform duration-200" class:rotate-90={openSections.apiKey} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
    </button>

    {#if openSections.apiKey}
      <div class="px-4 pb-2 pt-0.5 space-y-1.5" transition:slide={slideIn}>
        <!-- Status -->
        {#if providerCfg?.api_key.configured}
          <div class="flex items-center gap-1.5">
            <span class="font-mono text-[10px] text-text-dim truncate">{providerCfg.api_key.masked}</span>
          </div>
          {#if providerCfg.api_key.source === 'environment'}
            <span class="font-mono text-[9px] text-text-dim/50">Read-only (set via environment variable)</span>
          {/if}
        {:else}
          <div class="font-mono text-[10px] text-text-dim">No API key configured</div>
        {/if}

        <!-- Actions -->
        {#if providerCfg?.api_key.source !== 'environment'}
          <div class="flex items-center gap-2">
            <button
              class="font-mono text-[9px] text-neon-cyan/70 hover:text-neon-cyan
                     transition-colors duration-150 uppercase"
              onclick={() => { if (expandApiKey) cancelApiKeyEdit(); else { expandApiKey = true; apiKeyError = ''; } }}
            >{expandApiKey ? 'Cancel' : (providerCfg?.api_key.configured ? 'Update' : 'Configure')}</button>
            {#if providerCfg?.api_key.configured && providerCfg.api_key.source === 'app' && !expandApiKey}
              <button
                onclick={handleDeleteApiKey}
                disabled={deletingApiKey}
                class="font-mono text-[9px] text-neon-red/50 hover:text-neon-red
                       disabled:opacity-40 transition-colors duration-150"
              >{deletingApiKey ? '...' : 'Remove'}</button>
            {/if}
          </div>
        {/if}

        <!-- Edit form -->
        {#if expandApiKey}
          <div class="space-y-1.5 pt-1" transition:slide={slideIn}>
            <div>
              <label for="nav-api-key" class="font-mono text-[8px] text-text-dim uppercase tracking-[0.08em] block mb-0.5">Anthropic API Key</label>
              <div class="relative">
                <input
                  id="nav-api-key"
                  type={showApiKeyInput ? 'text' : 'password'}
                  placeholder="sk-ant-..."
                  bind:value={apiKeyInput}
                  autocomplete="off" spellcheck="false"
                  class="w-full bg-bg-input border border-border-subtle px-2 py-1 pr-7
                         font-mono text-[10px] text-text-primary
                         focus:outline-none focus:border-neon-cyan/30
                         placeholder:text-text-dim/40 transition-colors duration-150"
                />
                <button
                  type="button"
                  class="absolute right-1.5 top-1/2 -translate-y-1/2
                         text-text-dim hover:text-text-secondary transition-colors duration-150"
                  onclick={() => { showApiKeyInput = !showApiKeyInput; }}
                  aria-label={showApiKeyInput ? 'Hide key' : 'Show key'}
                >
                  {#if showApiKeyInput}
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88"/>
                    </svg>
                  {:else}
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"/>
                      <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                    </svg>
                  {/if}
                </button>
              </div>
            </div>
            <div class="flex items-center gap-1.5">
              <button
                class="flex-1 px-2 py-1 bg-neon-cyan text-bg-primary border border-neon-cyan
                       hover:bg-[#00cce6] active:bg-[#00b8cf] transition-colors duration-150
                       font-mono text-[9px] tracking-[0.07em] uppercase
                       disabled:opacity-40 disabled:cursor-not-allowed"
                onclick={handleSaveApiKey}
                disabled={savingApiKey || !apiKeyInput.trim()}
              >{savingApiKey ? 'SAVING...' : 'SAVE'}</button>
              <button
                class="px-2 py-1 border border-border-subtle text-text-dim
                       hover:border-neon-cyan/25 hover:text-text-secondary
                       transition-colors duration-150 font-mono text-[9px] uppercase"
                onclick={cancelApiKeyEdit}
              >CANCEL</button>
            </div>
            {#if apiKeyError}
              <p class="font-mono text-[9px] text-neon-red leading-snug">{apiKeyError}</p>
            {/if}
          </div>
        {/if}
      </div>
    {/if}

    <!-- ── GitHub ────────────────────────────────────────────────────── -->
    <button
      class="w-full flex items-center gap-2 px-2 py-1.5
             hover:bg-bg-hover/30 transition-colors duration-150"
      onclick={() => toggleSection('github')}
      aria-expanded={openSections.github}
    >
      <span class="w-1.5 h-1.5 shrink-0 {github.isConnected ? 'bg-neon-green' : 'bg-text-dim/30'}"></span>
      <span class="font-display text-[10px] font-bold uppercase tracking-[0.08em] text-text-dim">GitHub</span>
      <span class="flex-1 text-right font-mono text-[9px] text-text-dim/40 truncate ml-1">
        {github.isConnected ? github.username : (workbench.githubOAuthEnabled ? 'Not connected' : 'Not configured')}
      </span>
      <svg class="w-3 h-3 text-text-dim/30 shrink-0 transition-transform duration-200" class:rotate-90={openSections.github} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
    </button>

    {#if openSections.github}
      <div class="px-4 pb-2 pt-0.5 space-y-1" transition:slide={slideIn}>
        {#if github.isConnected}
          <div class="flex items-center justify-between gap-2">
            <span class="text-[10px] text-text-dim">OAuth App</span>
            <button
              class="font-mono text-[9px] text-neon-red/60 hover:text-neon-red
                     transition-colors duration-150"
              onclick={handleDisconnectGitHub}
            >Disconnect</button>
          </div>
          <button
            onclick={handleReconnectGitHub}
            disabled={reconnecting}
            class="font-mono text-[10px] text-neon-cyan/60 hover:text-neon-cyan
                   disabled:opacity-40 transition-colors"
          >{reconnecting ? '...' : 'Refresh token'}</button>
          {#if reconnectError}
            <p class="font-mono text-[9px] text-neon-red">{reconnectError}</p>
          {:else if reconnectInfo}
            <p class="font-mono text-[9px] text-text-dim">{reconnectInfo}</p>
          {/if}
        {:else if workbench.githubOAuthEnabled}
          <button
            class="font-mono text-[10px] text-neon-cyan hover:text-neon-cyan/80
                   transition-colors duration-150"
            onclick={() => { window.location.href = getGitHubLoginUrl(); }}
          >Connect via GitHub</button>
        {:else}
          <span class="text-[10px] text-text-dim">GitHub App not configured</span>
        {/if}
      </div>
    {/if}

    <!-- ── GitHub App ────────────────────────────────────────────────── -->
    <button
      class="w-full flex items-center gap-2 px-2 py-1.5
             hover:bg-bg-hover/30 transition-colors duration-150"
      onclick={() => toggleSection('githubApp')}
      aria-expanded={openSections.githubApp}
    >
      <span class="w-1.5 h-1.5 shrink-0 {appConfig?.configured ? 'bg-neon-green' : 'bg-text-dim/30'}"></span>
      <span class="font-display text-[10px] font-bold uppercase tracking-[0.08em] text-text-dim">GitHub App</span>
      <span class="flex-1 text-right font-mono text-[9px] text-text-dim/40 truncate ml-1">
        {appConfig?.configured ? appConfig.client_id_masked : 'Not configured'}
      </span>
      <svg class="w-3 h-3 text-text-dim/30 shrink-0 transition-transform duration-200" class:rotate-90={openSections.githubApp} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
    </button>

    {#if openSections.githubApp}
      <div class="px-4 pb-2 pt-0.5 space-y-1.5" transition:slide={slideIn}>
        <!-- Actions -->
        <button
          class="font-mono text-[9px] text-neon-cyan/70 hover:text-neon-cyan
                 transition-colors duration-150 uppercase"
          onclick={() => { if (expandConfig) cancelConfig(); else { expandConfig = true; configError = ''; } }}
        >{expandConfig ? 'Cancel' : (appConfig?.configured ? 'Update credentials' : 'Configure')}</button>

        {#if expandConfig}
          <div class="space-y-1.5 pt-1" transition:slide={slideIn}>
            <!-- Client ID -->
            <div>
              <label for="nav-config-cid" class="font-mono text-[8px] text-text-dim uppercase tracking-[0.08em] block mb-0.5">Client ID</label>
              <input
                id="nav-config-cid"
                type="text"
                placeholder="Iv1.xxxxxxxxxxxxxxxxxxxx"
                bind:value={configClientId}
                autocomplete="off" spellcheck="false"
                class="w-full bg-bg-input border border-border-subtle px-2 py-1
                       font-mono text-[10px] text-text-primary
                       focus:outline-none focus:border-neon-cyan/30
                       placeholder:text-text-dim/40 transition-colors duration-150"
              />
            </div>
            <!-- Client Secret -->
            <div>
              <label for="nav-config-sec" class="font-mono text-[8px] text-text-dim uppercase tracking-[0.08em] block mb-0.5">Client Secret</label>
              <div class="relative">
                <input
                  id="nav-config-sec"
                  type={showConfigSecret ? 'text' : 'password'}
                  placeholder="••••••••••••••••••••••••••••••••"
                  bind:value={configSecret}
                  autocomplete="new-password" spellcheck="false"
                  class="w-full bg-bg-input border border-border-subtle px-2 py-1 pr-7
                         font-mono text-[10px] text-text-primary
                         focus:outline-none focus:border-neon-cyan/30
                         placeholder:text-text-dim/40 transition-colors duration-150"
                />
                <button
                  type="button"
                  class="absolute right-1.5 top-1/2 -translate-y-1/2
                         text-text-dim hover:text-text-secondary transition-colors duration-150"
                  onclick={() => { showConfigSecret = !showConfigSecret; }}
                  aria-label={showConfigSecret ? 'Hide secret' : 'Show secret'}
                >
                  {#if showConfigSecret}
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88"/>
                    </svg>
                  {:else}
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
                      <path stroke-linecap="round" stroke-linejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z"/>
                      <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"/>
                    </svg>
                  {/if}
                </button>
              </div>
            </div>
            <!-- Action row -->
            <div class="flex items-center gap-1.5 pt-0.5">
              <button
                class="flex-1 px-2 py-1 bg-neon-cyan text-bg-primary border border-neon-cyan
                       hover:bg-[#00cce6] active:bg-[#00b8cf] transition-colors duration-150
                       font-mono text-[9px] tracking-[0.07em] uppercase
                       disabled:opacity-40 disabled:cursor-not-allowed"
                onclick={handleSaveAppConfig}
                disabled={configSaving || !configClientId.trim() || !configSecret.trim()}
              >{configSaving ? 'SAVING...' : 'SAVE'}</button>
              <button
                class="px-2 py-1 border border-border-subtle text-text-dim
                       hover:border-neon-cyan/25 hover:text-text-secondary
                       transition-colors duration-150 font-mono text-[9px] uppercase"
                onclick={cancelConfig}
              >CANCEL</button>
            </div>
            {#if configError}
              <p class="font-mono text-[9px] text-neon-red leading-snug">{configError}</p>
            {/if}
          </div>
        {/if}

        <!-- Reconnect prompt -->
        {#if showReconnect}
          <div class="pt-1.5 border-t border-border-subtle">
            <p class="font-mono text-[9px] text-text-dim leading-snug">
              Credentials updated. Reconnect to apply:
              <button
                class="text-neon-cyan hover:text-neon-cyan/80 transition-colors duration-150 ml-1"
                onclick={async () => {
                  showReconnect = false;
                  await disconnectGitHub().catch(() => {});
                  window.location.href = getGitHubLoginUrl();
                }}
              >RECONNECT</button>
            </p>
          </div>
        {/if}
      </div>
    {/if}

    <!-- ═══ PIPELINE ═════════════════════════════════════════════════════ -->
    <div class="flex items-center gap-2 px-1 pt-3 pb-1">
      <span class="font-display text-[9px] font-bold uppercase tracking-[0.12em] text-text-dim/30">Pipeline</span>
      <div class="flex-1 h-px bg-border-subtle/30"></div>
    </div>

    <div class="px-2 space-y-1.5">
      <!-- Model -->
      <div class="space-y-0.5">
        <label class="font-mono text-[8px] text-text-dim/50 uppercase tracking-wider block" for="s-model">Model</label>
        <select
          id="s-model"
          class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-[11px] text-text-primary font-sans
                 focus:outline-none focus:border-neon-cyan/30 cursor-pointer appearance-none"
          style="background-image: url(data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%238b8ba8' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E); background-repeat: no-repeat; background-position: right 8px center; padding-right: 24px;"
          value={settings.default_model}
          onchange={(e) => updateSettings({ default_model: (e.target as HTMLSelectElement).value }).then(s => settings = s)}
        >
          <option value="auto">Auto (recommended)</option>
          <option value="claude-opus-4-6">Claude Opus</option>
          <option value="claude-sonnet-4-6">Claude Sonnet</option>
          <option value="claude-haiku-4-5-20251001">Claude Haiku</option>
        </select>
      </div>

      <!-- Default Strategy -->
      <div class="space-y-0.5">
        <label class="font-mono text-[8px] text-text-dim/50 uppercase tracking-wider block" for="s-strategy">Strategy</label>
        <select
          id="s-strategy"
          class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-[11px] text-text-primary font-sans
                 focus:outline-none focus:border-neon-cyan/30 cursor-pointer appearance-none"
          style="background-image: url(data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 24 24' fill='none' stroke='%238b8ba8' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E); background-repeat: no-repeat; background-position: right 8px center; padding-right: 24px;"
          value={settings.default_strategy ?? ''}
          onchange={(e) => {
            const val = (e.target as HTMLSelectElement).value;
            updateSettings({ default_strategy: val || null } as any).then(s => settings = s);
          }}
        >
          <option value="">Auto (recommended)</option>
          <option value="chain-of-thought">Chain of Thought</option>
          <option value="CO-STAR">CO-STAR</option>
          <option value="constraint-injection">Constraint Injection</option>
          <option value="context-enrichment">Context Enrichment</option>
          <option value="few-shot-scaffolding">Few-Shot Scaffolding</option>
          <option value="persona-assignment">Persona Assignment</option>
          <option value="RISEN">RISEN</option>
          <option value="role-task-format">Role-Task-Format</option>
          <option value="step-by-step">Step by Step</option>
          <option value="structured-output">Structured Output</option>
        </select>
      </div>

      <!-- Timeout + Retries in 2-col grid -->
      <div class="grid grid-cols-2 gap-2">
        <div class="space-y-0.5">
          <label class="font-mono text-[8px] text-text-dim/50 uppercase tracking-wider block" for="s-timeout">Timeout (s)</label>
          <input
            id="s-timeout"
            type="number" min="10" max="600"
            class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-[11px] text-text-primary font-mono
                   focus:outline-none focus:border-neon-cyan/30"
            value={settings.pipeline_timeout}
            onchange={(e) => handleNumberChange('pipeline_timeout', parseInt((e.target as HTMLInputElement).value))}
          />
        </div>
        <div class="space-y-0.5">
          <label class="font-mono text-[8px] text-text-dim/50 uppercase tracking-wider block" for="s-retries">Max Retries</label>
          <input
            id="s-retries"
            type="number" min="0" max="5"
            class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-[11px] text-text-primary font-mono
                   focus:outline-none focus:border-neon-cyan/30"
            value={settings.max_retries}
            onchange={(e) => handleNumberChange('max_retries', parseInt((e.target as HTMLInputElement).value))}
          />
        </div>
      </div>

      <!-- Toggles side by side -->
      <div class="flex items-center gap-4 pt-0.5">
        <label class="flex items-center gap-1.5 cursor-pointer">
          <div class="relative w-7 h-3.5 shrink-0">
            <input
              type="checkbox" class="sr-only peer"
              checked={settings.auto_validate}
              onchange={() => handleToggle('auto_validate', !settings!.auto_validate)}
            />
            <div class="absolute inset-0 border border-border-subtle bg-bg-input
                        peer-checked:border-neon-cyan/40 peer-checked:bg-neon-cyan/[0.08] transition-colors duration-200"></div>
            <div class="absolute left-0.5 top-0.5 w-2.5 h-2.5 bg-text-dim/40
                        peer-checked:translate-x-3.5 peer-checked:bg-neon-cyan transition-all duration-200"></div>
          </div>
          <span class="text-[10px] text-text-secondary">Auto-validate</span>
        </label>
        <label class="flex items-center gap-1.5 cursor-pointer" title="Stream tokens in real-time during optimization. Disable for batch mode (faster, no live preview).">
          <div class="relative w-7 h-3.5 shrink-0">
            <input
              type="checkbox" class="sr-only peer"
              checked={settings.stream_optimize}
              onchange={() => handleToggle('stream_optimize', !settings!.stream_optimize)}
            />
            <div class="absolute inset-0 border border-border-subtle bg-bg-input
                        peer-checked:border-neon-cyan/40 peer-checked:bg-neon-cyan/[0.08] transition-colors duration-200"></div>
            <div class="absolute left-0.5 top-0.5 w-2.5 h-2.5 bg-text-dim/40
                        peer-checked:translate-x-3.5 peer-checked:bg-neon-cyan transition-all duration-200"></div>
          </div>
          <span class="text-[10px] text-text-secondary">Stream optimize</span>
        </label>
      </div>
    </div>

    <!-- ═══ APP ═══════════════════════════════════════════════════════════ -->
    {#if auth.isAuthenticated}
      <div class="flex items-center gap-2 px-1 pt-3 pb-1">
        <span class="font-display text-[9px] font-bold uppercase tracking-[0.12em] text-text-dim/30">App</span>
        <div class="flex-1 h-px bg-border-subtle/30"></div>
      </div>

      <!-- ── Onboarding ──────────────────────────────────────────────── -->
      <button
        class="w-full flex items-center gap-2 px-2 py-1.5
               hover:bg-bg-hover/30 transition-colors duration-150"
        onclick={() => toggleSection('onboarding')}
        aria-expanded={openSections.onboarding}
      >
        <span class="w-1.5 shrink-0"></span>
        <span class="font-display text-[10px] font-bold uppercase tracking-[0.08em] text-text-dim">Onboarding</span>
        <span class="flex-1"></span>
        <svg class="w-3 h-3 text-text-dim/30 shrink-0 transition-transform duration-200" class:rotate-90={openSections.onboarding} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
      </button>

      {#if openSections.onboarding}
        <div class="px-4 pb-2 pt-0.5 space-y-1" transition:slide={slideIn}>
          <button
            onclick={() => { workbench.showOnboarding = true; }}
            class="w-full flex items-center justify-between px-2 py-1
                   border border-border-subtle text-text-secondary
                   hover:border-neon-cyan/30 hover:text-text-primary
                   transition-colors font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            <span>Replay welcome guide</span>
          </button>
          <button
            onclick={() => { import('$lib/stores/walkthrough.svelte').then(m => m.walkthrough.start()); }}
            class="w-full flex items-center justify-between px-2 py-1
                   border border-border-subtle text-text-secondary
                   hover:border-neon-cyan/30 hover:text-text-primary
                   transition-colors font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            <span>Interactive walkthrough</span>
          </button>
          <button
            onclick={() => { user.resetTips(); toast.success('Tips reset — they will appear again for new users'); }}
            class="w-full flex items-center justify-between px-2 py-1
                   border border-border-subtle text-text-secondary
                   hover:border-neon-cyan/30 hover:text-text-primary
                   transition-colors font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            <span>Reset all tips</span>
            <span class="text-[9px] text-text-dim/60">re-enables hints</span>
          </button>
        </div>
      {/if}

      <!-- ── Session ─────────────────────────────────────────────────── -->
      <button
        class="w-full flex items-center gap-2 px-2 py-1.5
               hover:bg-bg-hover/30 transition-colors duration-150"
        onclick={() => toggleSection('session')}
        aria-expanded={openSections.session}
      >
        <span class="w-1.5 shrink-0"></span>
        <span class="font-display text-[10px] font-bold uppercase tracking-[0.08em] text-text-dim">Session</span>
        <span class="flex-1"></span>
        <svg class="w-3 h-3 text-text-dim/30 shrink-0 transition-transform duration-200" class:rotate-90={openSections.session} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 5l7 7-7 7"/></svg>
      </button>

      {#if openSections.session}
        <div class="px-4 pb-2 pt-0.5 space-y-1" transition:slide={slideIn}>
          <button
            onclick={handleLogoutDevice}
            disabled={loggingOut}
            class="w-full flex items-center justify-between px-2 py-1
                   border border-border-subtle text-text-secondary
                   hover:border-neon-cyan/30 hover:text-text-primary
                   disabled:opacity-40 disabled:cursor-not-allowed
                   transition-colors font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            <span>{loggingOut ? 'Signing out...' : 'Sign out'}</span>
            <span class="text-[9px] text-text-dim/60">this device</span>
          </button>
          <button
            onclick={handleLogoutAllDevices}
            disabled={loggingOutAll}
            class="w-full flex items-center justify-between px-2 py-1
                   border border-neon-red/30 text-neon-red/70
                   hover:border-neon-red hover:text-neon-red
                   disabled:opacity-40 disabled:cursor-not-allowed
                   transition-colors font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            <span>{loggingOutAll ? 'Revoking...' : 'Logout all devices'}</span>
            <span class="text-[9px] text-text-dim/60">revokes all sessions</span>
          </button>
        </div>
      {/if}
    {/if}
  {/if}
</div>
