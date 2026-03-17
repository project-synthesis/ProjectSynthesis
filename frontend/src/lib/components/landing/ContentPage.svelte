<script lang="ts">
  import type { Section } from '$lib/content/types';
  import HeroSection from './sections/HeroSection.svelte';
  import ProseSection from './sections/ProseSection.svelte';
  import CardGrid from './sections/CardGrid.svelte';
  import EndpointList from './sections/EndpointList.svelte';
  import Timeline from './sections/Timeline.svelte';
  import ArticleList from './sections/ArticleList.svelte';
  import RoleList from './sections/RoleList.svelte';
  import ContactForm from './sections/ContactForm.svelte';
  import StepFlow from './sections/StepFlow.svelte';
  import CodeBlock from './sections/CodeBlock.svelte';
  import MetricBar from './sections/MetricBar.svelte';

  interface Props { sections: Section[]; }
  let { sections }: Props = $props();
</script>

{#each sections as section}
  <div class="content-section">
    {#if section.type === 'hero'}
      <HeroSection heading={section.heading} subheading={section.subheading} cta={section.cta} />
    {:else if section.type === 'prose'}
      <ProseSection blocks={section.blocks} />
    {:else if section.type === 'card-grid'}
      <CardGrid columns={section.columns} cards={section.cards} />
    {:else if section.type === 'endpoint-list'}
      <EndpointList groups={section.groups} />
    {:else if section.type === 'timeline'}
      <Timeline versions={section.versions} />
    {:else if section.type === 'article-list'}
      <ArticleList articles={section.articles} />
    {:else if section.type === 'role-list'}
      <RoleList roles={section.roles} />
    {:else if section.type === 'contact-form'}
      <ContactForm categories={section.categories} successMessage={section.successMessage} />
    {:else if section.type === 'step-flow'}
      <StepFlow steps={section.steps} />
    {:else if section.type === 'code-block'}
      <CodeBlock language={section.language} code={section.code} filename={section.filename} />
    {:else if section.type === 'metric-bar'}
      <MetricBar dimensions={section.dimensions} label={section.label} />
    {/if}
  </div>
{/each}

<style>
  .content-section {
    margin-bottom: 24px;
  }
  .content-section:last-child {
    margin-bottom: 0;
  }
</style>
