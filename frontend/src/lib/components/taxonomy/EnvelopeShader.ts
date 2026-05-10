// frontend/src/lib/components/taxonomy/EnvelopeShader.ts
//
// Plasma-envelopement shader. Reuses the BeamShader's multi-wave fluid +
// fresnel rim glow pattern so the envelope material is visually continuous
// with the beam — the user sees the same plasma medium engulfing the node
// that just travelled the tether to it.
//
// Adapted for spherical surfaces (icosahedron / dodecahedron node fills):
//   - No muzzle flash — the `pow(1 - vUv.x, 8)` hot-spot is tube-emitter-end
//     specific; on a sphere it would create a one-pole hot patch.
//   - No length-wise smoothFade — `smoothstep(0, 0.1, vUv.x)` would create a
//     visible UV seam at the wraparound. Rely on `uOpacity` alone for fade.
//   - Same fresnel rim glow — geometry-agnostic, renders silhouette correctly
//     on any closed surface.
//
// Material settings: AdditiveBlending, depthWrite=false, transparent=true,
// FrontSide. Renders inside the bloom pass so peak intensity blooms naturally.
import * as THREE from 'three';

export interface EnvelopeUniforms {
  uTime: THREE.IUniform<number>;
  uColorStart: THREE.IUniform<THREE.Color>;
  uColorEnd: THREE.IUniform<THREE.Color>;
  uOpacity: THREE.IUniform<number>;
  uFlowSpeed: THREE.IUniform<number>;
}

export function createEnvelopeUniforms(): Record<string, THREE.IUniform> {
  return {
    uTime: { value: 0.0 },
    uColorStart: { value: new THREE.Color(0x00e5ff) },
    uColorEnd: { value: new THREE.Color(0x00e5ff) },
    uOpacity: { value: 0.0 },
    uFlowSpeed: { value: 2.0 },
  };
}

export const ENVELOPE_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  void main() {
    vUv = uv;
    vNormal = normalize(normalMatrix * normal);
    vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
    vViewPosition = -mvPosition.xyz;
    gl_Position = projectionMatrix * mvPosition;
  }
`;

export const ENVELOPE_FRAGMENT_SHADER = /* glsl */ `
  uniform float uTime;
  uniform vec3 uColorStart;
  uniform vec3 uColorEnd;
  uniform float uOpacity;
  uniform float uFlowSpeed;

  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  void main() {
    // Same color-mix structure as BeamShader for visual continuity. On a
    // sphere, vUv.x crosses the surface — color gradient reads as "energy
    // arriving from one side", which works with the beam's directional
    // approach.
    vec3 color = mix(uColorStart, uColorEnd, vUv.x);

    float streamSpeed = uTime * uFlowSpeed * 1.5;

    // Multi-wave fluid pattern — identical frequencies + weights to
    // BeamShader, so the envelope's plasma turbulence reads as the same
    // medium. UV coordinates here map across the spherical surface rather
    // than along a tube length, but the additive composition produces a
    // similar shimmering plasma look.
    float wave1 = sin(vUv.x * 15.0 - streamSpeed) * 0.5 + 0.5;
    float wave2 = sin(vUv.x * 25.0 - streamSpeed * 1.8 + vUv.y * 12.0) * 0.5 + 0.5;
    float wave3 = sin(vUv.x * 5.0 - streamSpeed * 0.5 - vUv.y * 6.0) * 0.5 + 0.5;
    float fluid = (wave1 * 0.5 + wave2 * 0.3 + wave3 * 0.2);

    // Fresnel rim glow — geometry-agnostic, brightens silhouette where the
    // surface is most parallel to the view direction. Wraps the node
    // visually without obscuring the fill mesh inside.
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float fresnel = dot(normal, viewDir);
    float core = smoothstep(0.0, 0.8, fresnel);
    float rim = smoothstep(0.6, 1.0, 1.0 - fresnel) * 0.5;

    // Combine — note no muzzle flash (drop the tube-emitter pow(1-vUv.x, 8)
    // term) and no length-wise smoothFade (drop the seam-creating
    // smoothstep(0, 0.1, vUv.x)). uOpacity drives all phase blending.
    float energy = clamp((core * 1.2 + fluid * 0.8 + rim), 0.0, 1.5);
    float alpha = energy * uOpacity;

    // Boost color for bloom pass — same multiplier as BeamShader.
    vec3 glowingColor = color * (1.0 + energy * 0.8);
    gl_FragColor = vec4(glowingColor, alpha);
  }
`;
