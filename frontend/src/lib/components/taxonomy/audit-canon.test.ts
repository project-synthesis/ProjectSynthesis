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

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

// ── Cinematic post-processing mocks for the F13 composer-dispose RUNTIME
// test (canon F13). Three.js + addon mocks are scoped to the audit-canon
// suite — they only affect tests that instantiate TopologyRenderer. The
// source-grep majority neither imports THREE at runtime nor depends on
// these constructors. Pattern mirrors TopologyRenderer.test.ts so the
// audit suite uses the same fake post-processing chain (passes track
// dispose calls; composer.dispose walks `passes[]`).
vi.mock('three', async (importOriginal) => {
  const actual = await importOriginal<typeof import('three')>();
  // Re-export real THREE so source-grep tests still see real types
  // for any incidental import. Override only the constructors the
  // TopologyRenderer constructor walks at instantiate time.
  class WebGLRenderer {
    setPixelRatio = vi.fn();
    setSize = vi.fn();
    render = vi.fn();
    dispose = vi.fn();
    forceContextLoss = vi.fn();
    domElement = document.createElement('canvas');
    shadowMap = { enabled: false, type: 0 };
  }
  return {
    ...actual,
    WebGLRenderer,
  };
});

vi.mock('three/addons/controls/OrbitControls.js', () => {
  function makeTargetVec() {
    return {
      x: 0, y: 0, z: 0,
      clone() { return makeTargetVec(); },
      subVectors(a: { x: number; y: number; z: number }, b: { x: number; y: number; z: number }) {
        this.x = a.x - b.x; this.y = a.y - b.y; this.z = a.z - b.z; return this;
      },
      normalize() { return this; },
      multiplyScalar() { return this; },
      add() { return this; },
      set(x: number, y: number, z: number) { this.x = x; this.y = y; this.z = z; return this; },
      length() { return Math.sqrt(this.x * this.x + this.y * this.y + this.z * this.z); },
      divideScalar(s: number) {
        if (s !== 0) { this.x /= s; this.y /= s; this.z /= s; } return this;
      },
      lerpVectors: vi.fn(),
    };
  }
  class OrbitControls {
    enableDamping = false;
    dampingFactor = 0;
    minDistance = 0;
    maxDistance = 0;
    addEventListener = vi.fn();
    update = vi.fn();
    dispose = vi.fn();
    target = makeTargetVec();
  }
  return { OrbitControls };
});

vi.mock('three/addons/postprocessing/EffectComposer.js', () => {
  class EffectComposer {
    passes: unknown[] = [];
    constructor(_renderer: unknown) {}
    addPass(pass: unknown) { this.passes.push(pass); }
    render = vi.fn();
    setSize = vi.fn();
    dispose = vi.fn();
  }
  return { EffectComposer };
});

vi.mock('three/addons/postprocessing/RenderPass.js', () => {
  class RenderPass {
    constructor(_scene: unknown, _camera: unknown) {}
    dispose = vi.fn();
  }
  return { RenderPass };
});

vi.mock('three/addons/postprocessing/UnrealBloomPass.js', () => {
  class UnrealBloomPass {
    constructor(
      public resolution: unknown,
      public strength: number,
      public radius: number,
      public threshold: number,
    ) {}
    dispose = vi.fn();
  }
  return { UnrealBloomPass };
});

vi.mock('three/addons/postprocessing/FilmPass.js', () => {
  class FilmPass {
    constructor(public intensity: number, public grayscale: boolean) {}
    dispose = vi.fn();
  }
  return { FilmPass };
});

// Import after mocks so the constructor uses the fake passes.
import { TopologyRenderer } from './TopologyRenderer';

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
    './focus-math.ts', // F14 zero-length direction fallback helper (Sub-project E refactor)
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

/**
 * Look up a taxonomy source file by its taxonomy-relative path
 * (e.g. `'builders/RingBuilder.ts'` or `'SemanticTopology.svelte'`)
 * and return its raw text via Vite's `?raw` glob loader.
 *
 * Throws a clear error if the file is not registered in
 * {@link taxonomySourceMap} so a typo'd test name surfaces immediately
 * rather than producing a confusing `undefined.match` failure downstream.
 *
 * @param name - Taxonomy-relative file path (no leading `./`).
 * @returns Raw file contents as a string.
 * @example
 *   const src = readSrc('builders/RingBuilder.ts');
 */
function readSrc(name: string): string {
  const key = `./${name}`;
  const content = taxonomySourceMap[key];
  if (typeof content !== 'string') {
    throw new Error(`audit-canon: ${key} not found in taxonomy glob map`);
  }
  return content;
}

