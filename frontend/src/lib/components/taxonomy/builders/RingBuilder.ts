// frontend/src/lib/components/taxonomy/builders/RingBuilder.ts
//
// Sub-project D Cycle 4 — RED stub (replaced in GREEN).
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { SceneBuilder } from './SceneBuilder';
import type { BuilderContext } from './BuilderContext';
import type { ReadinessTier } from '../readiness-tier';

/** RingEntry as specified by spec §3.3 + rev-2 accessor canon. */
export interface ReadinessRingEntry {
  mesh: THREE.Mesh;
  material: THREE.MeshBasicMaterial;
  lastTier: ReadinessTier;
  lastSize: number;
  domain: string;
  nodeOpacity: number;
  tween: { cancel(): void } | null;
}

/**
 * RED stub — pinned to fail every Cycle-4 RingBuilder.test.ts assertion until
 * GREEN replaces with the spec §3.3 implementation. Every public method
 * throws so failures surface as `Error` rather than silent `undefined`.
 */
export class RingBuilder implements SceneBuilder {
  build(_data: SceneData, _scene: THREE.Scene, _ctx: BuilderContext): void {
    throw new Error('RingBuilder.build — not implemented (RED stub)');
  }

  dispose(): void {
    throw new Error('RingBuilder.dispose — not implemented (RED stub)');
  }

  getTemplateRing(_id: string): THREE.Mesh | undefined {
    throw new Error('RingBuilder.getTemplateRing — not implemented (RED stub)');
  }

  getReadinessRing(_id: string): ReadinessRingEntry | undefined {
    throw new Error('RingBuilder.getReadinessRing — not implemented (RED stub)');
  }

  readinessRingCount(): number {
    throw new Error('RingBuilder.readinessRingCount — not implemented (RED stub)');
  }

  readinessRingIds(): string[] {
    throw new Error('RingBuilder.readinessRingIds — not implemented (RED stub)');
  }
}
