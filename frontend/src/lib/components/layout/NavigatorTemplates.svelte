<script lang="ts">
  import { editor } from '$lib/stores/editor.svelte';

  const builtInTemplates = [
    { name: 'system-prompt.md', description: 'System prompt template for AI assistants' },
    { name: 'code-review.md', description: 'Code review request prompt template' },
    { name: 'data-analysis.md', description: 'Data analysis task prompt template' }
  ];

  function openTemplate(name: string) {
    editor.openTab({
      id: `template-${name}`,
      label: name,
      type: 'prompt',
      promptText: `# Template: ${name}\n\nEdit this template to get started.`,
      dirty: false
    });
  }
</script>

<div class="p-2 space-y-2">
  <div class="flex items-center justify-between px-1">
    <span class="text-[10px] uppercase tracking-wider text-text-dim font-semibold">Templates</span>
  </div>

  {#if builtInTemplates.length > 0}
    <div class="space-y-0.5">
      {#each builtInTemplates as template}
        <button
          class="w-full flex items-center gap-2 px-2 py-1.5 rounded text-left text-xs text-text-secondary hover:bg-bg-hover hover:text-text-primary transition-colors group"
          onclick={() => openTemplate(template.name)}
        >
          <svg class="w-3.5 h-3.5 text-neon-purple/60 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.5">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"></path>
          </svg>
          <div class="min-w-0">
            <div class="truncate">{template.name}</div>
            <div class="text-[10px] text-text-dim truncate">{template.description}</div>
          </div>
        </button>
      {/each}
    </div>
  {:else}
    <div class="text-xs text-text-dim px-2 py-6 text-center">
      <p>No templates available.</p>
      <p class="mt-1 text-[10px]">Templates help you start with pre-built prompt structures.</p>
    </div>
  {/if}
</div>
