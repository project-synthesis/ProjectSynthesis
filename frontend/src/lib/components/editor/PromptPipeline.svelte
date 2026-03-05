<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import StageTrack from '$lib/components/pipeline/StageTrack.svelte';
  import RunHeader from '$lib/components/pipeline/RunHeader.svelte';
  import ForgeArtifact from './ForgeArtifact.svelte';

  // Show the forge artifact view when a completed run is loaded (not actively forging)
  let showArtifact = $derived(!forge.isForging && forge.completedStages > 0 && forge.streamingText);
</script>

<div class="flex flex-col h-full animate-slide-in-right">
  {#if !forge.isForging && forge.completedStages === 0}
    <div class="flex flex-col items-center justify-center py-16 text-center">
      <svg class="w-12 h-12 text-text-dim/30 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1">
        <path stroke-linecap="round" stroke-linejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z"></path>
      </svg>
      <p class="text-sm text-text-secondary mb-1">No pipeline running</p>
      <p class="text-xs text-text-dim">Click "Forge" in the Edit tab to start optimizing your prompt.</p>
    </div>
  {:else if showArtifact}
    <ForgeArtifact />
  {:else}
    <div class="p-4 space-y-4">
      <RunHeader />
      <StageTrack />
    </div>
  {/if}
</div>
