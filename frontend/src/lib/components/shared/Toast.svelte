<script lang="ts">
  let { message = '', type = 'info', visible = false }: {
    message?: string;
    type?: 'info' | 'success' | 'error' | 'warning';
    visible?: boolean;
  } = $props();

  const typeStyles: Record<string, string> = {
    info: 'border-neon-cyan/30 text-neon-cyan',
    success: 'border-neon-green/30 text-neon-green',
    error: 'border-neon-red/30 text-neon-red',
    warning: 'border-neon-yellow/30 text-neon-yellow'
  };

  const typeIcons: Record<string, string> = {
    info: 'M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    success: 'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
    error: 'M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z',
    warning: 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L3.34 16.5c-.77.833.192 2.5 1.732 2.5z'
  };
</script>

{#if visible}
  <div class="fixed bottom-12 right-4 z-[500] animate-slide-up-in">
    <div class="flex items-center gap-2 px-4 py-2.5 bg-bg-card border rounded-lg {typeStyles[type]}">
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d={typeIcons[type]}></path>
      </svg>
      <span class="text-sm text-text-primary">{message}</span>
    </div>
  </div>
{/if}
