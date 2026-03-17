<script lang="ts">
  import Navbar from '$lib/components/landing/Navbar.svelte';
  import Footer from '$lib/components/landing/Footer.svelte';
  import ContentPage from '$lib/components/landing/ContentPage.svelte';

  let { data } = $props();
  const page = $derived(data.page);
</script>

<svelte:head>
  <title>{page.title} — Project Synthesis</title>
  <meta name="description" content={page.description} />
</svelte:head>

<Navbar />

<main id="main-content" class="content-page">
  <ContentPage sections={page.sections} />
</main>

<Footer />

<style>
  .content-page {
    max-width: 1120px;
    margin: 0 auto;
    padding: 52px 16px 40px;
  }
</style>
