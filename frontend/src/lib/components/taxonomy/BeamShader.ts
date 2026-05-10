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
  /**
   * Leading-edge progress 0..1. During firing this advances from 0 to 1
   * in lockstep with state-time so the beam's catenary curve appears to
   * extend from origin toward target rather than fading in along its
   * full length. Held at 1.0 throughout sustain and terminate.
   *
   * The fragment shader uses it to soft-mask alpha for `vUv.x` past the
   * head. The cluster sees no light until the head reaches the target
   * (head=1.0), which is exactly when the firing→sustain edge fires
   * `onImpact`.
   */
  uHead: THREE.IUniform<number>;
}

export function createBeamUniforms(): Record<string, THREE.IUniform> {
  return {
    uTime: { value: 0.0 },
    uColorStart: { value: new THREE.Color(0x00e5ff) },
    uColorEnd: { value: new THREE.Color(0x00e5ff) },
    uOpacity: { value: 0.0 },
    uFlowSpeed: { value: 2.0 },
    uThickness: { value: 1.0 },
    uHead: { value: 0.0 },
  };
}

export const BEAM_VERTEX_SHADER = /* glsl */ `
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

export const BEAM_FRAGMENT_SHADER = /* glsl */ `
  uniform float uTime;
  uniform vec3 uColorStart;
  uniform vec3 uColorEnd;
  uniform float uOpacity;
  uniform float uFlowSpeed;
  uniform float uThickness;
  uniform float uHead;

  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  void main() {
    vec3 color = mix(uColorStart, uColorEnd, vUv.x);

    // Speed modifiers
    float streamSpeed = uTime * uFlowSpeed * 1.5;

    // Smooth fluid overlapping sine waves
    float wave1 = sin(vUv.x * 15.0 - streamSpeed) * 0.5 + 0.5;
    float wave2 = sin(vUv.x * 25.0 - streamSpeed * 1.8 + vUv.y * 12.0) * 0.5 + 0.5;
    float wave3 = sin(vUv.x * 5.0 - streamSpeed * 0.5 - vUv.y * 6.0) * 0.5 + 0.5;

    float fluid = (wave1 * 0.5 + wave2 * 0.3 + wave3 * 0.2);

    // Soft radial core (glow) - assuming y maps around the circumference or across the width
    // With TUBULAR_SEGMENTS=24 and RADIAL_SEGMENTS=12, vUv.y goes 0 to 1 around the tube
    // We want a glowing core that looks volumetric.
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float fresnel = dot(normal, viewDir);
    float core = smoothstep(0.0, 0.8, fresnel); // Brighter in the center facing the camera
    float rim = smoothstep(0.6, 1.0, 1.0 - fresnel) * 0.5; // Soft rim light

    // Muzzle / Injection Flash
    float muzzle = pow(1.0 - vUv.x, 8.0) * 1.2;

    // Combine organic fluid components
    float energy = clamp((core * 1.2 + fluid * 0.8 + rim + muzzle), 0.0, 1.5);

    // Length-wise fade in/out - smooth bounds
    float smoothFade = smoothstep(0.0, 0.1, vUv.x) * smoothstep(1.0, 0.8, vUv.x);

    // Progressive head — uHead advances 0..1 during the firing phase so
    // the visible portion of the beam extends along the catenary toward
    // the target. The smoothstep gives a soft 4%-wide trailing edge at
    // the leading position so the head isn't a hard cutoff. Past the
    // head, alpha is zeroed entirely. Held at 1.0 during sustain +
    // terminate so the full beam is visible.
    float headMask = smoothstep(uHead + 0.04, uHead - 0.02, vUv.x);

    // Additive alpha based on energy, fade, and the leading-edge mask
    float alpha = energy * uOpacity * smoothFade * uThickness * headMask;

    // Boost color intensity to feed the UnrealBloomPass for a radiant core
    vec3 glowingColor = color * (1.0 + energy * 0.8);

    gl_FragColor = vec4(glowingColor, alpha);
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
    
    // Smooth spherical shockwave expanding outward
    float dist = length(position.xyz);
    // Expand the wave radius based on uRipple (uRipple decays from 1 to 0, so wave travels outward)
    float waveRadius = (1.0 - uRipple) * 3.0; 
    float wave = smoothstep(0.5, 0.0, abs(dist - waveRadius));
    
    // Organic fluid displacement
    float organic = sin(position.x * 12.0) * cos(position.y * 12.0 + uRipple * 10.0);
    float displacement = wave * (0.3 + organic * 0.2) * uRipple;
    
    vec3 displaced = position + normal * displacement;
    
    gl_Position = projectionMatrix * modelViewMatrix * vec4(displaced, 1.0);
  }
`;

export const RIPPLE_FRAGMENT_SHADER = /* glsl */ `
  uniform vec3 uColor;
  uniform float uRipple;
  uniform float uOpacity;

  void main() {
    // Keep absolute brand color, just flash the opacity on ripple impact
    float flashOpacity = min(1.0, uOpacity + uRipple * 0.6);
    
    // Boost color intensity to feed the UnrealBloomPass on impact
    vec3 flashColor = uColor * (1.0 + uRipple * 1.2);
    
    gl_FragColor = vec4(flashColor, flashOpacity);
  }
`;
