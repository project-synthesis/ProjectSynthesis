// frontend/src/lib/components/taxonomy/ImpactCoordinator.ts
//
// Sub-project C — RED-phase stub. Full implementation lands in Task 2 (GREEN)
// per spec §3.1. Stub is functional-but-incorrect so the 22 RED tests
// fail on assertion shape (not on import error).
//
// Spec: docs/superpowers/specs/2026-05-17-impact-coordinator-design.md

import type * as THREE from 'three';
import type { BeamPool } from './BeamPool';
import type { EnvelopePool } from './EnvelopePool';
import type { ClusterPhysics } from './ClusterPhysics';
import type { AnimationCoordinator } from './AnimationCoordinator';
import type { TopologyRenderer } from './TopologyRenderer';
import type { SceneNode } from './TopologyData';

export type Trigger = 'entrance' | 'post-growth' | 'optimization' | 'click';

export interface TriggerPreset {
  sustainMs: number;
  thicknessMultiplier: number;
  applySizeFactor: boolean;
  applyClickRadiusFloor: boolean;
  kineticDisplacement: boolean;
  sizeFactorSustainBonus: boolean;
  marksEngulfed: boolean;
}

// Stub TRIGGER_PRESETS — placeholder values; correct values land in GREEN.
export const TRIGGER_PRESETS: Readonly<Record<Trigger, Readonly<TriggerPreset>>> = {
  entrance: { sustainMs: 0, thicknessMultiplier: 1.0, applySizeFactor: false, applyClickRadiusFloor: false, kineticDisplacement: false, sizeFactorSustainBonus: false, marksEngulfed: false },
  'post-growth': { sustainMs: 0, thicknessMultiplier: 1.0, applySizeFactor: false, applyClickRadiusFloor: false, kineticDisplacement: false, sizeFactorSustainBonus: false, marksEngulfed: false },
  optimization: { sustainMs: 0, thicknessMultiplier: 1.0, applySizeFactor: false, applyClickRadiusFloor: false, kineticDisplacement: false, sizeFactorSustainBonus: false, marksEngulfed: false },
  click: { sustainMs: 0, thicknessMultiplier: 1.0, applySizeFactor: false, applyClickRadiusFloor: false, kineticDisplacement: false, sizeFactorSustainBonus: false, marksEngulfed: false },
};

export const SELECTION_EMISSIVE_FLOOR = 1.0;

export interface ImpactRequest {
  trigger: Trigger;
  node: SceneNode;
  group: THREE.Group;
}

export interface ImpactCoordinatorDeps {
  beamPool: BeamPool;
  envelopePool: EnvelopePool;
  clusterPhysics: ClusterPhysics;
  flashEmissive: (nodeId: string, color: THREE.Color) => void;
  getSceneNode: (id: string) => SceneNode | undefined;
  getBeamGroup: (id: string) => THREE.Group | undefined;
  getNodeMesh: (id: string) => THREE.Mesh | undefined;
  getSelectedId: () => string | null;
  isFlashActive: (id: string) => boolean;
  renderer: TopologyRenderer;
  animationCoordinator: AnimationCoordinator;
}

export class ImpactCoordinator {
  constructor(_deps: ImpactCoordinatorDeps) {
    // Stub: no registration; tests assert presence and will fail.
  }
  fire(_request: ImpactRequest): void {}
  isEngulfed(_nodeId: string): boolean { return false; }
  clearEngulfed(_id?: string): void {}
  dispose(): void {}
}
