<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { taxonomyColor } from '$lib/utils/colors';

  const suggestion = $derived(clustersStore.suggestion);
  const hasSuggestion = $derived(suggestion !== null);

  let selectedIds = $state<Set<string>>(new Set());

  // Re-seed selection whenever forgeStore.appliedPatternIds changes (mount +
  // post-apply round-trip). Selection does NOT reset on a new suggestion —
  // the user's toggles carry forward across cluster matches until APPLY
  // commits them, matching the C16 panel-persistence contract.
  $effect(() => {
    const initial = forgeStore.appliedPatternIds ?? [];
    selectedIds = new Set(initial);
  });

  function toggle(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedIds = next;
  }

  function truncate(text: string, n: number): string {
    return text.length <= n ? text : text.slice(0, n - 1) + '…';
  }

  const metaPatterns = $derived(suggestion?.meta_patterns ?? []);
  const metaSelectedCount = $derived(
    metaPatterns.filter((p) => selectedIds.has(p.id)).length,
  );

  const globalPatterns = $derived(suggestion?.cross_cluster_patterns ?? []);
  const globalSelectedCount = $derived(
    globalPatterns.filter((p) => selectedIds.has(p.id)).length,
  );

  const totalSelected = $derived(metaSelectedCount + globalSelectedCount);

  function apply() {
    if (totalSelected === 0 || !suggestion) return;
    forgeStore.appliedPatternIds = Array.from(selectedIds);
    forgeStore.appliedPatternLabel = `${suggestion.cluster.label} (${totalSelected})`;
  }

  const STORAGE_KEY = 'synthesis:context_panel_open';

  let isOpen = $state<boolean>(
    typeof localStorage !== 'undefined'
      ? (localStorage.getItem(STORAGE_KEY) ?? 'true') !== 'false'
      : true,
  );

  function toggleCollapse() {
    isOpen = !isOpen;
    try {
      localStorage.setItem(STORAGE_KEY, String(isOpen));
    } catch {
      /* ignore — private browsing etc. */
    }
  }

  const SYNTHESIS_STATES = new Set(['analyzing', 'optimizing', 'scoring', 'forging']);
  const isSynthesizing = $derived(SYNTHESIS_STATES.has(forgeStore.status));
</script>

