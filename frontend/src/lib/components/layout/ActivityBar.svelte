<script lang="ts">
  type Activity = 'editor' | 'history' | 'github' | 'settings';

  let { active = $bindable('editor') }: { active: Activity } = $props();

  const activities: { id: Activity; label: string }[] = [
    { id: 'editor', label: 'Editor' },
    { id: 'history', label: 'History' },
    { id: 'github', label: 'GitHub' },
    { id: 'settings', label: 'Settings' },
  ];
</script>

<nav
  class="activity-bar"
  style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);"
  aria-label="Activity bar"
>
  <!-- Brand mark at top of activity bar -->
  <div class="brand-mark" title="Project Synthesis">
    <svg width="12" height="12" viewBox="0 0 32 32" aria-hidden="true">
      <defs>
        <linearGradient id="ab" x1="0" y1="0" x2="1" y2="1"><stop offset="0%" stop-color="#00e5ff"/><stop offset="100%" stop-color="#a855f7"/></linearGradient>
        <clipPath id="abt"><rect x="0" y="0" width="32" height="15"/></clipPath>
        <clipPath id="abb"><rect x="0" y="17" width="32" height="15"/></clipPath>
      </defs>
      <g clip-path="url(#abt)" transform="translate(-1.5,0)"><g transform="translate(16,16) skewX(-10) translate(-16,-16)"><polyline fill="none" stroke="url(#ab)" stroke-width="4" stroke-linecap="square" stroke-linejoin="bevel" points="23,6 9,6 9,10 12,14 20,18 23,22 23,26 9,26"/></g></g>
      <g clip-path="url(#abb)" transform="translate(1.5,0)"><g transform="translate(16,16) skewX(-10) translate(-16,-16)"><polyline fill="none" stroke="url(#ab)" stroke-width="4" stroke-linecap="square" stroke-linejoin="bevel" points="23,6 9,6 9,10 12,14 20,18 23,22 23,26 9,26"/></g></g>
      <rect x="0" y="15" width="32" height="2" fill="var(--color-bg-secondary)"/>
    </svg>
  </div>

  {#each activities as act}
    <button
      class="activity-icon"
      class:active={active === act.id}
      onclick={() => (active = act.id)}
      title={act.label}
      aria-label={act.label}
      aria-pressed={active === act.id}
    >
      {#if act.id === 'editor'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><path d="M13.5 2.5l2 2-9 9H4.5v-2l9-9z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
      {:else if act.id === 'history'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><circle cx="9" cy="9" r="7" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M9 5v4l3 2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>
      {:else if act.id === 'github'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><circle cx="6" cy="4.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="12" cy="4.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="6" cy="13.5" r="1.5" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M6 6v6M12 6c0 4-6 4-6 6" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>
      {:else if act.id === 'settings'}
        <svg class="icon-svg" viewBox="0 0 18 18" aria-hidden="true"><path d="M7.5 2h3l.4 2.1a5.5 5.5 0 011.3.7l2-.8 1.5 2.6-1.6 1.3a5.5 5.5 0 010 1.5l1.6 1.3-1.5 2.6-2-.8a5.5 5.5 0 01-1.3.7L10.5 16h-3l-.4-2.1a5.5 5.5 0 01-1.3-.7l-2 .8-1.5-2.6 1.6-1.3a5.5 5.5 0 010-1.5L2.3 7.3l1.5-2.6 2 .8a5.5 5.5 0 011.3-.7z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><circle cx="9" cy="9" r="2.2" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>
      {/if}
    </button>
  {/each}
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
      color 200ms cubic-bezier(0.16, 1, 0.3, 1),
      background 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .activity-icon:hover {
    color: var(--color-text-primary);
    background: var(--color-bg-hover);
    border-color: transparent;
  }

  .activity-icon.active {
    color: var(--color-neon-cyan);
    border-color: transparent;
    background: transparent;
  }

  .activity-icon.active::before {
    content: '';
    position: absolute;
    left: -4px;
    top: 6px;
    bottom: 6px;
    width: 2px;
    background: var(--color-neon-cyan);
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
