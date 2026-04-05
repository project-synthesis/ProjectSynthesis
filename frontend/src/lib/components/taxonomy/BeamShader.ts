// frontend/src/lib/components/taxonomy/BeamShader.ts
import * as THREE from 'three';

/** Uniform types for the plasma beam ShaderMaterial. */
export interface BeamUniforms {
  uTime: THREE.IUniform<number>;
  uColorStart: THREE.IUniform<THREE.Color>;
  uColorEnd: THREE.IUniform<THREE.Color>;
  uOpacity: THREE.IUniform<number>;
  uFlowSpeed: THREE.IUniform<number>;
  uThickness: THREE.IUniform<number>;
}

export function createBeamUniforms(): Record<string, THREE.IUniform> {
  return {
    uTime: { value: 0.0 },
    uColorStart: { value: new THREE.Color(0x00e5ff) },
    uColorEnd: { value: new THREE.Color(0x00e5ff) },
    uOpacity: { value: 0.0 },
    uFlowSpeed: { value: 2.0 },
    uThickness: { value: 1.0 },
  };
}

export const BEAM_VERTEX_SHADER = /* glsl */ `
  varying vec2 vUv;

  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

export const BEAM_FRAGMENT_SHADER = /* glsl */ `
  uniform float uTime;
  uniform vec3 uColorStart;
  uniform vec3 uColorEnd;
  uniform float uOpacity;
  uniform float uFlowSpeed;
  uniform float uThickness;

  varying vec2 vUv;

  void main() {
    vec3 color = mix(uColorStart, uColorEnd, vUv.x);
    float scroll = vUv.x - uTime * uFlowSpeed;
    float pulse = 0.5 + 0.5 * sin(scroll * 12.0);
    float energy = 0.6 + 0.4 * pulse;
    float radial = abs(vUv.y - 0.5) * 2.0;
    float falloff = 1.0 - smoothstep(0.0, 1.0, radial * uThickness);
    float alpha = energy * falloff * uOpacity;
    gl_FragColor = vec4(color * energy, alpha);
  }
`;

/** Uniform types for the cluster wireframe ripple ShaderMaterial. */
export interface RippleUniforms {
  uColor: THREE.IUniform<THREE.Color>;
  uOpacity: THREE.IUniform<number>;
  uRipple: THREE.IUniform<number>;
}

export function createRippleUniforms(): Record<string, THREE.IUniform> {
  return {
    uColor: { value: new THREE.Color(0xffffff) },
    uOpacity: { value: 1.0 },
    uRipple: { value: 0.0 },
  };
}

export const RIPPLE_VERTEX_SHADER = /* glsl */ `
  uniform float uRipple;
  varying vec2 vUv;

  void main() {
    vUv = uv;
    vec3 displaced = position + normal * uRipple * 0.15;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
  }
`;

export const RIPPLE_FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;

  void main() {
    gl_FragColor = vec4(uColor, uOpacity);
  }
`;
