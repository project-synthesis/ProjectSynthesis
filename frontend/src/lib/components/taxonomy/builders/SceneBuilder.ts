// frontend/src/lib/components/taxonomy/builders/SceneBuilder.ts
import type * as THREE from 'three';
import type { SceneData } from '../TopologyData';
import type { BuilderContext } from './BuilderContext';

/**
 * Every scene-construction concern in rebuildScene splits into a
 * SceneBuilder. Builders are stateless across rebuilds except for their
 * persistent parent groups (set userData.persistent = true at lazy
 * construction; survive cleanupScene via Sub-project A's persistence
 * contract).
 *
 * Build order is fixed (see spec §3.4): clusters → domains → edges →
 * rings → dust. Edges depend on cluster + domain mesh positions written
 * to ctx.nodeMeshes; rings depend on the same; dust is order-independent
 * but conventionally runs last.
 *
 * dispose() is called on component unmount via the cleanup return in
 * SemanticTopology.svelte. Builders release their persistent parents
 * (if any) + clear internal pool maps. Per-rebuild ephemeral children
 * are disposed by cleanupScene (Sub-project A) at the START of the next
 * rebuild — builders do NOT need to dispose ephemeral children
 * themselves.
 */
export interface SceneBuilder {
  /**
   * Construct this builder's scene children for the given SceneData.
   * Writes shared state to ctx as documented per-builder.
   * Invariant: idempotent across rebuilds (calling build() N times
   * produces the same scene state as calling it once with the final
   * SceneData) IF cleanupScene runs between rebuilds.
   */
  build(data: SceneData, scene: THREE.Scene, ctx: BuilderContext): void;

  /**
   * Release persistent parent groups + clear internal state.
   * Idempotent; subsequent calls no-op. Called on SemanticTopology
   * unmount.
   */
  dispose(): void;
}
