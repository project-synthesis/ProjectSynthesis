<script lang="ts">
  import { base } from '$app/paths';
  import { page } from '$app/stores';
  import Logo from '$lib/components/shared/Logo.svelte';

  let scrolled = $state(false);
  let mobileMenuOpen = $state(false);

  // On content subpages, anchor links become absolute so they navigate home
  const isContentPage = $derived($page.url.pathname !== `${base}/`
    && $page.url.pathname !== base
    && $page.url.pathname !== `${base}`);
  const anchorPrefix = $derived(isContentPage ? `${base}/` : '');

  const navLinks = $derived([
    { label: 'Pipeline', href: `${anchorPrefix}#pipeline` },
    { label: 'Example', href: `${anchorPrefix}#example` },
    { label: 'Capabilities', href: `${anchorPrefix}#capabilities` },
    { label: 'Integrations', href: `${anchorPrefix}#integrations` },
  ]);

  $effect(() => {
    const onScroll = () => { scrolled = window.scrollY > 80; };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  });
</script>

<a href="#main-content" class="skip-link">
  Skip to content
</a>

<header
  class="navbar"
  class:navbar--scrolled={scrolled}
  class:navbar--animate={true}
>
  <nav class="navbar__inner" aria-label="Main navigation">
    <a href="{base}/" class="navbar__logo" aria-label="Project Synthesis home">
      <Logo size={24} variant="full" />
    </a>

    <div class="navbar__links">
      {#each navLinks as link}
        <a href={link.href} class="navbar__link">{link.label}</a>
      {/each}
    </div>

    <div class="navbar__actions">
      <a href="{base}/app" class="navbar__cta navbar__cta--primary">
        Open App
      </a>
      <a href="https://github.com/project-synthesis/ProjectSynthesis" class="navbar__cta" target="_blank" rel="noopener">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
        GitHub
      </a>
    </div>

    <button
      class="navbar__mobile-toggle"
      aria-label={mobileMenuOpen ? 'Close menu' : 'Open menu'}
      aria-expanded={mobileMenuOpen}
      aria-controls="mobile-nav-menu"
      onclick={() => mobileMenuOpen = !mobileMenuOpen}
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
        {#if mobileMenuOpen}
          <path d="M3 3L11 11M11 3L3 11" stroke="currentColor" stroke-width="1"/>
        {:else}
          <path d="M2 4H12M2 7H12M2 10H12" stroke="currentColor" stroke-width="1"/>
        {/if}
      </svg>
    </button>
  </nav>

  {#if mobileMenuOpen}
    <div class="navbar__mobile-menu" id="mobile-nav-menu">
      {#each navLinks as link}
        <a href={link.href} class="navbar__mobile-link" onclick={() => mobileMenuOpen = false}>{link.label}</a>
      {/each}
      <a href="{base}/app" class="navbar__mobile-cta navbar__cta--primary" onclick={() => mobileMenuOpen = false}>
        Open App
      </a>
      <a href="https://github.com/project-synthesis/ProjectSynthesis" class="navbar__mobile-cta" target="_blank" rel="noopener">
        <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>
        GitHub
      </a>
    </div>
  {/if}
</header>

<style>
  .navbar {
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 50;
    transition: all var(--duration-hover) var(--ease-spring);
    border-bottom: 1px solid transparent;
  }

  .navbar--animate {
    animation: fade-in-down 0.8s var(--ease-spring) forwards;
  }

  @keyframes fade-in-down {
    0% { opacity: 0; transform: translateY(-16px); }
    100% { opacity: 1; transform: translateY(0); }
  }

  .navbar--scrolled {
    background: color-mix(in srgb, var(--color-bg-secondary) 92%, transparent);
    border-bottom-color: var(--color-border-subtle);
  }

  .navbar__inner {
    max-width: 1120px;
    margin: 0 auto;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 16px;
    height: 36px;
  }

  .navbar__logo {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: 13px;
    letter-spacing: 0.15em;
    text-decoration: none;
  }

  .navbar__links {
    display: flex;
    gap: 20px;
  }

  .navbar__link {
    font-family: var(--font-sans);
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-secondary);
    text-decoration: none;
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .navbar__link:hover { color: var(--color-text-primary); }
  .navbar__link:focus-visible { outline: 1px solid rgba(0, 229, 255, 0.3); outline-offset: 2px; }

  /* ── Desktop CTAs ── */

  .navbar__actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .navbar__cta {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    height: 22px;
    padding: 0 8px;
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-sans);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    text-decoration: none;
    transition: all var(--duration-hover) var(--ease-spring);
    white-space: nowrap;
    gap: 4px;
  }

  .navbar__cta:hover {
    color: var(--color-text-primary);
    border-color: var(--color-text-dim);
    transform: translateY(-1px);
  }

  .navbar__cta:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .navbar__cta--primary {
    color: var(--color-neon-cyan);
    border-color: var(--color-neon-cyan);
    background: rgba(0, 229, 255, 0.06);
  }

  .navbar__cta--primary:hover {
    background: rgba(0, 229, 255, 0.14);
    border-color: var(--color-neon-cyan);
    color: var(--color-neon-cyan);
  }

  /* ── Mobile ── */

  .navbar__mobile-toggle {
    display: none;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    padding: 0;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
    background: transparent;
  }

  .navbar__mobile-toggle:focus-visible {
    outline: 1px solid rgba(0, 229, 255, 0.3);
    outline-offset: 2px;
  }

  .navbar__mobile-menu {
    display: none;
    flex-direction: column;
    gap: 2px;
    padding: 6px 16px 8px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .navbar__mobile-link {
    font-size: 11px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-secondary);
    text-decoration: none;
    padding: 4px 0;
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .navbar__mobile-link:hover { color: var(--color-text-primary); }
  .navbar__mobile-link:focus-visible { outline: 1px solid rgba(0, 229, 255, 0.3); outline-offset: 2px; }

  .navbar__mobile-cta {
    display: flex;
    align-items: center;
    justify-content: center;
    height: 22px;
    padding: 0 8px;
    font-size: 10px;
    font-weight: 600;
    font-family: var(--font-sans);
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--color-text-secondary);
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    text-decoration: none;
    margin-top: 4px;
    gap: 4px;
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .navbar__mobile-cta:hover { color: var(--color-text-primary); border-color: var(--color-text-dim); }
  .navbar__mobile-cta:focus-visible { outline: 1px solid rgba(0, 229, 255, 0.3); outline-offset: 2px; }

  /* Skip link — invisible until focused */
  .skip-link {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
    text-decoration: none;
  }

  .skip-link:focus {
    position: fixed;
    top: 4px;
    left: 4px;
    z-index: 100;
    width: auto;
    height: auto;
    padding: 2px 6px;
    margin: 0;
    overflow: visible;
    clip: auto;
    font-size: 10px;
    color: var(--color-neon-cyan);
    background: var(--color-bg-card);
    border: 1px solid var(--color-neon-cyan);
  }

  @media (max-width: 640px) {
    .navbar__links { display: none; }
    .navbar__actions { display: none; }
    .navbar__mobile-toggle { display: flex; }
    .navbar__mobile-menu { display: flex; }
  }
</style>
