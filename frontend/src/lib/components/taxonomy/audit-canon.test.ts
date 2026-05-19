// frontend/src/lib/components/taxonomy/audit-canon.test.ts
//
// Sub-project F (Audit-as-Code) — final closure of the Pattern Graph
// architecture hardening program.
//
// Every `it()` corresponds to one canon checklist line in
// `.claude/skills/brand-guidelines/references/3d-visualization.md`
// `## Audit Checklist` section. The `_canon-coverage.test.ts` meta-test
// verifies every canon line maps to one test here.
//
// Convention: every `it()` title starts with the canon tag (e.g.,
// `F1:` or `PERF:`) followed by the canon line's first ~8 significant
// words. The meta-test's `extractCanonTag` reducer enforces the mapping.
//
// Test mode split (~80/20 per spec §3.5):
// - Source-grep: read .ts/.svelte via import.meta.glob('?raw'); regex-match patterns.
// - Runtime: instantiate the class, assert behavior. Only where source-grep
//   cannot capture intent (e.g., composer.dispose calls all passes).
//
// Spec: docs/superpowers/specs/2026-05-19-audit-as-code-design.md

import { describe, expect, it } from 'vitest';

// ── Source-grep helpers ──────────────────────────────────────────────

const taxonomySourceMap = import.meta.glob<string>(
  [
    './SemanticTopology.svelte',
    './TopologyRenderer.ts',
    './TopologyInteraction.ts', // beyond spec §3.3 — added because some F-test anchors may live here (handleNodeClick + focusOn caller); harmless if unused
    './BeamPool.ts',
    './EnvelopePool.ts',
    './ClusterPhysics.ts',
    './ImpactCoordinator.ts',
    './SelectionController.ts',
    './FlashController.ts',
    './AnimationCoordinator.ts',
    './DomainEdgeShader.ts',
    './EdgeShader.ts', // declares F5 `uTime` uniform consumed by EdgeBuilder via createEdgeDepthUniforms
    './builders/ClusterBuilder.ts',
    './builders/DomainBuilder.ts',
    './builders/EdgeBuilder.ts',
    './builders/RingBuilder.ts',
    './builders/DustBuilder.ts',
  ],
  { query: '?raw', import: 'default', eager: true },
);

function readSrc(name: string): string {
  const key = `./${name}`;
  const content = taxonomySourceMap[key];
  if (typeof content !== 'string') {
    throw new Error(`audit-canon: ${key} not found in taxonomy glob map`);
  }
  return content;
}

function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

/**
 * Assert a `new <ctorName>({ ...props... })` constructor invocation exists
 * in `src`, with each prop matching the supplied regex.
 *
 * Use `THREE\\.MeshStandardMaterial` (escaped dot, escaped backslash) for
 * namespaced ctors. Props match against the constructor's object-literal
 * argument body — extracted by balanced-brace scan from the literal's
 * opening `{` (so nested `{ ... }` type casts inside prop expressions
 * don't truncate the body prematurely).
 */
function expectMaterialRecipe(
  src: string,
  ctorName: string,
  props: Record<string, RegExp>,
): void {
  const stripped = stripComments(src);
  const ctorOpen = new RegExp(`new\\s+${ctorName}\\s*\\(\\s*\\{`);
  const openMatch = stripped.match(ctorOpen);
  expect(openMatch, `expected ${ctorName} constructor call`).not.toBeNull();
  // Walk forward from the opening `{` and balance braces to find the
  // matching close — the object literal may contain nested braces from
  // TypeScript type casts inside prop expressions.
  const openIdx = openMatch!.index! + openMatch![0].length;
  let depth = 1;
  let i = openIdx;
  while (i < stripped.length && depth > 0) {
    const ch = stripped[i];
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    if (depth === 0) break;
    i++;
  }
  expect(depth, `${ctorName} constructor body has unbalanced braces`).toBe(0);
  const ctorArgs = stripped.slice(openIdx, i);
  for (const [propName, valueRegex] of Object.entries(props)) {
    const propPattern = new RegExp(`${propName}\\s*:\\s*${valueRegex.source}`);
    expect(ctorArgs, `${ctorName} missing ${propName} matching ${valueRegex}`).toMatch(propPattern);
  }
}

/**
 * Assert `const|let|var <identifier> = <valueRegex>` exists in `src`.
 */
