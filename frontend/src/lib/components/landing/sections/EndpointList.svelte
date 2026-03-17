<script lang="ts">
  import type { EndpointListSection } from '$lib/content/types';

  interface Props {
    groups: EndpointListSection['groups'];
  }

  let { groups }: Props = $props();

  const METHOD_COLORS: Record<string, string> = {
    GET:    '#4d8eff',
    POST:   '#22ff88',
    PUT:    '#ff8c00',
    DELETE: '#ff3366',
    PATCH:  '#fbbf24',
    SSE:    '#00e5ff',
    TOOL:   '#00e5ff',
  };
</script>

<div class="endpoint-list">
  {#each groups as group, gi}
    <div class="endpoint-group" data-reveal style="--i:{gi}">
      <h3 class="endpoint-group__name font-display">{group.name}</h3>
      <div class="endpoint-group__rows">
        {#each group.endpoints as ep}
          {#if ep.details}
            <details class="endpoint-row endpoint-row--details">
              <summary class="endpoint-row__summary">
                <span
                  class="endpoint-row__badge font-mono"
                  style="background:{METHOD_COLORS[ep.method] ?? '#8b8ba8'}"
                >{ep.method}</span>
                <span class="endpoint-row__path font-mono">{ep.path}</span>
                <span class="endpoint-row__desc">{ep.description}</span>
              </summary>
              <div class="details-content endpoint-row__details-body">
                {ep.details}
              </div>
            </details>
          {:else}
            <div class="endpoint-row">
              <span
                class="endpoint-row__badge font-mono"
                style="background:{METHOD_COLORS[ep.method] ?? '#8b8ba8'}"
              >{ep.method}</span>
              <span class="endpoint-row__path font-mono">{ep.path}</span>
              <span class="endpoint-row__desc">{ep.description}</span>
            </div>
          {/if}
        {/each}
      </div>
    </div>
  {/each}
</div>

<style>
  .endpoint-list {
    display: flex;
    flex-direction: column;
    gap: 20px;
  }

  .endpoint-group__name {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-secondary);
    margin: 0 0 8px 0;
  }

  .endpoint-group__rows {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .endpoint-row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 4px 0;
  }

  .endpoint-row--details {
    display: block;
    padding: 0;
  }

  .endpoint-row__summary {
    display: flex;
    align-items: baseline;
    gap: 8px;
    padding: 4px 0;
    cursor: pointer;
    list-style: none;
  }

  .endpoint-row__summary::-webkit-details-marker {
    display: none;
  }

  .endpoint-row__badge {
    display: inline-block;
    font-size: 9px;
    font-weight: 700;
    padding: 1px 4px;
    color: #06060c;
    flex-shrink: 0;
    line-height: 1.4;
  }

  .endpoint-row__path {
    font-size: 11px;
    color: var(--color-text-primary);
    flex-shrink: 0;
  }

  .endpoint-row__desc {
    font-size: 11px;
    color: var(--color-text-secondary);
  }

  .endpoint-row__details-body {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 4px 0 8px 0;
    margin-left: calc(9px * 3 + 8px * 2);
    line-height: 1.6;
  }
</style>
