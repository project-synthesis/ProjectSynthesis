<script lang="ts">
  import { diffWords } from 'diff';

  let { original = '', modified = '' }: { original?: string; modified?: string } = $props();

  let viewMode = $state<'side-by-side' | 'inline'>('side-by-side');
  let showDiffsOnly = $state(false);
  let granularity = $state<'line' | 'word'>('line');

  // Scroll sync state
  let leftCol: HTMLElement | null = $state(null);
  let rightCol: HTMLElement | null = $state(null);
  let _syncing = false;

  function syncScroll(source: HTMLElement, target: HTMLElement | null) {
    if (_syncing || !target) return;
    _syncing = true;
    target.scrollTop = source.scrollTop;
    target.scrollLeft = source.scrollLeft;
    _syncing = false;
  }

  type DiffType = 'same' | 'added' | 'removed';

  interface DiffLine {
    type: DiffType;
    lineNum: number;
    text: string;
  }

  interface SidePair {
    left: { type: 'same' | 'removed'; text: string; lineNum: number } | null;
    right: { type: 'same' | 'added'; text: string; lineNum: number } | null;
  }

  interface LcsAction {
    type: DiffType;
    origIdx?: number;
    modIdx?: number;
  }

  // Compute LCS actions once; both diffLines and sidePairs derive from this
  let lcsResult = $derived.by(() => {
    const origLines = original.split('\n');
    const modLines = modified.split('\n');
    const m = origLines.length;
    const n = modLines.length;

    const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        dp[i][j] = origLines[i - 1] === modLines[j - 1]
          ? dp[i - 1][j - 1] + 1
          : Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }

    const actions: LcsAction[] = [];
    let i = m, j = n;
    while (i > 0 || j > 0) {
      if (i > 0 && j > 0 && origLines[i - 1] === modLines[j - 1]) {
        actions.unshift({ type: 'same', origIdx: i - 1, modIdx: j - 1 });
        i--; j--;
      } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
        actions.unshift({ type: 'added', modIdx: j - 1 });
        j--;
      } else {
        actions.unshift({ type: 'removed', origIdx: i - 1 });
        i--;
      }
    }

    return { actions, origLines, modLines };
  });

  let diffLines = $derived.by((): DiffLine[] => {
    const { actions, origLines, modLines } = lcsResult;
    let lineNum = 0;
    return actions.map(a => ({
      type: a.type,
      lineNum: ++lineNum,
      text: a.type === 'added' ? modLines[a.modIdx!] : origLines[a.origIdx!]
    }));
  });

  let sidePairs = $derived.by((): SidePair[] => {
    const { actions, origLines, modLines } = lcsResult;
    const pairs: SidePair[] = [];
    let leftNum = 0, rightNum = 0;
    for (const a of actions) {
      if (a.type === 'same') {
        leftNum++; rightNum++;
        pairs.push({
          left:  { type: 'same', text: origLines[a.origIdx!], lineNum: leftNum },
          right: { type: 'same', text: modLines[a.modIdx!],   lineNum: rightNum }
        });
      } else if (a.type === 'removed') {
        leftNum++;
        pairs.push({ left: { type: 'removed', text: origLines[a.origIdx!], lineNum: leftNum }, right: null });
      } else {
        rightNum++;
        pairs.push({ left: null, right: { type: 'added', text: modLines[a.modIdx!], lineNum: rightNum } });
      }
    }
    return pairs;
  });

  let filteredDiffLines = $derived(
    showDiffsOnly ? diffLines.filter(d => d.type !== 'same') : diffLines
  );

  let filteredSidePairs = $derived(
    showDiffsOnly ? sidePairs.filter(p => (p.left?.type !== 'same') || (p.right?.type !== 'same')) : sidePairs
  );

  interface WordPart {
    value: string;
    added?: boolean;
    removed?: boolean;
  }

  function renderWordDiff(origLine: string, modLine: string): WordPart[] {
    return diffWords(origLine, modLine) as WordPart[];
  }

  // Pre-compute word diffs for consecutive removed/added pairs (inline view)
  let inlineWordDiffs = $derived.by((): Map<number, WordPart[]> => {
    if (granularity === 'line') return new Map();
    const result = new Map<number, WordPart[]>();
    for (let i = 0; i < diffLines.length - 1; i++) {
      if (diffLines[i].type === 'removed' && diffLines[i + 1].type === 'added') {
        const parts = renderWordDiff(diffLines[i].text, diffLines[i + 1].text);
        result.set(i, parts.filter(p => !p.added));         // removed parts for removed line
        result.set(i + 1, parts.filter(p => !p.removed));  // added parts for added line
      }
    }
    return result;
  });

  // Pre-compute word diffs for side-by-side: match adjacent (removed, added) pairs
  let sideWordDiffs = $derived.by((): Map<number, { left: WordPart[]; right: WordPart[] }> => {
    if (granularity === 'line') return new Map();
    const result = new Map<number, { left: WordPart[]; right: WordPart[] }>();
    for (let i = 0; i < sidePairs.length - 1; i++) {
      const cur = sidePairs[i];
      const nxt = sidePairs[i + 1];
      if (cur.left && !cur.right && cur.left.type === 'removed' &&
          !nxt.left && nxt.right && nxt.right.type === 'added') {
        const parts = renderWordDiff(cur.left.text, nxt.right.text);
        result.set(i, {
          left: parts.filter(p => !p.added),
          right: parts.filter(p => !p.removed),
        });
      }
    }
    return result;
  });

  function lineClass(type: string): string {
    switch (type) {
      case 'added': return 'bg-neon-green/10 text-neon-green';
      case 'removed': return 'bg-neon-red/10 text-neon-red';
      default: return 'text-text-secondary';
    }
  }

  // When word-level diff is active, keep text color but drop the full-line background
  // so that individual word highlights are the primary visual signal.
  function lineTextClass(type: string): string {
    switch (type) {
      case 'added': return 'text-neon-green';
      case 'removed': return 'text-neon-red';
      default: return 'text-text-secondary';
    }
  }

  function linePrefix(type: string): string {
    switch (type) {
      case 'added': return '+';
      case 'removed': return '-';
      default: return ' ';
    }
  }