{#if !isSynthesizing}
<!-- svelte-ignore a11y_no_redundant_roles -->
<aside
  class="context-panel"
  class:context-panel--collapsed={!isOpen}
  role="complementary"
  aria-label="Pattern context"
  data-test="context-panel"
  data-collapsed={!isOpen}
>
  <header class="panel-header">
    <span class="panel-title">CONTEXT</span>
    <button
      type="button"
      class="collapse-btn"
      onclick={toggleCollapse}
      aria-expanded={isOpen}
      aria-controls="context-panel-body"
      aria-label={isOpen ? 'Collapse pattern context' : 'Expand pattern context'}
    >
      {isOpen ? '∨' : '∧'}
    </button>
  </header>
  <div id="context-panel-body" class="panel-body" hidden={!isOpen}>

  {#if !hasSuggestion}
    <div class="empty-state">
      <p class="empty-copy">Start typing to see related clusters and patterns.</p>
      <p class="empty-sub">Waiting for prompt — at least 30 characters.</p>
    </div>
  {:else if suggestion}
    <section class="identity-row" aria-label="Matched cluster">
      <div class="identity-primary">
        <span
          class="domain-dot"
          data-test="domain-dot"
          style="background-color: {taxonomyColor(suggestion.cluster.domain)};"
        ></span>
        <span class="cluster-label">{suggestion.cluster.label}</span>
      </div>
      <div class="identity-meta">
        <span class="similarity">matched {Math.round(suggestion.similarity * 100)}%</span>
        <span class="meta-sep">·</span>
        <span class="match-level">{suggestion.match_level}</span>
      </div>
    </section>

    <section class="pattern-section" data-test="meta-section" aria-label="Meta-patterns">
      <header class="section-heading">
        <span class="section-title">META-PATTERNS</span>
        <span class="section-count" class:section-count--active={metaSelectedCount > 0}>
          {metaSelectedCount}/{metaPatterns.length}{metaSelectedCount > 0 ? ' ✔' : ''}
        </span>
      </header>
      <ul class="pattern-list">
        {#each metaPatterns as p (p.id)}
          <li class="pattern-row" data-test="pattern-row">
            <label class="pattern-label">
              <input
                type="checkbox"
                checked={selectedIds.has(p.id)}
                onchange={() => toggle(p.id)}
                aria-describedby="pattern-{p.id}-text"
              />
              <span id="pattern-{p.id}-text" class="pattern-text">{truncate(p.pattern_text, 60)}</span>
            </label>
          </li>
        {/each}
      </ul>
    </section>

    {#if globalPatterns.length > 0}
      <section class="pattern-section pattern-section--global" data-test="global-section" aria-label="Global patterns">
        <header class="section-heading">
          <span class="section-title">GLOBAL</span>
          <span class="section-count" class:section-count--active={globalSelectedCount > 0}>
            {globalSelectedCount}/{globalPatterns.length}{globalSelectedCount > 0 ? ' ✔' : ''}
          </span>
        </header>
        <ul class="pattern-list">
          {#each globalPatterns as p (p.id)}
            <li class="pattern-row" data-test="pattern-row">
              <label class="pattern-label">
                <input
                  type="checkbox"
                  checked={selectedIds.has(p.id)}
                  onchange={() => toggle(p.id)}
                  aria-describedby="pattern-{p.id}-text"
                />
                <span id="pattern-{p.id}-text" class="pattern-text">{truncate(p.pattern_text, 60)}</span>
              </label>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <footer class="apply-footer">
      <button
        type="button"
        class="apply-btn"
        disabled={totalSelected === 0}
        onclick={apply}
      >
        APPLY {totalSelected}
      </button>
    </footer>
  {/if}
  </div>
</aside>
{/if}

<style>
  .context-panel {
    display: flex;
    flex-direction: column;
    width: 240px;
    height: 100%;
    background: var(--color-bg-secondary);
    border-left: 1px solid var(--color-border-subtle);
    font-family: var(--font-sans);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
    padding: 0 6px;
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .empty-state {
    padding: 6px;
    color: var(--color-text-secondary);
    font-size: 11px;
  }

  .empty-copy { margin: 0 0 4px 0; }
  .empty-sub { margin: 0; color: var(--color-text-dim); font-size: 10px; }

  .identity-row {
    padding: 4px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .identity-primary {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 20px;
    color: var(--color-text-primary);
  }
  .domain-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    flex-shrink: 0;
  }
  .cluster-label {
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .identity-meta {
    height: 18px;
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }
  .meta-sep { color: var(--color-text-dim); }
  .match-level { font-variant: tabular-nums; }

  .pattern-section { border-bottom: 1px solid var(--color-border-subtle); }
  .section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 20px;
    padding: 0 6px;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }
  .section-count {
    font-family: var(--font-mono);
    font-size: 10px;
  }
  .section-count--active { color: var(--color-neon-cyan); }
  .pattern-list { list-style: none; padding: 0; margin: 0; }
  .pattern-row { height: 20px; border-top: 1px solid var(--color-border-subtle); padding: 0 6px; }
  .pattern-label { display: flex; align-items: center; gap: 6px; height: 20px; cursor: pointer; }
  .pattern-label input[type="checkbox"] {
    appearance: none;
    width: 10px;
    height: 10px;
    margin: 0;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    cursor: pointer;
  }
  .pattern-label input[type="checkbox"]:hover {
    border-color: var(--color-neon-cyan);
  }
  .pattern-label input[type="checkbox"]:checked {
    border-color: var(--color-neon-cyan);
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
  }
  .pattern-label input[type="checkbox"]:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
  .pattern-text {
    font-size: 11px;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .pattern-section--global {
    border-left: 1px solid var(--color-neon-purple);
  }

  .apply-footer {
    padding: 4px 6px;
    display: flex;
    justify-content: flex-end;
  }
  .apply-btn {
    height: 20px;
    padding: 0 8px;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-cyan);
    background: transparent;
    border: 1px solid var(--color-neon-cyan);
    cursor: pointer;
    transition: all var(--duration-hover) var(--ease-spring);
  }
  .apply-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    background: color-mix(in srgb, var(--color-neon-cyan) 6%, transparent);
  }
  .apply-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .apply-btn:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .context-panel--collapsed { width: 28px; }
  .collapse-btn {
    height: 20px;
    padding: 0 4px;
    background: transparent;
    border: none;
    color: var(--color-text-dim);
    cursor: pointer;
    font-family: var(--font-mono);
    transition: color var(--duration-hover) var(--ease-spring);
  }
  .collapse-btn:hover { color: var(--color-text-primary); }
  .collapse-btn:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }
</style>
