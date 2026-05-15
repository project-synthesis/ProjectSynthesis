/**
 * Domain structural-edge shader — the "slow heartbeat" parent pulse.
 *
 * Mirrors `EdgeShader.ts` (hierarchical edge shader) structurally, but
 * runs at half frequency and omits depth attenuation. Domain dodecahedron
 * edges are always near their anchor node, so the depth-fade is wasteful —
 * and the slower rhythm differentiates the parent container's pulse from
 * the faster child data-flow signal.
 *
 * Shared `uTime` uniform driven by the same `_removeEdgeAnim` callback
 * that drives hierarchical edges, so the two systems stay phase-coherent.
 *
 * Brand: `.claude/skills/brand-guidelines/references/3d-visualization.md`
 *        canon F5 extension — domain structural edges as "slow heartbeat"
 */
import * as THREE from 'three';

export const DOMAIN_EDGE_VERTEX = /* glsl */ `
  varying float vLocalDist;
  void main() {
    // Pass local-space radial distance for organic modulation.
    vLocalDist = length(position.xyz);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const DOMAIN_EDGE_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  uniform float uTime;

  varying float vLocalDist;

  void main() {
    // Slow heartbeat — half the frequency of hierarchical child edges.
    // Single sine for a deep, rhythmic pulse (not the two-sine interference
    // used by child edges, which reads as "busy data flow"). The domain
    // container should feel ponderous, steady, authoritative.
    float heartbeat = sin(vLocalDist * 0.3 - uTime * 1.5) * 0.5 + 0.5;

    // Gentler second harmonic for organic warmth — prevents the single sine
    // from reading as sterile.
    float warmth = sin(uTime * 0.8 + vLocalDist * 0.15) * 0.5 + 0.5;
    float organicEnergy = heartbeat * 0.75 + warmth * 0.25;

    // Higher baseline than child edges (0.4 vs 0.3) — the domain container
    // should never fully "dim out" between pulses.
    float opacity = uOpacity * (0.4 + organicEnergy * 0.6);

    // Moderate HDR boost — enough to subtly feed the bloom pass, but less
    // aggressive than child edges (0.5 vs 0.8) so domains don't outshine
    // the data-carrying children.
    vec3 glowingColor = uColor * (1.0 + organicEnergy * 0.5);

    gl_FragColor = vec4(glowingColor, opacity);
  }
`;

/** Create uniforms for the domain edge shader.
 *  Simplified vs `createEdgeDepthUniforms` — no depth range parameters,
 *  because domain structural edges don't need distance-based attenuation
 *  (they stay clustered around their anchor node). */
export function createDomainEdgeUniforms(
  color: number,
  baseOpacity: number,
): Record<string, THREE.IUniform> {
  return {
    uColor: { value: new THREE.Color(color) },
    uOpacity: { value: baseOpacity },
    uTime: { value: 0.0 },
  };
}
