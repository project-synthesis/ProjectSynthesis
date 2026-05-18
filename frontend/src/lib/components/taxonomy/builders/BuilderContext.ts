// frontend/src/lib/components/taxonomy/builders/BuilderContext.ts
//
// Shared per-rebuild state explicitly threaded through every builder.
// Decouples builders from SemanticTopology.svelte's internal Map/array
// state. Trivially mockable in tests via the createBuilderContext()
// factory.

import * as THREE from 'three';

export interface BuilderContext {
  /**
   * Maps cluster/domain nodeId → primary fill Mesh.
   * Written by ClusterBuilder + DomainBuilder; read by EdgeBuilder
   * (endpoint position lookups), RingBuilder (readiness ring placement),
   * and downstream consumers (e.g., per-frame breathing handler).
   */
  nodeMeshes: Map<string, THREE.Mesh>;
  /**
   * Maps nodeId → parent Group. Used by the impact coordinator + beam
   * pool to acquire beams against the right group.
   */
  beamNodeGroups: Map<string, THREE.Group>;
  /**
   * Per-rebuild edge-shader uniform records. EdgeBuilder pushes to this
   * array; the breathing/ambient per-frame handler reads `uTime.value` to
   * drive the F5 hierarchical-edge pulse.
   */
  edgeUniforms: Array<Record<string, THREE.IUniform>>;
  /**
   * Top-level domain groups. DomainBuilder pushes; the domain-rotation
   * per-frame handler reads `g.rotation.y` to advance.
   */
  domainGroups: THREE.Group[];
  /**
   * Per-cluster shader material refs (for canon F5 edge pulse + ripple
   * uniform writes from the impact coordinator's physics callback).
   * ClusterBuilder constructs + populates; ImpactCoordinator reads.
   *
   * Replaces the prior implementation that walked `group.children[1]` to
   * find the wire mesh (current line ~2421 in SemanticTopology.svelte's
   * physics handler). Cycle 1 + Cycle 6 migrate that handler to read via
   * this map; integration test INT-6 pins the ImpactCoordinator wiring.
   */
  clusterShaderMaterials: Map<string, THREE.ShaderMaterial>;
  /**
   * Per-node phase offset for desynchronized organic breathing (canon T1.1).
   * Set by ClusterBuilder + DomainBuilder during construction; read by
   * the breathing-phase callback.
   */
  nodePhaseOffsets: Map<string, number>;
  /**
   * Top-level scene-node lookup. Populated by the orchestrator from
   * `data.nodes` BEFORE the first builder runs. Read by every builder
   * + downstream consumers.
   */
  sceneNodeMap: Map<string, import('../TopologyData').SceneNode>;
}

/**
 * Factory for a fresh BuilderContext at the start of each rebuildScene.
 * Each call returns a new context with empty collections — builders mutate
 * during build(). The orchestrator pre-populates sceneNodeMap from
 * data.nodes before invoking the first builder.
 */
export function createBuilderContext(): BuilderContext {
  return {
    nodeMeshes: new Map(),
    beamNodeGroups: new Map(),
    edgeUniforms: [],
    domainGroups: [],
    clusterShaderMaterials: new Map(),
    nodePhaseOffsets: new Map(),
    sceneNodeMap: new Map(),
  };
}
