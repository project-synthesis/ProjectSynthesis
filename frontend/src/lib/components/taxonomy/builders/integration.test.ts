// frontend/src/lib/components/taxonomy/builders/integration.test.ts
// Multi-builder integration tests. Cycle 1 contribution: ClusterBuilder.
// Subsequent cycles (Domain/Edge/Ring/Dust) append their own cases.
import { describe, it, expect, beforeEach } from 'vitest';
import * as THREE from 'three';
import { ClusterBuilder } from './ClusterBuilder';
import { DomainBuilder } from './DomainBuilder';
import { EdgeBuilder } from './EdgeBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';
import type { SceneData, SceneNode } from '../TopologyData';

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

function makeNode(id: string, overrides: Partial<SceneNode> = {}): SceneNode {
  return {
    id,
    position: [0, 0, 0],
    color: '#00ffaa',
    size: 1.5,
    opacity: 1.0,
    persistence: 1.0,
    state: 'active',
    label: id,
    visible: true,
    coherence: 0.5,
    avgScore: null,
    domain: 'backend',
    memberCount: 4,
    isSubDomain: false,
    template_count: 0,
    ...overrides,
  } as SceneNode;
}

function makeData(nodes: SceneNode[]): SceneData {
  return { nodes, edges: [] } as SceneData;
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

describe('builders/integration — Cycle 2 domain sequences', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  it('Cycle-2-A: Cluster + Domain both build → ctx.nodeMeshes has both; userData.isStructural correct on each group', () => {
    const cluster = makeNode('c1', { state: 'active', color: '#ff00ff' });
    const domain = makeNode('d1', { state: 'domain', position: [3, 0, 0] });
    const data = makeData([cluster, domain]);
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(ctx.nodeMeshes.size).toBe(2);
    expect(scene.children.length).toBe(2);
    const groups = scene.children as THREE.Group[];
    const clusterGroup = groups.find((g) => g.userData.isStructural === false);
    const domainGroup = groups.find((g) => g.userData.isStructural === true);
    expect(clusterGroup).toBeDefined();
    expect(domainGroup).toBeDefined();
  });

  it('Cycle-2-B: DomainBuilder pushes domain-edge uniforms into shared ctx.edgeUniforms', () => {
    const domain1 = makeNode('d1', { state: 'domain' });
    const domain2 = makeNode('d2', { state: 'domain', position: [3, 0, 0] });
    const data = makeData([domain1, domain2]);
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new DomainBuilder(null).build(data, scene, ctx);
    // Two domain nodes → two domain-edge uniform records pushed.
    expect(ctx.edgeUniforms.length).toBe(2);
    for (const u of ctx.edgeUniforms) {
      expect(u.uTime).toBeDefined();
      // uTime is an IUniform with a .value (initially 0.0 per createDomainEdgeUniforms).
      expect((u.uTime as THREE.IUniform).value).toBeDefined();
    }
  });

  it('Cycle-2-C: ctx.domainGroups receives every state=domain + state=project group (M2 union predicate)', () => {
    const domain = makeNode('d1', { state: 'domain' });
    const project = makeNode('p1', { state: 'project', position: [3, 0, 0] });
    const cluster = makeNode('c1', { state: 'active', position: [6, 0, 0] });
    const data = makeData([domain, project, cluster]);
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new DomainBuilder(null).build(data, scene, ctx);
    expect(ctx.domainGroups.length).toBe(2);
    // Each group is registered in ctx.beamNodeGroups too (downstream
    // beam-pool acquires by beamNodeGroups[id]).
    expect(ctx.beamNodeGroups.has('d1')).toBe(true);
    expect(ctx.beamNodeGroups.has('p1')).toBe(true);
    expect(ctx.beamNodeGroups.has('c1')).toBe(false);
  });
});

describe('builders/integration — Cycle 3 edge sequences', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  it('Cycle-3-A: EdgeBuilder reads ctx.sceneNodeMap to position hierarchical edges (3 nodes + 2 hier edges → 1 merged bucket)', () => {
    const dom = makeNode('d1', { state: 'domain' });
    const c1 = makeNode('c1', { state: 'active', position: [5, 0, 0] });
    const c2 = makeNode('c2', { state: 'active', position: [-5, 0, 0] });
    const data = {
      nodes: [dom, c1, c2],
      edges: [
        { from: 'd1', to: 'c1', type: 'hierarchical' },
        { from: 'd1', to: 'c2', type: 'hierarchical' },
      ],
    } as unknown as SceneData;
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup === true,
    ) as THREE.Group;
    expect(hierGroup).toBeDefined();
    // Both child edges live inside hierGroup, bucketed by parent d1.
    expect(hierGroup.children.length).toBe(1); // one merged bucket per parent
  });

  it('Cycle-3-B: F5 uniforms array contains BOTH domain-edge AND hierarchical-edge records', () => {
    const dom = makeNode('d1', { state: 'domain' });
    const c1 = makeNode('c1', { state: 'active', position: [5, 0, 0] });
    const data = {
      nodes: [dom, c1],
      edges: [{ from: 'd1', to: 'c1', type: 'hierarchical' }],
    } as unknown as SceneData;
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new DomainBuilder(null).build(data, scene, ctx); // pushes 1 domain-edge uniform
    new EdgeBuilder().build(data, scene, ctx); // pushes 1 hierarchical bucket uniform
    expect(ctx.edgeUniforms.length).toBe(2);
    for (const u of ctx.edgeUniforms) {
      expect(u.uTime).toBeDefined();
      expect((u.uTime as THREE.IUniform).value).toBeDefined();
    }
  });

  it('Cycle-3-C: edge ephemeral-flag — no userData.persistent on any of the 3 sub-groups', () => {
    const c1 = makeNode('c1', { state: 'active' });
    const c2 = makeNode('c2', { state: 'active', position: [5, 0, 0] });
    const dom = makeNode('d1', { state: 'domain', position: [10, 0, 0] });
    const data = {
      nodes: [c1, c2, dom],
      edges: [
        { from: 'c1', to: 'c2', type: 'similarity' },
        { from: 'd1', to: 'c1', type: 'hierarchical' },
        { from: 'd1', to: 'c1', type: 'injection' },
      ],
    } as unknown as SceneData;
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    new ClusterBuilder(null).build(data, scene, ctx);
    new DomainBuilder(null).build(data, scene, ctx);
    new EdgeBuilder().build(data, scene, ctx);
    // Walk scene children — none of the edge sub-groups have userData.persistent.
    for (const child of scene.children) {
      const u = (child as THREE.Group).userData ?? {};
      if (u.isInterClusterEdgeGroup || u.isSimilarityEdge || u.isInjectionEdge) {
        expect(u.persistent).not.toBe(true);
      }
    }
  });
});
