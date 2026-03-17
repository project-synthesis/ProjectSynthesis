<script lang="ts">
  import { base } from '$app/paths';
  import Navbar from '$lib/components/landing/Navbar.svelte';
  import Footer from '$lib/components/landing/Footer.svelte';
</script>

<Navbar />

<main id="main-content" class="error-page">
  <div class="error-page__inner">
    <p class="error-page__code">404</p>
    <h1 class="error-page__heading">PAGE NOT FOUND</h1>
    <p class="error-page__sub">This route doesn't exist in the pipeline.</p>
    <a href="{base}/" class="error-page__cta">Back to Home</a>
  </div>
</main>

<Footer />

<style>
  .error-page {
    max-width: 1120px;
    margin: 0 auto;
    padding: 120px 16px 80px;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .error-page__inner {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    text-align: center;
  }

  .error-page__code {
    font-family: var(--font-mono);
    font-size: 13px;
    font-weight: 500;
    letter-spacing: 0.12em;
    color: var(--color-accent);
    text-transform: uppercase;
    margin: 0;
  }

  .error-page__heading {
    font-family: var(--font-display);
    font-size: clamp(24px, 5vw, 40px);
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-primary);
    margin: 0;
  }

  .error-page__sub {
    font-family: var(--font-sans);
    font-size: 15px;
    color: var(--color-text-secondary);
    margin: 0;
  }

  .error-page__cta {
    display: inline-block;
    margin-top: 8px;
    padding: 10px 24px;
    font-family: var(--font-mono);
    font-size: 12px;
    font-weight: 500;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    text-decoration: none;
    color: var(--color-text-primary);
    border: 1px solid var(--color-border);
    background: transparent;
    transition: border-color 200ms, color 200ms;
  }

  .error-page__cta:hover {
    border-color: var(--color-accent);
    color: var(--color-accent);
  }
</style>