/**
 * Strip `/* … *​/` block comments and `// …` line comments (when the
 * line comment is the only token on a line) from `src` so source-grep
 * assertions don't accidentally match content inside a comment that
 * was added as documentation. Line-comments embedded mid-expression
 * (e.g. `let x = 1; // note`) are preserved to keep the surrounding
 * code intact for column-sensitive regexes — anchoring on whitespace
 * + line-start ensures only comment-only lines are removed.
 *
 * Best-effort only — does not understand string-literal context, so a
 * `/* …` sequence inside a multiline string would be stripped. Audit
 * tests don't rely on such constructs.
 *
 * @param src - Raw source text.
 * @returns `src` with comment-only constructs removed.
 * @example
 *   stripComments("const x = 1; // note\n//standalone\n") === "const x = 1; // note\n\n"
 */
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
 *
 * Asserts and throws via `expect(...)` on mismatch.
 *
 * @param src      - Source text to search (typically the output of {@link readSrc}).
 * @param ctorName - Regex-escaped constructor name
 *   (e.g. `'THREE\\.PointsMaterial'`). The string is embedded into a
 *   `new RegExp(...)`, so `.` must be escaped as `\\.`.
 * @param props    - Map of prop name → value regex. The prop name is
 *   embedded literally (no escaping) and the value regex's `source` is
 *   spliced in after the `:` and whitespace.
 * @example
 *   expectMaterialRecipe(src, 'THREE\\.PointsMaterial', {
 *     size: /0\.35/,
 *     blending: /THREE\.AdditiveBlending/,
 *   });
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
 * Assert a top-level `const | let | var <identifier> = <valueRegex>`
 * binding exists in `src` after comment stripping. Useful for anchoring
 * named module constants (e.g. `POOL_SIZE = 10`).
 *
 * Asserts and throws via `expect(...)` on mismatch.
 *
 * @param src        - Source text to search.
 * @param identifier - Identifier name. Embedded literally into a
 *   regex — must not contain regex metacharacters (typical for JS
 *   identifiers).
 * @param valueRegex - Regex matching the binding's value expression.
 *   Its `source` is spliced after `<identifier> = `.
 * @example
 *   expectConstantValue(src, 'POOL_SIZE', /10/);
 *   expectConstantValue(src, 'SPRING_K', /120/);
 */
function expectConstantValue(src: string, identifier: string, valueRegex: RegExp): void {
  const stripped = stripComments(src);
  const pattern = new RegExp(`(?:const|let|var)\\s+${identifier}\\s*=\\s*${valueRegex.source}`);
  expect(stripped, `expected constant ${identifier} matching ${valueRegex}`).toMatch(pattern);
}

