<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import CopyButton from '$lib/components/shared/CopyButton.svelte';
</script>

<div class="space-y-2 text-xs" aria-live="polite">
  {#if forge.stageStatuses['optimize'] === 'running'}
    <div class="space-y-2">
      <div class="flex items-center gap-2 text-neon-cyan">
        <span class="w-3 h-3 rounded-full animate-spin" style="border: 2px solid transparent; border-top-color: #00e5ff;"></span>
        <span>Generating optimized prompt...</span>
      </div>

      {#if forge.streamingText}
        <div class="bg-bg-primary border border-border-accent rounded-lg p-3 relative">
          <pre class="text-text-primary text-[13px] font-sans whitespace-pre-wrap leading-relaxed">{forge.streamingText}<span class="streaming-cursor"></span></pre>
        </div>
      {/if}
    </div>
  {:else if forge.stageStatuses['optimize'] === 'done'}
    <div class="bg-bg-primary border border-neon-green/20 rounded-lg p-3 relative">
      <div class="flex items-center justify-between mb-2">
        <span class="font-display text-[11px] font-bold uppercase text-text-dim" style="letter-spacing: 0.08em;">Optimized Prompt</span>
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
  {:else}
    <p class="text-text-dim">Waiting for Strategy stage...</p>
  {/if}
</div>
