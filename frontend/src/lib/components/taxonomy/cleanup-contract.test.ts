/**
 * Cleanup contract test — Pattern Graph 3D scope.
 *
 * Spec: `.claude/skills/brand-guidelines/references/3d-visualization.md`
 * Disposal Contract + Audit Checklist.
 *
 * Locks the canon cleanup behavior into the spec. Specifically asserts
 * that every canonical animation canceller is wired in the cleanup return
 * and every disposable is released — including the canon F5/F8/F10
 * atmospheric cancellers (`_removeEdgeAnim`, `_breathingAnim`,
 * `_removeDustAnim`) that were earlier walked back under the
 * over-restrictive interpretation.
 *
 * Source-grep over `import.meta.glob ?raw` — no module load, no mocks.
 */
import { describe, expect, test } from 'vitest';

const semTopSourceMap = import.meta.glob<string>(
  ['./SemanticTopology.svelte'],
  { query: '?raw', import: 'default', eager: true },
);

function readSemTopSource(): string {
  const content = semTopSourceMap['./SemanticTopology.svelte'];
  if (typeof content !== 'string') {
    throw new Error('cleanup-contract: SemanticTopology.svelte not found in glob map');
  }
  return content;
}

/**
 * Slice the cleanup return body out of the onMount block.
 *
 * Anchors on `</script>` near EOF and walks backward to find the last
 * `return () => {` before it (= the onMount cleanup return). Then walks
 * forward, balancing braces, to find the matching close.
 */
function extractCleanupBody(src: string): string {
  const closeMarker = '</script>';
  const closeIdx = src.indexOf(closeMarker);
  if (closeIdx < 0) {
    throw new Error('cleanup-contract: </script> not found');
  }
  const returnPattern = /return\s*\(\s*\)\s*=>\s*\{/g;
  let lastReturnIdx = -1;
  let match: RegExpExecArray | null;
  while ((match = returnPattern.exec(src)) !== null) {
    if (match.index < closeIdx) {
      lastReturnIdx = match.index + match[0].length;
    }
  }
  if (lastReturnIdx < 0) {
    throw new Error('cleanup-contract: onMount cleanup return not found');
  }
  let depth = 1;
  let i = lastReturnIdx;
  while (i < src.length && depth > 0) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    i++;
  }
  return src.slice(lastReturnIdx, i - 1);
}

describe('Cleanup contract — canon animation cancellers', () => {
  test('all 8 canonical cancellers wired in cleanup body', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    // Per canon F5/F8/F10 + existing pre-canon cancellers. Every
    // `addAnimationCallback` registration must have a matching invocation
    // in the cleanup return.
    const required = [
      // pre-canon (already wired)
      'removeBeamUpdate()',
      '_removeRingLodUpdate()',
      '_removeFormationAnim?.()',
      '_removeDomainRotation?.()',
      '_removeReadinessBillboard?.()',
      // canon F5/F8/F10
      '_removeEdgeAnim?.()',
      '_removeDustAnim?.()',
      '_breathingAnim?.()',
    ];
    const missing = required.filter((sig) => !cleanup.includes(sig));
    expect(missing).toEqual([]);
  });
});

describe('Cleanup contract — canon disposables', () => {
  test('all top-level disposables released in cleanup body', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    const required = [
      'beamPool?.dispose()',
      'clusterPhysics?.clear()',
      'disposeRingEntry(entry)',
      '_readinessRings.clear()',
      'ro.disconnect()',
      'interaction?.dispose()',
      'labels?.dispose()',
      'renderer?.dispose()',
    ];
    const missing = required.filter((sig) => !cleanup.includes(sig));
    expect(missing).toEqual([]);
  });

  test('template-ring pool drained on unmount (high-water mark retained)', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    expect(cleanup).toContain('_templateRingById.clear()');
    expect(cleanup).toContain('_freeTemplateRings.length = 0');
    expect(cleanup).toMatch(/_templateRingPool/);
    expect(cleanup).toMatch(/_freeTemplateRings\.push/);
  });
});

describe('Cleanup contract — canon F2 globalThis state + disposal', () => {
  test('__semTopGlowTexture is the only production globalThis assignment', () => {
    // Per canon F2: __semTopGlowTexture is the canonical CanvasTexture
    // cache for the radial-gradient glow used by domain anchor energy
    // cores. The component lazy-builds it once via the `_glowTextureBuilt`
    // sentinel and disposes + nulls it in the cleanup return (assertion
    // below). Other production globalThis assignments are regressions.
    const src = readSemTopSource();
    const lines = src.split('\n');
    const productionGlobals: string[] = [];
    const testGatedGlobals: string[] = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!/\(?\s*globalThis\s*(as\s+\w+)?\s*\)?\s*\.\s*\w+\s*=/.test(line)) continue;
      const window = lines.slice(Math.max(0, i - 6), i + 1).join('\n');
      const isTestGated =
        /import\.meta\.env\.MODE\s*===\s*['"]test['"]/.test(window) ||
        /import\.meta\.env\.MODE\s*!==\s*['"]production['"]/.test(window);
      if (isTestGated) {
        testGatedGlobals.push(`${i + 1}: ${line.trim()}`);
      } else {
        productionGlobals.push(`${i + 1}: ${line.trim()}`);
      }
    }
    // Production globalThis assignments allowed: only the canon F2
    // glow texture cache (set + cleared = 2 assignments). Anything else
    // here is a regression.
    const unauthorized = productionGlobals.filter(
      (entry) => !entry.includes('__semTopGlowTexture'),
    );
    expect(unauthorized).toEqual([]);
  });

  test('__semTopGlowTexture is disposed and nulled in cleanup body (canon F2)', () => {
    // Per canon F2 "Disposal: texture disposed on unmount". The cleanup
    // return must explicitly call `.dispose()` on the cached texture and
    // reset the global + the `_glowTextureBuilt` sentinel so a remount
    // rebuilds the texture cleanly.
    const cleanup = extractCleanupBody(readSemTopSource());
    expect(cleanup).toMatch(/__semTopGlowTexture/);
    expect(cleanup).toMatch(/glowTex.*\.dispose\(\)/s);
    expect(cleanup).toMatch(/__semTopGlowTexture\s*\)?\s*=\s*undefined/);
    expect(cleanup).toMatch(/_glowTextureBuilt\s*=\s*false/);
  });
});
