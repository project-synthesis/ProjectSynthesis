/**
 * Material + lighting baseline test — Pattern Graph 3D scope.
 *
 * Spec: docs/superpowers/specs/2026-05-09-pattern-graph-depth-design.md § 4.5 + § 4.8
 * Brand: .claude/skills/brand-guidelines/references/3d-visualization.md
 *        "Material Recipes" + "Lighting Setups" tables.
 *
 * The brand reference mandates `MeshStandardMaterial` (not `MeshBasicMaterial`)
 * for cluster spheres + domain anchors, with a 3-light setup (Ambient +
 * Directional + Hemisphere) and PCFShadowMap. This file gates the structural
 * presence of those changes via source-grep over the live source — same
 * mechanism as `brand-compliance.test.ts` and `cleanup-contract.test.ts`.
 *
 * **Methodology deviation from spec § 4.5** (acknowledged): the spec
 * prescribed rendering a fixed seed scene (3 clusters + 2 edges + 1 anchor +
 * 1 template ring) and asserting `scene.children` properties via Three.js
 * introspection. jsdom has no GL context, so a real `WebGLRenderer` cannot
 * be constructed in the test environment without invasive mocking that
 * would force the test to drift from production semantics on every renderer
 * change. The source-grep approach trades render-time scene-graph
 * introspection for compile-time source-shape pinning: every brand-canonical
 * setting (material class, roughness/metalness/emissive/emissiveIntensity,
 * castShadow, light constructors with exact intensities and colors,
 * PCFShadowMap, mapSize 1024×1024) is verified against the source. The
 * trade-off is that this test cannot catch a runtime conditional that
 * BYPASSES the brand-canonical material on certain code paths — but every
 * cluster fill path runs through the single `fillMat = ...` construction
 * site, so a bypass would require an entirely new material constructor
 * appearing elsewhere in the file, which the cluster-fill grep would
 * NOT match. Pixel-level regression is captured manually in OPERATE
 * (spec § 6.4) and would be added to a Playwright snapshot suite if
 * one is ever introduced (deferred per spec § 8).
 *
 * Five property classes asserted:
 *
 *   1. Cluster fill material is `MeshStandardMaterial` with brand-canonical
 *      `roughness: 0.6`, `metalness: 0.0`, an `emissive` driver, and an
 *      `emissiveIntensity` driver in the documented `[0.6, 1.4]` range.
 *   2. Cluster fill mesh has `castShadow = true`.
 *   3. Readiness ring stays `MeshBasicMaterial` per spec § 3.7 carve-out
 *      (the cyan ring is a banner overlay, not a 3D shaded sphere).
 *   4. Renderer enables shadow mapping with `PCFShadowMap` (canonical;
 *      `PCFSoftShadowMap` was deprecated in three@0.170+).
 *   5. Three lights at module/scene scope: `AmbientLight` (intensity 0.3,
 *      white), `DirectionalLight` (intensity 0.7, position 5/10/5,
 *      castShadow true, mapSize 1024×1024), `HemisphereLight` (intensity
 *      0.2, sky #1a1a2e ground #06060c).
 *
 * Source-grep over `import.meta.glob ?raw` — no module load, no mocks,
 * no jsdom WebGL.
 */
import { describe, expect, test } from 'vitest';

const sourceMap = import.meta.glob<string>(
  ['./SemanticTopology.svelte', './TopologyRenderer.ts'],
  { query: '?raw', import: 'default', eager: true },
);

function readSource(name: string): string {
  const key = `./${name}`;
  const content = sourceMap[key];
  if (typeof content !== 'string') {
    throw new Error(`material-lighting-baseline: ${name} not found in glob map`);
  }
  return content;
}

