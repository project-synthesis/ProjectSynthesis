<script lang="ts">
  import { fetchSettings, updateSettings, fetchProviderStatus, type AppSettings } from '$lib/api/client';
  import { workbench } from '$lib/stores/workbench.svelte';

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
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
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
    <span class="text-[10px] uppercase tracking-wider text-text-dim font-semibold">Settings</span>
    {#if saving}
      <span class="text-[10px] text-neon-cyan">Saving...</span>
    {/if}
  </div>

  {#if loading}
    <div class="text-xs text-text-dim px-2 py-4 text-center">Loading settings...</div>
  {:else if error}
    <div class="text-xs text-neon-red bg-neon-red/10 px-2 py-1.5 rounded border border-neon-red/20">
      {error}
    </div>
  {:else if settings}
    <div class="space-y-2 px-1">
      <!-- Provider Info -->
      <div class="space-y-1 mb-3 p-2 rounded bg-bg-card border border-border-subtle">
        <div class="text-[10px] uppercase tracking-wider text-text-dim font-semibold mb-1">Provider</div>
        <div class="flex items-center gap-2">
          <span class="w-2 h-2 rounded-full {workbench.provider === 'anthropic' || workbench.provider === 'claude_cli' ? 'bg-neon-green' : workbench.provider === 'openai' || workbench.provider === 'anthropic_api' ? 'bg-neon-yellow' : 'bg-neon-red'}"></span>
          <span class="text-xs text-text-primary font-medium">
            {workbench.provider === 'anthropic' || workbench.provider === 'claude_cli' ? 'CLI (Claude)' : workbench.provider === 'openai' || workbench.provider === 'anthropic_api' ? 'API (Paid)' : 'Not detected'}
          </span>
        </div>
        {#if workbench.providerModel}
          <div class="text-[10px] text-text-dim font-mono ml-4">{workbench.providerModel}</div>
        {/if}
        <div class="flex items-center gap-1.5 mt-0.5">
          <span class="w-1.5 h-1.5 rounded-full {workbench.isConnected ? 'bg-neon-green' : 'bg-neon-red'}"></span>
          <span class="text-[10px] text-text-dim">{workbench.isConnected ? 'Connected' : 'Disconnected'}</span>
        </div>
      </div>

      <!-- Default Model -->
      <div class="space-y-0.5">
        <label class="text-[10px] text-text-dim block" for="setting-model">Default Model</label>
        <select
          id="setting-model"
          class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30"
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
          class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30"
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
          class="w-full bg-bg-input border border-border-subtle rounded px-2 py-1 text-xs text-text-primary focus:outline-none focus:border-neon-cyan/30"
          value={settings.max_retries}
          onchange={(e) => handleNumberChange('max_retries', parseInt((e.target as HTMLInputElement).value))}
        />
      </div>

      <!-- Auto Validate -->
      <label class="flex items-center gap-2 py-0.5 cursor-pointer">
        <input
          type="checkbox"
          class="accent-neon-cyan"
          checked={settings.auto_validate}
          onchange={() => handleToggle('auto_validate', !settings!.auto_validate)}
        />
        <span class="text-xs text-text-secondary">Auto-validate</span>
      </label>

      <!-- Stream Optimize -->
      <label class="flex items-center gap-2 py-0.5 cursor-pointer">
        <input
          type="checkbox"
          class="accent-neon-cyan"
          checked={settings.stream_optimize}
          onchange={() => handleToggle('stream_optimize', !settings!.stream_optimize)}
        />
        <span class="text-xs text-text-secondary">Stream optimization</span>
      </label>
    </div>
  {/if}
</div>
