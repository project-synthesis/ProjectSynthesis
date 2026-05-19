// frontend/src/lib/components/taxonomy/SelectionController.ts
// Sub-project E — selection state machine (stub for Cycle 2 RED).
import * as THREE from 'three';
import type { TopologyRenderer } from './TopologyRenderer';
import type { AnimationCoordinator } from './AnimationCoordinator';
import type { ImpactCoordinator } from './ImpactCoordinator';
import type { SceneNode } from './TopologyData';
import { FlashController } from './FlashController';

export type SelectionState = 'idle' | 'focusing' | 'focused' | 'impacting' | 'engulfed';

export interface SelectionControllerDeps {
  renderer: TopologyRenderer;
  animationCoordinator: AnimationCoordinator;
  impactCoordinator: ImpactCoordinator;
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  getSceneNode: (id: string) => SceneNode | undefined;
  getBeamGroup: (id: string) => THREE.Group | undefined;
  getBaseEmissive: (id: string) => number | undefined;
  highlightColor: number;
}

export const SELECTION_EMISSIVE_FLOOR = 1.0;

// FlashController is imported because GREEN (Task 2.2) wires SC to construct
// an FC instance internally. Reference it here to keep the import live for
// the stub compile (Cycle 2 RED — the stub throws but the import is real).
void FlashController;

export class SelectionController {
  constructor(_deps: SelectionControllerDeps) {
    throw new Error('SelectionController not yet implemented');
  }
  get state(): SelectionState {
    throw new Error('not implemented');
  }
  get selectedId(): string | null {
    throw new Error('not implemented');
  }
  isEngulfed(_id: string): boolean {
    throw new Error('not implemented');
  }
  select(_id: string | null): void {
    throw new Error('not implemented');
  }
  deselect(): void {
    throw new Error('not implemented');
  }
  onImpact(_id: string): void {
    throw new Error('not implemented');
  }
  afterRebuild(): void {
    throw new Error('not implemented');
  }
  flash(_id: string, _c: THREE.Color): void {
    throw new Error('not implemented');
  }
  isFlashActive(_id: string): boolean {
    throw new Error('not implemented');
  }
  dispose(): void {
    throw new Error('not implemented');
  }
}
