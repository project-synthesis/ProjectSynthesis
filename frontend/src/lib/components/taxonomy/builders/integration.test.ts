// frontend/src/lib/components/taxonomy/builders/integration.test.ts
// Multi-builder integration tests. Cycle 1 contribution: ClusterBuilder.
// Subsequent cycles (Domain/Edge/Ring/Dust) append their own cases.
import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import { ClusterBuilder } from './ClusterBuilder';
import { createBuilderContext } from './BuilderContext';
import type { SceneData } from '../TopologyData';

function makeCluster(id: string, size = 5) {
  return {
    id, label: id, color: '#ff00ff', state: 'active' as const, size,
    position: [0, 0, 0], members: ['m1', 'm2', 'm3'],
    metaPatterns: [], score: 7.5, coherence: 0.8, taskType: 'general',
    // SceneData-shape fields ClusterBuilder reads during build:
    visible: true, opacity: 1.0, memberCount: 3, avgScore: 7.5,
    isSubDomain: false,
  };
}

describe('ClusterBuilder — integration with real THREE.Scene', () => {
  it('INT-cluster-1: build adds ephemeral cluster group with fill + wireframe children to scene', () => {
    const scene = new THREE.Scene();
    const cb = new ClusterBuilder(null);
    const ctx = createBuilderContext();
    const data: SceneData = { nodes: [makeCluster('c1')], edges: [] } as unknown as SceneData;
    ctx.sceneNodeMap.set('c1', data.nodes[0]);

    cb.build(data, scene, ctx);

    expect(scene.children.length).toBe(1);
    const group = scene.children[0] as THREE.Group;
    expect(group.type).toBe('Group');
    // group should NOT have userData.persistent flag — cluster groups are ephemeral
    expect(group.userData.persistent).toBeFalsy();
    // group should contain Mesh + LineSegments children
    const meshChild = group.children.find((c) => c.type === 'Mesh');
    const wireChild = group.children.find((c) => c.type === 'LineSegments');
    expect(meshChild).toBeDefined();
    expect(wireChild).toBeDefined();
  });

  it('INT-cluster-2: build followed by dispose leaves no ClusterBuilder-owned state in ctx', () => {
    const scene = new THREE.Scene();
    const cb = new ClusterBuilder(null);
    const ctx = createBuilderContext();
    const data: SceneData = { nodes: [makeCluster('c1'), makeCluster('c2')], edges: [] } as unknown as SceneData;
    ctx.sceneNodeMap.set('c1', data.nodes[0]);
    ctx.sceneNodeMap.set('c2', data.nodes[1]);

    cb.build(data, scene, ctx);
    expect(ctx.nodeMeshes.size).toBe(2);

    cb.dispose();
    // dispose is idempotent + clears any builder-internal state; ctx
    // ownership transfers to the orchestrator (which will recreate ctx
    // per rebuild). Builder dispose should NOT mutate ctx the caller
    // owns.
    expect(ctx.nodeMeshes.size).toBe(2); // ctx state survives — orchestrator owns
  });
});
