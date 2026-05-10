/**
 * Cleanup contract test — Pattern Graph 3D scope.
 *
 * Spec: docs/superpowers/specs/2026-05-09-pattern-graph-depth-design.md § 4.3
 * Brand: .claude/skills/brand-guidelines/references/3d-visualization.md "Disposal Contract"
 *
 * The cut-from-main `SemanticTopology.svelte` already has a comprehensive
 * cleanup return — every animation canceller is invoked, every pool is
 * drained, and the renderer + interaction + labels are disposed. This file
 * exists as the **regression gate** that locks those properties into the
 * spec, so a future change cannot quietly drop a canceller or leak a
 * disposable.
 *
 * Six property classes asserted via source-grep on the live source:
 *
 *   1. Walked-back animation identifiers absent (`_removeDustAnim`,
 *      `_breathingAnim`, `_removeEdgeAnim` — these were the original
 *      branch's banned-effect features).
 *   2. Walked-back banned-terminology pool identifiers absent
 *      (`__semTopHaloPool`, `_haloPool`, `_haloById`, `_haloGroup`,
 *      `_freeHalos`, `HALO_POOL_*`, `_acquireHalo`, `_releaseHalo`,
 *      `__semTopGlowTexture`).
 *   3. Renamed pool identifiers present (`_templateRingPool`,
 *      `_templateRingById`, `_templateRingGroup`, `_freeTemplateRings`).
 *   4. Every animation canceller in the cleanup return.
 *   5. Every disposable's `.dispose()` (or equivalent) in the cleanup return.
 *   6. Any globalThis assignment is gated by `import.meta.env.MODE === 'test'`.
 *
 * Source-grep over `import.meta.glob ?raw` (same mechanism as
 * brand-compliance.test.ts) — no module load, no mocks, no jsdom WebGL.
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
 * Slice the cleanup return body out of the onMount block. The cleanup is
 * the last `return () => { ... }` inside the onMount call. We anchor on
 * the closing `};\n  });\n</script>` near EOF and walk backward to find
 * the matching `return () => {`.
 *
 * If the slice fails, every cleanup-presence test will fail loudly —
 * that's the desired behavior since structural change to the onMount
 * shape is itself worth flagging.
 */
function extractCleanupBody(src: string): string {
  // Find the onMount cleanup return. The pattern is:
  //   return () => {
  //     ... cleanup statements ...
  //   };
  // followed by   });   // closes onMount
  // followed by   </script>
  const closeMarker = '</script>';
  const closeIdx = src.indexOf(closeMarker);
  if (closeIdx < 0) {
    throw new Error('cleanup-contract: </script> not found');
  }
  // Walk backward to find `return () => {` — the last one before </script>
  // is the onMount cleanup.
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

  // Walk forward, balancing braces, to find the matching close.
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

describe('Cleanup contract — walked-back identifiers absent', () => {
  test('banned animation identifiers do not appear in source', () => {
    const src = readSemTopSource();
    // Per spec § 4.3: these were the original branch's banned-effect
    // features (dust particle system, breathing oscillation on cluster
    // scale, edge animation pulse). All walked back.
    const banned = ['_removeDustAnim', '_breathingAnim', '_removeEdgeAnim'];
    const hits = banned.filter((id) => src.includes(id));
    expect(hits).toEqual([]);
  });

  test('banned-terminology pool identifiers do not appear in source', () => {
    const src = readSemTopSource();
    // Per spec § 3.7: every old `_halo*` / `__semTopHaloPool` / `HALO_*` /
    // `_acquireHalo` / `_releaseHalo` was renamed to `_templateRing*` in
    // cycle 2. The `__semTopGlowTexture` global was removed entirely.
    const banned = [
      '__semTopHaloPool',
      '_haloPool',
      '_haloById',
      '_haloGroup',
      '_freeHalos',
      'HALO_POOL_',
      '_acquireHalo',
      '_releaseHalo',
      '__semTopGlowTexture',
    ];
    const hits = banned.filter((id) => src.includes(id));
    expect(hits).toEqual([]);
  });
});

describe('Cleanup contract — renamed identifiers present', () => {
  test('renamed template-ring pool identifiers all appear in source', () => {
    const src = readSemTopSource();
    const required = [
      '_templateRingPool',
      '_templateRingById',
      '_templateRingGroup',
      '_freeTemplateRings',
    ];
    const missing = required.filter((id) => !src.includes(id));
    expect(missing).toEqual([]);
  });
});

describe('Cleanup contract — animation cancellers wired in cleanup return', () => {
  test('all five surviving cancellers invoked in cleanup body', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    // Per spec § 4.3: existing cleanup wiring (cut-from-main lines 1647-1653).
    //   - removeBeamUpdate()           : beam pool per-frame update canceller
    //   - _removeRingLodUpdate()        : LOD opacity sweep canceller
    //   - _removeFormationAnim?.()      : formation lerp canceller (nullable)
    //   - _removeDomainRotation?.()     : domain rotation canceller (nullable)
    //   - _removeReadinessBillboard?.() : ring billboard canceller (nullable)
    const required = [
      'removeBeamUpdate()',
      '_removeRingLodUpdate()',
      '_removeFormationAnim?.()',
      '_removeDomainRotation?.()',
      '_removeReadinessBillboard?.()',
    ];
    const missing = required.filter((sig) => !cleanup.includes(sig));
    expect(missing).toEqual([]);
  });
});

