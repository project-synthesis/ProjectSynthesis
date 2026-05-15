/**
 * Visual V3 — comprehensive test suite for Pattern Graph T1/T2 features.
 *
 * Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md`
 *
 * Tests cover:
 *   T1.1 — Desynchronized Organic Breathing (F8)
 *   T1.2 — Coherence-Driven Color Temperature (F1)
 *   T1.3 — Domain Edge Reactivity (F5)
 *   T1.4 — SMAA Anti-Aliasing (F13)
 *   T2.1 — Score-Driven Emissive Modulation (F1)
 *   T2.2 — Template Ring Entry/Exit Transitions (F3)
 *   T2.3 — Edge Weight Visual Encoding (F5)
 *   T2.4 — Hover Proximity Field (F8)
 *
 * Methodology: source-grep over `import.meta.glob ?raw` for structural
 * presence, plus direct unit tests for EdgeShader's aAlpha attribute.
 */
import { describe, expect, test } from 'vitest';
import { EDGE_DEPTH_VERTEX, EDGE_DEPTH_FRAGMENT } from './EdgeShader';

const sourceMap = import.meta.glob<string>(
  ['./SemanticTopology.svelte', './TopologyRenderer.ts', './EdgeShader.ts', './DomainEdgeShader.ts'],
  { query: '?raw', import: 'default', eager: true },
);

function read(name: string): string {
  const key = `./${name}`;
  const content = sourceMap[key];
  if (typeof content !== 'string') throw new Error(`visual-v3: ${name} not found`);
  return content;
}

// ── T1.1 — Desynchronized Organic Breathing ─────────────────────────

describe('T1.1 — Desynchronized Organic Breathing (F8)', () => {
  test('_nodePhaseOffsets Map is declared', () => {
    expect(read('SemanticTopology.svelte')).toMatch(/_nodePhaseOffsets.*Map<string,\s*number>/);
  });

  test('phase offsets populated via char-code hash in rebuildScene', () => {
    const src = read('SemanticTopology.svelte');
    // Hash loop: sum of charCodes
    expect(src).toMatch(/node\.id\.charCodeAt/);
    // Map to [0, 2π]
    expect(src).toMatch(/_nodePhaseOffsets\.set\(/);
    expect(src).toMatch(/Math\.PI\s*\*\s*2/);
  });

  test('breathing callback reads per-node phase offset', () => {
    const src = read('SemanticTopology.svelte');
    // Must read phase from the map, not use a global scaleBase
    expect(src).toMatch(/_nodePhaseOffsets\.get\(nodeId\)/);
    // Phase is added to _breathingTime
    expect(src).toMatch(/\(_breathingTime\s*\+\s*phase\)/);
  });

  test('scaleBase is computed INSIDE the per-node loop (not before it)', () => {
    const src = read('SemanticTopology.svelte');
    // Negative gate: the old pattern had `const scaleBase = Math.sin(_breathingTime * 1.5)`
    // BEFORE the `for (const [nodeId, mesh] of nodeMeshes)` loop.
    // The new pattern computes it inside the loop with per-node phase.
    // Verify the new pattern exists
    expect(src).toMatch(/const\s+phase\s*=\s*_nodePhaseOffsets\.get\(nodeId\)/);
    expect(src).toMatch(/const\s+scaleBase\s*=\s*Math\.sin\(\(_breathingTime\s*\+\s*phase\)/);
  });
});

// ── T1.2 — Coherence-Driven Color Temperature ──────────────────────

describe('T1.2 — Coherence-Driven Color Temperature (F1)', () => {
  test('coherenceBoost computed from node.coherence', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/coherenceBoost\s*=.*node\.coherence/);
  });

  test('emissive color lerps toward white for clusters', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/emissiveColor\.lerp\(\s*new\s+THREE\.Color\(0xffffff\)/);
  });

  test('emissiveIntensity compounds coherence boost multiplicatively', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/\(1\.0\s*\+\s*coherenceBoost\)/);
  });

  test('domain anchors do not participate (coherenceBoost = 0)', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/coherenceBoost\s*=\s*isStructural\s*\?\s*0/);
  });
});

// ── T1.3 — Domain Edge Reactivity ───────────────────────────────────

