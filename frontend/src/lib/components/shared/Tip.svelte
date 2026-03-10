<script lang="ts">
  import { user } from '$lib/stores/user.svelte';
  import { trackOnboardingEvent } from '$lib/api/client';

  interface Props {
    id: string;
    text: string;
  }
  const { id, text }: Props = $props();

  let visible = $derived(
    user.isNewUser && !user.preferences.dismissedTips.includes(id)
  );

  function dismiss() {
    user.dismissTip(id);
    trackOnboardingEvent('tip_dismissed', { tip: id }).catch(() => {});
  }
</script>

{#if visible}
  <div class="flex items-center gap-1.5 mt-1 animate-fade-in">
    <span class="font-mono text-[8px] text-neon-cyan/50 uppercase tracking-[0.1em]">TIP</span>
    <span class="font-mono text-[9px] text-text-dim/60">{text}</span>
    <button onclick={dismiss} class="text-text-dim/30 hover:text-text-dim ml-auto text-[10px] shrink-0" aria-label="Dismiss tip">x</button>
  </div>
{/if}
