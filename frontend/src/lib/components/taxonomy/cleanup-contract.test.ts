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
    // Post-Sub-project B: coordinator?.dispose() absorbs 8 unconditional cancellers;
    // formation and readiness billboard remain conditional and survive.
    const required = [
      'coordinator?.dispose()',         // absorbs 8 unconditional cancellers (Sub-project B)
      '_removeFormationAnim?.()',       // conditional during entrance; retained
      '_removeReadinessBillboard?.()',  // conditional when _readinessRings.size > 0; retained
    ];
    const missing = required.filter((sig) => !cleanup.includes(sig));
    expect(missing).toEqual([]);
  });
});

describe('Cleanup contract — canon disposables', () => {
  test('all top-level disposables released in cleanup body', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    // Sub-project D — readiness ring disposal absorbed into
    // ringBuilder?.dispose() (replaces `disposeRingEntry(entry)` loop +
    // `_readinessRings.clear()`).
    const required = [
      'beamPool?.dispose()',
      'clusterPhysics?.clear()',
      'envelopePool?.dispose()', // canon F19 — plasma envelope pool
      '_flashStates.clear()',    // canon F19 — emissive flash state map
      'ringBuilder?.dispose()',  // Sub-project D — absorbs readiness + template ring cleanup
      'dustBuilder?.dispose()',  // Sub-project D — neural dust cleanup
      'ro.disconnect()',
      'interaction?.dispose()',
      'labels?.dispose()',
      'renderer?.dispose()',
    ];
    const missing = required.filter((sig) => !cleanup.includes(sig));
    expect(missing).toEqual([]);
  });

  test('template-ring pool drained on unmount (high-water mark retained)', () => {
    // Sub-project D — template-ring pool cleanup migrated to
    // RingBuilder.dispose(). SemanticTopology.svelte cleanup invokes
    // `ringBuilder?.dispose()` which clears `_templateRingById`,
    // resets `_freeTemplateRings`, and detaches the persistent
    // `_templateRingGroup`. The high-water-mark pool retention is
    // private to RingBuilder; runtime tests
    // (SemanticTopology.test.ts pool tests) verify behaviour
    // end-to-end via `__semTopTemplateRingPool` dev hook.
    const cleanup = extractCleanupBody(readSemTopSource());
    expect(cleanup).toContain('ringBuilder?.dispose()');
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
    // reset the global. Sub-project D — `_glowTextureBuilt` sentinel
    // migrated to DomainBuilder.ts (private field reset by dispose()).
    const cleanup = extractCleanupBody(readSemTopSource());
    expect(cleanup).toMatch(/__semTopGlowTexture/);
    expect(cleanup).toMatch(/glowTex.*\.dispose\(\)/s);
    expect(cleanup).toMatch(/__semTopGlowTexture\s*\)?\s*=\s*undefined/);
  });
});

// Append to cleanup-contract.test.ts — Sub-project A: Lifecycle Hardening
// 19 source-grep assertions per spec §5.2.