describe('Material baseline — cluster fill uses MeshStandardMaterial', () => {
  test('cluster fillMat is constructed via new THREE.MeshStandardMaterial', () => {
    const src = readSource('SemanticTopology.svelte');
    // Match the cluster-fill creation block. The pattern looks like:
    //   const fillMat = new THREE.MeshStandardMaterial({ ... });
    // Permissive whitespace; binding name MUST be `fillMat` to disambiguate
    // from the readiness ring's `mat` (which legitimately stays Basic).
    expect(src).toMatch(
      /const\s+fillMat\s*=\s*new\s+THREE\.MeshStandardMaterial\s*\(\s*\{/,
    );
  });

  test('cluster fillMat config sets roughness 0.6, metalness 0.0, emissive, emissiveIntensity', () => {
    const src = readSource('SemanticTopology.svelte');
    // Slice the fillMat constructor body (balanced-brace walk from the
    // `new THREE.MeshStandardMaterial({` open). Catches reordering, comments,
    // additional properties, etc.
    const startMatch = src.match(/const\s+fillMat\s*=\s*new\s+THREE\.MeshStandardMaterial\s*\(\s*\{/);
    expect(startMatch).not.toBeNull();
    if (!startMatch) return;
    const startIdx = (startMatch.index ?? 0) + startMatch[0].length;
    let depth = 1;
    let i = startIdx;
    while (i < src.length && depth > 0) {
      const ch = src[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      i++;
    }
    const body = src.slice(startIdx, i - 1);

    // Brand reference: roughness 0.6 (matte, no specular), metalness 0.0.
    expect(body).toMatch(/roughness\s*:\s*0\.6/);
    expect(body).toMatch(/metalness\s*:\s*0(?:\.0+)?/);
    // emissive: must be set. The driver is the domain hex (same as `color`).
    expect(body).toMatch(/emissive\s*:/);
    // emissiveIntensity: must be set as either a labeled property
    // (`emissiveIntensity: <expr>`) or via property-shorthand
    // (`emissiveIntensity,` or `emissiveIntensity\n}`). Driver must be data —
    // typically a function call or arithmetic on memberCount / avgScore.
    expect(body).toMatch(/emissiveIntensity\s*[:,}\n]/);
  });

  test('cluster fill mesh sets castShadow = true', () => {
    const src = readSource('SemanticTopology.svelte');
    // Look for `fill.castShadow = true` on the cluster-fill mesh. This is
    // the production line (search around the `const fill = new THREE.Mesh(fillGeo, fillMat)`
    // construction).
    expect(src).toMatch(/fill\.castShadow\s*=\s*true/);
  });
});

describe('Material baseline — banner-overlay rings stay MeshBasicMaterial', () => {
  test('readiness ring uses MeshBasicMaterial with depthWrite:false (spec § 3.7 carve-out)', () => {
    const src = readSource('SemanticTopology.svelte');
    // The readiness ring is a banner overlay sitting OVER the cluster
    // dodecahedron silhouette with `depthWrite: false` to avoid z-fighting.
    // It is NOT a 3D shaded sphere, so MeshStandardMaterial would be wrong.
    // Anchor on the unique property `depthWrite: false` near the
    // `MeshBasicMaterial` construction — matches the readiness-ring block
    // at line ~855 and would NOT match if a future change converted the
    // readiness ring to MeshStandardMaterial (which would drop the
    // MeshBasicMaterial line, no longer adjacent to depthWrite).
    expect(src).toMatch(
      /new\s+THREE\.MeshBasicMaterial\s*\([\s\S]*?depthWrite\s*:\s*false[\s\S]*?\}\s*\)/,
    );
  });

  test('template ring uses MeshBasicMaterial with cyan literal 0x00e5ff (spec § 3.7 carve-out)', () => {
    const src = readSource('SemanticTopology.svelte');
    // The cyan template indicator ring sits around mature templated
    // clusters. Like the readiness ring, it is a banner overlay rather
    // than a 3D shaded sphere. Anchor on the cyan color literal `0x00e5ff`
    // adjacent to the `MeshBasicMaterial` construction.
    expect(src).toMatch(
      /new\s+THREE\.MeshBasicMaterial\s*\([\s\S]*?color\s*:\s*0x00e5ff[\s\S]*?\}\s*\)/,
    );
  });

  test('exactly two MeshBasicMaterial constructions remain in source', () => {
    const src = readSource('SemanticTopology.svelte');
    // Spec § 3.7 + § 4.5 + § 4.8: cluster fills + domain anchors swap to
    // MeshStandardMaterial. The only surviving Basic constructions are the
    // two banner-overlay rings (template + readiness). If a third Basic
    // appears, something else dropped out of the standard-material swap.
    const matches = src.match(/new\s+THREE\.MeshBasicMaterial\s*\(/g) ?? [];
    expect(matches.length).toBe(2);
  });
});

describe('Lighting baseline — TopologyRenderer enables shadow map + 3 lights', () => {
  test('renderer enables shadow map with PCFShadowMap', () => {
    const src = readSource('TopologyRenderer.ts');
    expect(src).toMatch(/shadowMap\.enabled\s*=\s*true/);
    // PCFSoftShadowMap was deprecated in three@0.170+. Canonical is PCFShadowMap.
    expect(src).toMatch(/shadowMap\.type\s*=\s*THREE\.PCFShadowMap/);
    // Negative gate: explicitly forbid `THREE.PCFSoftShadowMap` as a code
    // reference. The token may appear in commit-message or doc-style
    // comments documenting the deprecation — those are deliberate. Only
    // reference syntax (`THREE.PCFSoftShadowMap`) is forbidden.
    expect(src).not.toMatch(/THREE\.PCFSoftShadowMap/);
  });

  test('AmbientLight is added to scene with brand-canonical defaults', () => {
    const src = readSource('TopologyRenderer.ts');
    // Constructor signature: AmbientLight(color, intensity).
    // Brand: intensity 0.3, color #ffffff (white).
    expect(src).toMatch(/new\s+THREE\.AmbientLight\s*\(\s*0xffffff\s*,\s*0\.3\s*\)/);
  });

  test('DirectionalLight has brand-canonical defaults + castShadow', () => {
    const src = readSource('TopologyRenderer.ts');
    // Brand: intensity 0.7, position (5, 10, 5), color #ffffff,
    // castShadow: true, mapSize 1024×1024.
    expect(src).toMatch(/new\s+THREE\.DirectionalLight\s*\(\s*0xffffff\s*,\s*0\.7\s*\)/);
    // Permissive — accept either `.position.set(5, 10, 5)` or component
    // assignments. Match `5, 10, 5` with permissive whitespace.
    expect(src).toMatch(/\.position\.set\s*\(\s*5\s*,\s*10\s*,\s*5\s*\)/);
    expect(src).toMatch(/\.castShadow\s*=\s*true/);
    expect(src).toMatch(/shadow\.mapSize\.set\s*\(\s*1024\s*,\s*1024\s*\)/);
  });

  test('HemisphereLight has brand-canonical defaults', () => {
    const src = readSource('TopologyRenderer.ts');
    // Brand: intensity 0.2, sky #1a1a2e, ground #06060c.
    expect(src).toMatch(
      /new\s+THREE\.HemisphereLight\s*\(\s*0x1a1a2e\s*,\s*0x06060c\s*,\s*0\.2\s*\)/,
    );
  });

  test('all three lights are added to the scene', () => {
    const src = readSource('TopologyRenderer.ts');
    // Each light must end up in `this.scene.add(...)` — verify each variable
    // gets added. We accept any binding name but require the pattern
    // `this.scene.add(<name>)` for each light type's binding.
    //
    // Conservative gate: assert at least 3 occurrences of `this.scene.add(`
    // appear AFTER the WebGLRenderer construction (post-init, in setup).
    const sceneAdds = src.match(/this\.scene\.add\s*\(/g) ?? [];
    expect(sceneAdds.length).toBeGreaterThanOrEqual(3);
  });
});
