<script lang="ts">
  import { fetchSettings, updateSettings, fetchProviderStatus, disconnectGitHub, unlinkRepo, getGitHubLoginUrl, logoutAllDevices, type AppSettings } from '$lib/api/client';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { auth } from '$lib/stores/auth.svelte';
  import { toast } from '$lib/stores/toast.svelte';

  let settings = $state<AppSettings | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let saving = $state(false);

  async function loadSettings() {
    loading = true;
    error = null;
    try {
      const [s, status] = await Promise.all([
        fetchSettings(),
        fetchProviderStatus().catch(() => null)
      ]);
      settings = s;
      if (status) workbench.isConnected = status.healthy;
    } catch (err) {
      error = (err as Error).message;
    } finally {
      loading = false;
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
      await unlinkRepo().catch(() => {}); // best-effort — local selection already cleared
      toast.success('GitHub disconnected');
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      github.disconnect(); // always clear local state, even on API failure
    }
  }

  let loggingOutAll = $state(false);

  async function handleLogoutAllDevices() {
    if (loggingOutAll) return;
    loggingOutAll = true;
    try {
      const result = await logoutAllDevices();
      toast.success(`Logged out of ${result.revoked_sessions} device${result.revoked_sessions !== 1 ? 's' : ''}`);
      // auth.clearToken() is called inside logoutAllDevices() — UI will reflect logout
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

  $effect(() => {
    loadSettings();
  });
</script>

<div class="p-2 space-y-3">
  <div class="flex items-center justify-between px-1">
    <span class="font-display text-[11px] font-bold uppercase text-text-dim">Settings</span>
    {#if saving}
      <span class="text-[10px] text-neon-cyan">Saving...</span>
    {/if}
  </div>

  {#if loading}
    <div class="text-xs text-text-secondary px-2 py-4 text-center">Loading settings...</div>
  {:else if error}
    <div class="text-xs text-neon-red bg-neon-red/10 px-2 py-1.5 border border-neon-red/20">
      {error}
    </div>
  {:else if settings}
    <div class="space-y-2 px-1">
      <!-- Provider Info -->
      <div class="space-y-1 mb-3 p-2 bg-bg-card border border-border-subtle">
        <div class="font-display text-[11px] font-bold uppercase text-text-dim mb-1">Provider</div>
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 {workbench.provider === 'anthropic' || workbench.provider === 'claude_cli' ? 'bg-neon-green' : workbench.provider === 'openai' || workbench.provider === 'anthropic_api' ? 'bg-neon-yellow' : 'bg-neon-red'}"></span>
          <span class="text-xs text-text-primary font-medium">
            {workbench.provider === 'anthropic' || workbench.provider === 'claude_cli' ? 'CLI (Claude)' : workbench.provider === 'openai' || workbench.provider === 'anthropic_api' ? 'API (Paid)' : 'Not detected'}
          </span>
        </div>
        {#if workbench.providerModel}
          <div class="text-[10px] text-text-dim font-mono ml-4">{workbench.providerModel}</div>
        {/if}
        <div class="flex items-center gap-1.5 mt-0.5">
          <span class="w-1.5 h-1.5 {workbench.isConnected ? 'bg-neon-green' : 'bg-neon-red'}"></span>
          <span class="text-[10px] text-text-dim">{workbench.isConnected ? 'Backend connected' : 'Backend disconnected'}</span>
        </div>
        <div class="flex items-center gap-1.5 mt-0.5">
          <span class="w-1.5 h-1.5 {workbench.mcpConnected ? 'bg-neon-cyan' : 'bg-neon-red/70'}"></span>
          <span class="text-[10px] text-text-dim">MCP {workbench.mcpConnected ? 'online' : 'offline'}</span>
        </div>
      </div>

      <!-- GitHub Connection -->
      <div class="space-y-1 mb-3 p-2 bg-bg-card border border-border-subtle">
        <div class="font-display text-[11px] font-bold uppercase text-text-dim mb-1">GitHub</div>
        {#if github.isConnected}
          <div class="flex items-center justify-between gap-2">
            <div class="flex items-center gap-2 min-w-0">
              <span class="w-2 h-2 bg-neon-green shrink-0"></span>
              <span class="text-xs text-text-primary font-medium truncate">{github.username}</span>
            </div>
            <button
              class="text-[10px] text-neon-red/80 hover:text-neon-red shrink-0"
              onclick={handleDisconnectGitHub}
            >Disconnect</button>
          </div>
          <div class="text-[10px] text-text-dim ml-4">OAuth App</div>
        {:else if workbench.githubOAuthEnabled}
          <div class="flex items-center gap-2 mb-1">
            <span class="w-2 h-2 bg-text-dim/30 shrink-0"></span>
            <span class="text-xs text-text-dim">Not connected</span>
          </div>
          <button
            class="text-[10px] text-neon-cyan hover:text-neon-cyan/80 ml-4"
            onclick={() => { window.location.href = getGitHubLoginUrl(); }}
          >Connect via GitHub →</button>
        {:else}
          <span class="text-[10px] text-text-dim">GitHub App not configured</span>
        {/if}
      </div>

      <!-- Default Model -->
      <div class="space-y-0.5">
        <label class="text-[10px] text-text-dim block" for="setting-model">Default Model</label>
        <select
          id="setting-model"
          class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-xs text-text-primary
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

      <!-- Pipeline Timeout -->
      <div class="space-y-0.5">
        <label class="text-[10px] text-text-dim block" for="setting-timeout">Pipeline Timeout (s)</label>
        <input
          id="setting-timeout"
          type="number"
          min="10"
          max="600"
          class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30"
          value={settings.pipeline_timeout}
          onchange={(e) => handleNumberChange('pipeline_timeout', parseInt((e.target as HTMLInputElement).value))}
        />
      </div>

      <!-- Max Retries -->
      <div class="space-y-0.5">
        <label class="text-[10px] text-text-dim block" for="setting-retries">Max Retries</label>
        <input
          id="setting-retries"
          type="number"
          min="0"
          max="5"
          class="w-full bg-bg-input border border-border-subtle px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30"
          value={settings.max_retries}
          onchange={(e) => handleNumberChange('max_retries', parseInt((e.target as HTMLInputElement).value))}
        />
      </div>

      <!-- Auto Validate -->
      <label class="flex items-center gap-2 py-0.5 cursor-pointer">
        <div class="relative w-7 h-3.5 shrink-0">
          <input
            id="setting-auto-validate"
            name="auto_validate"
            type="checkbox"
            class="sr-only peer"
            checked={settings.auto_validate}
            onchange={() => handleToggle('auto_validate', !settings!.auto_validate)}
          />
          <div class="absolute inset-0 border border-border-subtle bg-bg-input
                      peer-checked:border-neon-cyan/40 peer-checked:bg-neon-cyan/[0.08] transition-colors duration-200"></div>
          <div class="absolute left-0.5 top-0.5 w-2.5 h-2.5 bg-text-dim/40
                      peer-checked:translate-x-3.5 peer-checked:bg-neon-cyan transition-all duration-200"></div>
        </div>
        <span class="text-xs text-text-secondary">Auto-validate</span>
      </label>

      <!-- Stream Optimize -->
      <label class="flex items-center gap-2 py-0.5 cursor-pointer">
        <div class="relative w-7 h-3.5 shrink-0">
          <input
            id="setting-stream-optimize"
            name="stream_optimize"
            type="checkbox"
            class="sr-only peer"
            checked={settings.stream_optimize}
            onchange={() => handleToggle('stream_optimize', !settings!.stream_optimize)}
          />
          <div class="absolute inset-0 border border-border-subtle bg-bg-input
                      peer-checked:border-neon-cyan/40 peer-checked:bg-neon-cyan/[0.08] transition-colors duration-200"></div>
          <div class="absolute left-0.5 top-0.5 w-2.5 h-2.5 bg-text-dim/40
                      peer-checked:translate-x-3.5 peer-checked:bg-neon-cyan transition-all duration-200"></div>
        </div>
        <span class="text-xs text-text-secondary">Stream optimization</span>
      </label>

      <!-- Session Security -->
      {#if auth.isAuthenticated}
        <div class="space-y-1 mt-3 pt-3 border-t border-border-subtle">
          <div class="font-display text-[11px] font-bold uppercase text-text-dim mb-2">Session</div>
          <button
            onclick={handleLogoutAllDevices}
            disabled={loggingOutAll}
            class="w-full flex items-center justify-between px-2 py-1.5
                   border border-neon-red/30 text-neon-red/70
                   hover:border-neon-red hover:text-neon-red
                   disabled:opacity-40 disabled:cursor-not-allowed
                   transition-colors font-mono text-[10px] uppercase tracking-[0.05em]"
          >
            <span>{loggingOutAll ? 'Revoking…' : 'Logout all devices'}</span>
            <span class="text-[9px] text-text-dim/60">revokes all sessions</span>
          </button>
        </div>
      {/if}
    </div>
  {/if}
</div>
