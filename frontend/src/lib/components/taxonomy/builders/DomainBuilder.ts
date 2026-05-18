// frontend/src/lib/components/taxonomy/builders/DomainBuilder.ts
//
// Sub-project D Cycle 2 — RED-phase stub. Full implementation lands in
// Task 7 (GREEN) per spec §3.3. Stub is functional-but-incorrect so the
// 15 RED tests fail on assertion shape (not import error).
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import type * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { TopologyInteraction } from '../TopologyInteraction';

export class DomainBuilder implements SceneBuilder {
  constructor(private readonly _interaction: TopologyInteraction | null) {
    // Stub: stores dep; GREEN wires registerNode.
    void this._interaction;
  }
  build(_data: SceneData, _scene: THREE.Scene, _ctx: BuilderContext): void {}
  dispose(): void {}
}
