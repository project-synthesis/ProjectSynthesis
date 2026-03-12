<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
</script>

<div class="space-y-2 text-xs" aria-live="polite">
  {#if forge.stageStatuses['optimize'] === 'running'}
    <div class="space-y-2">
      <div class="flex items-center gap-2 text-neon-cyan">
        <span class="w-3 h-3 rounded-full animate-spin" style="border: 1px solid transparent; border-top-color: #00e5ff;"></span>
        <span>Generating optimized prompt...</span>
      </div>

      {#if forge.streamingText}
        <div class="bg-bg-primary border border-border-accent rounded-lg p-3 relative">
          <pre class="text-text-primary text-[13px] font-sans whitespace-pre-wrap leading-relaxed">{forge.streamingText}<span class="streaming-cursor"></span></pre>
        </div>
      {:else if forge.optimizeStreaming === false}
        <!-- Confirmed batch mode: indeterminate progress bar while waiting for atomic response -->
        <div class="bg-bg-primary border border-border-subtle p-3">
          <div class="h-0.5 w-full bg-border-subtle overflow-hidden">
            <div class="h-full w-1/3 bg-neon-cyan/40 animate-indeterminate"></div>
          </div>
          <p class="text-text-dim text-[10px] mt-2">Processing prompt (batch mode)</p>
        </div>
      {:else}
        <!-- Streaming enabled but no text yet (adaptive thinking phase) -->
        <div class="bg-bg-primary border border-border-subtle p-3">
          <div class="h-0.5 w-full bg-border-subtle overflow-hidden">
            <div class="h-full w-1/3 bg-neon-cyan/40 animate-indeterminate"></div>
          </div>
          <p class="text-text-dim text-[10px] mt-2">Thinking…</p>
        </div>
      {/if}
    </div>
  {:else if forge.stageStatuses['optimize'] === 'done'}
    <div class="bg-bg-primary border border-neon-green/20 rounded-lg p-3 relative">
      <div class="flex items-center justify-between mb-2">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim">Optimized Prompt</span>
        {#if forge.streamingText}
          <CopyButton text={forge.streamingText} />
        {/if}
      </div>
      <pre class="text-text-primary text-[13px] font-sans whitespace-pre-wrap leading-relaxed">{forge.streamingText || 'No output generated.'}</pre>
    </div>

    <!-- Word count delta badges -->
    {#if forge.rawPrompt && forge.streamingText}
      {@const originalWords = forge.rawPrompt.split(/\s+/).filter(Boolean).length}
      {@const optimizedWords = forge.streamingText.split(/\s+/).filter(Boolean).length}
      {@const delta = optimizedWords - originalWords}
      <div class="flex gap-2 mt-1">
        <span class="font-mono text-[10px] {delta >= 0 ? 'text-neon-green' : 'text-neon-red'}">
          {delta >= 0 ? '+' : ''}{delta} words
        </span>
      </div>
    {/if}

    <!-- Changes Made (N16) -->
    {@const optimizeData = (forge.stageResults['optimize']?.data || {}) as Record<string, unknown>}
    {@const changesMade = (optimizeData.changes_made || []) as string[]}
    {#if changesMade.length > 0}
      <div class="mt-2">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim">Changes Made</span>
        <ul class="mt-1 space-y-0.5">
          {#each changesMade as change}
            <li class="flex gap-1.5 items-start">
              <span class="text-neon-cyan/60 shrink-0 font-mono">→</span>
              <span class="text-text-secondary text-xs">{change}</span>
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {:else}
    <p class="text-text-secondary">Waiting for Strategy stage...</p>
  {/if}
</div>
