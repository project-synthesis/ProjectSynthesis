<script lang="ts">
  import type { EditorTab } from '$lib/stores/editor.svelte';
  import { editor } from '$lib/stores/editor.svelte';
  import { workbench } from '$lib/stores/workbench.svelte';
  import { github } from '$lib/stores/github.svelte';
  import { history } from '$lib/stores/history.svelte';
  import { user } from '$lib/stores/user.svelte';
  import { samplePrompts, categoryColors, difficultyColors, type SamplePrompt } from '$lib/utils/samplePrompts';
  import { pipelineStages } from '$lib/utils/strategyReference';
  import { trackOnboardingEvent } from '$lib/api/client';

  let { tab }: { tab: EditorTab } = $props();

  // Checklist items — reactive
  let checklistItems = $derived([
    { label: 'Backend connected', done: workbench.isConnected, action: null },
    { label: 'MCP server online', done: workbench.mcpConnected, action: null },
    { label: 'GitHub account linked', done: github.isConnected, action: () => workbench.setActivity('github') },
    { label: 'Repository selected', done: !!github.selectedRepo, action: () => workbench.setActivity('github') },
    { label: 'First synthesis complete', done: history.totalCount > 0, action: () => { if (samplePrompts.length > 0) loadSample(samplePrompts[0]); } },
  ]);

  let completedCount = $derived(checklistItems.filter(i => i.done).length);
  let progressPct = $derived((completedCount / checklistItems.length) * 100);

  const stages = pipelineStages;

  let expandedStage = $state<string | null>(null);

  // Keyboard shortcuts organized by category
  const shortcuts = {
    General: [
      { keys: 'Ctrl+K', action: 'Command Palette' },
      { keys: 'Ctrl+N', action: 'New Prompt' },
      { keys: 'Ctrl+W', action: 'Close Tab' },
      { keys: 'Ctrl+S', action: 'Save' },
    ],
    Navigation: [
      { keys: 'Ctrl+B', action: 'Toggle Navigator' },
      { keys: 'Ctrl+I', action: 'Toggle Inspector' },
      { keys: 'F6', action: 'Cycle Zones' },
      { keys: 'Ctrl+Tab', action: 'Next Tab' },
    ],
    Forge: [
      { keys: 'Ctrl+Enter', action: 'Synthesize' },
      { keys: 'Escape', action: 'Cancel Forge' },
      { keys: '@', action: 'Context Sources' },
      { keys: 'Ctrl+1-8', action: 'Switch to Tab N' },
    ],
  };

  function loadSample(sample: SamplePrompt) {
    // Write to the tab via the store-owned object
    const storeTab = editor.openTabs.find(t => t.id === tab.id);
    if (storeTab) {
      storeTab.promptText = sample.text;
      storeTab.label = sample.title;
    }
    trackOnboardingEvent('sample_loaded', { sample: sample.id }).catch(() => {});
  }
</script>

