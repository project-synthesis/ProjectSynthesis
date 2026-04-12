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
  uniform float uMinOpacity;

  varying float vDepth;

  void main() {
    float t = clamp((vDepth - uNearDist) / (uFarDist - uNearDist), 0.0, 1.0);
    float opacity = mix(uBaseOpacity, uMinOpacity, t);
    gl_FragColor = vec4(uColor, opacity);
  }
`;

/** Create uniforms for the edge depth shader. */
export function createEdgeDepthUniforms(color: number, baseOpacity: number) {
  return {
    uColor: { value: new THREE.Color(color) },
    uBaseOpacity: { value: baseOpacity },
    uNearDist: { value: 10.0 },
    uFarDist: { value: 80.0 },
    uMinOpacity: { value: 0.03 },
  };
}
