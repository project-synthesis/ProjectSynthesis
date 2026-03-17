<script lang="ts">
  import { base } from '$app/paths';

  const columns = $derived([
    {
      title: 'Product',
      links: [
        { label: 'Pipeline', href: `${base}/pipeline` },
        { label: 'Scoring', href: `${base}/scoring` },
        { label: 'Refinement', href: `${base}/refinement` },
        { label: 'Integrations', href: `${base}/integrations` },
      ],
    },
    {
      title: 'Resources',
      links: [
        { label: 'Documentation', href: `${base}/documentation` },
        { label: 'API Reference', href: `${base}/api-reference` },
        { label: 'MCP Server', href: `${base}/mcp-server` },
        { label: 'Changelog', href: `${base}/changelog` },
      ],
    },
    {
      title: 'Company',
      links: [
        { label: 'About', href: `${base}/about` },
        { label: 'Blog', href: `${base}/blog` },
        { label: 'Careers', href: `${base}/careers` },
        { label: 'Contact', href: `${base}/contact` },
      ],
    },
    {
      title: 'Legal',
      links: [
        { label: 'Privacy', href: `${base}/privacy` },
        { label: 'Terms', href: `${base}/terms` },
        { label: 'Security', href: `${base}/security` },
      ],
    },
  ]);

  const year = new Date().getFullYear();
</script>

<footer class="footer">
  <div class="footer__inner">
    <div class="footer__grid">
      {#each columns as col}
        <div class="footer__column">
          <h4 class="footer__heading">{col.title}</h4>
          <ul class="footer__list">
            {#each col.links as link}
              <li><a href={link.href} class="footer__link">{link.label}</a></li>
            {/each}
          </ul>
        </div>
      {/each}
    </div>

    <div class="footer__bottom">
      <span class="footer__copyright">&copy; {year} Project Synthesis</span>
      <span class="footer__version">v2.0</span>
    </div>
  </div>
</footer>

<style>
  .footer {
    border-top: 1px solid var(--color-border-subtle);
    background: var(--color-bg-primary);
    padding: 24px 16px 16px;
  }

  .footer__inner {
    max-width: 1120px;
    margin: 0 auto;
  }

  .footer__grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 24px;
    margin-bottom: 20px;
  }

  .footer__heading {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-secondary);
    margin: 0 0 8px 0;
  }

  .footer__list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .footer__link {
    font-size: 10px;
    color: var(--color-text-secondary);
    text-decoration: none;
    transition: color var(--duration-hover) var(--ease-spring);
  }

  .footer__link:hover {
    color: var(--color-text-primary);
  }

  .footer__bottom {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-top: 12px;
    border-top: 1px solid var(--color-border-subtle);
  }

  .footer__copyright {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }

  .footer__version {
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }

  @media (max-width: 640px) {
    .footer__grid {
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }
  }
</style>
