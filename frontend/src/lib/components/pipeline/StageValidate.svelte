<script lang="ts">
  import { forge } from '$lib/stores/forge.svelte';
  import ScoreCircle from '$lib/components/shared/ScoreCircle.svelte';
  import ScoreBar from '$lib/components/shared/ScoreBar.svelte';

  let result = $derived(forge.stageResults['validate']);
  let data = $derived((result?.data || {}) as Record<string, unknown>);
  let scores = $derived((data.scores || {}) as Record<string, number>);

  // Codebase accuracy: show when explore completed with real context
  let exploreData = $derived((forge.stageResults['explore']?.data || {}) as Record<string, unknown>);
  let exploreQuality = $derived((exploreData.explore_quality as string) || '');
  let exploreRepo = $derived((exploreData.repo as string) || '');
  let hasCodebaseContext = $derived(
    forge.stageStatuses['explore'] === 'done' &&
    (exploreQuality === 'complete' || exploreQuality === 'partial') &&
    exploreRepo.length > 0
  );
</script>

<div class="space-y-2 text-xs">
  {#if forge.stageStatuses['validate'] === 'running'}
    <div class="flex items-center gap-2 text-neon-cyan">
      <span class="w-3 h-3 rounded-full animate-spin" style="border: 1px solid transparent; border-top-color: #00e5ff;"></span>
      <span>Validating optimized prompt...</span>
    </div>
  {:else if result}
    <!-- Overall score (28px per spec) -->
    {#if forge.overallScore != null}
      <div class="flex items-center gap-3 p-2 bg-bg-primary rounded-lg border border-border-subtle" style="box-shadow: inset 0 0 0 1px rgba(0, 229, 255, 0.4);">
        <ScoreCircle score={forge.overallScore} size={28} />
        <div>
          <span class="text-sm font-semibold text-text-primary">Overall Score</span>
          <span class="text-[10px] text-text-dim font-mono block">{forge.overallScore}/10</span>
        </div>
      </div>
    {/if}

    <!-- Individual scores (staggered 100ms per spec) -->
    {#each Object.entries(scores) as [key, val], i}
      <div class="space-y-1 animate-stagger-fade-in" style="animation-delay: {i * 100}ms;">
        <div class="flex justify-between">
          <span class="text-text-dim capitalize">{key.replace(/_/g, ' ')}</span>
          <div class="flex items-center gap-1.5">
            <ScoreCircle score={typeof val === 'number' ? val : 0} size={16} />
            <span class="text-text-secondary font-mono text-[10px]">{val}/10</span>
          </div>
        </div>
        <ScoreBar score={typeof val === 'number' ? val : 0} max={10} />
      </div>
    {/each}

    <!-- Verdict -->
    {#if data.verdict}
      <div class="mt-2 p-2 bg-bg-primary rounded-lg border border-border-subtle">
        <div class="flex items-center gap-2 mb-1">
          <span class="font-display text-[11px] font-bold uppercase text-text-dim">Verdict</span>
          {#if data.is_improvement === true}
            <span class="text-[10px] text-neon-green font-mono">✓ Improved</span>
          {:else if data.is_improvement === false}
            <span class="text-[10px] text-neon-red font-mono">✗ Not Improved</span>
          {/if}
        </div>
        <p class="text-xs text-text-secondary">{data.verdict}</p>
        {#if hasCodebaseContext}
          <p class="mt-1.5 font-mono text-[11px]" style="color: #7a7a9e;">
            <span style="color: var(--color-neon-teal, #00d4aa);">◆</span> Codebase accuracy verified against {exploreRepo}
          </p>
        {/if}
      </div>
    {/if}
  {:else}
    <p class="text-text-secondary">Waiting for Optimize stage...</p>
  {/if}
</div>
