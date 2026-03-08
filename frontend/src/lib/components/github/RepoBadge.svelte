<script lang="ts">
  let { name, isPrivate = false }: { name: string; isPrivate?: boolean } = $props();
</script>

<span
  class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-bg-card border border-border-subtle text-text-secondary"
  title="{name}{isPrivate ? ' (private)' : ''}"
>
  {#if isPrivate}
    <svg class="w-2.5 h-2.5 text-neon-yellow" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
      <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 10-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 002.25-2.25v-6.75a2.25 2.25 0 00-2.25-2.25H6.75a2.25 2.25 0 00-2.25 2.25v6.75a2.25 2.25 0 002.25 2.25z"></path>
    </svg>
  {:else}
    <svg class="w-2.5 h-2.5 text-text-dim" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
      <path stroke-linecap="round" stroke-linejoin="round" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
    </svg>
  {/if}
  <span>{name}</span>
</span>
