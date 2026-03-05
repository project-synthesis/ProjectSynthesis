<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';

  let result = $derived(forge.stageResults['explore']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);
  let toolCalls = $derived(((data.tool_calls || data.files || []) as Array<Record<string, unknown>>));
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['explore'] === 'running'}
    <div class="flex items-center gap-2 text-neon-purple">
      <span class="w-3 h-3 rounded-full animate-spin" style="border: 2px solid transparent; border-top-color: #a855f7;"></span>
      <span>Exploring prompt context...</span>
    </div>
  {:else if result}
    <!-- Terminal-style tool call feed per spec: Geist Mono 11px, bg-input, stagger-fade-in -->
    {#if toolCalls.length > 0}
      <div class="bg-bg-input rounded-md p-2 space-y-1">
        {#each toolCalls as call, i}
          <div
            class="font-mono text-[11px] text-text-secondary animate-stagger-fade-in"
            style="animation-delay: {i * 50}ms;"
          >
            <span class="text-neon-purple/80">▸</span> {call.name || call.file || call.path || JSON.stringify(call)}
          </div>
        {/each}
      </div>
    {/if}

    <div class="space-y-1.5 font-mono text-[10px]">
      {#if data.domain}
        <div class="flex justify-between border-b border-border-subtle py-0.5">
          <span class="text-text-dim">Domain</span>
          <span class="text-text-secondary">{data.domain}</span>
        </div>
      {/if}
      {#if data.intent}
        <div class="flex justify-between border-b border-border-subtle py-0.5">
          <span class="text-text-dim">Intent</span>
          <span class="text-text-secondary">{data.intent}</span>
        </div>
      {/if}
      {#if data.complexity}
        <div class="flex justify-between border-b border-border-subtle py-0.5">
          <span class="text-text-dim">Complexity</span>
          <span class="text-text-secondary">{data.complexity}</span>
        </div>
      {/if}
    </div>

    {#if data.summary}
      <p class="text-text-dim mt-1 italic text-xs">{data.summary}</p>
    {/if}

    {#if toolCalls.length > 0}
      <div class="text-neon-purple/80 font-mono text-[10px]">
        Grounded in {toolCalls.length} file{toolCalls.length !== 1 ? 's' : ''}
      </div>
    {/if}
  {:else}
    <p class="text-text-dim">Waiting to start...</p>
  {/if}
</div>
