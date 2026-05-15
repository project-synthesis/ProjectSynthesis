/**
 * DomainEdgeShader — unit tests.
 *
 * Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md`
 *        canon F5 extension — domain structural edge shader.
 *
 * Verifies the shader module's public surface:
 *   1. Exported shader strings contain expected GLSL constructs.
 *   2. `createDomainEdgeUniforms` returns the required uniform structure.
 *   3. Shader design constraints (half-frequency, no depth attenuation).
 */
import { describe, expect, test } from 'vitest';
import * as THREE from 'three';
import {
  DOMAIN_EDGE_VERTEX,
  DOMAIN_EDGE_FRAGMENT,
  createDomainEdgeUniforms,
} from './DomainEdgeShader';

describe('DomainEdgeShader — vertex shader', () => {
  test('declares vLocalDist varying for organic modulation', () => {
    expect(DOMAIN_EDGE_VERTEX).toMatch(/varying\s+float\s+vLocalDist/);
  });

  test('computes vLocalDist from position in main()', () => {
    expect(DOMAIN_EDGE_VERTEX).toMatch(/vLocalDist\s*=\s*length\s*\(\s*position/);
  });

  test('outputs gl_Position via projectionMatrix * modelViewMatrix', () => {
    expect(DOMAIN_EDGE_VERTEX).toMatch(/gl_Position\s*=\s*projectionMatrix\s*\*\s*modelViewMatrix/);
  });
});

describe('DomainEdgeShader — fragment shader', () => {
  test('declares all three uniforms (uColor, uOpacity, uTime)', () => {
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/uniform\s+vec3\s+uColor/);
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/uniform\s+float\s+uOpacity/);
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/uniform\s+float\s+uTime/);
  });

  test('uses half-frequency heartbeat (1.5 multiplier, not 3.0)', () => {
    // Canon F5 extension: domain edges pulse at half the frequency of
    // hierarchical child edges. The child shader uses `uTime * 3.0`,
    // so the domain shader must use a lower multiplier.
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/uTime\s*\*\s*1\.5/);
    // Negative gate: must NOT use the child edge's full-speed 3.0 multiplier
    expect(DOMAIN_EDGE_FRAGMENT).not.toMatch(/uTime\s*\*\s*3\.0/);
  });

  test('higher baseline opacity (0.4) than child edges (0.3)', () => {
    // The domain container should never fully dim out between pulses.
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/0\.4\s*\+/);
  });

  test('moderate HDR boost (0.5) lower than child edge boost (0.8)', () => {
    // Domains should not outshine data-carrying children.
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/organicEnergy\s*\*\s*0\.5/);
  });

  test('does NOT reference depth attenuation uniforms', () => {
    // Domain edges are always near their anchor node — no depth fade needed.
    // These are the uniforms from the child EdgeShader that should NOT appear.
    expect(DOMAIN_EDGE_FRAGMENT).not.toMatch(/uNearDist/);
    expect(DOMAIN_EDGE_FRAGMENT).not.toMatch(/uFarDist/);
    expect(DOMAIN_EDGE_FRAGMENT).not.toMatch(/uMaxReduction/);
  });

  test('outputs gl_FragColor with glowingColor and opacity', () => {
    expect(DOMAIN_EDGE_FRAGMENT).toMatch(/gl_FragColor\s*=\s*vec4\s*\(\s*glowingColor/);
  });
});

describe('createDomainEdgeUniforms — uniform structure', () => {
  test('returns object with uColor, uOpacity, uTime keys', () => {
    const uniforms = createDomainEdgeUniforms(0xff0000, 0.8);
    expect(uniforms).toHaveProperty('uColor');
    expect(uniforms).toHaveProperty('uOpacity');
    expect(uniforms).toHaveProperty('uTime');
    // Negative gate: must NOT have depth uniforms
    expect(uniforms).not.toHaveProperty('uNearDist');
    expect(uniforms).not.toHaveProperty('uFarDist');
    expect(uniforms).not.toHaveProperty('uMaxReduction');
  });

  test('uColor is a THREE.Color with the input hex', () => {
    const uniforms = createDomainEdgeUniforms(0x00ff00, 1.0);
    expect(uniforms.uColor.value).toBeInstanceOf(THREE.Color);
    const c = uniforms.uColor.value as THREE.Color;
    expect(c.r).toBeCloseTo(0);
    expect(c.g).toBeCloseTo(1);
    expect(c.b).toBeCloseTo(0);
  });

  test('uOpacity.value matches the input baseOpacity', () => {
    const uniforms = createDomainEdgeUniforms(0x000000, 0.65);
    expect(uniforms.uOpacity.value).toBe(0.65);
  });

  test('uTime.value starts at 0.0', () => {
    const uniforms = createDomainEdgeUniforms(0x000000, 1.0);
    expect(uniforms.uTime.value).toBe(0.0);
  });
});
