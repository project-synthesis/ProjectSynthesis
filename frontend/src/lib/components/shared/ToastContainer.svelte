<script lang="ts">
  import { toast } from '$lib/stores/toast.svelte';

  const typeStyles: Record<string, string> = {
    info:    'border-neon-cyan/40 text-neon-cyan',
    success: 'border-neon-green/40 text-neon-green',
    error:   'border-neon-red/40 text-neon-red',
    warning: 'border-neon-yellow/40 text-neon-yellow'
  };

  const typeRings: Record<string, string> = {
    info:    'rgba(0, 229, 255, 0.15)',
    success: 'rgba(34, 255, 136, 0.15)',
    error:   'rgba(255, 51, 102, 0.15)',
    warning: 'rgba(251, 191, 36, 0.15)'
  };

  const typeIcons: Record<string, string> = {
    info: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    success: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
    error: 'M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z',
    warning: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z'
  };
</script>

{#if toast.toasts.length > 0}
  <div class="fixed bottom-10 right-4 z-[500] flex flex-col gap-2" data-testid="toast-container">
    {#each toast.toasts as item (item.id)}
      <div
        class="flex items-center gap-2 px-4 py-2.5 bg-bg-card border rounded-lg {typeStyles[item.type]}"
        style="box-shadow: inset 0 0 0 1px {typeRings[item.type] ?? 'transparent'}; animation: {item.dismissing ? 'slide-out-right 0.3s ease-in forwards' : 'slide-in-right 0.3s cubic-bezier(0.16, 1, 0.3, 1) both'}"
        role="alert"
        aria-live="assertive"
      >
        <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
          <path stroke-linecap="round" stroke-linejoin="round" d={typeIcons[item.type]}></path>
        </svg>
        <span class="text-sm text-text-primary">{item.message}</span>
        {#if item.action}
          <button
            class="ml-2 text-xs font-mono underline underline-offset-2 opacity-80 hover:opacity-100 transition-opacity shrink-0"
            onclick={(e) => { e.stopPropagation(); item.action!.onClick(); toast.dismiss(item.id); }}
          >{item.action.label}</button>
        {/if}
        <button
          class="ml-2 opacity-60 hover:opacity-100 transition-opacity"
          onclick={(e) => { e.stopPropagation(); toast.dismiss(item.id); }}
          aria-label="Dismiss"
        >
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"></path>
          </svg>
        </button>
      </div>
    {/each}
  </div>
{/if}
