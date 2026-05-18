// frontend/src/lib/components/taxonomy/builders/ClusterBuilder.ts
//
// Sub-project D Cycle 1 — RED-phase stub. Full implementation lands in
// Task 2 (GREEN) per spec §3.3. Stub is functional-but-incorrect so the
// 17 RED tests fail on assertion shape (not import error).
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import type * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { TopologyInteraction } from '../TopologyInteraction';

export class ClusterBuilder implements SceneBuilder {
  constructor(private readonly _interaction: TopologyInteraction | null) {
    // Stub: stores dep but does not use it. GREEN wires registerNode.
    void this._interaction;
  }

  build(_data: SceneData, _scene: THREE.Scene, _ctx: BuilderContext): void {
    // Stub: no scene mutation. Tests assert ctx.nodeMeshes population +
    // scene.children counts; both fail because this body is empty.
  }

  dispose(): void {
    // Stub: no internal state. Idempotency test still passes — dispose() is
    // a no-op which is by definition idempotent.
  }
}
