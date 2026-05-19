/**
 * Visual V3 Tier 3 — comprehensive test suite for Pattern Graph polish features.
 *
 * Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md`
 *
 * Tests cover:
 *   T3.1 — Domain-Tinted Neural Dust
 *   T3.2 — Formation Animation Stagger by Depth
 *   T3.3 — Beam Impact Camera Micro-Shake
 *   T3.4 — Idle Ambient Energy Pulse
 *
 * Methodology: source-grep over `import.meta.glob ?raw` for structural
 * presence and integration wiring.
 */
import { describe, expect, test } from 'vitest';

const sourceMap = import.meta.glob<string>(
  [
    './SemanticTopology.svelte',
    './ClusterPhysics.ts',
    './ImpactCoordinator.ts',
    // Sub-project D — Neural Dust migrated to DustBuilder.ts.
    './builders/DustBuilder.ts',
  ],
  { query: '?raw', import: 'default', eager: true },
);

function read(name: string): string {
  const key = `./${name}`;
  const content = sourceMap[key];
  if (typeof content !== 'string') throw new Error(`visual-v3-tier3: ${name} not found`);
  return content;
}

// ── T3.1 — Domain-Tinted Neural Dust ────────────────────────────────

describe('T3.1 — Domain-Tinted Neural Dust', () => {
  // Sub-project D — Neural Dust construction migrated to DustBuilder.ts.
  test('dust material enables vertexColors', () => {
    const src = read('builders/DustBuilder.ts');
    expect(src).toMatch(/vertexColors:\s*true/);
  });

  test('dust geometry sets color attribute per vertex', () => {
    const src = read('builders/DustBuilder.ts');
    // DustBuilder may name its variable differently (e.g. `geometry`)
    // but the setAttribute('color', ...) pattern must remain.
    expect(src).toMatch(/setAttribute\(\s*['"]color['"]/);
  });

  test('dust color selection iterates over domain anchors to find nearest', () => {
    const src = read('builders/DustBuilder.ts');
    // Must contain a distance-check loop against domain anchors.
    expect(src).toMatch(/Math\.min\(|nearestDist/);
    // Domain anchors check.
    expect(src).toMatch(/state\s*===\s*['"]domain['"]/);
  });
});

// ── T3.2 — Formation Animation Stagger by Depth ──────────────────────

describe('T3.2 — Formation Animation Stagger by Depth', () => {
  test('formation setup calculates distance from origin for each node', () => {
    const src = read('SemanticTopology.svelte');
    // Must calculate length/distance of the target position
    expect(src).toMatch(/Math\.sqrt\(finalX\*finalX \+ finalY\*finalY \+ finalZ\*finalZ\)/);
  });

  test('delay factor is applied to the individual easeT calculation', () => {
    const src = read('SemanticTopology.svelte');
    // Look for a delay check within the node iteration in _removeFormationAnim loop
    expect(src).toMatch(/Math\.max\(0,\s*formProgress\s*-\s*delay\)/);
  });
});

// ── T3.3 — Beam Impact Camera Micro-Shake ───────────────────────────

describe('T3.3 — Beam Impact Camera Micro-Shake', () => {
  test('SemanticTopology declares _cameraShake accumulators', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/let\s+_cameraShake\s*=\s*0/);
  });

  test('breathing callback applies noise/random decay to camera rotation', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/camera\.rotation\.x\s*\+=/);
    expect(src).toMatch(/camera\.rotation\.y\s*\+=/);
    expect(src).toMatch(/_cameraShake\s*\*\=\s*0\.\d+/); // Decay multiplier
  });

  test('ClusterPhysics triggers shake on beam impact', () => {
    const src = read('ClusterPhysics.ts');
    // Should fire a custom event that SemanticTopology listens to
    expect(src).toMatch(/dispatchEvent\(new\s+CustomEvent\(\s*['"]beam-impact['"]/);
  });
});

// ── T3.4 — Idle Ambient Energy Pulse ────────────────────────────────

describe('T3.4 — Idle Ambient Energy Pulse', () => {
  test('SemanticTopology tracks selection separately from hover', () => {
    const src = read('SemanticTopology.svelte');
    expect(src).toMatch(/const\s+isSelected\s*=\s*.* === nodeId/);
  });

  test('idle pulse computes slow sine wave (post-Sub-project-C: extracted to ImpactCoordinator._tick)', () => {
    const src = read('ImpactCoordinator.ts');
    // Period should be slower than the 1.5 multiplier used for breathing.
    // Post-Sub-project-C the pulse accumulator is `_pulseTime` (real-time
    // delta-driven) instead of `_breathingTime` (fixed-step). Frequency
    // multiplier stays in the [0.2, 0.8] band.
    expect(src).toMatch(/Math\.sin\(\s*this\._pulseTime\s*\*\s*0\.[2-8]\s*\)/);
  });

  test('idle pulse adds to the target emissiveIntensity (post-Sub-project-C: in ImpactCoordinator._tick)', () => {
    const src = read('ImpactCoordinator.ts');
    expect(src).toMatch(/emissiveIntensity\s*=\s*[\s\S]*?idlePulse/);
  });
});
