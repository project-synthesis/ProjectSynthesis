<script lang="ts">
  import type { CodeBlockSection } from '$lib/content/types';

  interface Props {
    language: CodeBlockSection['language'];
    code: CodeBlockSection['code'];
    filename?: CodeBlockSection['filename'];
  }

  let { language, code, filename }: Props = $props();

  let copyLabel = $state('COPY');

  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    copyLabel = 'COPIED';
    setTimeout(() => { copyLabel = 'COPY'; }, 1500);
  }
</script>

<div class="code-block">
  {#if filename}
    <div class="code-block__header">
      <span class="code-block__filename">{filename}</span>
      <span class="code-block__lang">{language}</span>
    </div>
  {/if}
  <div class="code-block__body">
    <pre class="code-block__pre"><code>{code}</code></pre>
    <button class="code-block__copy" onclick={handleCopy} aria-label="Copy code">
      {copyLabel}
    </button>
  </div>
</div>

<style>
  .code-block {
    background: var(--color-bg-input);
    border: 1px solid var(--color-border-subtle);
    overflow: hidden;
  }

  .code-block__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 8px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .code-block__filename {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
  }

  .code-block__lang {
    font-family: var(--font-mono);
    font-size: 9px;
    color: var(--color-text-dim);
    text-transform: uppercase;
    opacity: 0.6;
  }

  .code-block__body {
    position: relative;
  }

  .code-block__pre {
    margin: 0;
    padding: 8px;
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--color-text-primary);
    white-space: pre-wrap;
    word-break: break-all;
    line-height: 1.6;
    overflow: auto;
  }

  .code-block__pre code {
    font-family: inherit;
    font-size: inherit;
    background: none;
    padding: 0;
  }

  .code-block__copy {
    position: absolute;
    top: 6px;
    right: 6px;
    height: 20px;
    padding: 0 6px;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 600;
    color: var(--color-text-dim);
    background: var(--color-bg-secondary);
    border: 1px solid var(--color-border-subtle);
    cursor: pointer;
    transition: all var(--duration-hover) var(--ease-spring);
  }

  .code-block__copy:hover {
    color: var(--color-neon-cyan);
    border-color: var(--color-border-accent);
    background: var(--color-bg-hover);
  }
</style>
