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
import { describe, expect, test, it } from 'vitest';

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
  test('all 10 canonical cancellers wired in cleanup body', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    // Per canon F5/F8/F10/F19 + existing pre-canon cancellers. Every
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
      // canon F19 (envelopement burst + emissive flash)
      '_removeEnvelopeUpdate?.()',
      '_removeFlashUpdate?.()',
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
      'envelopePool?.dispose()', // canon F19 — plasma envelope pool
      '_flashStates.clear()',    // canon F19 — emissive flash state map
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

// Append to cleanup-contract.test.ts — Sub-project A: Lifecycle Hardening
// 19 source-grep assertions per spec §5.2.

function readSource(relPath: string): string {
  // import.meta.glob pattern — match existing convention in cleanup-contract.test.ts
  const mod = import.meta.glob<string>(
    ['./BeamPool.ts', './EnvelopePool.ts', './TopologyLabels.ts', './TopologyRenderer.ts', './SemanticTopology.svelte', './scene-cleanup.ts'],
    { query: '?raw', import: 'default', eager: true },
  );
  const key = `./${relPath}`;
  const src = mod[key];
  if (typeof src !== 'string') throw new Error(`Source not found for glob key ${key}`);
  return src;
}

describe('Lifecycle Hardening — source-grep contract (spec §5.2)', () => {
  // ── #1 ──
  it('#1 — BeamPool.ts constructor sets userData.persistent = true via dot-assignment', () => {
    const src = readSource('BeamPool.ts');
    expect(src).toMatch(/this\.group\.userData\.persistent\s*=\s*true/);
  });

  // ── #2 ──
  it('#2 — EnvelopePool.ts constructor sets userData.persistent = true via dot-assignment', () => {
    const src = readSource('EnvelopePool.ts');
    expect(src).toMatch(/this\.group\.userData\.persistent\s*=\s*true/);
  });

  // ── #3 ──
  it('#3 — TopologyLabels.ts sets userData.persistent = true on _group (constructor body OR IIFE field-initializer)', () => {
    const src = readSource('TopologyLabels.ts');
    // Permit either form: `this._group.userData.persistent = true` (constructor body)
    // OR `g.userData.persistent = true` (IIFE local var)
    expect(src).toMatch(/this\._group\.userData\.persistent\s*=\s*true|g\.userData\.persistent\s*=\s*true/);
  });

  // ── #4 ──
  it('#4 — TopologyRenderer.ts sets userData.persistent = true on each of the 3 lights', () => {
    const src = readSource('TopologyRenderer.ts');
    expect(src).toMatch(/ambient\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/directional\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/hemisphere\.userData\.persistent\s*=\s*true/);
  });

  // ── #5 ──
  it('#5 — _dustPoints lazy block sets userData.persistent + isNeuralDust as dot-assignments, no object-replacement', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).toMatch(/_dustPoints\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/_dustPoints\.userData\.isNeuralDust\s*=\s*true/);
    expect(src).not.toMatch(/_dustPoints\.userData\s*=\s*\{/);
  });

  // ── #5b ──
  it('#5b — _readinessRingGroup lazy block uses dot-assignments, no object-replacement', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).toMatch(/_readinessRingGroup\.userData\.isReadinessRingGroup\s*=\s*true/);
    expect(src).not.toMatch(/_readinessRingGroup\.userData\s*=\s*\{/);
  });

  // ── #5c ──
  it('#5c — _templateRingGroup lazy block uses dot-assignments, no object-replacement', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).toMatch(/_templateRingGroup\.userData\.isTemplateRingGroup\s*=\s*true/);
    expect(src).not.toMatch(/_templateRingGroup\.userData\s*=\s*\{/);
  });

  // ── #6 ──
  it('#6 — _readinessRingGroup has userData.persistent = true', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).toMatch(/_readinessRingGroup\.userData\.persistent\s*=\s*true/);
  });

  // ── #7 ──
  it('#7 — _templateRingGroup has userData.persistent = true', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).toMatch(/_templateRingGroup\.userData\.persistent\s*=\s*true/);
  });

  // ── #8 — anti-pattern ──
  it('#8 — no source file matches the object-replacement anti-pattern userData = { ..., persistent: ... }', () => {
    const files = ['BeamPool.ts', 'EnvelopePool.ts', 'TopologyLabels.ts', 'TopologyRenderer.ts', 'SemanticTopology.svelte', 'scene-cleanup.ts'];
    for (const f of files) {
      const src = readSource(f);
      expect(src, `${f} should not have object-replacement assignment of persistent`).not.toMatch(/userData\s*=\s*\{[^{}]*?\bpersistent\s*:/);
    }
  });

  // ── #9 — rebuildScene body cleanup ──
  it('#9 — rebuildScene body calls cleanupScene() once and contains no manual save/restore for persistent groups', () => {
    const src = readSource('SemanticTopology.svelte');
    const rebuildIdx = src.indexOf('function rebuildScene(');
    expect(rebuildIdx).toBeGreaterThan(0);
    const handleLodIdx = src.indexOf('function handleLodChange(', rebuildIdx);
    const slice = src.slice(rebuildIdx, handleLodIdx > rebuildIdx ? handleLodIdx : rebuildIdx + 80000);

    expect(slice.match(/cleanupScene\(/g)?.length).toBe(1);
    // Manual remove patterns gone
    expect(slice).not.toMatch(/renderer\.scene\.remove\(\s*beamPool\.group\s*\)/);
    expect(slice).not.toMatch(/renderer\.scene\.remove\(\s*envelopePool\.group\s*\)/);
    expect(slice).not.toMatch(/renderer\.scene\.remove\(\s*_readinessRingGroup\s*\)/);
    expect(slice).not.toMatch(/renderer\.scene\.remove\(\s*_templateRingGroup\s*\)/);
    // Unconditional add patterns gone (one-time adds inside lazy if (!_X) blocks are EXEMPT — handled by #9c/#9d/#9e)
    // Bare unconditional add for beam/envelope/labels — should NOT appear in rebuildScene body
    expect(slice).not.toMatch(/^\s*renderer\.scene\.add\(\s*beamPool\.group\s*\)/m);
    expect(slice).not.toMatch(/^\s*renderer\.scene\.add\(\s*envelopePool\.group\s*\)/m);
    expect(slice).not.toMatch(/^\s*renderer\.scene\.add\(\s*labels\.group\s*\)/m);
  });

  // ── #9b — labels.group one-time add in onMount ──
  it('#9b — onMount body adds labels.group exactly once', () => {
    const src = readSource('SemanticTopology.svelte');
    const onMountIdx = src.indexOf('onMount(() => {');
    expect(onMountIdx).toBeGreaterThan(0);
    const onMountSlice = src.slice(onMountIdx, onMountIdx + 8000);
    expect(onMountSlice).toMatch(/renderer\.scene\.add\(\s*labels\??\.group\s*\)/);
  });

  // ── #9c — _readinessRingGroup lazy-block add ──
  it('#9c — _readinessRingGroup lazy block contains flag + scene.add', () => {
    const src = readSource('SemanticTopology.svelte');
    // Find the if (!_readinessRingGroup) block
    const blockIdx = src.search(/if\s*\(\s*!_readinessRingGroup\s*\)/);
    expect(blockIdx).toBeGreaterThan(0);
    const blockSlice = src.slice(blockIdx, blockIdx + 600);
    expect(blockSlice).toMatch(/_readinessRingGroup\.userData\.persistent\s*=\s*true/);
    expect(blockSlice).toMatch(/renderer\.scene\.add\(\s*_readinessRingGroup\s*\)/);
  });

  // ── #9d — _templateRingGroup lazy-block add ──
  it('#9d — _templateRingGroup lazy block contains flag + scene.add', () => {
    const src = readSource('SemanticTopology.svelte');
    const blockIdx = src.search(/if\s*\(\s*!_templateRingGroup\s*\)/);
    expect(blockIdx).toBeGreaterThan(0);
    const blockSlice = src.slice(blockIdx, blockIdx + 800);
    expect(blockSlice).toMatch(/_templateRingGroup\.userData\.persistent\s*=\s*true/);
    expect(blockSlice).toMatch(/renderer\.scene\.add\(\s*_templateRingGroup\s*\)/);
  });

  // ── #9e — _dustPoints lazy-block add ──
  it('#9e — _dustPoints lazy block contains flag + scene.add; no bare scene.add(_dustPoints) outside the block', () => {
    const src = readSource('SemanticTopology.svelte');
    const blockIdx = src.search(/if\s*\(\s*!_dustPoints\s*\)/);
    expect(blockIdx).toBeGreaterThan(0);
    const blockSlice = src.slice(blockIdx, blockIdx + 4000);
    expect(blockSlice).toMatch(/_dustPoints\.userData\.persistent\s*=\s*true/);
    expect(blockSlice).toMatch(/renderer\.scene\.add\(\s*_dustPoints\s*\)/);
    // Outside the block (rebuildScene body excluding the if-block): no bare scene.add(_dustPoints)
    const rebuildIdx = src.indexOf('function rebuildScene(');
    const handleLodIdx = src.indexOf('function handleLodChange(', rebuildIdx);
    const rebuildSlice = src.slice(rebuildIdx, handleLodIdx > rebuildIdx ? handleLodIdx : rebuildIdx + 80000);
    // count scene.add(_dustPoints) — must be exactly 1
    expect(rebuildSlice.match(/renderer\.scene\.add\(\s*_dustPoints\s*\)/g)?.length).toBe(1);
  });

  // ── #10 — no while-loop in rebuildScene ──
  it('#10 — SemanticTopology.svelte no longer contains the while (renderer.scene.children.length > 0) loop', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).not.toMatch(/while\s*\(\s*renderer\.scene\.children\.length\s*>\s*0\s*\)/);
  });

  // ── #11 — imports cleanupScene ──
  it('#11 — SemanticTopology.svelte imports cleanupScene from ./scene-cleanup', () => {
    const src = readSource('SemanticTopology.svelte');
    expect(src).toMatch(/import\s*\{[^}]*\bcleanupScene\b[^}]*\}\s*from\s*['"]\.\/scene-cleanup['"]/);
  });

  // ── #12 — scene-cleanup.ts exports cleanupScene ──
  it('#12 — scene-cleanup.ts exports cleanupScene', () => {
    const src = readSource('scene-cleanup.ts');
    expect(src).toMatch(/export\s+function\s+cleanupScene\s*\(/);
  });

  // ── #13 — cleanupScene body has no per-element disposer calls ──
  it('#13 — scene-cleanup.ts cleanupScene body does not call per-element disposers (disposeRingEntry, _releaseTemplateRing, etc.)', () => {
    const src = readSource('scene-cleanup.ts');
    expect(src).not.toMatch(/disposeRingEntry/);
    expect(src).not.toMatch(/_releaseTemplateRing/);
    expect(src).not.toMatch(/disposeBeam/);
    expect(src).not.toMatch(/disposeEnvelope/);
  });
});