describe('Cleanup contract — disposables drained in cleanup return', () => {
  test('all top-level disposables released in cleanup body', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    // Per spec § 4.3 + cut-from-main lines 1643-1687:
    //   - beamPool?.dispose()          : Three.js Group + per-beam mesh dispose
    //   - clusterPhysics?.clear()      : map clear (no GPU resources)
    //   - disposeRingEntry(entry)      : per-readiness-ring dispose
    //   - _readinessRings.clear()      : map clear post-dispose
    //   - ro.disconnect()              : ResizeObserver
    //   - interaction?.dispose()       : raycaster wiring
    //   - labels?.dispose()            : sprite group + textures
    //   - renderer?.dispose()          : WebGL context + scene traversal dispose
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

  test('template-ring pool drained on unmount', () => {
    const cleanup = extractCleanupBody(readSemTopSource());
    // The pool itself is retained as a high-water mark across remounts
    // (intentional — avoids re-allocation on quick remount), but the
    // active-by-id map and free list MUST reset, and pool meshes MUST
    // be hidden + returned to the free list.
    expect(cleanup).toContain('_templateRingById.clear()');
    expect(cleanup).toContain('_freeTemplateRings.length = 0');
    // The "return to free list" loop reads `_templateRingPool` and pushes
    // each into `_freeTemplateRings` — match by both signals.
    expect(cleanup).toMatch(/_templateRingPool/);
    expect(cleanup).toMatch(/_freeTemplateRings\.push/);
  });
});

describe('Cleanup contract — globalThis pollution gated to test mode', () => {
  test('every globalThis assignment is gated by import.meta.env.MODE === "test"', () => {
    const src = readSemTopSource();
    // For each `globalThis` assignment in source, the same line OR the
    // immediately preceding `if (...)` guard must mention `import.meta.env`
    // and `'test'`. We tokenize by line and check the preceding 5 lines.
    const lines = src.split('\n');
    const violations: string[] = [];
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      // Match `(globalThis as any).X = …` or `globalThis.X = …` assignments.
      // Read-only access (e.g. inside type assertions for tests) is fine.
      if (!/\(?\s*globalThis\s*(as\s+\w+)?\s*\)?\s*\.\s*\w+\s*=/.test(line)) continue;

      // Look backward up to 5 lines for the test-mode guard.
      const window = lines.slice(Math.max(0, i - 5), i + 1).join('\n');
      const isGated =
        /import\.meta\.env\.MODE\s*===\s*['"]test['"]/.test(window) ||
        /import\.meta\.env\.MODE\s*!==\s*['"]production['"]/.test(window);
      if (!isGated) {
        violations.push(`${i + 1}: ${line.trim()}`);
      }
    }
    expect(violations).toEqual([]);
  });
});
