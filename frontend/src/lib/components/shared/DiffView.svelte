<script lang="ts">
  import { diffLines, diffWords } from 'diff';

  interface Props {
    original: string;
    optimized: string;
  }

  let { original, optimized }: Props = $props();

  type DiffMode = 'unified' | 'split';
  let mode = $state<DiffMode>('unified');

  // ---- Line-level diff ----
  interface DiffLine {
    type: 'equal' | 'removed' | 'added';
    text: string;
    oldNum: number | null;
    newNum: number | null;
  }

  const diffResult = $derived.by(() => {
    const changes = diffLines(original, optimized);
    const lines: DiffLine[] = [];
    let oldNum = 1;
    let newNum = 1;

    for (const change of changes) {
      const lineTexts = change.value.replace(/\n$/, '').split('\n');
      for (const text of lineTexts) {
        if (change.added) {
          lines.push({ type: 'added', text, oldNum: null, newNum: newNum++ });
        } else if (change.removed) {
          lines.push({ type: 'removed', text, oldNum: oldNum++, newNum: null });
        } else {
          lines.push({ type: 'equal', text, oldNum: oldNum++, newNum: newNum++ });
        }
      }
    }
    return lines;
  });

  // ---- Word-level diff for inline highlighting ----
  interface WordSpan {
    type: 'equal' | 'removed' | 'added';
    value: string;
  }

  function wordDiff(a: string, b: string): WordSpan[] {
    return diffWords(a, b).map((c) => ({
      type: c.added ? 'added' : c.removed ? 'removed' : 'equal',
      value: c.value,
    }));
  }

  // ---- Split view: pair removed+added lines for word-level comparison ----
  interface SplitPair {
    left: DiffLine | null;
    right: DiffLine | null;
    words: WordSpan[] | null; // word-level diff when both sides present
  }

  const splitPairs = $derived.by(() => {
    const pairs: SplitPair[] = [];
    const lines = diffResult;
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      if (line.type === 'equal') {
        pairs.push({ left: line, right: line, words: null });
        i++;
      } else if (line.type === 'removed') {
        // Collect consecutive removed lines
        const removed: DiffLine[] = [];
        while (i < lines.length && lines[i].type === 'removed') {
          removed.push(lines[i]);
          i++;
        }
        // Collect consecutive added lines
        const added: DiffLine[] = [];
        while (i < lines.length && lines[i].type === 'added') {
          added.push(lines[i]);
          i++;
        }
        // Pair them up
        const max = Math.max(removed.length, added.length);
        for (let j = 0; j < max; j++) {
          const left = removed[j] ?? null;
          const right = added[j] ?? null;
          const words = left && right ? wordDiff(left.text, right.text) : null;
          pairs.push({ left, right, words });
        }
      } else if (line.type === 'added') {
        pairs.push({ left: null, right: line, words: null });
        i++;
      } else {
        i++;
      }
    }
    return pairs;
  });

  // ---- Stats ----
  const stats = $derived.by(() => {
    let added = 0;
    let removed = 0;
    let unchanged = 0;
    for (const line of diffResult) {
      if (line.type === 'added') added++;
      else if (line.type === 'removed') removed++;
      else unchanged++;
    }
    return { added, removed, unchanged, total: added + removed + unchanged };
  });
</script>

