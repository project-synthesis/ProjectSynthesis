<script lang="ts">
  import type { HeroSection } from '$lib/content/types';

  interface Props {
    heading: HeroSection['heading'];
    subheading: HeroSection['subheading'];
    cta?: HeroSection['cta'];
  }

  let { heading, subheading, cta }: Props = $props();
</script>

<div class="hero" data-reveal>
  <h1 class="hero__heading text-gradient-forge font-display" style="view-transition-name: page-title">
    {heading}
  </h1>
  <p class="hero__sub">{subheading}</p>
  {#if cta}
    <a class="hero__cta" href={cta.href}>{cta.label}</a>
  {/if}
</div>

<style>
  .hero {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .hero__heading {
    font-size: clamp(18px, 3vw, 28px);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    margin: 0;
    line-height: 1.15;
  }

  .hero__sub {
    font-size: 12px;
    color: var(--color-text-secondary);
    max-width: 560px;
    margin: 0;
    line-height: 1.6;
  }

  .hero__cta {
    display: inline-flex;
    align-items: center;
    height: 24px;
    padding: 0 12px;
    border: 1px solid var(--color-neon-cyan);
    color: var(--color-neon-cyan);
    font-family: var(--font-sans);
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    text-decoration: none;
    background: transparent;
    align-self: flex-start;
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .hero__cta:hover {
    background: var(--color-bg-hover);
    border-color: var(--color-neon-cyan);
  }
</style>
