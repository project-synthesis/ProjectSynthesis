// frontend/src/lib/components/taxonomy/builders/EdgeBuilder.ts
//
// Sub-project D Cycle 3 — RED-phase stub. Full implementation lands in
// Task 12 (GREEN) per spec §3.3. Stub is functional-but-incorrect so the
// 15 RED tests fail on assertion shape (not import error).

import type * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';

export class EdgeBuilder implements SceneBuilder {
  build(_data: SceneData, _scene: THREE.Scene, _ctx: BuilderContext): void {}
  dispose(): void {}
}