<div class="diffview">
  <!-- Toolbar -->
  <div class="diff-toolbar">
    <span class="diff-title">DIFF</span>
    <div class="diff-stats">
      <span class="stat stat--removed">-{stats.removed}</span>
      <span class="stat stat--added">+{stats.added}</span>
      <span class="stat stat--unchanged">{stats.unchanged} unchanged</span>
    </div>
    <div class="diff-spacer"></div>
    <div class="mode-toggle">
      <button
        class="mode-btn"
        class:mode-btn--active={mode === 'unified'}
        onclick={() => mode = 'unified'}
      >UNIFIED</button>
      <button
        class="mode-btn"
        class:mode-btn--active={mode === 'split'}
        onclick={() => mode = 'split'}
      >SPLIT</button>
    </div>
  </div>

  <!-- Unified view -->
  {#if mode === 'unified'}
    <div class="diff-scroll">
      <table class="diff-table" role="presentation">
        <tbody>
          {#each diffResult as line, idx (idx)}
            <tr class="diff-row diff-row--{line.type}">
              <td class="line-num line-num--old">{line.oldNum ?? ''}</td>
              <td class="line-num line-num--new">{line.newNum ?? ''}</td>
              <td class="line-marker">
                {#if line.type === 'removed'}-{:else if line.type === 'added'}+{:else}&nbsp;{/if}
              </td>
              <td class="line-content">
                <pre class="line-text">{line.text || ' '}</pre>
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

  <!-- Split view -->
  {:else}
    <div class="split-container">
      <!-- Left header -->
      <div class="split-header split-header--left">
        <span class="split-label">ORIGINAL</span>
        <span class="split-count stat--removed">-{stats.removed}</span>
      </div>
      <div class="split-divider-header"></div>
      <!-- Right header -->
      <div class="split-header split-header--right">
        <span class="split-label">OPTIMIZED</span>
        <span class="split-count stat--added">+{stats.added}</span>
      </div>

      <!-- Split scroll area -->
      <div class="split-scroll">
        <table class="diff-table split-table" role="presentation">
          <tbody>
            {#each splitPairs as pair, idx (idx)}
              <tr class="diff-row"
                class:diff-row--removed={pair.left?.type === 'removed'}
                class:diff-row--added={!pair.left && pair.right?.type === 'added'}
                class:diff-row--changed={pair.left?.type === 'removed' && pair.right?.type === 'added'}
              >
                <!-- Left side -->
                <td class="line-num line-num--old">{pair.left?.oldNum ?? ''}</td>
                <td class="line-marker line-marker--left">
                  {#if pair.left?.type === 'removed'}-{:else if pair.left?.type === 'equal'}&nbsp;{:else}&nbsp;{/if}
                </td>
                <td class="line-content split-cell split-cell--left"
                  class:cell--removed={pair.left?.type === 'removed'}
                  class:cell--empty={!pair.left}
                >
                  {#if pair.left}
                    <pre class="line-text">{#if pair.words}{#each pair.words as span}{#if span.type !== 'added'}<span class="word-span" class:word--removed={span.type === 'removed'}>{span.value}</span>{/if}{/each}{:else}{pair.left.text || ' '}{/if}</pre>
                  {:else}
                    <pre class="line-text">&nbsp;</pre>
                  {/if}
                </td>

                <!-- Divider -->
                <td class="split-divider-cell"></td>

                <!-- Right side -->
                <td class="line-num line-num--new">{pair.right?.newNum ?? ''}</td>
                <td class="line-marker line-marker--right">
                  {#if pair.right?.type === 'added'}+{:else if pair.right?.type === 'equal'}&nbsp;{:else}&nbsp;{/if}
                </td>
                <td class="line-content split-cell split-cell--right"
                  class:cell--added={pair.right?.type === 'added'}
                  class:cell--empty={!pair.right}
                >
                  {#if pair.right}
                    <pre class="line-text">{#if pair.words}{#each pair.words as span}{#if span.type !== 'removed'}<span class="word-span" class:word--added={span.type === 'added'}>{span.value}</span>{/if}{/each}{:else}{pair.right.text || ' '}{/if}</pre>
                  {:else}
                    <pre class="line-text">&nbsp;</pre>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    </div>
  {/if}
</div>

<style>
  /* ================================================================
     Production Diff Viewer — Industrial Cyberpunk
     Colors: neon-red (#ff3366) for removed, neon-cyan (#00e5ff) for added
     No red/green. No glow. 1px contours. Ultra-compact.
     ================================================================ */

  .diffview {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
  }

  /* ---- Toolbar ---- */
  .diff-toolbar {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 28px;
    padding: 0 6px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
    flex-shrink: 0;
  }

  .diff-title {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .diff-stats {
    display: flex;
    gap: 6px;
    align-items: center;
  }

  .stat {
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .stat--removed {
    color: var(--color-neon-red);
  }

  .stat--added {
    color: var(--color-neon-cyan);
  }

  .stat--unchanged {
    color: var(--color-text-dim);
  }

  .diff-spacer {
    flex: 1;
  }

  .mode-toggle {
    display: flex;
    gap: 0;
  }

  .mode-btn {
    height: 20px;
    padding: 0 8px;
    background: transparent;
    border: 1px solid var(--color-border-subtle);
    color: var(--color-text-dim);
    font-family: var(--font-display);
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all 200ms cubic-bezier(0.16, 1, 0.3, 1);
  }

  .mode-btn:first-child {
    border-right: none;
  }

  .mode-btn:hover {
    background: var(--color-bg-hover);
    color: var(--color-text-primary);
  }

  .mode-btn--active {
    background: var(--color-bg-hover);
    border-color: var(--color-border-accent);
    color: var(--color-text-primary);
  }

  /* ---- Scroll containers ---- */
  .diff-scroll {
    flex: 1;
    overflow: auto;
    background: var(--color-bg-primary);
    min-height: 0;
  }

  /* ---- Table layout ---- */
  .diff-table {
    width: 100%;
    border-collapse: collapse;
    border-spacing: 0;
  }

  .diff-row {
    height: 20px;
  }

  /* Unified row backgrounds */
  .diff-row--removed {
    background: rgba(255, 51, 102, 0.06);
  }

  .diff-row--added {
    background: rgba(0, 229, 255, 0.06);
  }

  .diff-row--equal {
    background: transparent;
  }

  /* ---- Line numbers ---- */
  .line-num {
    width: 36px;
    min-width: 36px;
    padding: 0 4px;
    text-align: right;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
    user-select: none;
    vertical-align: top;
    opacity: 0.5;
    border-right: 1px solid var(--color-border-subtle);
  }

  .diff-row--removed .line-num {
    color: var(--color-neon-red);
    opacity: 0.6;
  }

  .diff-row--added .line-num {
    color: var(--color-neon-cyan);
    opacity: 0.6;
  }

  /* ---- Marker column (+/-/space) ---- */
  .line-marker {
    width: 16px;
    min-width: 16px;
    text-align: center;
    font-family: var(--font-mono);
    font-size: 10px;
    font-weight: 700;
    color: var(--color-text-dim);
    user-select: none;
    vertical-align: top;
    padding-top: 1px;
  }

  .diff-row--removed .line-marker {
    color: var(--color-neon-red);
  }

  .diff-row--added .line-marker {
    color: var(--color-neon-cyan);
  }

  /* ---- Line content ---- */
  .line-content {
    padding: 0 6px;
    vertical-align: top;
  }

  .line-text {
    margin: 0;
    font-family: var(--font-sans);
    font-size: 12px;
    line-height: 20px;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--color-text-primary);
  }

  .diff-row--removed .line-text {
    color: var(--color-text-secondary);
  }

  .diff-row--added .line-text {
    color: var(--color-text-primary);
  }

  /* ---- Word-level highlights ---- */
  .word-span {
    display: inline;
  }

  .word--removed {
    background: rgba(255, 51, 102, 0.15);
    color: var(--color-neon-red);
    text-decoration: line-through;
    text-decoration-color: rgba(255, 51, 102, 0.4);
  }

  .word--added {
    background: rgba(0, 229, 255, 0.12);
    color: var(--color-neon-cyan);
  }

  /* ---- Split view ---- */
  .split-container {
    flex: 1;
    display: grid;
    grid-template-columns: 1fr 1px 1fr;
    grid-template-rows: 24px 1fr;
    min-height: 0;
    overflow: hidden;
  }

  .split-header {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 6px;
    background: var(--color-bg-secondary);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .split-header--left {
    grid-column: 1;
  }

  .split-header--right {
    grid-column: 3;
  }

  .split-divider-header {
    grid-column: 2;
    background: var(--color-border-subtle);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .split-label {
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }

  .split-count {
    font-family: var(--font-mono);
    font-size: 10px;
  }

  .split-scroll {
    grid-column: 1 / -1;
    grid-row: 2;
    overflow: auto;
    background: var(--color-bg-primary);
    min-height: 0;
  }

  .split-table {
    table-layout: fixed;
  }

  .split-cell {
    width: 50%;
  }

  .split-cell--left {
    border-right: none;
  }

  .split-cell--right {
    border-left: none;
  }

  .cell--removed {
    background: rgba(255, 51, 102, 0.06);
  }

  .cell--added {
    background: rgba(0, 229, 255, 0.06);
  }

  .cell--empty {
    background: var(--color-bg-input);
  }

  .split-divider-cell {
    width: 1px;
    min-width: 1px;
    max-width: 1px;
    background: var(--color-border-subtle);
    padding: 0;
  }

  /* ---- Changed rows in split (both sides modified) ---- */
  .diff-row--changed .cell--removed {
    background: rgba(255, 51, 102, 0.06);
  }

  .diff-row--changed .cell--added {
    background: rgba(0, 229, 255, 0.06);
  }

  /* ---- Left marker in split ---- */
  .line-marker--left {
    border-right: 1px solid var(--color-border-subtle);
  }

  .cell--removed .line-text,
  .diff-row--changed .split-cell--left .line-text {
    color: var(--color-text-secondary);
  }

  .cell--added .line-text,
  .diff-row--changed .split-cell--right .line-text {
    color: var(--color-text-primary);
  }

  /* Split removed/added marker colors */
  .cell--removed + .split-divider-cell + td + .line-marker--right,
  .diff-row--changed .line-marker--left {
    color: var(--color-neon-red);
  }

  .diff-row--changed .line-marker--right {
    color: var(--color-neon-cyan);
  }
</style>