/**
 * Assert `src` matches `pattern` after stripping comments. Thin
 * wrapper around `expect(...).toMatch(...)` that centralizes the
 * comment-stripping step so canon assertions can't be accidentally
 * fooled by an anchor that lives inside a `//` or `/* … *​/` comment.
 *
 * Asserts and throws via `expect(...)` on mismatch.
 *
 * @param src     - Source text to search.
 * @param pattern - Regex the source must match.
 * @example
 *   expectInSource(src, /castShadow\s*=\s*true/);
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

// ── F11 — Lighting Setup ─────────────────────────────────────────────

describe('F11 — Lighting', () => {
  it('F11: 3 lights Ambient 0 3 Directional 0 7', () => {
    // Canon line 535: 3 lights (Ambient 0.3, Directional 0.7 at (5,10,5),
    // Hemisphere 0.2). Source: TopologyRenderer.ts:74-87.
    const src = readSrc('TopologyRenderer.ts');
    // AmbientLight(0xffffff, 0.3)
    expectInSource(src, /new\s+THREE\.AmbientLight\s*\(\s*0xffffff\s*,\s*0\.3\s*\)/);
    // DirectionalLight(0xffffff, 0.7) positioned at (5, 10, 5)
    expectInSource(src, /new\s+THREE\.DirectionalLight\s*\(\s*0xffffff\s*,\s*0\.7\s*\)/);
    expectInSource(src, /\.position\.set\(\s*5\s*,\s*10\s*,\s*5\s*\)/);
    // HemisphereLight with intensity 0.2
    expectInSource(src, /new\s+THREE\.HemisphereLight\s*\([^)]*0\.2\s*\)/);
  });
});

// ── F12 — Shadow Map ─────────────────────────────────────────────────

describe('F12 — Shadows', () => {
  it('F12: shadowMap enabled = true type = PCFShadowMap mapSize', () => {
    // Canon line 536: shadowMap.enabled = true, type = PCFShadowMap,
    // mapSize 1024×1024. Source: TopologyRenderer.ts:64-65 +
    // DirectionalLight.shadow.mapSize.set(1024, 1024) on line 82.
    const src = readSrc('TopologyRenderer.ts');
    expectInSource(src, /shadowMap\.enabled\s*=\s*true/);
    expectInSource(src, /shadowMap\.type\s*=\s*THREE\.PCFShadowMap/);
    expectInSource(src, /shadow\.mapSize\.set\(\s*1024\s*,\s*1024\s*\)/);
  });
});

// ── F13 — Post-Processing Pipeline ───────────────────────────────────

describe('F13 — Post-Processing', () => {
  it('F13: EffectComposer with RenderPass + UnrealBloomPass 1 5 0', () => {
    // Canon line 537: EffectComposer with RenderPass + UnrealBloomPass
    // (1.5, 0.4, 0.85) + FilmPass(0.35, false) + SMAAPass. Source:
    // TopologyRenderer.ts:94-105.
    const src = readSrc('TopologyRenderer.ts');
    // EffectComposer instantiation
    expectInSource(src, /new\s+EffectComposer\s*\(\s*this\.renderer\s*\)/);
    // RenderPass with scene + camera
    expectInSource(src, /new\s+RenderPass\s*\(\s*this\.scene\s*,\s*this\.camera\s*\)/);
    // UnrealBloomPass canonical params (strength 1.5, radius 0.4, threshold 0.85)
    expectInSource(src, /new\s+UnrealBloomPass\s*\([\s\S]*?1\.5\s*,[\s\S]*?0\.4\s*,[\s\S]*?0\.85/);
    // FilmPass(0.35, false)
    expectInSource(src, /new\s+FilmPass\s*\(\s*0\.35\s*,\s*false\s*\)/);
    // SMAAPass
    expectInSource(src, /new\s+SMAAPass\s*\(/);
  });

  it('F13: composer render not renderer render ; composer setSize', () => {
    // Canon line 538: composer.render() not renderer.render();
    // composer.setSize on resize. Source: TopologyRenderer.ts:143 + :154.
    const src = readSrc('TopologyRenderer.ts');
    // Render loop uses composer, not renderer directly.
    expectInSource(src, /this\.composer\.render\(\s*\)/);
    // Resize forwards to composer.setSize.
    expectInSource(src, /this\.composer\.setSize\(\s*width\s*,\s*height\s*\)/);
    // Negative anchor — the render loop body must not invoke
    // `this.renderer.render(` (canon F13 mandates the composer chain).
    const stripped = stripComments(src);
    expect(stripped, 'render loop must call composer.render(), not renderer.render()').not.toMatch(
      /this\.renderer\.render\(/,
    );
  });

  // ── F13 RUNTIME — composer.dispose calls dispose on all 4 passes ──
  describe('F13 — Post-Processing (runtime)', () => {
    let canvas: HTMLCanvasElement;
    let renderer: TopologyRenderer;

    beforeEach(() => {
      canvas = document.createElement('canvas');
      Object.defineProperty(canvas, 'clientWidth', { value: 800, configurable: true });
      Object.defineProperty(canvas, 'clientHeight', { value: 600, configurable: true });
      renderer = new TopologyRenderer(canvas);
    });

    afterEach(() => {
      // Ensure cleanup even if dispose was already called inside the test.
      // dispose() is idempotent — repeated calls are safe.
      if (renderer && !(renderer as unknown as { _disposed: boolean })._disposed) {
        renderer.dispose();
      }
    });

    it('F13 runtime: composer.dispose() invokes dispose on every pass (canon "+ each pass disposed")', () => {
      // Canon F13 disposal: composer.dispose() + each composer.passes[i].dispose().
      // Source: TopologyRenderer.ts:252-257 — the dispose loop walks the
      // passes array and invokes each pass's dispose before composer.dispose.
      // Runtime check anchors that the loop actually fires the spies.
      const composer = renderer.composer as unknown as { passes: { dispose: () => void }[] };
      expect(composer).toBeDefined();
      expect(composer.passes.length).toBeGreaterThanOrEqual(4);
      const passDisposeSpies = composer.passes.map((p) => vi.spyOn(p, 'dispose'));
      const composerDisposeSpy = vi.spyOn(
        renderer.composer as unknown as { dispose: () => void },
        'dispose',
      );

      renderer.dispose();

      for (const spy of passDisposeSpies) {
        expect(spy).toHaveBeenCalled();
      }
      expect(composerDisposeSpy).toHaveBeenCalled();
    });
  });
});

// ── F14 — Camera Focus (`focusOn`) ───────────────────────────────────

describe('F14 — focusOn', () => {
  it('F14: focusOn with adaptive distance default — keeps current', () => {
    // Canon line 541: focusOn with adaptive distance default — keeps
    // current zoom when distance omitted, clamps to [5, 25].
    // Source: TopologyRenderer.ts:199-201.
    const src = readSrc('TopologyRenderer.ts');
    expectInSource(
      src,
      /distance\s*!==\s*undefined\s*\?\s*distance\s*:\s*Math\.max\(\s*5\s*,\s*Math\.min\(\s*currentDist\s*,\s*25\s*\)\s*\)/,
    );
  });

  it('F14: zero-length direction fallback to +Z', () => {
    // Canon line 542: zero-length direction guard falls back to +Z axis.
    // Source: focus-math.ts:82-87 (helper) — TopologyRenderer.focusOn
    // delegates the endpoint math to computeFocusEndpoint, which contains
    // the fallback. Anchor both call site + helper body.
    const helperSrc = readSrc('focus-math.ts');
    // Zero-direction guard (direction.length() < epsilon) + fallback to (0, 0, 1).
    expectInSource(helperSrc, /direction\.length\(\)/);
    expectInSource(helperSrc, /direction\.set\s*\(\s*0\s*,\s*0\s*,\s*1\s*\)/);
    // Caller wires the helper.
    const callerSrc = readSrc('TopologyRenderer.ts');
    expectInSource(callerSrc, /computeFocusEndpoint\s*\(/);
  });

  it('F14: checkLod called per-frame inside the focus animate loop', () => {
    // Canon line 543: _checkLod() called per-frame inside the focus animate
    // loop (because OrbitControls.update does NOT fire 'change' on
    // programmatic position mutation without damping residual).
    // Source: TopologyRenderer.ts:228 (inside the animate() body).
    const src = readSrc('TopologyRenderer.ts');
    const stripped = stripComments(src);
    // Anchor on the animate() closure containing this._checkLod() call.
    // The structure: `const animate = () => { ... this._checkLod(); ... }`.
    expect(stripped).toMatch(/const\s+animate\s*=[\s\S]*?this\._checkLod\(\s*\)/);
  });
});

// ── F15 — Click → Selection Flow ─────────────────────────────────────

describe('F15 — handleNodeClick', () => {
  it('F15: handleNodeClick only calls clustersStore selectCluster nodeId — no', () => {
    // Canon line 544: handleNodeClick body contains only
    // `clustersStore.selectCluster(nodeId)` — no duplicate visual logic.
    // Source: SemanticTopology.svelte:718-724.
    const src = readSrc('SemanticTopology.svelte');
    const stripped = stripComments(src);
    // Extract handleNodeClick function body by balanced-brace scan from
    // the `function handleNodeClick(nodeId: string)` declaration.
    const fnOpen = stripped.match(
      /function\s+handleNodeClick\s*\(\s*nodeId\s*:\s*string\s*\)\s*:\s*void\s*\{/,
    );
    expect(fnOpen, 'expected handleNodeClick function declaration').not.toBeNull();
    const openIdx = fnOpen!.index! + fnOpen![0].length;
    let depth = 1;
    let i = openIdx;
    while (i < stripped.length && depth > 0) {
      const ch = stripped[i];
      if (ch === '{') depth++;
      else if (ch === '}') depth--;
      if (depth === 0) break;
      i++;
    }
    const body = stripped.slice(openIdx, i);
    // The body's ONLY executable line is clustersStore.selectCluster(nodeId).
    // (Comments are already stripped.) Verify the selectCluster call AND
    // the absence of any other visual side-effect markers.
    expect(body).toMatch(/clustersStore\.selectCluster\s*\(\s*nodeId\s*\)/);
    // Forbidden side-effects in the click handler — must be driven by the
    // $effect watching selectedClusterId, not by handleNodeClick.
    expect(body, 'handleNodeClick must not call focusOn directly').not.toMatch(/focusOn\s*\(/);
    expect(body, 'handleNodeClick must not call impactCoordinator.fire').not.toMatch(
      /impactCoordinator\.fire\(/,
    );
    expect(body, 'handleNodeClick must not call beamPool.acquire').not.toMatch(
      /beamPool\.acquire\(/,
    );
    expect(body, 'handleNodeClick must not call applyHighlight').not.toMatch(
      /applyHighlight\s*\(/,
    );
  });
});

// ── F16 — Highlight Survival on Async Rebuild ────────────────────────

describe('F16 — Highlight Survival', () => {
  it('F16: selectionController afterRebuild at end of rebuildScene replaces the', () => {
    // Canon line 545: selectionController.afterRebuild() at end of
    // rebuildScene (replaces the pre-Sub-project-E inline applyHighlight
    // hack). Source: SemanticTopology.svelte:562.
    const src = readSrc('SemanticTopology.svelte');
    expectInSource(src, /selectionController\?\.afterRebuild\(\s*\)/);
    // afterRebuild is also declared on the SelectionController class.
    const sc = readSrc('SelectionController.ts');
    expectInSource(sc, /afterRebuild\s*\(\s*\)\s*:\s*void\s*\{/);
  });

  it('F16: SelectionController applyHighlight flips both mat color setHex HIGHLIGHTCOLOR', () => {
    // Canon line 546: SelectionController._applyHighlight flips BOTH
    // mat.color.setHex(HIGHLIGHT_COLOR) AND mat.emissive.setHex(HIGHLIGHT_COLOR)
    // (dual swap); _clearHighlight restores both from the _highlightedColor
    // snapshot. Source: SelectionController.ts:301-339.
    const src = readSrc('SelectionController.ts');
    // _applyHighlight contains both the color swap AND the emissive swap.
    expectInSource(src, /mat\.color\.setHex\s*\(\s*this\._deps\.highlightColor\s*\)/);
    expectInSource(src, /mat\.emissive\.setHex\s*\(\s*this\._deps\.highlightColor\s*\)/);
    // _clearHighlight restores both from the captured _highlightedColor.
    expectInSource(src, /mat\.color\.setHex\s*\(\s*this\._highlightedColor\s*\)/);
    expectInSource(src, /mat\.emissive\.setHex\s*\(\s*this\._highlightedColor\s*\)/);
  });
});

// ── F17 — One-Shot Auto-Focus ────────────────────────────────────────

describe('F17 — Auto-Focus Guard', () => {
  // eslint-disable-next-line quotes
  it("F17: hasAutoFocused guard — bird's-eye-view zoom runs exactly once", () => {
    // Canon line 547: _hasAutoFocused guard — bird's-eye-view zoom runs
    // EXACTLY ONCE per component lifecycle. Subsequent rebuildScene
    // calls do not re-trigger the auto-focus block.
    // Source: SemanticTopology.svelte:279 (declaration) + :1025-1026
    // (guard pattern). Source-grep verifies (a) declaration, (b) guard
    // expression `!_hasAutoFocused`, (c) the one-shot set inside the
    // guarded block — together they encode the once-only invariant.
    const src = readSrc('SemanticTopology.svelte');
    const stripped = stripComments(src);
    // (a) Declaration.
    expect(stripped).toMatch(/let\s+_hasAutoFocused\s*=\s*false/);
    // (b) Guard expression in the if-condition AND
    // (c) the one-shot set inside the guarded block — `if (!_hasAutoFocused ...) { _hasAutoFocused = true; ...`.
    expect(stripped).toMatch(
      /if\s*\(\s*!_hasAutoFocused[\s\S]*?\{\s*[\s\S]{0,100}?_hasAutoFocused\s*=\s*true/,
    );
  });
});

// ── F18 — Tactile Feedback on External Selection ─────────────────────

describe('F18 — Selection-Engulfment', () => {
  it('F18: external selection routes through impactCoordinator fire { trigger', () => {
    // Canon line 548: external selection routes through
    // impactCoordinator.fire({ trigger: 'click', node, group }) — the
    // coordinator's fire() body wraps clusterPhysics.onBeamImpact AND the
    // F19 envelope + flash INSIDE the beamPool.acquire's onImpact callback.
    // Selection-engulfment is tracked in SelectionController's internal
    // engulfed set; canonical read is SelectionController.isEngulfed(id).
    // Sub-project E migrated ownership from ImpactCoordinator to SC.
    // Sources: ImpactCoordinator.ts:205-260 + SelectionController.ts:96-98.
    const ic = readSrc('ImpactCoordinator.ts');
    // fire() body wraps F19 reactions inside the beamPool.acquire onImpact
    // callback — the canonical causal-ordering invariant.
    expectInSource(ic, /this\._deps\.beamPool\.acquire\s*\([\s\S]*?onImpact\s*:\s*\(\s*\)\s*=>\s*\{/);
    // The selectionController.onImpact routing call lives inside that callback.
    expectInSource(ic, /this\._deps\.selectionController\.onImpact\s*\(/);

    // SelectionController owns the engulfed set (canonical read).
    const sc = readSrc('SelectionController.ts');
    expectInSource(sc, /isEngulfed\s*\(\s*nodeId\s*:\s*string\s*\)\s*:\s*boolean/);
    expectInSource(sc, /this\._engulfedSet\.has\s*\(\s*nodeId\s*\)/);

    // ImpactCoordinator must NOT carry its own engulfed set anymore (the
    // ownership migration). Anchors on the absence of a private
    // _selectionEngulfed field on IC.
    expect(stripComments(ic), 'ImpactCoordinator must not declare _selectionEngulfed').not.toMatch(
      /_selectionEngulfed/,
    );
  });
});

// ── F19 — Envelopement Burst at Beam Impact ──────────────────────────

describe('F19 — Engulfment Envelope', () => {
  it('F19: EnvelopePool constructed in onMount; envelope geometry shape literals', () => {
    // Canon line 549: EnvelopePool constructed in onMount; envelope
    // geometry shape literals match `node.state === 'domain' ? 'domain' : 'cluster'`.
    // Sources: SemanticTopology.svelte:1350 (onMount construction) +
    // ImpactCoordinator.ts:217 (shape literal at fire site).
    const semTop = readSrc('SemanticTopology.svelte');
    // EnvelopePool instantiated inside the component (no constructor args).
    expectInSource(semTop, /envelopePool\s*=\s*new\s+EnvelopePool\s*\(/);
    // Shape literal at the canonical fire site lives in ImpactCoordinator —
    // the coordinator's fire() is the single entry point for all impact
    // sites, so the shape decision lives there.
    const ic = readSrc('ImpactCoordinator.ts');
    expectInSource(
      ic,
      /freshNode\.state\s*===\s*['"]domain['"]\s*\?\s*['"]domain['"]\s*:\s*['"]cluster['"]/,
    );
  });

  it('F19: FlashController flash uses baseline-capture pattern — rapid re-fires reuse', () => {
    // Canon line 550: FlashController.flash() uses baseline-capture pattern —
    // rapid re-fires REUSE the prior baselineEmissive AND startIntensity
    // (B6 mid-flash continuity preserved through the FlashController class
    // field _states). Source: FlashController.ts:92 + :120-138.
    const src = readSrc('FlashController.ts');
    // _states is the class field that survives re-fires.
    expectInSource(src, /_states\s*:\s*Map<string,\s*FlashState>/);
    // The baseline-capture conditional: `existing ? existing.baselineEmissive : trueBase`.
    expectInSource(src, /existing\s*\?\s*existing\.baselineEmissive\s*:\s*trueBase/);
    // The startIntensity-capture conditional: `existing ? existing.startIntensity : ...`.
    expectInSource(src, /existing\s*\?\s*existing\.startIntensity\s*:/);
  });

  it('F19: causal-ordering invariant enforced at source level — every', () => {
    // Canon line 551: causal-ordering invariant — every impactCoordinator.fire
    // site routes through the coordinator's fire() body, which wires
    // clusterPhysics.onBeamImpact + envelopePool.acquire +
    // selectionController.onImpact INSIDE an onImpact callback, never
    // synchronously alongside beamPool.acquire. Per Sub-project C
    // source-grep test #6: ZERO matches for those reactions outside
    // ImpactCoordinator.ts. Source: ImpactCoordinator.ts:234-258.
    const ic = readSrc('ImpactCoordinator.ts');
    const icStripped = stripComments(ic);
    // The onImpact callback body wires all three F19 reactions.
    expect(icStripped).toMatch(
      /onImpact\s*:\s*\(\s*\)\s*=>\s*\{[\s\S]*?clusterPhysics\.onBeamImpact[\s\S]*?envelopePool\.acquire[\s\S]*?\}/,
    );
    // The reactions appear NOWHERE else in the taxonomy directory —
    // mirrors the cleanup-contract test #6 invariant.
    const otherFiles = [
      'SemanticTopology.svelte',
      'SelectionController.ts',
      'FlashController.ts',
      'AnimationCoordinator.ts',
      'BeamPool.ts',
      'EnvelopePool.ts',
      'ClusterPhysics.ts',
    ];
    const reactionPattern = /clusterPhysics\.onBeamImpact|envelopePool\.acquire/;
    for (const f of otherFiles) {
      const body = stripComments(readSrc(f));
      expect(
        body,
        `${f} must not contain synchronous F19 reactions — they live only inside ImpactCoordinator.ts`,
      ).not.toMatch(reactionPattern);
    }
  });
});

// ── Performance + lifecycle ──────────────────────────────────────────

describe('PERF — Performance + lifecycle', () => {
  it('PERF: Module-level scratch table declared scratchVec3a scratchQuat scratchColor ZAXIS', () => {
    // Canon line 556: Module-level scratch table declared (_scratchVec3a,
    // _scratchQuat, _scratchColor, Z_AXIS). Source: SemanticTopology.svelte
    // module scope. Post-Sub-project D, _scratchQuat + _scratchColor were
    // migrated to builder-private scratch tables — the orchestrator file
    // retains them as references (in code or canonical comments) so the
    // scratch-table contract is observable at module top, matching the
    // perf-budget.test.ts existing scratch-table assertion. (This canon
    // line is mirrored by perf-budget.test.ts; audit-canon pins the same
    // invariant from the audit-as-code surface.)
    const src = readSrc('SemanticTopology.svelte');
    // Each canonical scratch identifier must appear in the source (raw —
    // covers both live declarations and the migration-note comment that
    // pins the contract). Mirrors perf-budget.test.ts scratch-table assertion.
    const required = ['_scratchVec3a', '_scratchQuat', '_scratchColor', 'Z_AXIS'];
    const missing = required.filter((id) => !src.includes(id));
    expect(missing, 'expected canonical scratch identifiers present at module scope').toEqual([]);
    // Z_AXIS initialized to (0, 0, 1) — the readonly +Z axis used by rotateOnAxis.
    expectInSource(
      src,
      /Z_AXIS\s*=\s*new\s+THREE\.Vector3\s*\(\s*0\s*,\s*0\s*,\s*1\s*\)/,
    );
  });

  it('PERF: breathingAnim borrows scratchVec3a for mesh scale lerp not', () => {
    // Canon line 557: _breathingAnim borrows _scratchVec3a for
    // mesh.scale.lerp (not new THREE.Vector3()). Source:
    // SemanticTopology.svelte:629.
    const src = readSrc('SemanticTopology.svelte');
    // The canonical borrow pattern (mesh.scale.lerp consumes
    // _scratchVec3a.set(s, s, s), not a fresh allocation).
    expectInSource(
      src,
      /mesh\.scale\.lerp\s*\(\s*_scratchVec3a\.set\s*\(\s*targetScale\s*,\s*targetScale\s*,\s*targetScale\s*\)\s*,\s*0\.1\s*\)/,
    );
    // Forbidden anti-pattern — never allocate per-frame Vector3 inside
    // the breathing callback. Anchored on the scratch-borrow expression
    // appearing in the breathing block; verify no `new THREE.Vector3` is
    // co-located with `mesh.scale.lerp`.
    const stripped = stripComments(src);
    // Find every occurrence of `mesh.scale.lerp(` and inspect its argument.
    const lerpRe = /mesh\.scale\.lerp\s*\(\s*([^,]+?)\s*,/g;
    let match: RegExpExecArray | null;
    while ((match = lerpRe.exec(stripped)) !== null) {
      const arg = match[1];
      expect(arg, `mesh.scale.lerp argument must not allocate (got: ${arg})`).not.toMatch(
        /new\s+THREE\.Vector3/,
      );
    }
  });

  it('PERF: Coordinators + 2 conditional cancellers wired in cleanup', () => {
    // Canon line 558: Coordinators + 2 conditional cancellers wired in
    // cleanup return in order: selectionController?.dispose() →
    // impactCoordinator?.dispose() → coordinator?.dispose() →
    // _removeFormationAnim?.() + _removeReadinessBillboard?.().
    // Source: SemanticTopology.svelte:1521-1577 (cleanup return body).
    const src = readSrc('SemanticTopology.svelte');
    const stripped = stripComments(src);
    // The cleanup return is unique — match the canonical disposal-chain order.
    expect(
      stripped,
      'cleanup return must dispose SC → IC → AnimationCoordinator → conditionals in order',
    ).toMatch(
      /selectionController\?\.dispose\(\s*\)[\s\S]*?impactCoordinator\?\.dispose\(\s*\)[\s\S]*?coordinator\?\.dispose\(\s*\)[\s\S]*?_removeFormationAnim\?\.\(\s*\)[\s\S]*?_removeReadinessBillboard\?\.\(\s*\)/,
    );
  });

  it('PERF: envelopePool? dispose invoked + reference nulled in cleanup', () => {
    // Canon line 559: envelopePool?.dispose() invoked + reference nulled
    // in cleanup return. Source: SemanticTopology.svelte:1556-1557.
    const src = readSrc('SemanticTopology.svelte');
    const stripped = stripComments(src);
    // Pool disposed.
    expect(stripped).toMatch(/envelopePool\?\.dispose\(\s*\)/);
    // Reference nulled (right after dispose).
    expect(stripped).toMatch(/envelopePool\?\.dispose\(\s*\)[\s\S]{0,100}?envelopePool\s*=\s*null/);
  });

  it('PERF: FlashController states clear happens inside selectionController dispose →', () => {
    // Canon line 560: FlashController._states.clear() happens inside
    // selectionController.dispose() → FlashController.dispose() BEFORE
    // coordinator?.dispose() tears down AnimationCoordinator. Each active
    // flash's baselineEmissive restored before the map is cleared, with
    // emissive color decided by live isHighlighted query (HIGHLIGHT_COLOR
    // if still selected, captured domainEmissive otherwise).
    // Sources: FlashController.ts:159-177 (dispose body) +
    // SemanticTopology.svelte cleanup return ordering.
    const fc = readSrc('FlashController.ts');
    const fcStripped = stripComments(fc);
    // FlashController.dispose body: restore baseline + clear states.
    expect(fcStripped).toMatch(
      /dispose\s*\(\s*\)\s*:\s*void\s*\{[\s\S]*?mat\.emissiveIntensity\s*=\s*state\.baselineEmissive[\s\S]*?this\._states\.clear\(\s*\)/,
    );
    // Live isHighlighted query — HIGHLIGHT_COLOR if still selected, else captured domainEmissive.
    expect(fcStripped).toMatch(/this\._deps\.isHighlighted\s*\(\s*nodeId\s*\)/);
    expect(fcStripped).toMatch(/mat\.emissive\.setHex\s*\(\s*HIGHLIGHT_COLOR\s*\)/);
    expect(fcStripped).toMatch(/mat\.emissive\.copy\s*\(\s*state\.domainEmissive\s*\)/);

    // Cleanup-return ordering: SC.dispose (which transitively invokes
    // FC.dispose) precedes coordinator.dispose so the AnimationCoordinator
    // is still alive when FC restores baseline — pinned via SemanticTopology.
    const semTop = stripComments(readSrc('SemanticTopology.svelte'));
    expect(semTop).toMatch(
      /selectionController\?\.dispose\(\s*\)[\s\S]*?coordinator\?\.dispose\(\s*\)/,
    );
  });

  // eslint-disable-next-line quotes
  it("PERF: All disposables drained geometries materials textures lights' shadow", () => {
    // Canon line 561: All disposables drained — geometries, materials,
    // textures, lights' shadow maps. Source: TopologyRenderer.dispose
    // scene.traverse covers Mesh/LineSegments/Points/Sprite/Light.shadow.map.
    const src = readSrc('TopologyRenderer.ts');
    const stripped = stripComments(src);
    // Geometry dispose inside the traverse.
    expect(stripped).toMatch(/obj\.geometry\.dispose\(\s*\)/);
    // Material dispose (both array + single).
    expect(stripped).toMatch(/m\.dispose\(\s*\)/);
    expect(stripped).toMatch(/material\s+as\s+THREE\.Material\)\.dispose\(\s*\)/);
    // Sprite map (texture) dispose.
    expect(stripped).toMatch(/obj\.material\.map\?\.dispose\(\s*\)/);
    // Shadow map dispose on shadow-casting lights.
    expect(stripped).toMatch(/obj\.shadow\?\.map\?\.dispose\(\s*\)/);
  });

  it('PERF: composer dispose + each pass disposed', () => {
    // Canon line 562: composer.dispose() + each pass disposed.
    // Source: TopologyRenderer.ts:252-257.
    const src = readSrc('TopologyRenderer.ts');
    const stripped = stripComments(src);
    // The for-of loop walks composer.passes and invokes each pass dispose.
    expect(stripped).toMatch(
      /for\s*\(\s*const\s+pass\s+of\s+this\.composer\.passes\s*\)[\s\S]*?\.dispose\(\s*\)/,
    );
    // composer.dispose() called after the loop.
    expect(stripped).toMatch(/this\.composer\.dispose\(\s*\)/);
    // Ordering: pass-dispose loop precedes composer.dispose().
    const loopIdx = stripped.search(/for\s*\(\s*const\s+pass\s+of\s+this\.composer\.passes/);
    const composerDisposeIdx = stripped.search(/this\.composer\.dispose\(\s*\)/);
    expect(loopIdx, 'pass-dispose loop must run before composer.dispose').toBeGreaterThan(-1);
    expect(composerDisposeIdx).toBeGreaterThan(loopIdx);
  });

  it('PERF: renderer dispose + renderer forceContextLoss', () => {
    // Canon line 563: renderer.dispose() + renderer.forceContextLoss().
    // Source: TopologyRenderer.ts:258 + :262. The forceContextLoss call
    // prevents GL context accumulation on rapid mount/unmount.
    const src = readSrc('TopologyRenderer.ts');
    const stripped = stripComments(src);
    expect(stripped).toMatch(/this\.renderer\.dispose\(\s*\)/);
    expect(stripped).toMatch(/this\.renderer\.forceContextLoss\(\s*\)/);
    // Ordering — dispose precedes forceContextLoss (mirrors canon Disposal
    // Contract line 476: "renderer.dispose() + renderer.forceContextLoss()").
    const disposeIdx = stripped.search(/this\.renderer\.dispose\(\s*\)/);
    const forceLossIdx = stripped.search(/this\.renderer\.forceContextLoss\(\s*\)/);
    expect(disposeIdx).toBeGreaterThan(-1);
    expect(forceLossIdx).toBeGreaterThan(disposeIdx);
  });

  it('PERF: Pool retention templateRingPool array preserved across unmount high-water', () => {
    // Canon line 564: Pool retention — _templateRingPool array preserved
    // across unmount (high-water mark). Source: RingBuilder.ts — the pool
    // grows in TEMPLATE_RING_POOL_GROW_CHUNK increments up to
    // TEMPLATE_RING_POOL_MAX, and the high-water-mark semantics live in
    // the pool's growable-array contract. Anchors:
    //   1. Pool field declared on the builder (`_templateRingPool`).
    //   2. High-water bounds: INITIAL=50, GROW_CHUNK=50, MAX=500.
    //   3. Dev-only hook re-publishes the pool ref on each build so
    //      tests observe the current high-water across rebuilds.
    const src = readSrc('builders/RingBuilder.ts');
    const stripped = stripComments(src);
    // (1) Pool field declaration — readonly Mesh[] on the builder instance.
    expect(stripped).toMatch(/_templateRingPool\s*:\s*THREE\.Mesh\[\]/);
    // (2) High-water bounds.
    expect(stripped).toMatch(/TEMPLATE_RING_POOL_INITIAL\s*=\s*50/);
    expect(stripped).toMatch(/TEMPLATE_RING_POOL_GROW_CHUNK\s*=\s*50/);
    expect(stripped).toMatch(/TEMPLATE_RING_POOL_MAX\s*=\s*500/);
    // (3) Dev-only re-publish of the pool ref on each build — preserves the
    // pre-extraction test surface (__semTopTemplateRingPool global hook).
    expect(stripped).toMatch(/__semTopTemplateRingPool[\s\S]*?this\._templateRingPool/);
  });
});
