<script lang="ts">
  import Logo from '$lib/components/shared/Logo.svelte';
  import { tooltip } from '$lib/actions/tooltip';
  import { ACTIVITY_TOOLTIPS } from '$lib/utils/ui-tooltips';
  import { handleTablistArrowKeys } from '$lib/utils/keyboard';

  type Activity = 'editor' | 'history' | 'clusters' | 'github' | 'settings';

  let { active = $bindable('editor') }: { active: Activity } = $props();

  const activities: { id: Activity; label: string }[] = [
    { id: 'editor', label: 'Editor' },
    { id: 'history', label: 'History' },
    { id: 'clusters', label: 'Clusters' },
    { id: 'github', label: 'GitHub' },
    { id: 'settings', label: 'Settings' },
  ];

  const activityIds = $derived(activities.map((a) => a.id));

  function onKeyDown(event: KeyboardEvent) {
    handleTablistArrowKeys(
      event,
      { items: activityIds, current: active, orientation: 'vertical' },
      (next) => {
        active = next;
      },
    );
  }
</script>

<nav
  class="activity-bar"
  style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);"
  aria-label="Activity bar"
>
  <!-- Brand mark at top of activity bar -->
  <div class="brand-mark" use:tooltip={ACTIVITY_TOOLTIPS.brand}>
    <Logo size={24} variant="mark" />
  </div>

  <div
    class="activity-tablist"
    role="tablist"
    aria-orientation="vertical"
    aria-label="Primary sections"
    tabindex={-1}
    onkeydown={onKeyDown}
  >
  {#each activities as act}
    <button
      class="activity-icon"
      class:active={active === act.id}
      onclick={() => (active = act.id)}
      use:tooltip={act.label}
      role="tab"
      aria-label={act.label}
      aria-selected={active === act.id}
      tabindex={active === act.id ? 0 : -1}
    >
      {#if act.id === 'editor'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><path d="M13.5 2.5l2 2-9 9H4.5v-2l9-9z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      {:else if act.id === 'history'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9 5v4l3 2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      {:else if act.id === 'clusters'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="4" cy="13" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="14" cy="13" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 6.5L5 11.5M10 6.5L13 11.5M5.5 13L12.5 13" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
      {:else if act.id === 'github'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><circle cx="6" cy="4.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="4.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="6" cy="13.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M6 6v6M12 6c0 4-6 4-6 6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
      {:else if act.id === 'settings'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><path d="M7.5 2h3l.4 2.1a5.5 5.5 0 011.3.7l2-.8 1.5 2.6-1.6 1.3a5.5 5.5 0 010 1.5l1.6 1.3-1.5 2.6-2-.8a5.5 5.5 0 01-1.3.7L10.5 16h-3l-.4-2.1a5.5 5.5 0 01-1.3-.7l-2 .8-1.5-2.6 1.6-1.3a5.5 5.5 0 010-1.5L2.3 7.3l1.5-2.6 2 .8a5.5 5.5 0 011.3-.7z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><circle cx="9" cy="9" r="2.2" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>
      {/if}
    </button>
  {/each}
  </div>
</nav>

<style>
  .activity-bar {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding-top: 0;
    gap: 1px;
    height: 100%;
  }

  .activity-tablist {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 1px;
    width: 100%;
  }

  .activity-icon {
    width: 28px;
    height: 28px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: none;
    background: transparent;
    color: var(--color-text-dim);
    cursor: pointer;
    position: relative;
    transition:
      color var(--duration-hover) var(--ease-spring),
      background var(--duration-hover) var(--ease-spring);
  }

  .activity-icon:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: transparent;
  }

  .activity-icon.active {
    color: var(--tier-accent, var(--color-neon-cyan));
    border-color: transparent;
    background: transparent;
  }

  .activity-icon.active::before {
    content: '';
    position: absolute;
    left: -3px;
    top: 6px;
    bottom: 6px;
    width: 1px;
    background: var(--tier-accent, var(--color-neon-cyan));
  }

  .activity-icon:active {
    transform: none;
    border-color: transparent;
  }

  .brand-mark {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 24px;
    flex-shrink: 0;
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .icon-svg {
    width: 14px;
    height: 14px;
    flex-shrink: 0;
  }
</style>
