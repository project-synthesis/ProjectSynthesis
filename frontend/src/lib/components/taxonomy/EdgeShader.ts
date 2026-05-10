/**
 * Depth-attenuated edge shader for topology hierarchical edges.
 *
 * Fades edges based on distance from camera — background edges become
 * near-invisible, giving the 3D depth natural z-culling for visual clarity.
 */
import * as THREE from 'three';

export const EDGE_DEPTH_VERTEX = /* glsl */ `
  varying float vDepth;
  void main() {
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vDepth = -mvPosition.z;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

export const EDGE_DEPTH_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uBaseOpacity;
  uniform float uNearDist;
  uniform float uFarDist;
  uniform float uMaxReduction;
  uniform float uTime;

  varying float vDepth;

  void main() {
    // Synaptic data tendons (canon F5). Edges aren't static lines — they
    // are flowing energy paths between domains. The shader composes two
    // sine interferences (slow flow + faster pulse) into an "organic
    // energy" amplitude that modulates opacity AND boosts color
    // intensity above 1.0 so the UnrealBloomPass picks it up dynamically
    // as the data flows.
    float t = clamp((vDepth - uNearDist) / (uFarDist - uNearDist), 0.0, 1.0);

    // Two-sine interference for organic flow (not a sterile cycle).
    float flow = sin(vDepth * 0.4 - uTime * 3.0) * 0.5 + 0.5;
    float pulse = sin(uTime * 2.0 + vDepth * 0.1) * 0.5 + 0.5;
    float organicEnergy = flow * 0.7 + pulse * 0.3;

    // Depth attenuation × organic envelope. The 0.3 baseline prevents
    // edges from disappearing entirely between pulses.
    float opacity = uBaseOpacity * (1.0 - t * uMaxReduction) * (0.3 + organicEnergy * 0.7);

    // HDR color boost — multiplies above 1.0 into the EffectComposer
    // bloom pass, producing dynamic glow as energy peaks cross the
    // bloom threshold (canon F13).
    vec3 glowingColor = uColor * (1.0 + organicEnergy * 0.8);

    gl_FragColor = vec4(glowingColor, opacity);
  }
`;

/** Create uniforms for the edge depth shader.
 *  Camera range: starts at z=80, auto-focuses to ~60, zoom range 3-200.
 *  Proportional model: far edges render at (1 - maxReduction) of base opacity.
 *  With maxReduction=0.25: near=full base, far=75% of base — subtle depth cue
 *  that doesn't compete with density opacity for visibility control. */
export function createEdgeDepthUniforms(color: number, baseOpacity: number) {
  return {
    uColor: { value: new THREE.Color(color) },
    uBaseOpacity: { value: baseOpacity },
    uNearDist: { value: 30.0 },
    uFarDist: { value: 120.0 },
    uMaxReduction: { value: 0.25 },
    uTime: { value: 0.0 },
  };
}
