<script lang="ts">
  import { workbench, type Activity } from '$lib/stores/workbench.svelte';
  import HelixMark from '$lib/components/shared/HelixMark.svelte';

  const activities: { id: Activity; icon: string; label: string }[] = [
    { id: 'files', icon: 'M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z', label: 'Files' },
    { id: 'history', icon: 'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z', label: 'History' },
    { id: 'chains', icon: 'M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1', label: 'Chains' },
    { id: 'templates', icon: 'M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z', label: 'Templates' },
    { id: 'github', icon: 'M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z', label: 'GitHub' },
    { id: 'search', icon: 'M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z', label: 'Search' },
    { id: 'settings', icon: 'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z M15 12a3 3 0 11-6 0 3 3 0 016 0z', label: 'Settings' }
  ];

  function handleKeydown(e: KeyboardEvent, index: number) {
    let newIndex = index;
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      newIndex = (index + 1) % activities.length;
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      newIndex = (index - 1 + activities.length) % activities.length;
    } else if (e.key === 'Home') {
      e.preventDefault();
      newIndex = 0;
    } else if (e.key === 'End') {
      e.preventDefault();
      newIndex = activities.length - 1;
    } else {
      return;
    }
    const btns = document.querySelectorAll<HTMLElement>('[data-activity-btn]');
    btns[newIndex]?.focus();
  }
</script>

<nav
  class="flex flex-col items-center w-[40px] h-full bg-bg-secondary border-r border-border-subtle py-2 gap-1"
  aria-label="Activity Bar"
>
  {#each activities as act, i}
    <button
      class="w-8 h-8 flex items-center justify-center rounded-md transition-colors duration-150 relative
        focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-neon-cyan/30 focus-visible:ring-offset-0
        {workbench.activeActivity === act.id && !workbench.navigatorCollapsed
          ? 'bg-neon-cyan/[0.08] text-neon-cyan'
          : 'text-text-dim hover:text-text-secondary hover:bg-bg-hover'}"
      title={act.label}
      aria-label={act.label}
      data-activity-btn
      onclick={() => workbench.setActivity(act.id)}
      onkeydown={(e: KeyboardEvent) => handleKeydown(e, i)}
    >
      <!-- Active left border indicator (1px neon-cyan) -->
      {#if workbench.activeActivity === act.id && !workbench.navigatorCollapsed}
        <span class="absolute left-0 top-1 bottom-1 w-[1px] bg-neon-cyan"></span>
      {/if}
      <svg class="w-[18px] h-[18px]" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d={act.icon}></path>
      </svg>
    </button>
  {/each}

  <div class="flex-1"></div>

  <!-- Branding at bottom -->
  <div class="w-8 h-8 flex items-center justify-center" title="Project Synthesis">
    <HelixMark size={20} instanceId={7} opacity={0.7} />
  </div>
</nav>
