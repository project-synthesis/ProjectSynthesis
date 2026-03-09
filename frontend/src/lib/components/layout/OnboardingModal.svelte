<script lang="ts">
  import { apiFetch } from '$lib/api/client';

  interface Props {
    onComplete: () => void;
  }
  const { onComplete }: Props = $props();

  let displayName = $state('');
  let saving = $state(false);
  let error = $state('');

  async function handleComplete() {
    saving = true;
    error = '';
    try {
      await apiFetch('/auth/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          display_name: displayName.trim() || null,
          onboarding_completed_at: new Date().toISOString(),
        }),
      });
      onComplete();
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }
</script>

<!-- Onboarding modal overlay -->
<div class="fixed inset-0 z-50 flex items-center justify-center bg-bg-primary/90">
  <div class="bg-bg-card border border-border-subtle w-full max-w-sm p-8">
    <h2 class="font-display text-sm tracking-[0.15em] uppercase text-neon-cyan mb-1">
      Welcome
    </h2>
    <p class="font-mono text-[9px] text-text-dim mb-6">
      Set up your profile to get started.
    </p>

    <label class="font-mono text-[8px] text-text-dim uppercase tracking-[0.08em] block mb-1">
      Display Name (optional)
    </label>
    <input
      type="text"
      placeholder="Your name"
      bind:value={displayName}
      class="w-full bg-bg-input border border-border-subtle px-2.5 py-1.5
             font-mono text-[11px] text-text-primary focus:outline-none
             focus:border-neon-cyan/30 placeholder:text-text-dim/40 mb-4"
    />

    {#if error}
      <p class="font-mono text-[9px] text-neon-red mb-3">{error}</p>
    {/if}

    <button
      onclick={handleComplete}
      disabled={saving}
      class="w-full flex items-center justify-center gap-2 px-4 py-2.5
             bg-neon-cyan text-bg-primary border border-neon-cyan
             hover:bg-[#00cce6] font-mono text-[11px] tracking-[0.07em] uppercase
             disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {saving ? 'SAVING…' : 'GET STARTED'}
    </button>
  </div>
</div>