</script>

<div class="text-xs">
  <!-- Toolbar -->
  <div class="flex items-center justify-between px-2 py-1 bg-bg-secondary/50 border-b border-border-subtle">
    <div class="flex items-center gap-1.5">
      <button
        class="text-[10px] px-2 py-0.5 {viewMode === 'side-by-side' ? 'btn-outline-cyan' : 'btn-outline-subtle'}"
        onclick={() => viewMode = 'side-by-side'}
      >
        Side-by-side
      </button>
      <button
        class="text-[10px] px-2 py-0.5 {viewMode === 'inline' ? 'btn-outline-cyan' : 'btn-outline-subtle'}"
        onclick={() => viewMode = 'inline'}
      >
        Inline
      </button>
      <button
        class="text-[10px] px-2 py-0.5 {granularity === 'word' ? 'btn-outline-cyan' : 'btn-outline-subtle'}"
        onclick={() => granularity = granularity === 'line' ? 'word' : 'line'}
      >
        Word
      </button>
    </div>
    <label class="flex items-center gap-1.5 cursor-pointer">
      <input
        type="checkbox"
        name="show-diffs-only"
        bind:checked={showDiffsOnly}
        class="w-3 h-3 border-border-subtle accent-neon-cyan"
      />
      <span class="text-[10px] text-text-dim">Diffs only</span>
    </label>
  </div>

  <!-- Side-by-side view -->
  {#if viewMode === 'side-by-side'}
    <div class="grid grid-cols-2 gap-px bg-border-subtle">
      <!-- Original -->
      <div bind:this={leftCol} onscroll={() => syncScroll(leftCol!, rightCol)} class="bg-bg-card p-1 overflow-auto max-h-[60vh] font-mono">
        <div class="text-[10px] text-neon-red/60 uppercase tracking-wider font-semibold mb-1">Original</div>
        {#each filteredSidePairs as pair, i}
          {@const oi = sidePairs.indexOf(pair)}
          {@const leftWordDiff = granularity === 'word' && pair.left?.type === 'removed' && sideWordDiffs.has(oi)}
          {#if pair.left}
            <div class="relative py-0.5 px-1 flex gap-2 {leftWordDiff ? lineTextClass(pair.left.type) : lineClass(pair.left.type)}"
                 style="animation: list-item-in 0.12s cubic-bezier(0.16,1,0.3,1) {Math.min(i*8,120)}ms both;">
              {#if pair.left.type === 'removed'}<span class="absolute left-0 inset-y-0 w-[1px] bg-neon-red/50"></span>{/if}
              <span class="text-text-dim/40 select-none w-4 text-right shrink-0">{pair.left.lineNum}</span>
              <span class="whitespace-pre-wrap break-all">
                {#if leftWordDiff}
                  {#each sideWordDiffs.get(oi)!.left as part}
                    {#if part.removed}<span class="bg-neon-red/20">{part.value}</span>{:else}{part.value}{/if}
                  {/each}
                {:else}
                  {pair.left.text}
                {/if}
              </span>
            </div>
          {:else}
            <div class="py-0.5 px-1 flex gap-2 opacity-30">
              <span class="w-4 shrink-0"></span>
              <span class="whitespace-pre-wrap"> </span>
            </div>
          {/if}
        {/each}
      </div>

      <!-- Modified -->
      <div bind:this={rightCol} onscroll={() => syncScroll(rightCol!, leftCol)} class="bg-bg-card p-1 overflow-auto max-h-[60vh] font-mono">
        <div class="text-[10px] text-neon-green/60 uppercase tracking-wider font-semibold mb-1">Modified</div>
        {#each filteredSidePairs as pair, i}
          {@const oi = sidePairs.indexOf(pair)}
          {@const rightWordDiff = granularity === 'word' && pair.right?.type === 'added' && sideWordDiffs.has(oi - 1)}
          {#if pair.right}
            <div class="relative py-0.5 px-1 flex gap-2 {rightWordDiff ? lineTextClass(pair.right.type) : lineClass(pair.right.type)}"
                 style="animation: list-item-in 0.12s cubic-bezier(0.16,1,0.3,1) {Math.min(i*8,120)}ms both;">
              {#if pair.right.type === 'added'}<span class="absolute left-0 inset-y-0 w-[1px] bg-neon-green/50"></span>{/if}
              <span class="text-text-dim/40 select-none w-4 text-right shrink-0">{pair.right.lineNum}</span>
              <span class="whitespace-pre-wrap break-all">
                {#if rightWordDiff}
                  {#each sideWordDiffs.get(oi - 1)!.right as part}
                    {#if part.added}<span class="bg-neon-green/20">{part.value}</span>{:else}{part.value}{/if}
                  {/each}
                {:else}
                  {pair.right.text}
                {/if}
              </span>
            </div>
          {:else}
            <div class="py-0.5 px-1 flex gap-2 opacity-30">
              <span class="w-4 shrink-0"></span>
              <span class="whitespace-pre-wrap"> </span>
            </div>
          {/if}
        {/each}
      </div>
    </div>

  <!-- Inline view -->
  {:else}
    <div class="bg-bg-card p-1 font-mono">
      {#each filteredDiffLines as line, i}
        {@const originalIdx = diffLines.indexOf(line)}
        {@const hasWordDiff = granularity === 'word' && line.type !== 'same' && inlineWordDiffs.has(originalIdx)}
        <div class="relative py-0.5 px-1 flex gap-2 {hasWordDiff ? lineTextClass(line.type) : lineClass(line.type)}"
             style="animation: list-item-in 0.12s cubic-bezier(0.16,1,0.3,1) {Math.min(i*8,120)}ms both;">
          {#if line.type === 'removed'}<span class="absolute left-0 inset-y-0 w-[1px] bg-neon-red/50"></span>{/if}
          {#if line.type === 'added'}<span class="absolute left-0 inset-y-0 w-[1px] bg-neon-green/50"></span>{/if}
          <span class="text-text-dim/40 select-none w-3 shrink-0">{linePrefix(line.type)}</span>
          <span class="text-text-dim/40 select-none w-4 text-right shrink-0">{line.lineNum}</span>
          <span class="whitespace-pre-wrap break-all">
            {#if hasWordDiff}
              {#each inlineWordDiffs.get(originalIdx)! as part}
                {#if part.removed}
                  <span class="bg-neon-red/20">{part.value}</span>
                {:else if part.added}
                  <span class="bg-neon-green/20">{part.value}</span>
                {:else}
                  {part.value}
                {/if}
              {/each}
            {:else}
              {line.text}
            {/if}
          </span>
        </div>
      {/each}
    </div>
  {/if}
</div>