<div class="h-full overflow-y-auto p-6" style="overscroll-behavior: contain;">
  <div class="max-w-2xl mx-auto space-y-8">

    <!-- Section 1: Header -->
    <div class="text-center">
      <h1 class="font-display text-lg tracking-[0.2em] uppercase text-transparent bg-clip-text bg-gradient-to-r from-neon-cyan to-neon-purple mb-1">
        PROJECT SYNTHESIS
      </h1>
      <span class="inline-block px-2 py-0.5 border border-neon-cyan/30 font-mono text-[8px] text-neon-cyan/70 uppercase tracking-[0.1em]">
        Quick Start Guide
      </span>
    </div>

    <!-- Section 2: Setup Checklist -->
    <div class="border border-border-subtle p-4">
      <h2 class="font-display text-[11px] uppercase tracking-[0.08em] text-text-primary mb-3">Setup Checklist</h2>

      <!-- Progress bar -->
      <div class="h-1 bg-bg-input mb-3 overflow-hidden">
        <div
          class="h-full bg-neon-cyan transition-all duration-500"
          style="width: {progressPct}%"
        ></div>
      </div>

      <div class="space-y-1.5">
        {#each checklistItems as item}
          <div class="flex items-center gap-2">
            <span class="w-3 h-3 flex items-center justify-center shrink-0 border {item.done ? 'border-neon-green bg-neon-green/10' : 'border-border-subtle'}">
              {#if item.done}
                <svg class="w-2 h-2 text-neon-green" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="3">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path>
                </svg>
              {/if}
            </span>
            <span class="font-mono text-[10px] {item.done ? 'text-text-dim line-through' : 'text-text-secondary'}">
              {item.label}
            </span>
            {#if !item.done && item.action}
              <button
                onclick={item.action}
                class="font-mono text-[9px] text-neon-cyan/70 hover:text-neon-cyan ml-auto transition-colors"
              >SET UP</button>
            {/if}
          </div>
        {/each}
      </div>

      <div class="mt-2 font-mono text-[9px] text-text-dim/50">{completedCount}/{checklistItems.length} complete</div>
    </div>

    <!-- Section 3: Sample Prompts Gallery -->
    <div>
      <div class="flex items-center justify-between mb-3">
        <h2 class="font-display text-[11px] uppercase tracking-[0.08em] text-text-primary">Sample Prompts</h2>
        <button
          onclick={() => workbench.setActivity('templates')}
          class="font-mono text-[9px] text-neon-cyan/60 hover:text-neon-cyan transition-colors"
        >VIEW ALL TEMPLATES</button>
      </div>

      <div class="flex gap-3 overflow-x-auto pb-2" style="-webkit-overflow-scrolling: touch;">
        {#each samplePrompts.slice(0, 6) as sample}
          <div class="shrink-0 w-56 border border-border-subtle p-3 space-y-2 hover:border-neon-cyan/20 transition-colors">
            <div class="flex items-center gap-2">
              <span class="inline-block px-1.5 py-0.5 border font-mono text-[7px] uppercase" style="color: {categoryColors[sample.category]}; border-color: {categoryColors[sample.category]}40">
                {sample.category}
              </span>
              <span class="font-mono text-[7px] uppercase" style="color: {difficultyColors[sample.difficulty]}">
                {sample.difficulty}
              </span>
            </div>
            <div class="font-display text-[10px] uppercase text-text-primary">{sample.title}</div>
            <div class="font-mono text-[8px] text-text-dim leading-snug line-clamp-2">{sample.description}</div>
            <button
              onclick={() => loadSample(sample)}
              class="w-full px-2 py-1 border border-neon-cyan/30 font-mono text-[9px] text-neon-cyan uppercase tracking-[0.05em] hover:bg-neon-cyan/5 transition-colors"
            >TRY THIS</button>
          </div>
        {/each}
      </div>
    </div>

    <!-- Section 4: Pipeline Stages Reference -->
    <div>
      <h2 class="font-display text-[11px] uppercase tracking-[0.08em] text-text-primary mb-3">Pipeline Stages</h2>

      <div class="flex items-center gap-1 mb-3 overflow-x-auto">
        {#each stages as stage, i}
          {#if i > 0}
            <svg class="w-3 h-3 text-text-dim/30 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"></path>
            </svg>
          {/if}
          <button
            class="px-2 py-1 border font-mono text-[9px] uppercase shrink-0 transition-colors
              {expandedStage === stage.name ? 'bg-opacity-10' : 'hover:border-opacity-60'}"
            style="color: {stage.color}; border-color: {expandedStage === stage.name ? stage.color : stage.color + '30'};
              {expandedStage === stage.name ? `background-color: ${stage.color}10` : ''}"
            onclick={() => expandedStage = expandedStage === stage.name ? null : stage.name}
          >{stage.name}</button>
        {/each}
      </div>

      {#if expandedStage}
        {@const stage = stages.find(s => s.name === expandedStage)}
        {#if stage}
          <div class="p-3 border border-border-subtle animate-fade-in" style="border-left: 2px solid {stage.color};">
            <div class="font-display text-[10px] uppercase text-text-primary">{stage.name}</div>
            <div class="font-mono text-[9px] text-text-dim mt-1 leading-relaxed">{stage.desc}</div>
          </div>
        {/if}
      {/if}
    </div>

    <!-- Section 5: Keyboard Reference -->
    <div>
      <h2 class="font-display text-[11px] uppercase tracking-[0.08em] text-text-primary mb-3">Keyboard Shortcuts</h2>
      <div class="grid grid-cols-3 gap-4">
        {#each Object.entries(shortcuts) as [category, items]}
          <div>
            <div class="font-mono text-[8px] text-neon-cyan/50 uppercase tracking-[0.1em] mb-1.5">{category}</div>
            <div class="space-y-1">
              {#each items as shortcut}
                <div class="flex items-center justify-between gap-1">
                  <kbd class="px-1 bg-bg-input border border-border-subtle text-[8px] text-text-dim font-mono shrink-0" style="padding-top:1px;padding-bottom:1px;">{shortcut.keys}</kbd>
                  <span class="font-mono text-[8px] text-text-dim/60 text-right">{shortcut.action}</span>
                </div>
              {/each}
            </div>
          </div>
        {/each}
      </div>
    </div>
  </div>
</div>
