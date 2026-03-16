<script lang="ts">
  type Activity = 'editor' | 'history' | 'github' | 'settings';

  let { active = $bindable('editor') }: { active: Activity } = $props();

  const activities: { id: Activity; icon: string; label: string }[] = [
    { id: 'editor', icon: '✎', label: 'Editor' },
    { id: 'history', icon: '⏱', label: 'History' },
    { id: 'github', icon: '⑂', label: 'GitHub' },
    { id: 'settings', icon: '⚙', label: 'Settings' },
  ];
</script>

<nav
  class="activity-bar"
  style="background: var(--color-bg-secondary); border-right: 1px solid var(--color-border-subtle);"
  aria-label="Activity bar"
>
  {#each activities as act}
    <button
      class="activity-icon"
      class:active={active === act.id}
      onclick={() => (active = act.id)}
      title={act.label}
      aria-label={act.label}
      aria-pressed={active === act.id}
    >
      <span class="text-base">{act.icon}</span>
    </button>
  {/each}
</nav>

<style>
  .activity-bar {
    display: flex;
    flex-direction: column;
    align-items: center;
    padding-top: 8px;
    gap: 2px;
    height: 100%;
  }

  .activity-icon {
    width: 40px;
    height: 40px;
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
    top: 8px;
    bottom: 8px;
    width: 2px;
    background: var(--color-neon-cyan);
  }

  .activity-icon:active {
    transform: none;
    border-color: transparent;
  }
</style>
