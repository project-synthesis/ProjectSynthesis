// frontend/src/lib/components/taxonomy/builders/DustBuilder.ts
//
// Sub-project D Cycle 5 RED stub — intentionally fails every test in
// DustBuilder.test.ts so the RED→GREEN cycle stays disciplined. Replaced
// verbatim by the spec §3.3 implementation in Cycle 5 GREEN.
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';

export class DustBuilder implements SceneBuilder {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  build(_data: SceneData, _scene: THREE.Scene, _ctx: BuilderContext): void {
    // RED: no-op stub.
  }

  dispose(): void {
    // RED: no-op stub.
  }

  dustPoints(): THREE.Points | null {
    return null;
  }
}
