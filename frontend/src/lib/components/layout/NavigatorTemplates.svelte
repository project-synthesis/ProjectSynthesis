<script lang="ts">
  import { editor } from '$lib/stores/editor.svelte';
  import { samplePrompts, promptCategories, categoryLabels, categoryColors, difficultyColors, type SamplePrompt } from '$lib/utils/samplePrompts';

  let activeCategory = $state<string>('all');
  let searchQuery = $state('');

  let filteredPrompts = $derived.by(() => {
    let results = samplePrompts;
    if (activeCategory !== 'all') {
      results = results.filter(p => p.category === activeCategory);
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      results = results.filter(p =>
        p.title.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q) ||
        p.category.includes(q)
      );
    }
    return results;
  });

  function openTemplate(sample: SamplePrompt) {
    editor.openTab({
      id: `sample-${sample.id}`,
      label: sample.title,
      type: 'prompt',
      promptText: sample.text,
      dirty: false,
    });
  }
</script>

<div class="p-2 space-y-2">
  <div class="flex items-center justify-between px-1">
    <span class="font-display text-[11px] font-bold uppercase text-text-dim">Templates</span>
    <span class="font-mono text-[9px] text-text-dim/50">{filteredPrompts.length}</span>
  </div>

  <!-- Search -->
  <div class="px-1">
    <input
      type="text"
      placeholder="Search templates..."
      bind:value={searchQuery}
      class="w-full bg-bg-input border border-border-subtle px-2 py-1
             font-mono text-[10px] text-text-primary focus:outline-none
             focus:border-neon-cyan/30 placeholder:text-text-dim/40"
    />
  </div>

  <!-- Category filter chips -->
  <div class="flex flex-wrap gap-1 px-1">
    <button
      class="px-1.5 py-0.5 font-mono text-[8px] uppercase transition-colors
        {activeCategory === 'all' ? 'bg-neon-cyan/10 text-neon-cyan border border-neon-cyan/30' : 'text-text-dim border border-border-subtle hover:border-neon-cyan/20'}"
      onclick={() => activeCategory = 'all'}
    >All</button>
    {#each promptCategories as cat}
      <button
        class="px-1.5 py-0.5 font-mono text-[8px] uppercase transition-colors
          {activeCategory === cat ? 'bg-opacity-10 border' : 'text-text-dim border border-border-subtle hover:border-opacity-40'}"
        style="{activeCategory === cat ? `color: ${categoryColors[cat]}; border-color: ${categoryColors[cat]}40; background-color: ${categoryColors[cat]}10` : ''}"
        onclick={() => activeCategory = cat}
      >{categoryLabels[cat] ?? cat}</button>
    {/each}
  </div>

  <!-- Template cards -->
  {#if filteredPrompts.length > 0}
    <div class="space-y-1 px-1">
      {#each filteredPrompts as template}
        <button
          class="w-full text-left p-2 border border-border-subtle hover:border-neon-cyan/20 transition-colors group"
          onclick={() => openTemplate(template)}
        >
          <div class="flex items-center gap-1.5 mb-1">
            <span
              class="px-1 py-0.5 border font-mono text-[7px] uppercase"
              style="color: {categoryColors[template.category]}; border-color: {categoryColors[template.category]}40"
            >{template.category}</span>
            <span class="font-mono text-[7px] uppercase" style="color: {difficultyColors[template.difficulty]}">{template.difficulty}</span>
            {#if template.suggestedStrategy !== 'auto'}
              <span class="font-mono text-[7px] text-text-dim/40 ml-auto">{template.suggestedStrategy}</span>
            {/if}
          </div>
          <div class="font-mono text-[10px] text-text-primary truncate">{template.title}</div>
          <div class="font-mono text-[8px] text-text-dim mt-0.5 line-clamp-2 leading-snug">{template.description}</div>
        </button>
      {/each}
    </div>
  {:else}
    <div class="text-center px-2 py-6">
      <p class="font-mono text-[10px] text-text-dim">No templates match your search.</p>
    </div>
  {/if}
</div>