function expectConstantValue(src: string, identifier: string, valueRegex: RegExp): void {
  const stripped = stripComments(src);
  const pattern = new RegExp(`(?:const|let|var)\\s+${identifier}\\s*=\\s*${valueRegex.source}`);
  expect(stripped, `expected constant ${identifier} matching ${valueRegex}`).toMatch(pattern);
}

/**
 * Assert `src` contains `pattern` (substring or regex) after stripping
 * comments. Useful for short canonical-string anchors like
 * `castShadow = true`.
 */
function expectInSource(src: string, pattern: RegExp): void {
  expect(stripComments(src)).toMatch(pattern);
}

// ── F1 — Cluster Sphere Fill ─────────────────────────────────────────

describe('F1 — Cluster Sphere Fill', () => {
  it('F1: cluster fill = MeshStandardMaterial with roughness 0 6', () => {
    const src = readSrc('builders/ClusterBuilder.ts');
    expectMaterialRecipe(src, 'THREE\\.MeshStandardMaterial', {
      roughness: /0\.6/,
      metalness: /0/,
    });
  });

  it('F1: cluster mesh castShadow = true AND receiveShadow =', () => {
    const src = readSrc('builders/ClusterBuilder.ts');
    expectInSource(src, /castShadow\s*=\s*true/);
    expectInSource(src, /receiveShadow\s*=\s*true/);
  });

  it('F1: coherence-driven color temperature — cluster emissive lerps toward', () => {
    const src = readSrc('builders/ClusterBuilder.ts');
    // Canon line says `coherence * 0.03`; real source implements this via
    // two-step multiplication: `coherenceBoost = 0.1 * node.coherence` then
    // `color.lerp(white, coherenceBoost * 0.3)` → effective factor at
    // coherence=1 is 0.1 * 0.3 = 0.03 (matches canon's stated factor).
    // Source ref: ClusterBuilder.ts:214-217.
    expectInSource(src, /coherenceBoost\s*=\s*0\.1\s*\*\s*node\.coherence/);
    expectInSource(src, /coherenceBoost\s*\*\s*0\.3/);
  });

  it('F1: score-driven emissive modulation — avgScore lifts emissiveIntensity via', () => {
    const src = readSrc('builders/ClusterBuilder.ts');
    // Canon line: `0.85 + 0.15 * clamp(avgScore/10, 0, 1)` multiplier
    expectInSource(src, /0\.85\s*\+\s*0\.15\s*\*/);
    expectInSource(src, /avgScore/);
  });
});

// ── F2 — Domain Anchor Vertex Points ─────────────────────────────────

