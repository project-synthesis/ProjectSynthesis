<script lang="ts">
  import type { RoleListSection } from '$lib/content/types';

  interface Props {
    roles: RoleListSection['roles'];
  }

  let { roles }: Props = $props();
</script>

<div class="role-list">
  {#each roles as role, i}
    <div class="role-card" data-reveal style="--i:{i}">
      <div class="role-card__left">
        <h3 class="role-card__title">{role.title}</h3>
        <p class="role-card__desc">{role.description}</p>
      </div>
      <div class="role-card__right">
        <span class="role-card__type font-mono">{role.type}</span>
        <span class="role-card__dept font-mono">{role.department}</span>
      </div>
    </div>
  {/each}
</div>

<style>
  .role-list {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .role-card {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 16px;
    padding: 8px;
    background: var(--color-bg-card);
    border: 1px solid var(--color-border-subtle);
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .role-card:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
  }

  .role-card__left {
    flex: 1;
    min-width: 0;
  }

  .role-card__title {
    font-size: 12px;
    font-weight: 600;
    color: var(--color-text-primary);
    margin: 0 0 3px 0;
  }

  .role-card__desc {
    font-size: 11px;
    color: var(--color-text-secondary);
    margin: 0;
    line-height: 1.5;
  }

  .role-card__right {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 4px;
    flex-shrink: 0;
  }

  .role-card__type {
    font-size: 9px;
    font-weight: 600;
    color: var(--color-neon-green);
    border: 1px solid rgba(34, 255, 136, 0.2);
    padding: 1px 4px;
    white-space: nowrap;
  }

  .role-card__dept {
    font-size: 9px;
    color: var(--color-text-dim);
    white-space: nowrap;
  }
</style>
