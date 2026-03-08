<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import StageTrack from '$lib/components/pipeline/StageTrack.svelte';
  import RunHeader from '$lib/components/pipeline/RunHeader.svelte';
  import ForgeArtifact from './ForgeArtifact.svelte';

  // Show the forge artifact view when a completed run is loaded (not actively forging)
  let showArtifact = $derived(!forge.isForging && forge.completedStages > 0 && forge.streamingText);
</script>

<div class="flex flex-col h-full animate-slide-in-right" aria-live="polite">
  {#if !forge.isForging && forge.completedStages === 0 && !forge.error}
    <div class="flex flex-col items-center justify-center h-full gap-4 py-16 animate-fade-in">
      <div class="w-10 h-10 border border-border-subtle flex items-center justify-center opacity-30">
        <span class="text-gradient-forge font-display font-bold text-base">⚡</span>
      </div>
      <div class="text-center space-y-1.5">
        <p class="section-heading text-[11px]">Ready to Synthesize</p>
        <p class="text-[11px] text-text-dim/60 leading-relaxed">
          Press <kbd>Ctrl+Enter</kbd> or click<br>the Synthesize button to start
        </p>
      </div>
    </div>
  {:else if forge.error && !forge.isForging && forge.completedStages === 0}
    <div class="flex flex-col items-center justify-center h-full gap-2 py-16">
      <svg class="w-5 h-5 opacity-60 text-neon-red" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
        <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"></path>
      </svg>
      <span class="text-xs text-neon-red">Pipeline failed to start</span>
      <span class="text-[11px] text-text-dim font-mono text-center max-w-xs truncate">{forge.error}</span>
    </div>
  {:else if showArtifact}
    <ForgeArtifact />
  {:else}
    <div class="flex-1 min-h-0 overflow-y-auto p-4 space-y-4" style="overscroll-behavior: contain;">
      <RunHeader />
      <StageTrack />
    </div>
  {/if}
</div>