describe('F2 — Domain Anchor Vertex Points', () => {
  it('F2: domain anchor vertex points = PointsMaterial with radial-gradient', () => {
    const src = readSrc('builders/DomainBuilder.ts');
    expectMaterialRecipe(src, 'THREE\\.PointsMaterial', {
      size: /0\.35/,
      blending: /THREE\.AdditiveBlending/,
    });
    // map prop wires to the lazy radial-gradient texture cached on globalThis.
    expectInSource(src, /map:\s*\([^)]*globalThis[^)]*\)[\s\S]*?__semTopGlowTexture/);
  });

  it('F2: JSDOM stability — if !ctx globalThis semTopGlowTexture =', () => {
    const src = readSrc('builders/DomainBuilder.ts');
    // Canon line: `if (!ctx) globalThis.__semTopGlowTexture = undefined fallback`.
    // Source implements the fallback via the inverse-truthy branch
    // `if (c2d)` that ONLY assigns when canvas context exists, so the
    // global stays undefined under JSDOM (no canvas). Anchor tests:
    //   1. The lazy-build guard exists on the canvas context.
    //   2. There's an explicit canvas getContext('2d') call paired with
    //      a truthy guard before any assignment to __semTopGlowTexture.
    // The canon-literal `if (!ctx)` text is a stylistic divergence — the
    // contract is "JSDOM gets undefined for the texture map" which the
    // current source upholds.
    expectInSource(src, /getContext\(['"]2d['"]\)/);
    expectInSource(src, /if\s*\(\s*!?\s*c?2?d\s*\)|if\s*\(\s*!?\s*ctx\s*\)/);
    expectInSource(src, /__semTopGlowTexture/);
  });
});

// ── F3 — Template Cluster Indicator Rings ────────────────────────────

describe('F3 — Template Cluster Indicator Rings', () => {
  it('F3: template ring = RingGeometry 1 25 1 35', () => {
    const src = readSrc('builders/RingBuilder.ts');
    const stripped = stripComments(src);
    // Constants `TEMPLATE_RING_INNER_RADIUS = 1.25`, `_OUTER_RADIUS = 1.35`,
    // `_SEGMENTS = 64` are declared at module scope and passed into
    // `new THREE.RingGeometry(...)`.
    expect(stripped).toMatch(/TEMPLATE_RING_INNER_RADIUS\s*=\s*1\.25/);
    expect(stripped).toMatch(/TEMPLATE_RING_OUTER_RADIUS\s*=\s*1\.35/);
    expect(stripped).toMatch(/TEMPLATE_RING_SEGMENTS\s*=\s*64/);
    // Material + color + opacity + DoubleSide.
    expect(stripped).toMatch(/TEMPLATE_RING_COLOR\s*=\s*0x00e5ff/);
    expect(stripped).toMatch(/TEMPLATE_RING_RESTING_OPACITY\s*=\s*0\.35/);
    expectMaterialRecipe(src, 'THREE\\.MeshBasicMaterial', {
      side: /THREE\.DoubleSide/,
    });
  });

  it('F3: template ring pool = templateRingPool with INITIAL=50 /', () => {
    const src = readSrc('builders/RingBuilder.ts');
    expectConstantValue(src, 'TEMPLATE_RING_POOL_INITIAL', /50/);
    expectConstantValue(src, 'TEMPLATE_RING_POOL_GROW_CHUNK', /50/);
    expectConstantValue(src, 'TEMPLATE_RING_POOL_MAX', /500/);
    expectInSource(src, /_templateRingPool\b/);
  });

  it('F3: hover behavior — opacity oscillates rotation z spins', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: hover → opacity oscillates, rotation.z spins.
    // Source ref: SemanticTopology.svelte:670-671 (template ring hover block).
    expectInSource(src, /templateRing\.rotation\.z\s*\+=/);
    expectInSource(src, /ringMat\.opacity\s*=\s*0\.35\s*\+\s*Math\.sin/);
  });

  it('F3: entry/exit transitions — 400ms cubic ease-in on acquire', () => {
    const src = readSrc('builders/RingBuilder.ts');
    // Canon: 400ms cubic ease-in on acquire, 300ms cubic ease-out on release.
    // RED-phase anchor — either as literal ms values (400 / 300) or as
    // duration-named constants; allow both shapes.
    const stripped = stripComments(src);
    expect(stripped).toMatch(/(?:400\b|ENTRY_DURATION|ENTRY_MS)/);
    expect(stripped).toMatch(/(?:300\b|EXIT_DURATION|EXIT_MS)/);
    // Cubic easing reference — either an explicit cubic-ease helper or
    // the canonical `1 - Math.pow(1 - t, 3)` shape.
    expect(stripped).toMatch(/cubic|Math\.pow\(\s*1\s*-\s*t/);
  });
});

// ── F4 — Readiness Rings ─────────────────────────────────────────────

describe('F4 — Readiness Rings', () => {
  it('F4: readiness ring uses MeshBasicMaterial with depthWrite false AND', () => {
    const src = readSrc('builders/RingBuilder.ts');
    // Anchor on the readiness-ring material block (lines 551-557):
    // MeshBasicMaterial with depthWrite: false AND side: DoubleSide.
    const stripped = stripComments(src);
    expect(stripped).toMatch(/depthWrite:\s*false[\s\S]{0,200}side:\s*THREE\.DoubleSide/);
  });

  it('F4: per-frame billboard via removeReadinessBillboard', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: `_removeReadinessBillboard` per-frame canceller registered in
    // onMount; module-scope let-binding around line 167.
    expectInSource(src, /let\s+_removeReadinessBillboard\s*:/);
    expectInSource(src, /_removeReadinessBillboard\s*=\s*coordinator\.register\(\s*['"]camera['"]/);
  });

  it('F4: LOD-attenuated opacity far/mid/near = 0 4/0 7/1 0', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: far/mid/near = 0.4/0.7/1.0. Source ref: READINESS_LOD_OPACITY map (lines 93-97).
    const stripped = stripComments(src);
    expect(stripped).toMatch(/READINESS_LOD_OPACITY/);
    expect(stripped).toMatch(/far:\s*0\.4/);
    expect(stripped).toMatch(/mid:\s*0\.7/);
    expect(stripped).toMatch(/near:\s*1\.0/);
  });
});

// ── F5 — Hierarchical & Domain Edges ─────────────────────────────────

describe('F5 — Hierarchical & Domain Edges', () => {
  it('F5: hierarchical edges use ShaderMaterial with uTime uniform driven', () => {
    const src = readSrc('builders/EdgeBuilder.ts');
    // ShaderMaterial constructor lives in EdgeBuilder; the `uTime` uniform
    // itself is declared in EdgeShader.ts (createEdgeDepthUniforms factory).
    // EdgeBuilder wires the factory output into the ShaderMaterial.
    expectInSource(src, /new\s+THREE\.ShaderMaterial\s*\(/);
    expectInSource(src, /createEdgeDepthUniforms/);
    // `uTime` uniform declaration in EdgeShader.ts.
    const edgeShader = readSrc('EdgeShader.ts');
    expectInSource(edgeShader, /uTime\s*:\s*\{\s*value:\s*0/);
    // The per-frame _removeEdgeAnim canceller drives uniforms.uTime.value.
    const semTop = readSrc('SemanticTopology.svelte');
    expectInSource(semTop, /_removeEdgeAnim\s*=\s*coordinator\.register/);
    expectInSource(semTop, /u\.uTime\.value\s*=\s*_edgeTime/);
  });

  it('F5: catenary sag = 0 15 distance', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: `0.15 * distance`. Source ref: buildCurvePositions in
    // SemanticTopology.svelte:386 (`len * 0.15 + spread * len * 0.2`).
    expectInSource(src, /len\s*\*\s*0\.15/);
  });

  it('F5: domain structural edges use DomainEdgeShader ShaderMaterial with shared', () => {
    const src = readSrc('DomainEdgeShader.ts');
    // Canon: domain structural edges use DomainEdgeShader (ShaderMaterial)
    // with shared uTime, half-frequency heartbeat.
    expectInSource(src, /uTime/);
    // Heartbeat behavioral anchor — the GLSL fragment defines a `heartbeat`
    // float driven at half the hierarchical-edge frequency.
    // Domain uses `uTime * 1.5` vs hierarchical `uTime * 3.0` (see
    // EdgeShader.ts:42). The half-multiplier IS the canon's "half
    // frequency" claim, encoded as code.
    expectInSource(src, /heartbeat\s*=\s*sin\([^)]*uTime\s*\*\s*1\.5/);
    // Hierarchical comparison: `uTime * 3.0` lives in EdgeShader.ts.
    const hierarchical = readSrc('EdgeShader.ts');
    expectInSource(hierarchical, /sin\([^)]*uTime\s*\*\s*3\.0/);
  });

  it('F5: per-vertex aAlpha attribute encodes child member count weight', () => {
    const src = readSrc('builders/EdgeBuilder.ts');
    // Canon: per-vertex aAlpha encodes child member count weight (floor 0.3, max 1.0).
    // Source ref: EdgeBuilder.ts:362-365 (`memberAlpha = 0.3 + 0.7 * (...)`).
    expectInSource(src, /aAlpha/);
    expectInSource(src, /memberAlpha\s*=[\s\S]{0,80}0\.3\s*\+\s*0\.7\s*\*/);
  });
});

// ── F6 — Similarity & Injection Edges ────────────────────────────────

describe('F6 — Similarity & Injection Edges', () => {
  it('F6: similarity edges use LineDashedMaterial; injection edges use LineBasicMaterial;', () => {
    const src = readSrc('builders/EdgeBuilder.ts');
    expectInSource(src, /new\s+THREE\.LineDashedMaterial\s*\(/);
    expectInSource(src, /new\s+THREE\.LineBasicMaterial\s*\(/);
    // Visibility-toggle via clustersStore lives in SemanticTopology.svelte —
    // EdgeBuilder owns the material instances; verify both store flags wire
    // from the svelte body.
    const semTop = readSrc('SemanticTopology.svelte');
    expectInSource(semTop, /clustersStore\.showSimilarityEdges/);
    expectInSource(semTop, /clustersStore\.showInjectionEdges/);
  });
});

// ── F7 — Beam Pool ───────────────────────────────────────────────────

describe('F7 — Beam Pool', () => {
  it('F7: BeamPool of 10 reusable PlasmaBeam instances; FPS-weapon NDC', () => {
    const src = readSrc('BeamPool.ts');
    expectConstantValue(src, 'POOL_SIZE', /10/);
    // FPS-weapon NDC origin — `_ndcOrigin = new THREE.Vector3(0.0, -1.0, -0.99)`
    // captured in BeamPool.ts:13.
    expectInSource(src, /_ndcOrigin\s*=\s*new\s+THREE\.Vector3\s*\(/);
    expectInSource(src, /\.unproject\(\s*camera\s*\)/);
  });
});

// ── F8 — Breathing Animation ─────────────────────────────────────────

describe('F8 — Breathing Animation', () => {
  it('F8: breathingAnim per-frame callback updates every cluster mesh +', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: `_breathingAnim` per-frame callback updates every cluster mesh + ring.
    expectInSource(src, /_breathingAnim\s*=\s*coordinator\.register\(\s*['"]breathing['"]/);
    // Cluster mesh + template ring + readiness ring touched per-frame in the
    // breathing callback (lines 597-647 reach all 3).
    expectInSource(src, /getTemplateRing\(/);
    expectInSource(src, /getReadinessRing\(/);
  });

  it('F8: per-node phase offset via nodePhaseOffsets — deterministic hash', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: `_nodePhaseOffsets` Map; deterministic hash of node ID → [0, 2π].
    expectInSource(src, /let\s+_nodePhaseOffsets\s*:\s*Map<string,\s*number>/);
    expectInSource(src, /_nodePhaseOffsets\.get\(\s*nodeId\s*\)/);
  });

  it('F8: hover amplifies breathing to ±10%', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon (updated Cycle 1 GREEN — Type-D adjudication): hover amplifies
    // breathing to ±10%. Source ref: SemanticTopology.svelte:625
    // (`hoverAmplification = isHovered ? 1.1 : ...`). Earlier canon line
    // said ±12% (1.12) but the shipped source ships 1.1; canon updated
    // to match per spec §4.2.1 user-adjudication.
    // Anchor on the canonical hover-amplification expression itself —
    // `hoverAmplification = isHovered ? 1.1 : ...` — to avoid matching
    // unrelated `0.1` literals.
    expectInSource(src, /hoverAmplification\s*=\s*isHovered\s*\?\s*1\.1\b/);
  });

  it('F8: hover proximity field — clusters within 8 units', () => {
    const src = readSrc('SemanticTopology.svelte');
    // Canon: clusters within 8 units of hovered cluster receive distance-
    // attenuated breathing amplification (quadratic falloff, max 60% of
    // full hover amplitude, radius 8 units).
    // Three explicit anchors per plan Task 1.1 Step 3 F8 bullet:
    //   1. Radius constant: `HOVER_PROXIMITY_RADIUS` declaration OR
    //      literal `8.0` in distance check.
    //   2. Quadratic falloff: `Math.pow(...)` or `falloff * falloff` expression.
    //   3. 60% cap: `0.6 *` multiplier on the amplified amplitude.
    expectInSource(src, /HOVER_PROXIMITY_RADIUS|\b8\.0\b/);
    expectInSource(src, /Math\.pow|\bt\s*\*\s*t\b|falloff\s*\*\s*falloff/);
    // 60% cap — `* 0.6` (suffix) or `0.6 *` (prefix), both valid expressions.
    expectInSource(src, /\*\s*0\.6\b|\b0\.6\s*\*/);
  });
});

// ── F9 — Cluster Physics ─────────────────────────────────────────────

describe('F9 — Cluster Physics', () => {
  it('F9: ClusterPhysics integration uses k=120 d=12 dt clamp=0 1', () => {
    const src = readSrc('ClusterPhysics.ts');
    const stripped = stripComments(src);
    expect(stripped).toMatch(/SPRING_K\s*=\s*120/);
    expect(stripped).toMatch(/SPRING_D\s*=\s*12/);
    expect(stripped).toMatch(/0\.1/); // dt clamp
    expect(stripped).toMatch(/1e-4/); // velocityFloor literal
  });

  it('F9: snap-to-target floor prevents infinite micro-oscillation', () => {
    const src = readSrc('ClusterPhysics.ts');
    // Canon: snap-to-target floor (prevents infinite micro-oscillation).
    // Source ref: VELOCITY_FLOOR constant + a position-snap branch on the
    // velocity check.
    expectInSource(src, /VELOCITY_FLOOR\s*=\s*1e-4/);
    expectInSource(src, /velocity|VELOCITY_FLOOR/);
    expectInSource(src, /snap/i);
  });
});

// ── F10 — Neural Dust ────────────────────────────────────────────────

describe('F10 — Neural Dust', () => {
  it('F10: Neural Dust = 3000-particle Points cloud 0x88ccff additive', () => {
    const src = readSrc('builders/DustBuilder.ts');
    expectConstantValue(src, 'DUST_COUNT', /3000/);
    expectConstantValue(src, 'DUST_FALLBACK_COLOR', /0x88ccff/);
    expectInSource(src, /THREE\.AdditiveBlending/);
    // Slow XY rotation — happens in SemanticTopology.svelte ambient callback
    // because DustBuilder exposes the Points handle but the rotation tick is
    // owned by the orchestrator (matches canon F10's "slow XY rotation" tag).
    const semTop = readSrc('SemanticTopology.svelte');
    expectInSource(semTop, /dust\.rotation\.y\s*\+=/);
    expectInSource(semTop, /dust\.rotation\.x\s*\+=/);
  });
});

// ── Cross-cutting (PERF lines 550-551) ───────────────────────────────

describe('PERF — Cross-cutting', () => {
  it('PERF: All four impact trigger sites entrance burst post-growth', () => {
    // Canon line 550: all four impact trigger sites (entrance burst,
    // post-growth burst, optimization event, click selection) route through
    // ImpactCoordinator.fire(...). Click-fire migrated to SelectionController
    // per Sub-project E B2, so SemanticTopology.svelte holds 3 sites
    // (entrance, post-growth, optimization) and SelectionController.ts holds
    // the click site. Total of 4 impactCoordinator.fire( call sites across
    // non-test source files.
    const semTop = readSrc('SemanticTopology.svelte');
    const selCtrl = readSrc('SelectionController.ts');
    const semTopFires = (stripComments(semTop).match(/impactCoordinator\.fire\(/g) ?? []).length;
    const selCtrlFires = (stripComments(selCtrl).match(/impactCoordinator\.fire\(/g) ?? []).length;
    expect(semTopFires, 'expected 3 impactCoordinator.fire( call sites in SemanticTopology.svelte').toBe(3);
    expect(selCtrlFires, 'expected 1 impactCoordinator.fire( call site in SelectionController.ts').toBe(1);

    // Verify reactions live ONLY inside ImpactCoordinator.ts — no
    // synchronous clusterPhysics.onBeamImpact / envelopePool.acquire /
    // deps.flashEmissive at any other call site.
    const otherFiles = [
      'SemanticTopology.svelte',
      'SelectionController.ts',
      'FlashController.ts',
      'AnimationCoordinator.ts',
      'BeamPool.ts',
      'EnvelopePool.ts',
      'ClusterPhysics.ts',
    ];
    const reactionPattern = /clusterPhysics\.onBeamImpact|envelopePool\.acquire|deps\.flashEmissive/;
    for (const f of otherFiles) {
      const body = stripComments(readSrc(f));
      expect(body, `${f} must not contain synchronous reaction-pattern calls`).not.toMatch(reactionPattern);
    }
  });

  it('PERF: Scene construction routes through 5 SceneBuilder instances ClusterBuilder', () => {
    // Canon line 551: scene construction routes through 5 SceneBuilder
    // instances in cluster → domain → edge → ring → dust order.
    // Source ref: SemanticTopology.svelte:435-439.
    const src = readSrc('SemanticTopology.svelte');
    const stripped = stripComments(src);
    // Exactly 5 builder.build(data, ...) calls in rebuildScene.
    const buildCalls = stripped.match(/\.build\(\s*data\b/g) ?? [];
    expect(buildCalls.length, 'expected 5 builder.build(data, ...) calls in rebuildScene').toBe(5);

    // Ordering anchor — cluster → domain → edge → ring → dust.
    const orderRegex =
      /clusterBuilder\.build\([\s\S]*?domainBuilder\.build\([\s\S]*?edgeBuilder\.build\([\s\S]*?ringBuilder\.build\([\s\S]*?dustBuilder\.build\(/;
    expect(stripped, 'expected cluster → domain → edge → ring → dust order').toMatch(orderRegex);
  });
});

// (F11-F19 + Performance/lifecycle tests go in Cycle 2 RED.)