describe('T1.3 — Domain Edge Reactivity (F5)', () => {
  test('DomainEdgeShader is imported in SemanticTopology', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/import\s*\{[^}]*DOMAIN_EDGE_VERTEX[^}]*\}\s*from\s*['"]\.\/DomainEdgeShader['"]/);
  });

  test('domain edges use ShaderMaterial (not LineBasicMaterial)', () => {
    const src = read('SemanticTopology.svelte');
    // The domain edge block must use createDomainEdgeUniforms + ShaderMaterial
    expect(src).toMatch(/createDomainEdgeUniforms/);
    expect(src).toMatch(/vertexShader:\s*DOMAIN_EDGE_VERTEX/);
    expect(src).toMatch(/fragmentShader:\s*DOMAIN_EDGE_FRAGMENT/);
  });

  test('domain edge uniforms registered in _edgeUniforms for shared time pulse', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/_edgeUniforms\.push\(domainEdgeUniforms\)/);
  });

  test('dim-sweep handles ShaderMaterial for both cluster wire and domain edges', () => {
    const src = read('SemanticTopology.svelte');
    // The comment should reference both types
    expect(src).toMatch(/cluster wireframe ripple \+ domain edge heartbeat/);
  });
});

// ── T1.4 — SMAA Anti-Aliasing ───────────────────────────────────────

describe('T1.4 — SMAA Anti-Aliasing (F13)', () => {
  test('SMAAPass is imported in TopologyRenderer', () => {
    const src = read('TopologyRenderer.ts');
    expect(src).toMatch(/import\s*\{\s*SMAAPass\s*\}\s*from\s*['"]three\/addons\/postprocessing\/SMAAPass\.js['"]/);
  });

  test('SMAAPass is added to the composer chain', () => {
    const src = read('TopologyRenderer.ts');
    expect(src).toMatch(/new\s+SMAAPass\s*\(/);
    expect(src).toMatch(/composer\.addPass\(\s*new\s+SMAAPass/);
  });

  test('SMAAPass is added AFTER FilmPass (correct ordering)', () => {
    const src = read('TopologyRenderer.ts');
    const filmIdx = src.indexOf('new FilmPass');
    const smaaIdx = src.indexOf('new SMAAPass');
    expect(filmIdx).toBeGreaterThan(-1);
    expect(smaaIdx).toBeGreaterThan(-1);
    expect(smaaIdx).toBeGreaterThan(filmIdx);
  });
});

// ── T2.1 — Score-Driven Emissive Modulation ─────────────────────────

describe('T2.1 — Score-Driven Emissive Modulation (F1)', () => {
  test('scoreModifier computed from node.avgScore', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/scoreModifier.*node\.avgScore/);
  });

  test('scoreModifier range is [0.85, 1.0] (floor prevents dim-out)', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/0\.85\s*\+\s*0\.15/);
  });

  test('emissiveIntensity compounds all three signals multiplicatively', () => {
    const src = read('SemanticTopology.svelte');
    // Must multiply baseEmissiveIntensity * coherenceBoost * scoreModifier
    expect(src).toMatch(
      /emissiveIntensity\s*=\s*baseEmissiveIntensity\s*\*\s*\(1\.0\s*\+\s*coherenceBoost\)\s*\*\s*scoreModifier/,
    );
  });
});

// ── T2.2 — Template Ring Entry/Exit Transitions ─────────────────────

describe('T2.2 — Template Ring Entry/Exit Transitions (F3)', () => {
  test('entry transition: 400ms with cubic ease-out', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/ENTRY_MS\s*=\s*400/);
    // Cubic ease-out pattern: 1 - (1-t)^3
    expect(src).toMatch(/1\s*-\s*\(1\s*-\s*t\)\s*\*\s*\(1\s*-\s*t\)\s*\*\s*\(1\s*-\s*t\)/);
  });

  test('entry starts at opacity 0 and scale 0.5', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/entryMat\.opacity\s*=\s*0/);
    expect(src).toMatch(/mesh\.scale\.setScalar\(0\.5\)/);
  });

  test('exit transition: 300ms before pool return', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/EXIT_MS\s*=\s*300/);
  });

  test('exit resets material state before returning to pool', () => {
    const src = read('SemanticTopology.svelte');
    // After exit animation completes, material is reset for reuse
    expect(src).toMatch(/mat\.opacity\s*=\s*0\.35.*reset for reuse/s);
    expect(src).toMatch(/mesh\.scale\.setScalar\(1\.0\)/);
  });

  test('isNew flag correctly tracks newly acquired rings', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/const\s+isNew\s*=\s*!_templateRingById\.has\(c\.id\)/);
  });
});

// ── T2.3 — Edge Weight Visual Encoding ──────────────────────────────

