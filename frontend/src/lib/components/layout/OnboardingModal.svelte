<script lang="ts">
  import { patchAuthMe } from '$lib/api/client';

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
      await patchAuthMe({
        display_name: displayName.trim() || null,
        onboarding_completed: true,
      });
      onComplete();
    } catch (err) {
      error = (err as Error).message;
    } finally {
      saving = false;
    }
  }

  function handleSkip() {
    // Dismiss without saving — user can complete profile later via Settings
    onComplete();
  }
</script>

<!-- Full-screen onboarding overlay -->
<div class="fixed inset-0 z-50 flex items-center justify-center bg-bg-primary/90">
  <div class="bg-bg-card border border-border-subtle w-full max-w-sm p-8">
    <h2 class="font-display text-sm tracking-[0.15em] uppercase text-neon-cyan mb-1">
      Welcome to Project Synthesis
    </h2>
    <p class="font-mono text-[9px] text-text-dim mb-6">
      Optionally set a display name to personalise your workspace.
    </p>

    <label class="font-mono text-[8px] text-text-dim uppercase tracking-[0.08em] block mb-1">
      Display Name <span class="text-text-dim/50">(optional)</span>
    </label>
    <input
      type="text"
      maxlength="128"
      placeholder="Your name"
      bind:value={displayName}
      onkeydown={(e) => { if (e.key === 'Enter') handleComplete(); }}
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
             disabled:opacity-40 disabled:cursor-not-allowed mb-2"
    >
      {saving ? 'SAVING…' : 'GET STARTED'}
    </button>

    <button
      onclick={handleSkip}
      class="w-full px-4 py-1.5 border border-border-subtle font-mono text-[10px]
             text-text-dim tracking-[0.05em] uppercase hover:border-neon-cyan/30
             hover:text-text-secondary transition-colors"
    >
      Skip
    </button>
  </div>
</div>
