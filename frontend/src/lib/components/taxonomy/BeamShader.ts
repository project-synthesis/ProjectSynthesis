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

  varying vec2 vUv;
  varying vec3 vNormal;
  varying vec3 vViewPosition;

  void main() {
    vec3 color = uColorStart; // Exact color of the node
    
    // Speed modifiers
    float streamSpeed = uTime * uFlowSpeed * 1.5; 
    
    // 1. Structural Geometric Edges (Wireframe aesthetic merging with domain aesthetic)
    // Create sharp straight lines instead of smooth curves by stepping over the radial UV map.
    // vUv.y maps around the beam outline (8 sides).
    float edgeLine = step(0.90, fract(vUv.y * 8.0)); // Thinner, sharper lines
    
    // 2. High-speed Hexagonal Data Packets travelling strictly along the geometric structure
    float packetLong = step(0.85, fract(vUv.x * 10.0 - streamSpeed));
    float packetShort = step(0.97, fract(vUv.x * 25.0 - streamSpeed * 1.8));
    float dataStream = max(packetLong * 0.7, packetShort);
    
    // 3. Angular helix (step function forces it into rings/hexagons)
    // Using floor to lock it to discrete geometric steps
    float geoSteps = floor(vUv.y * 8.0) / 8.0;
    float helix = step(0.90, fract(vUv.x * 8.0 + geoSteps * 4.0 - streamSpeed * 0.7));
    
    // 4. Sharp Fresnel Contour (simulates 1px neon glass rim)
    vec3 normal = normalize(vNormal);
    vec3 viewDir = normalize(vViewPosition);
    float fresnel = 1.0 - abs(dot(normal, viewDir));
    float rimLine = step(0.85, fresnel); // very sharp step to match precise wireframes
    
    // Muzzle / Injection Flash
    float muzzle = pow(1.0 - vUv.x, 20.0) * 1.5;

    // Combine structural components
    // Emphasize the geometric wiring and neon structure over soft gradient emission
    float basePresence = 0.05; 
    float structure = (edgeLine * 0.5) + (dataStream * 1.2) + (helix * 0.8) + (rimLine * 0.8) + muzzle;
    
    // Keep raw color to match strictly to domain
    float energy = clamp(basePresence + structure, 0.0, 1.0);
    
    // Length-wise fade in/out - extremely sharp bounds
    float sharpFade = step(0.01, vUv.x) * step(vUv.x, 0.99);
    
    // Clamp alpha output
    float alpha = energy * uOpacity * sharpFade * step(0.5, uThickness);
    
    gl_FragColor = vec4(color, alpha);
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
    // Digital jitter scale on impact instead of smooth ballooning
    float noise = fract(sin(dot(position.xyz, vec3(12.9898, 78.233, 45.164))) * 43758.5453);
    float jitter = 0.3 + 0.7 * noise; // erratic offset based on vertex position
    
    vec3 displaced = position + normal * uRipple * (0.15 * jitter);
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
    gl_FragColor = vec4(uColor, flashOpacity);
  }
`;