describe('T2.3 — Edge Weight Visual Encoding (F5)', () => {
  test('EdgeShader vertex shader declares aAlpha attribute', () => {
    expect(EDGE_DEPTH_VERTEX).toMatch(/attribute\s+float\s+aAlpha/);
  });

  test('EdgeShader vertex shader passes aAlpha as vAlpha varying', () => {
    expect(EDGE_DEPTH_VERTEX).toMatch(/varying\s+float\s+vAlpha/);
    expect(EDGE_DEPTH_VERTEX).toMatch(/vAlpha\s*=\s*aAlpha/);
  });

  test('EdgeShader fragment declares vAlpha varying', () => {
    expect(EDGE_DEPTH_FRAGMENT).toMatch(/varying\s+float\s+vAlpha/);
  });

  test('EdgeShader fragment multiplies vAlpha into opacity', () => {
    expect(EDGE_DEPTH_FRAGMENT).toMatch(/uBaseOpacity\s*\*\s*vAlpha/);
  });

  test('HierEdge interface has optional memberCount field', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/interface\s+HierEdge[\s\S]*?memberCount\?\s*:\s*number/);
  });

  test('buildMergedCurveGeometry returns alphas array', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/buildMergedCurveGeometry.*:\s*\{[^}]*alphas:\s*number\[\]/);
  });

  test('per-vertex alpha floor is 0.3 (edges never vanish)', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/0\.3\s*\+\s*0\.7\s*\*/);
  });

  test('aAlpha attribute set on edge geometry', () => {
    const src = read('SemanticTopology.svelte');
    // Both initial build and formation rebuild must set the attribute
    const matches = src.match(/setAttribute\(\s*['"]aAlpha['"]/g) ?? [];
    expect(matches.length).toBeGreaterThanOrEqual(2);
  });

  test('child memberCount carried in edge building loop', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/memberCount:\s*to\.memberCount/);
  });
});

// ── T2.4 — Hover Proximity Field ────────────────────────────────────

describe('T2.4 — Hover Proximity Field (F8)', () => {
  test('PROXIMITY_RADIUS constant is 8.0 scene units', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/PROXIMITY_RADIUS\s*=\s*8\.0/);
  });

  test('proximity field uses quadratic falloff', () => {
    const src = read('SemanticTopology.svelte');
    // t * t pattern for quadratic
    expect(src).toMatch(/proximityFactor\s*=\s*t\s*\*\s*t\s*\*\s*0\.6/);
  });

  test('max proximity amplitude is 60% of full hover', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/\*\s*0\.6.*max 60%/);
  });

  test('distance computed from hovered node position (scalar math, no allocation)', () => {
    const src = read('SemanticTopology.svelte');
    // Must use direct position array access, not new Vector3
    expect(src).toMatch(/node\.position\[0\]\s*-\s*hoveredNode\.position\[0\]/);
    expect(src).toMatch(/node\.position\[1\]\s*-\s*hoveredNode\.position\[1\]/);
    expect(src).toMatch(/node\.position\[2\]\s*-\s*hoveredNode\.position\[2\]/);
  });

  test('hoverAmplification composes proximity with direct hover', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/hoverAmplification\s*=\s*isHovered\s*\?\s*1\.1\s*:/);
    expect(src).toMatch(/targetScaleMultiplier\s*=\s*scaleBase\s*\*\s*hoverAmplification/);
  });
});

// ── Integration: all T1/T2 features coexist without conflicts ───────

describe('Integration — V3 visual features coexistence', () => {
  test('emissive uses coherence-warmed color (not raw node.color)', () => {
    const src = read('SemanticTopology.svelte');
    // The fillMat constructor must use `emissiveColor` (coherence-warmed),
    // not `new THREE.Color(node.color)` for emissive.
    expect(src).toMatch(/emissive:\s*emissiveColor/);
  });

  test('emissiveIntensity is computed from baseEmissiveIntensity (not inline)', () => {
    const src = read('SemanticTopology.svelte');
    // Must use the compound variable, not an inline formula
    expect(src).toMatch(/const\s+emissiveIntensity\s*=\s*baseEmissiveIntensity/);
  });

  test('both domain edge and cluster wire ShaderMaterials handled by dim sweep', () => {
    const src = read('SemanticTopology.svelte');
    // The isShaderMaterial guard must handle uOpacity for both
    expect(src).toMatch(/isShaderMaterial.*uniforms\?\.uOpacity/);
  });

  test('_nodePhaseOffsets populated in same rebuildScene pass as fill material', () => {
    const src = read('SemanticTopology.svelte');
    // Both _nodePhaseOffsets.set and fillMat must appear in the same per-node loop
    const phaseSetIdx = src.indexOf('_nodePhaseOffsets.set(');
    const fillMatIdx = src.indexOf('const fillMat = new THREE.MeshStandardMaterial');
    expect(phaseSetIdx).toBeGreaterThan(-1);
    expect(fillMatIdx).toBeGreaterThan(-1);
    // Phase offset is set near the fill material (same loop body)
    expect(Math.abs(phaseSetIdx - fillMatIdx)).toBeLessThan(2000);
  });
});
