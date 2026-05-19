// frontend/src/lib/components/taxonomy/FlashController.ts
// Sub-project E — extracted from inline flashEmissive + _tickFlashStates in SemanticTopology.svelte.
import * as THREE from 'three';
import type { AnimationCoordinator } from './AnimationCoordinator';

export interface FlashState {
  startTime: number;
  baselineEmissive: number;
  startIntensity: number;
  domainEmissive: THREE.Color;
}

export interface FlashControllerDeps {
  animationCoordinator: AnimationCoordinator;
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  isHighlighted: (id: string) => boolean;
}

export class FlashController {
  constructor(_deps: FlashControllerDeps) {
    throw new Error('FlashController not yet implemented');
  }
  flash(_nodeId: string, _color: THREE.Color): void {
    throw new Error('not implemented');
  }
  isActive(_nodeId: string): boolean {
    throw new Error('not implemented');
  }
  dispose(): void {
    throw new Error('not implemented');
  }
}