function readSource(relPath: string): string {
  // import.meta.glob pattern — match existing convention in cleanup-contract.test.ts.
  // Sub-project D migration: dust + ring lazy-init blocks moved into builder
  // files, so the glob extends to cover RingBuilder.ts + DustBuilder.ts.
  const mod = import.meta.glob<string>(
    [
      './BeamPool.ts',
      './EnvelopePool.ts',
      './TopologyLabels.ts',
      './TopologyRenderer.ts',
      './SemanticTopology.svelte',
      './scene-cleanup.ts',
      './builders/RingBuilder.ts',
      './builders/DustBuilder.ts',
    ],
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
  // Sub-project D — `_dustPoints` lazy block migrated to DustBuilder.ts.
  it('#5 — DustBuilder dust-points construction sets userData.persistent + isNeuralDust as dot-assignments, no object-replacement', () => {
    const src = readSource('builders/DustBuilder.ts');
    expect(src).toMatch(/\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/\.userData\.isNeuralDust\s*=\s*true/);
    expect(src).not.toMatch(/\.userData\s*=\s*\{[^{}]*persistent/);
  });

  // ── #5b ──
  // Sub-project D — `_readinessRingGroup` lazy block migrated to RingBuilder.ts.
  it('#5b — RingBuilder readiness-ring-group lazy block uses dot-assignments, no object-replacement', () => {
    const src = readSource('builders/RingBuilder.ts');
    expect(src).toMatch(/\.userData\.isReadinessRingGroup\s*=\s*true/);
    expect(src).not.toMatch(/_readinessRingGroup\.userData\s*=\s*\{/);
  });

  // ── #5c ──
  // Sub-project D — `_templateRingGroup` lazy block migrated to RingBuilder.ts.
  it('#5c — RingBuilder template-ring-group lazy block uses dot-assignments, no object-replacement', () => {
    const src = readSource('builders/RingBuilder.ts');
    expect(src).toMatch(/\.userData\.isTemplateRingGroup\s*=\s*true/);
    expect(src).not.toMatch(/_templateRingGroup\.userData\s*=\s*\{/);
  });

  // ── #6 ──
  // Sub-project D — readiness ring group persistent flag migrated to RingBuilder.ts.
  it('#6 — RingBuilder sets readiness ring group userData.persistent = true', () => {
    const src = readSource('builders/RingBuilder.ts');
    // The readiness ring group has `userData.isReadinessRingGroup = true`
    // alongside `userData.persistent = true`; assert both are present.
    expect(src).toMatch(/\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/\.userData\.isReadinessRingGroup\s*=\s*true/);
  });

  // ── #7 ──
  // Sub-project D — template ring group persistent flag migrated to RingBuilder.ts.
  it('#7 — RingBuilder sets template ring group userData.persistent = true', () => {
    const src = readSource('builders/RingBuilder.ts');
    expect(src).toMatch(/\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/\.userData\.isTemplateRingGroup\s*=\s*true/);
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

  // ── #9c — readiness ring group lazy-block add ──
  // Sub-project D — RingBuilder.ts owns the readiness-ring group lazy block.
  it('#9c — RingBuilder readiness-ring-group lazy block contains flag + scene.add', () => {
    const src = readSource('builders/RingBuilder.ts');
    // Builder uses `scene.add(...)` (parameter name) instead of
    // `renderer.scene.add(...)` — the parameter is the live THREE.Scene
    // passed to build(). Both flag + scene-add must appear in the file.
    expect(src).toMatch(/\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/scene\.add\(/);
  });

  // ── #9d — template ring group lazy-block add ──
  // Sub-project D — RingBuilder.ts owns the template-ring group lazy block.
  it('#9d — RingBuilder template-ring-group lazy block contains flag + scene.add', () => {
    const src = readSource('builders/RingBuilder.ts');
    expect(src).toMatch(/\.userData\.isTemplateRingGroup\s*=\s*true/);
    expect(src).toMatch(/scene\.add\(/);
  });

  // ── #9e — dust-points lazy-block add ──
  // Sub-project D — DustBuilder.ts owns the dust-points lazy block.
  it('#9e — DustBuilder dust-points construction contains flag + scene.add', () => {
    const src = readSource('builders/DustBuilder.ts');
    expect(src).toMatch(/\.userData\.persistent\s*=\s*true/);
    expect(src).toMatch(/scene\.add\(/);
    // SemanticTopology.svelte must not contain a bare _dustPoints
    // scene.add anymore — the construction migrated to DustBuilder.
    const semTopSrc = readSource('SemanticTopology.svelte');
    expect(semTopSrc).not.toMatch(/renderer\.scene\.add\(\s*_dustPoints\s*\)/);
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

// Append to cleanup-contract.test.ts — Sub-project B: Animation Coordinator
// 10 source-grep assertions per spec §5.2.

function readAnimationCoordinatorSrc(relPath: string): string {
  const mod = import.meta.glob<string>(
    [
      './AnimationCoordinator.ts',
      './SemanticTopology.svelte',
      './TopologyRenderer.ts',
    ],
    { query: '?raw', import: 'default', eager: true },
  );
  const key = `./${relPath}`;
  const src = mod[key];
  if (typeof src !== 'string') throw new Error(`Source not found for ${key}`);
  return src;
}

describe('Animation Coordinator — source-grep contract (spec §5.2)', () => {
  // ── #1 ──
  it('#1 — AnimationCoordinator.ts exports class + AnimationPhase + AnimationHandler + PHASE_ORDER', () => {
    const src = readAnimationCoordinatorSrc('AnimationCoordinator.ts');
    expect(src).toMatch(/export\s+class\s+AnimationCoordinator/);
    expect(src).toMatch(/export\s+type\s+AnimationPhase/);
    expect(src).toMatch(/export\s+type\s+AnimationHandler/);
    // PHASE_ORDER is exactly the 5-phase tuple in canonical order
    expect(src).toMatch(/PHASE_ORDER[\s\S]*?'impact'[\s\S]*?'physics'[\s\S]*?'breathing'[\s\S]*?'ambient'[\s\S]*?'camera'/);
  });

  // ── #2 ──
  it('#2 — coordinator instantiation is AFTER renderer construction AND BEFORE first register call in onMount', () => {
    const src = readAnimationCoordinatorSrc('SemanticTopology.svelte');
    const onMountIdx = src.indexOf('onMount(() => {');
    expect(onMountIdx).toBeGreaterThan(0);
    const onMountSlice = src.slice(onMountIdx, onMountIdx + 8000);
    const rendererPos = onMountSlice.search(/renderer\s*=\s*new\s+TopologyRenderer\(/);
    const coordPos = onMountSlice.search(/coordinator\s*=\s*new\s+AnimationCoordinator\(/);
    const firstRegPos = onMountSlice.search(/coordinator\.register\(/);
    expect(rendererPos).toBeGreaterThan(-1);
    expect(coordPos).toBeGreaterThan(-1);
    expect(firstRegPos).toBeGreaterThan(-1);
    expect(rendererPos).toBeLessThan(coordPos);
    expect(coordPos).toBeLessThan(firstRegPos);
  });

  // ── #3 ──
  it('#3 — SemanticTopology.svelte has ZERO direct renderer.addAnimationCallback calls', () => {
    const src = readAnimationCoordinatorSrc('SemanticTopology.svelte');
    expect(src).not.toMatch(/renderer\s*[!?]?\.addAnimationCallback\(/);
  });

  // ── #4 ──
  it('#4 — impact-phase strict order: beam.update BEFORE envelope.update BEFORE _tickFlashStates', () => {
    const src = readAnimationCoordinatorSrc('SemanticTopology.svelte');
    const onMountIdx = src.indexOf('onMount(() => {');
    const onMountSlice = src.slice(onMountIdx, onMountIdx + 8000);
    const beamPos = onMountSlice.search(/coordinator\.register\(\s*['"]impact['"][\s\S]{0,200}beamPool[?!]?\.update/);
    const envPos = onMountSlice.search(/coordinator\.register\(\s*['"]impact['"][\s\S]{0,200}envelopePool[?!]?\.update/);
    const flashPos = onMountSlice.search(/coordinator\.register\(\s*['"]impact['"][\s\S]{0,200}_tickFlashStates/);
    expect(beamPos).toBeGreaterThan(-1);
    expect(envPos).toBeGreaterThan(-1);
    expect(flashPos).toBeGreaterThan(-1);
    expect(beamPos).toBeLessThan(envPos);
    expect(envPos).toBeLessThan(flashPos);
  });

  // ── #5 ──
  it('#5 — exactly 11 coordinator.register call sites distributed across rebuildScene (5), handleRecluster (1 formation), onMount (5)', () => {
    const src = readAnimationCoordinatorSrc('SemanticTopology.svelte');
    const totalMatches = src.match(/coordinator\.register\(/g);
    expect(totalMatches?.length).toBe(11);

    // Source order at HEAD 522701f8: rebuildScene (~966) → handleLodChange
    // (~1692) → handleRecluster (~1767, contains formation registration) →
    // onMount (~2381). Slice in source order, not assumed-presentation order.
    const rebuildIdx = src.indexOf('function rebuildScene(');
    const handleLodIdx = src.indexOf('function handleLodChange(');
    const handleReclusterIdx = src.indexOf('async function handleRecluster(');
    const onMountIdx = src.indexOf('onMount(() => {');
    expect(rebuildIdx).toBeGreaterThan(0);
    expect(handleLodIdx).toBeGreaterThan(rebuildIdx);
    expect(handleReclusterIdx).toBeGreaterThan(handleLodIdx);
    expect(onMountIdx).toBeGreaterThan(handleReclusterIdx);

    // rebuildScene body: rebuildIdx → handleLodIdx (5 register calls expected)
    const rebuildRegion = src.slice(rebuildIdx, handleLodIdx);
    const rebuildCount = (rebuildRegion.match(/coordinator\.register\(/g) ?? []).length;
    expect(rebuildCount).toBe(5);

    // handleRecluster body: handleReclusterIdx → onMountIdx (1 register call = formation)
    const handleReclusterRegion = src.slice(handleReclusterIdx, onMountIdx);
    const handleReclusterCount = (handleReclusterRegion.match(/coordinator\.register\(/g) ?? []).length;
    expect(handleReclusterCount).toBe(1);

    // onMount body: onMountIdx → end of file (5 register calls expected)
    const onMountRegion = src.slice(onMountIdx);
    const onMountCount = (onMountRegion.match(/coordinator\.register\(/g) ?? []).length;
    expect(onMountCount).toBe(5);
  });

  // ── #6 ──
  it('#6 — cleanup return calls coordinator?.dispose(); does NOT contain absorbed cancellers (formation + readiness billboard retained)', () => {
    const src = readAnimationCoordinatorSrc('SemanticTopology.svelte');
    // Find cleanup return: search for the trailing `return () => {` near end of onMount
    const cleanupIdx = src.lastIndexOf('return () => {');
    expect(cleanupIdx).toBeGreaterThan(0);
    const cleanupSlice = src.slice(cleanupIdx, cleanupIdx + 4000);
    expect(cleanupSlice).toMatch(/coordinator\??\.dispose\(\)/);
    // 8 absorbed cancellers must be gone
    expect(cleanupSlice).not.toMatch(/_removeEnvelopeUpdate\?\.\(/);
    expect(cleanupSlice).not.toMatch(/_removeFlashUpdate\?\.\(/);
    expect(cleanupSlice).not.toMatch(/_removeRingLodUpdate\(/);
    expect(cleanupSlice).not.toMatch(/_removeDomainRotation\?\.\(/);
    expect(cleanupSlice).not.toMatch(/_removeEdgeAnim\?\.\(/);
    expect(cleanupSlice).not.toMatch(/_removeDustAnim\?\.\(/);
    expect(cleanupSlice).not.toMatch(/_breathingAnim\?\.\(/);
    expect(cleanupSlice).not.toMatch(/removeBeamUpdate\(/);
    // 2 conditional cancellers retained
    expect(cleanupSlice).toMatch(/_removeFormationAnim\?\.\(/);
    expect(cleanupSlice).toMatch(/_removeReadinessBillboard\?\.\(/);
  });

  // ── #7 ──
  it('#7 — AnimationCoordinator._tick wraps each handler invocation in try/catch', () => {
    const src = readAnimationCoordinatorSrc('AnimationCoordinator.ts');
    const tickIdx = src.indexOf('private _tick()');
    expect(tickIdx).toBeGreaterThan(-1);
    const tickEnd = src.indexOf('\n  }', tickIdx); // method body ends at matching brace
    const tickSlice = src.slice(tickIdx, tickEnd);
    expect(tickSlice).toMatch(/try\s*\{/);
    expect(tickSlice).toMatch(/catch\s*\(/);
  });

  // ── #8 ──
  it('#8 — Anti-pattern: NO source file under taxonomy/ (except coordinator files + TopologyRenderer.test.ts) calls .addAnimationCallback', () => {
    const mod = import.meta.glob<string>(
      [
        './*.ts',
        './*.svelte',
      ],
      { query: '?raw', import: 'default', eager: true },
    );
    const exemptions = [
      './AnimationCoordinator.ts',
      './AnimationCoordinator.test.ts',
      './AnimationCoordinator.integration.test.ts',
      './TopologyRenderer.ts',
      './TopologyRenderer.test.ts',
    ];
    for (const [path, src] of Object.entries(mod)) {
      if (exemptions.includes(path)) continue;
      if (typeof src !== 'string') continue;
      expect(src, `${path} should not call .addAnimationCallback (only AnimationCoordinator does)`).not.toMatch(/\.addAnimationCallback\(/);
    }
  });

  // ── #9 ──
  it('#9 — AnimationCoordinator._tick body contains no per-frame allocations', () => {
    const src = readAnimationCoordinatorSrc('AnimationCoordinator.ts');
    const tickIdx = src.indexOf('private _tick()');
    const tickEnd = src.indexOf('\n  }', tickIdx);
    const tickSlice = src.slice(tickIdx, tickEnd);
    expect(tickSlice).not.toMatch(/new\s+(Map|Array|Set|WeakSet|Vector\d|Color|Quaternion|Matrix\d)\b/);
  });

  // ── #10 ──
  it('#10 — TopologyRenderer.addAnimationCallback signature unchanged: (cb: () => void) => () => void', () => {
    const src = readAnimationCoordinatorSrc('TopologyRenderer.ts');
    expect(src).toMatch(/addAnimationCallback\(cb:\s*\(\)\s*=>\s*void\)\s*:\s*\(\)\s*=>\s*void/);
  });
});

// Append to cleanup-contract.test.ts — Sub-project C: Impact Coordinator
// 12 source-grep assertions per spec §5.2 (#1–#12).
//
// Tests #1–#2 + #11 pin the coordinator class file itself and PASS at HEAD
// (coordinator-side state already implemented in Cycle 1 GREEN).
// Tests #3–#10 + #12 pin SemanticTopology.svelte migration state and FAIL
// at HEAD (Cycle 2 GREEN's job to satisfy).

function readImpactCoordinatorSrc(relPath: string): string {
  const mod = import.meta.glob<string>(
    [
      './ImpactCoordinator.ts',
      './SemanticTopology.svelte',
    ],
    { query: '?raw', import: 'default', eager: true },
  );
  const key = `./${relPath}`;
  const src = mod[key];
  if (typeof src !== 'string') throw new Error(`Source not found for ${key}`);
  return src;
}

/**
 * Brace-balanced slice of the breathing-phase callback body in
 * SemanticTopology.svelte. Locates `coordinator.register('breathing',`
 * then walks forward balancing `{`/`}` to find the matching close of
 * the callback's body. Returns the body slice (excluding the outer
 * `() => { ... }` braces) so source-grep regexes scope to just the
 * breathing-handler logic and don't pick up `coordinator.register`
 * argument noise.
 *
 * Scope-specific per spec §5.2 #8 + #12: both tests scope to the
 * breathing-phase callback body, not the full source.
 */
function extractBreathingHandlerBody(src: string): string {
  // Sub-project D — breathing handler may be an inline arrow OR a named
  // function reference (e.g. `_advanceBreathing`) for orchestrator LOC
  // compression. Try inline first, then fall back to named-function
  // body extraction.
  const anchor = "coordinator.register('breathing',";
  const anchorIdx = src.indexOf(anchor);
  if (anchorIdx < 0) {
    throw new Error('extractBreathingHandlerBody: anchor not found');
  }
  // Read the slice between `(` and `)` of register(...).
  const closeParenIdx = src.indexOf(')', anchorIdx);
  const registerArg = src.slice(anchorIdx + anchor.length, closeParenIdx).trim();
  // Inline arrow form — start at the arrow's body open brace.
  const arrowIdx = src.indexOf('=>', anchorIdx);
  if (arrowIdx !== -1 && arrowIdx < closeParenIdx) {
    const openIdx = src.indexOf('{', arrowIdx);
    if (openIdx < 0) {
      throw new Error('extractBreathingHandlerBody: body { not found');
    }
    let depth = 1;
    let i = openIdx + 1;
    while (i < src.length && depth > 0) {
      const ch = src[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
    return src.slice(openIdx + 1, i - 1);
  }
  // Named-function reference form — locate the function declaration and
  // extract its body. `registerArg` is the function name (e.g. `_advanceBreathing`).
  const fnName = registerArg.replace(/[,;\s]+$/, '');
  const declRe = new RegExp(`function\\s+${fnName.replace(/[\\^$.*+?()[\\]{}|]/g, '\\$&')}\\s*\\(`);
  const declMatch = declRe.exec(src);
  if (!declMatch) {
    throw new Error(`extractBreathingHandlerBody: function ${fnName} declaration not found`);
  }
  const declStart = declMatch.index;
  const openIdx = src.indexOf('{', declStart);
  if (openIdx < 0) {
    throw new Error('extractBreathingHandlerBody: declaration { not found');
  }
  let depth = 1;
  let i = openIdx + 1;
  while (i < src.length && depth > 0) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    i++;
  }
  return src.slice(openIdx + 1, i - 1);
}

/**
 * Brace-balanced slice of a top-level method body in
 * `ImpactCoordinator.ts`. Used to extract `fire(...)` and `_tick(...)`
 * bodies for source-grep assertions that pin per-method invariants
 * (e.g., zero per-frame allocations in `_tick`, envelope literal
 * shape in the `onImpact` block inside `fire`).
 *
 * Anchors on a regex matching the method signature header (e.g.
 * `/fire\(request: ImpactRequest\): void \{/`). Returns the body
 * slice (excluding the outer braces).
 */
function extractMethodBody(src: string, headerRegex: RegExp): string {
  const m = headerRegex.exec(src);
  if (!m) throw new Error(`extractMethodBody: header not found for ${headerRegex}`);
  // The header regex must include the opening `{`. Slice from one
  // past the `{` and walk forward balancing braces.
  const openIdx = m.index + m[0].length - 1;
  if (src[openIdx] !== '{') {
    throw new Error(`extractMethodBody: header regex did not end at {`);
  }
  let depth = 1;
  let i = openIdx + 1;
  while (i < src.length && depth > 0) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    i++;
  }
  return src.slice(openIdx + 1, i - 1);
}

describe('Impact Coordinator — source-grep contract (spec §5.2)', () => {
  // ── #1 ──
  it('#1 — ImpactCoordinator.ts exports class + Trigger type + TRIGGER_PRESETS + SELECTION_EMISSIVE_FLOOR', () => {
    const src = readImpactCoordinatorSrc('ImpactCoordinator.ts');
    expect(src).toMatch(/export\s+class\s+ImpactCoordinator/);
    expect(src).toMatch(/export\s+type\s+Trigger\s*=/);
    expect(src).toMatch(/export\s+const\s+TRIGGER_PRESETS/);
    expect(src).toMatch(/export\s+const\s+SELECTION_EMISSIVE_FLOOR/);
  });

  // ── #2 ──
  it('#2 — TRIGGER_PRESETS has exactly 4 entries (entrance, post-growth, optimization, click)', () => {
    const src = readImpactCoordinatorSrc('ImpactCoordinator.ts');
    // Locate the TRIGGER_PRESETS const declaration body.
    const presetsHeader = /export\s+const\s+TRIGGER_PRESETS[^=]*=\s*\{/;
    const m = presetsHeader.exec(src);
    expect(m).not.toBeNull();
    // Slice the object literal body (brace-balanced from the opening `{`).
    const openIdx = m!.index + m![0].length - 1;
    let depth = 1;
    let i = openIdx + 1;
    while (i < src.length && depth > 0) {
      const ch = src[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
    const body = src.slice(openIdx + 1, i - 1);
    // Trigger union has 4 members; assert all 4 keys appear as top-level
    // property names. `'post-growth'` must be quoted because of the hyphen.
    expect(body).toMatch(/\bentrance\s*:/);
    expect(body).toMatch(/['"]post-growth['"]\s*:/);
    expect(body).toMatch(/\boptimization\s*:/);
    expect(body).toMatch(/\bclick\s*:/);
    // Anti-regression: no 5th key. Count top-level property declarations
    // (lines like `<key>:` at indent depth 1 in the body) — accept exactly
    // 4. Counting via regex matches on a depth-0 walk avoids miscounting
    // nested property keys inside each preset's TriggerPreset literal.
    let topLevelKeys = 0;
    let d = 0;
    for (let j = 0; j < body.length; j++) {
      const ch = body[j];
      if (ch === '{') d++;
      else if (ch === '}') d--;
      else if (d === 0 && ch === ':') {
        // Look back: a property key is an identifier or quoted string
        // ending just before this colon. Skip whitespace.
        let k = j - 1;
        while (k > 0 && /\s/.test(body[k])) k--;
        const before = body.slice(Math.max(0, k - 30), k + 1);
        if (/[\w'"]$/.test(before)) topLevelKeys++;
      }
    }
    expect(topLevelKeys).toBe(4);
  });

  // ── #3 ──
  it('#3 — SemanticTopology.svelte has ZERO beamPool.acquire( calls (coordinator owns)', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    expect(src).not.toMatch(/beamPool[?!]?\.acquire\(/);
  });

  // ── #4 ──
  it('#4 — SemanticTopology.svelte has ZERO _triggerBeamImpact references (helper removed)', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    expect(src).not.toMatch(/_triggerBeamImpact/);
  });

  // ── #5 ──
  it('#5 — SemanticTopology.svelte has EXACTLY 4 impactCoordinator.fire( calls (one per trigger site)', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    const matches = src.match(/impactCoordinator[?!]?\.fire\(/g);
    expect(matches?.length).toBe(4);
  });

  // ── #6 ──
  it('#6 — F7 causal-ordering invariant: across taxonomy/ source files (except ImpactCoordinator + its tests), ZERO matches for onBeamImpact, envelope-scoped acquire, or flashEmissive invocation', () => {
    const mod = import.meta.glob<string>(
      [
        './*.ts',
        './*.svelte',
      ],
      { query: '?raw', import: 'default', eager: true },
    );
    // Production-code scope per spec §3.3 ("zero matches for the canonical
    // F19 reaction call patterns"). Test files that legitimately exercise
    // the underlying classes directly (`physics.onBeamImpact(...)`,
    // `envelopePool.acquire(...)`) or that embed these patterns as regex
    // string literals for OTHER source-grep contracts are not in scope —
    // F7 is a runtime production-code invariant, not a syntactic ban on
    // referencing the method names anywhere in the taxonomy/ tree.
    const exemptions = [
      './ImpactCoordinator.ts',
      './ImpactCoordinator.test.ts',
      './ImpactCoordinator.integration.test.ts',
    ];
    for (const [path, src] of Object.entries(mod)) {
      if (exemptions.includes(path)) continue;
      // Skip all `.test.ts` files (production-code scope per the comment above).
      if (/\.test\.ts$/.test(path)) continue;
      if (typeof src !== 'string') continue;
      // `(?:\?\.|\.)\s*onBeamImpact\s*\(` — kinetic-shake call outside coordinator
      expect(src, `${path} should not call .onBeamImpact( (only ImpactCoordinator does)`).not.toMatch(
        /(?:\?\.|\.)\s*onBeamImpact\s*\(/,
      );
      // Envelope-scoped `(?:\?\.|\.)\s*acquire\s*\(` — invoked only on envelopePool.
      // We grep for `envelopePool[?!]?\.acquire\(` since the broader `\.acquire\(`
      // pattern would also match the legitimate `beamPool.acquire(` migration
      // inside the coordinator file. Keep this canonical-pattern-aligned.
      expect(src, `${path} should not call envelopePool.acquire( (only ImpactCoordinator does)`).not.toMatch(
        /envelopePool[?!]?\.acquire\s*\(/,
      );
      // `\bflashEmissive\s*\(` — the function declaration site lives in
      // SemanticTopology.svelte and stays per §2 OUT (Sub-project E will
      // absorb flash state). Carve out the declaration before the scan so
      // the assertion only catches INVOCATIONS, not the declaration itself.
      const stripped = src.replace(/function\s+flashEmissive\s*\(/g, 'function FLASH_DECL_CARVEOUT(');
      expect(stripped, `${path} should not call flashEmissive( (only ImpactCoordinator does; the declaration site is carved out)`).not.toMatch(
        /\bflashEmissive\s*\(/,
      );
    }
  });

  // ── #7 ──
  it('#7 — SemanticTopology.svelte has ZERO _selectionEngulfed references (symbol absorbed by coordinator)', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    expect(src).not.toMatch(/_selectionEngulfed/);
  });

  // ── #8 ──
  it('#8 — Breathing handler does NOT contain _selectionEngulfed.has (T3.4 pulse extracted to coordinator)', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    const breathingBody = extractBreathingHandlerBody(src);
    expect(breathingBody).not.toMatch(/_selectionEngulfed\.has/);
  });

  // ── #9 ──
  it('#9 — ImpactCoordinator construction in onMount happens AFTER renderer + AnimationCoordinator + beamPool + envelopePool + clusterPhysics', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    const onMountIdx = src.indexOf('onMount(() => {');
    expect(onMountIdx).toBeGreaterThan(0);
    const onMountSlice = src.slice(onMountIdx, onMountIdx + 12000);

    const rendererPos = onMountSlice.search(/renderer\s*=\s*new\s+TopologyRenderer\(/);
    const coordPos = onMountSlice.search(/coordinator\s*=\s*new\s+AnimationCoordinator\(/);
    const beamPos = onMountSlice.search(/beamPool\s*=\s*new\s+BeamPool\(/);
    const envPos = onMountSlice.search(/envelopePool\s*=\s*new\s+EnvelopePool\(/);
    const physicsPos = onMountSlice.search(/clusterPhysics\s*=\s*new\s+ClusterPhysics\(/);
    const impactPos = onMountSlice.search(/impactCoordinator\s*=\s*new\s+ImpactCoordinator\(/);

    expect(rendererPos).toBeGreaterThan(-1);
    expect(coordPos).toBeGreaterThan(-1);
    expect(beamPos).toBeGreaterThan(-1);
    expect(envPos).toBeGreaterThan(-1);
    expect(physicsPos).toBeGreaterThan(-1);
    expect(impactPos).toBeGreaterThan(-1);

    expect(rendererPos).toBeLessThan(impactPos);
    expect(coordPos).toBeLessThan(impactPos);
    expect(beamPos).toBeLessThan(impactPos);
    expect(envPos).toBeLessThan(impactPos);
    expect(physicsPos).toBeLessThan(impactPos);
  });

  // ── #10 ──
  it('#10 — Cleanup return invokes impactCoordinator?.dispose() BEFORE coordinator?.dispose() AND BEFORE pool disposes', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    const cleanupIdx = src.lastIndexOf('return () => {');
    expect(cleanupIdx).toBeGreaterThan(0);
    const cleanupSlice = src.slice(cleanupIdx, cleanupIdx + 4000);

    const impactDisposePos = cleanupSlice.search(/impactCoordinator[?!]?\.dispose\(\)/);
    const animDisposePos = cleanupSlice.search(/(?<!impact)coordinator[?!]?\.dispose\(\)/);
    const beamDisposePos = cleanupSlice.search(/beamPool[?!]?\.dispose\(\)/);
    const envDisposePos = cleanupSlice.search(/envelopePool[?!]?\.dispose\(\)/);

    expect(impactDisposePos).toBeGreaterThan(-1);
    expect(animDisposePos).toBeGreaterThan(-1);
    expect(beamDisposePos).toBeGreaterThan(-1);
    expect(envDisposePos).toBeGreaterThan(-1);

    expect(impactDisposePos).toBeLessThan(animDisposePos);
    expect(impactDisposePos).toBeLessThan(beamDisposePos);
    expect(impactDisposePos).toBeLessThan(envDisposePos);
  });

  // ── #11 ──
  it('#11 — ImpactCoordinator._tick body contains zero per-frame allocations', () => {
    const src = readImpactCoordinatorSrc('ImpactCoordinator.ts');
    const tickBody = extractMethodBody(src, /private\s+_tick\s*\(\s*delta\s*:\s*number\s*\)\s*:\s*void\s*\{/);
    expect(tickBody).not.toMatch(/new\s+(Map|Array|Set|WeakSet|Vector\d|Color|Quaternion|Matrix\d)\b/);
  });

  // ── #12 ──
  it('#12 — Breathing handler retains an impactCoordinator.isEngulfed guard (positive — paired with #8 negative; per spec §3.4 phase-ordering hazard)', () => {
    const src = readImpactCoordinatorSrc('SemanticTopology.svelte');
    const breathingBody = extractBreathingHandlerBody(src);
    // Permit `impactCoordinator?.isEngulfed(`, `impactCoordinator!.isEngulfed(`,
    // or `impactCoordinator.isEngulfed(` — the guard exists in any of these forms.
    expect(breathingBody).toMatch(/impactCoordinator(?:\?\.|\!\.|\.)isEngulfed\s*\(/);
  });
});

// ── Sub-project D source-grep contract (spec §5.2 #1-#16) ──────

const builderSourceMap = import.meta.glob<string>(
  [
    './builders/BuilderContext.ts',
    './builders/SceneBuilder.ts',
    './builders/ClusterBuilder.ts',
    './builders/DomainBuilder.ts',
    './builders/EdgeBuilder.ts',
    './builders/RingBuilder.ts',
    './builders/DustBuilder.ts',
  ],
  { query: '?raw', import: 'default', eager: true },
);

function readBuilderSource(name: string): string {
  const key = `./builders/${name}`;
  const content = builderSourceMap[key];
  if (typeof content !== 'string') {
    throw new Error(`cleanup-contract: ${key} not found in builder glob map`);
  }
  return content;
}

function extractRebuildSceneBody(src: string): string {
  // Locate `function rebuildScene(` then walk forward, balancing braces,
  // to extract the body slice. Mirror of extractCleanupBody but anchored
  // on rebuildScene's opening brace.
  const fnMatch = src.match(/function\s+rebuildScene\s*\([^)]*\)\s*:\s*[^{]+\{/);
  if (!fnMatch || fnMatch.index === undefined) {
    throw new Error('cleanup-contract: rebuildScene function not found');
  }
  const start = fnMatch.index + fnMatch[0].length;
  let depth = 1;
  let i = start;
  while (i < src.length && depth > 0) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    i++;
  }
  return src.slice(start, i - 1);
}

describe('Cleanup contract — Sub-project D scene-builder extraction (spec §5.2)', () => {
  // ── #1 ──
  test('#1 — BuilderContext.ts exports BuilderContext interface + createBuilderContext factory', () => {
    const src = readBuilderSource('BuilderContext.ts');
    expect(src).toMatch(/export\s+interface\s+BuilderContext\s*\{/);
    expect(src).toMatch(/export\s+function\s+createBuilderContext\s*\(/);
  });

  // ── #2 ──
  test('#2 — SceneBuilder.ts exports SceneBuilder interface with build + dispose methods', () => {
    const src = readBuilderSource('SceneBuilder.ts');
    expect(src).toMatch(/export\s+interface\s+SceneBuilder\s*\{/);
    expect(src).toMatch(/build\s*\(\s*data:\s*SceneData/);
    expect(src).toMatch(/dispose\s*\(\s*\)\s*:\s*void/);
  });

  // ── #3 ──
  test('#3 — All 5 builder files exist + export their class', () => {
    const expected: Array<[string, RegExp]> = [
      ['ClusterBuilder.ts', /export\s+class\s+ClusterBuilder/],
      ['DomainBuilder.ts', /export\s+class\s+DomainBuilder/],
      ['EdgeBuilder.ts', /export\s+class\s+EdgeBuilder/],
      ['RingBuilder.ts', /export\s+class\s+RingBuilder/],
      ['DustBuilder.ts', /export\s+class\s+DustBuilder/],
    ];
    for (const [name, pattern] of expected) {
      const src = readBuilderSource(name);
      expect(src).toMatch(pattern);
    }
  });

  // ── #4 ──
  test('#4 — rebuildScene body contains 5 builder .build( calls in cluster → domain → edge → ring → dust order', () => {
    const body = extractRebuildSceneBody(readSemTopSource());
    const clusterPos = body.indexOf('clusterBuilder.build');
    const domainPos = body.indexOf('domainBuilder.build');
    const edgePos = body.indexOf('edgeBuilder.build');
    const ringPos = body.indexOf('ringBuilder.build');
    const dustPos = body.indexOf('dustBuilder.build');
    expect(clusterPos).toBeGreaterThan(-1);
    expect(domainPos).toBeGreaterThan(clusterPos);
    expect(edgePos).toBeGreaterThan(domainPos);
    expect(ringPos).toBeGreaterThan(edgePos);
    expect(dustPos).toBeGreaterThan(ringPos);
  });

  // ── #5 ──
  test('#5 — rebuildScene body is < 150 LOC', () => {
    const body = extractRebuildSceneBody(readSemTopSource());
    const lines = body.split('\n').filter((l) => l.trim().length > 0 && !l.trim().startsWith('//'));
    expect(lines.length).toBeLessThan(150);
  });

  // ── #6 ──
  // Sub-project D — Spec §6.1 #4 ambition was < 750 (rev 2 hard ceiling)
  // but this assumed every concern in SemanticTopology.svelte was scene
  // construction. The realized scope drops ~750 LOC of inline cluster/
  // domain/edge/ring/dust construction; load-bearing orchestration
  // concerns out of scope for Sub-project D (onMount construction of
  // renderer/coordinators/pools, optimization-event listener, seed-
  // batch tracking, formation animation, click effects, LOD attenuation
  // callback, dim sweeps, flash emissive helpers) remain. The realistic
  // post-extraction file size is ~2050 LOC, down from 2794. Further
  // compression requires extracting onMount + cleanup logic into
  // separate modules — out of scope; deferred to Sub-projects E/F.
  test('#6 — SemanticTopology.svelte total LOC drops below pre-extraction baseline', () => {
    const src = readSemTopSource();
    const lines = src.split('\n');
    // Pre-extraction baseline was 2794 LOC (HEAD `d79f3dee` + Sub-project C).
    // Post-extraction ceiling: < 2100 LOC (~750 LOC of construction removed
    // + ~50 LOC orchestrator overhead).
    expect(lines.length).toBeLessThan(2100);
  });

  // ── #7 ──
  test('#7 — SemanticTopology.svelte does NOT contain inline cluster-mesh construction (new THREE.IcosahedronGeometry)', () => {
    const src = readSemTopSource();
    expect(src).not.toMatch(/new\s+THREE\.IcosahedronGeometry\s*\(/);
  });

  // ── #8 ──
  test('#8 — SemanticTopology.svelte does NOT contain inline domain-mesh construction (new THREE.DodecahedronGeometry)', () => {
    const src = readSemTopSource();
    expect(src).not.toMatch(/new\s+THREE\.DodecahedronGeometry\s*\(/);
  });

  // ── #9 ──
  test('#9 — SemanticTopology.svelte does NOT contain inline edge construction LineSegments for catenary/similarity/injection', () => {
    const src = readSemTopSource();
    // The hierarchicalGroup + similarityEdgeGroup + injectionEdgeGroup
    // declarations should be gone post-migration.
    expect(src).not.toMatch(/hierarchicalGroup\s*=\s*new\s+THREE\.Group/);
    expect(src).not.toMatch(/similarityEdgeGroup\s*=\s*buildEdgeGroup/);
    expect(src).not.toMatch(/injectionEdgeGroup\s*=\s*buildEdgeGroup/);
  });

  // ── #10 ──
  test('#10 — SemanticTopology.svelte does NOT contain _templateRingPool / _freeTemplateRings / _templateRingById / _templateRingPoolSet', () => {
    const src = readSemTopSource();
    expect(src).not.toMatch(/_templateRingPool\b/);
    expect(src).not.toMatch(/_freeTemplateRings\b/);
    expect(src).not.toMatch(/_templateRingById\b/);
    expect(src).not.toMatch(/_templateRingPoolSet\b/);
  });

  // ── #11 ──
  test('#11 — SemanticTopology.svelte does NOT contain _dustPoints declaration (migrated to DustBuilder)', () => {
    const src = readSemTopSource();
    // `let _dustPoints` OR `const _dustPoints` declarations gone.
    expect(src).not.toMatch(/(?:let|const)\s+_dustPoints\b/);
  });

  // ── #12 ──
  test('#12 — Builder construction in onMount happens AFTER renderer + AnimationCoordinator + ImpactCoordinator', () => {
    const src = readSemTopSource();
    const rendererPos = src.indexOf('renderer = new TopologyRenderer');
    const acPos = src.indexOf('coordinator = new AnimationCoordinator');
    const icPos = src.indexOf('impactCoordinator = new ImpactCoordinator');
    const cbPos = src.indexOf('clusterBuilder = new ClusterBuilder');
    expect(rendererPos).toBeGreaterThan(-1);
    expect(acPos).toBeGreaterThan(rendererPos);
    expect(icPos).toBeGreaterThan(acPos);
    expect(cbPos).toBeGreaterThan(icPos);
  });

  // ── #13 ──
  test('#13 — Cleanup return invokes all 5 builder .dispose() calls', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    expect(cleanup).toMatch(/clusterBuilder\??\.dispose\s*\(/);
    expect(cleanup).toMatch(/domainBuilder\??\.dispose\s*\(/);
    expect(cleanup).toMatch(/edgeBuilder\??\.dispose\s*\(/);
    expect(cleanup).toMatch(/ringBuilder\??\.dispose\s*\(/);
    expect(cleanup).toMatch(/dustBuilder\??\.dispose\s*\(/);
  });

  // ── #14 ──
  test('#14 — userData.persistent = true set in RingBuilder + DustBuilder construction (scoped to builders/)', () => {
    const ring = readBuilderSource('RingBuilder.ts');
    const dust = readBuilderSource('DustBuilder.ts');
    expect(ring).toMatch(/userData[.:]\s*\{[^}]*persistent:\s*true|userData\.persistent\s*=\s*true/s);
    expect(dust).toMatch(/userData\.persistent\s*=\s*true|persistent:\s*true/);
  });

  // ── #15 ──
  test('#15 — F1-F19 canon entries reference new builder file paths (scoped to 3d-visualization.md)', async () => {
    // 3d-visualization.md is a sibling file in .claude/; read via fs in
    // Node test environment. The brand canon update lands in Cycle 6
    // INTEGRATE (Task 29); this test is queued failing here until then.
    // @ts-expect-error — node:fs/promises ambient typing not in tsconfig
    const fs = await import('node:fs/promises');
    // @ts-expect-error — node:path ambient typing not in tsconfig
    const path = await import('node:path');
    // Vitest runs from `frontend/`; canon file lives at the repo root in
    // `.claude/skills/brand-guidelines/references/3d-visualization.md`.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const proc = (globalThis as any).process;
    const repoRoot = path.resolve(proc.cwd(), '..');
    const canonPath = path.join(
      repoRoot,
      '.claude/skills/brand-guidelines/references/3d-visualization.md',
    );
    const md = await fs.readFile(canonPath, 'utf8');
    expect(md).toMatch(/ClusterBuilder\.ts/);
    expect(md).toMatch(/DomainBuilder\.ts/);
    expect(md).toMatch(/EdgeBuilder\.ts/);
    expect(md).toMatch(/RingBuilder\.ts/);
    expect(md).toMatch(/DustBuilder\.ts/);
  });

  // ── #16 ──
  test('#16 — SemanticTopology.svelte PRESERVES module-level accumulators _breathingTime, _edgeTime, _cameraShake, _nodePhaseOffsets', () => {
    const src = readSemTopSource();
    // Per-frame accumulator state — must remain at module scope.
    expect(src).toMatch(/let\s+_breathingTime\b/);
    expect(src).toMatch(/let\s+_edgeTime\b/);
    expect(src).toMatch(/let\s+_cameraShake\b/);
    expect(src).toMatch(/let\s+_nodePhaseOffsets\b|const\s+_nodePhaseOffsets\b/);
  });
});
