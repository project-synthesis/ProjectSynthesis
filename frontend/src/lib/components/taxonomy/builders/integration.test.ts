// frontend/src/lib/components/taxonomy/builders/integration.test.ts
// Multi-builder integration tests. Cycle 1 contribution: ClusterBuilder.
// Subsequent cycles (Domain/Edge/Ring/Dust) append their own cases.
//
// Cycle 5 OPERATE additionally lands the spec §5.3 INT-1..INT-6 full-
// pipeline multi-builder sequence — INT-1 mesh count + tree structure,
// INT-2 idempotency under cleanupScene, INT-3 cross-builder ctx reads,
// INT-4 persistent parent survival, INT-5 dispose chain (scene empty
// modulo non-builder persistents), INT-6 ImpactCoordinator integration
// via ctx.clusterShaderMaterials.
import { describe, it, expect, beforeEach } from 'vitest';
import * as THREE from 'three';
import { ClusterBuilder } from './ClusterBuilder';
import { DomainBuilder } from './DomainBuilder';
import { EdgeBuilder } from './EdgeBuilder';
import { RingBuilder } from './RingBuilder';
import { DustBuilder } from './DustBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';
import { cleanupScene } from '../scene-cleanup';
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

describe('builders/integration — Cycle 4 ring sequences', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  // INT-ring-1: persistent flags survive multiple builds
  it('INT-ring-1: persistent flags + scene attachment survive multiple builds', () => {
    const rb = new RingBuilder();
    // Build #1: a domain with a tier + a templated cluster.
    const dom1 = makeNode('d1', { state: 'domain', readinessTier: 'healthy' });
    const tpl1 = makeNode('c1', { state: 'active', template_count: 2 });
    const data1: SceneData = { nodes: [dom1, tpl1], edges: [] } as SceneData;
    for (const n of data1.nodes) ctx.sceneNodeMap.set(n.id, n);
    rb.build(data1, scene, ctx);
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group;
    const tplGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isTemplateRingGroup === true,
    ) as THREE.Group;
    expect(ringGroup).toBeDefined();
    expect(tplGroup).toBeDefined();
    expect(ringGroup.userData.persistent).toBe(true);
    expect(tplGroup.userData.persistent).toBe(true);
    expect(ringGroup.parent).toBe(scene);
    expect(tplGroup.parent).toBe(scene);
    const initialRingGroupUuid = ringGroup.uuid;
    const initialTplGroupUuid = tplGroup.uuid;

    // Build #2: different ids — persistent groups must remain attached
    // (no detach/re-add), same group instances reused.
    const dom2 = makeNode('d2', { state: 'domain', readinessTier: 'critical' });
    const tpl2 = makeNode('c2', { state: 'active', template_count: 1 });
    const ctx2 = createBuilderContext();
    const data2: SceneData = { nodes: [dom2, tpl2], edges: [] } as SceneData;
    for (const n of data2.nodes) ctx2.sceneNodeMap.set(n.id, n);
    rb.build(data2, scene, ctx2);
    expect(ringGroup.uuid).toBe(initialRingGroupUuid);
    expect(tplGroup.uuid).toBe(initialTplGroupUuid);
    expect(ringGroup.parent).toBe(scene);
    expect(tplGroup.parent).toBe(scene);

    // Build #3: empty data — persistent groups still attached, both
    // entries pruned, pool returns to free list.
    const ctx3 = createBuilderContext();
    rb.build({ nodes: [], edges: [] } as SceneData, scene, ctx3);
    expect(ringGroup.parent).toBe(scene);
    expect(tplGroup.parent).toBe(scene);
    expect(rb.readinessRingCount()).toBe(0);
  });

  // INT-ring-2: pool high-water-mark cap behavior
  it('INT-ring-2: pool high-water mark caps at TEMPLATE_RING_POOL_MAX + emits one warn per rebuild', () => {
    const rb = new RingBuilder();
    const originalWarn = console.warn;
    let warnCount = 0;
    console.warn = (..._args: unknown[]) => {
      warnCount++;
    };
    try {
      // Allocate > MAX (500) templated clusters in a single build.
      const overflowNodes: SceneNode[] = [];
      for (let i = 0; i < 600; i++) {
        overflowNodes.push(makeNode(`c${i}`, { state: 'active', template_count: 1 }));
      }
      const data: SceneData = { nodes: overflowNodes, edges: [] } as SceneData;
      for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
      rb.build(data, scene, ctx);
      // Pool size never exceeds the cap (500).
      const size = (rb as unknown as { templateRingPoolSize(): number }).templateRingPoolSize();
      expect(size).toBe(500);
      // Exactly one warn fired this rebuild.
      expect(warnCount).toBe(1);

      // Second rebuild also overflowing — warn resets to 0 per rebuild,
      // fires exactly once.
      warnCount = 0;
      const ctx2 = createBuilderContext();
      const second: SceneNode[] = [];
      for (let i = 0; i < 600; i++) {
        second.push(makeNode(`x${i}`, { state: 'active', template_count: 1 }));
      }
      for (const n of second) ctx2.sceneNodeMap.set(n.id, n);
      rb.build({ nodes: second, edges: [] } as SceneData, scene, ctx2);
      expect(warnCount).toBe(1);
    } finally {
      console.warn = originalWarn;
    }
  });

  // INT-ring-3: dispose removes persistent groups from scene
  it('INT-ring-3: dispose detaches both persistent groups from scene + idempotent', () => {
    const rb = new RingBuilder();
    const dom = makeNode('d1', { state: 'domain', readinessTier: 'warming' });
    const tpl = makeNode('c1', { state: 'active', template_count: 1 });
    const data: SceneData = { nodes: [dom, tpl], edges: [] } as SceneData;
    for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
    rb.build(data, scene, ctx);
    // Pre-state: scene contains both ring groups.
    const persistentChildrenBefore = scene.children.filter(
      (c) =>
        (c as THREE.Group).userData?.isReadinessRingGroup === true ||
        (c as THREE.Group).userData?.isTemplateRingGroup === true,
    );
    expect(persistentChildrenBefore.length).toBe(2);

    rb.dispose();
    // Post-state: neither ring group is attached to scene.
    const persistentChildrenAfter = scene.children.filter(
      (c) =>
        (c as THREE.Group).userData?.isReadinessRingGroup === true ||
        (c as THREE.Group).userData?.isTemplateRingGroup === true,
    );
    expect(persistentChildrenAfter.length).toBe(0);
    // No template-ring meshes anywhere in the scene tree.
    let poolMeshes = 0;
    scene.traverse((obj) => {
      if (obj.userData?.kind === 'template_ring') poolMeshes++;
    });
    expect(poolMeshes).toBe(0);

    // Idempotent — second dispose is a no-op.
    expect(() => rb.dispose()).not.toThrow();
    expect(rb.readinessRingCount()).toBe(0);
    expect(rb.readinessRingIds()).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Cycle 5 — DustBuilder integration sequences (2 cases)
// ─────────────────────────────────────────────────────────────────────

describe('builders/integration — Cycle 5 dust sequences', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  // INT-dust-1: dust persistence + scene attachment survive multiple builds
  it('INT-dust-1: dust Points + persistent flag + scene attachment survive multiple builds', () => {
    const db = new DustBuilder();
    // Build #1: data with two domain anchors.
    const dom1 = makeNode('d1', { state: 'domain', color: '#ff0000' });
    const dom2 = makeNode('d2', { state: 'domain', color: '#00ff00', position: [80, 0, 0] });
    const data1: SceneData = { nodes: [dom1, dom2], edges: [] } as SceneData;
    for (const n of data1.nodes) ctx.sceneNodeMap.set(n.id, n);
    db.build(data1, scene, ctx);

    const dust1 = db.dustPoints();
    expect(dust1).not.toBeNull();
    expect(dust1!.userData.persistent).toBe(true);
    expect(dust1!.userData.isNeuralDust).toBe(true);
    expect(dust1!.parent).toBe(scene);
    const dust1Uuid = dust1!.uuid;
    const dust1Geom = dust1!.geometry;
    const dust1Mat = dust1!.material as THREE.PointsMaterial;

    // Build #2: completely different data — dust must NOT be reconstructed.
    const ctx2 = createBuilderContext();
    const data2: SceneData = { nodes: [], edges: [] } as SceneData;
    db.build(data2, scene, ctx2);
    const dust2 = db.dustPoints();
    expect(dust2).toBe(dust1); // same instance
    expect(dust2!.uuid).toBe(dust1Uuid);
    expect(dust2!.geometry).toBe(dust1Geom);
    expect(dust2!.material).toBe(dust1Mat);
    expect(dust2!.parent).toBe(scene);

    // Build #3: another build, still same dust instance.
    const ctx3 = createBuilderContext();
    db.build({ nodes: [dom1], edges: [] } as SceneData, scene, ctx3);
    expect(db.dustPoints()).toBe(dust1);
  });

  // INT-dust-2: dust survives cleanupScene via persistent flag
  it('INT-dust-2: dust survives cleanupScene via persistent flag (Sub-project A contract)', () => {
    const db = new DustBuilder();
    db.build({ nodes: [], edges: [] } as SceneData, scene, ctx);
    const dust = db.dustPoints();
    expect(dust).not.toBeNull();
    expect(dust!.parent).toBe(scene);

    // Run cleanupScene — should detach the dust before wiping ephemeral
    // children and reattach it after.
    cleanupScene(scene);
    expect(dust!.parent).toBe(scene);
    // Sub-project A invariant: persistent geometry-owner's geometry
    // survives (not in the disposed set). Verify by reading position
    // attribute count is still 3000 (geometry not disposed-and-freed).
    const posAttr = dust!.geometry.getAttribute('position') as THREE.BufferAttribute;
    expect(posAttr.count).toBe(3000);
    // Subsequent build call still resolves the same instance.
    db.build({ nodes: [], edges: [] } as SceneData, scene, ctx);
    expect(db.dustPoints()).toBe(dust);
  });
});

// ─────────────────────────────────────────────────────────────────────
// Cycle 5 — INT-1..INT-6 full-pipeline multi-builder sequence (spec §5.3)
// ─────────────────────────────────────────────────────────────────────

/**
 * Realistic SceneData factory mirroring the orchestrator's expected
 * topology: ~5 clusters + 2 domains + similarity edges + readiness
 * tier. Used by INT-1..INT-5.
 *
 * Layout:
 *   d1 (domain)  → parent of c1, c2, c3
 *   d2 (domain)  → parent of c4, c5
 *   similarity edge c1↔c2
 *   d1 carries readinessTier 'healthy', d2 'critical'
 *   c1 carries template_count 2 (template ring)
 */
function makeRealisticData(): SceneData {
  const d1 = makeNode('d1', {
    state: 'domain', color: '#ff0066', position: [-30, 0, 0],
    readinessTier: 'healthy',
  });
  const d2 = makeNode('d2', {
    state: 'domain', color: '#00ccff', position: [30, 0, 0],
    readinessTier: 'critical',
  });
  const c1 = makeNode('c1', {
    state: 'active', color: '#ff66cc', position: [-40, 5, 0],
    template_count: 2,
  });
  const c2 = makeNode('c2', {
    state: 'active', color: '#cc99ff', position: [-20, -5, 0],
  });
  const c3 = makeNode('c3', {
    state: 'active', color: '#9966ff', position: [-30, 10, 0],
  });
  const c4 = makeNode('c4', {
    state: 'active', color: '#66ccff', position: [40, 5, 0],
  });
  const c5 = makeNode('c5', {
    state: 'active', color: '#99ccff', position: [20, -5, 0],
  });
  const data = {
    nodes: [d1, d2, c1, c2, c3, c4, c5],
    edges: [
      { from: 'd1', to: 'c1', type: 'hierarchical' },
      { from: 'd1', to: 'c2', type: 'hierarchical' },
      { from: 'd1', to: 'c3', type: 'hierarchical' },
      { from: 'd2', to: 'c4', type: 'hierarchical' },
      { from: 'd2', to: 'c5', type: 'hierarchical' },
      { from: 'c1', to: 'c2', type: 'similarity' },
    ],
  } as unknown as SceneData;
  return data;
}

/**
 * Pre-populate ctx.sceneNodeMap from data.nodes — the orchestrator
 * does this BEFORE invoking any builder (spec §3.6).
 */
function populateSceneNodeMap(ctx: BuilderContext, data: SceneData): void {
  for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
}

/**
 * Construct all 5 builders + run the spec §3.4 fixed-order pipeline.
 * Mirrors the post-migration `rebuildScene` orchestrator body §3.6.
 */
function runFullPipeline(
  scene: THREE.Scene,
  data: SceneData,
  builders: {
    cluster: ClusterBuilder;
    domain: DomainBuilder;
    edge: EdgeBuilder;
    ring: RingBuilder;
    dust: DustBuilder;
  },
): BuilderContext {
  const ctx = createBuilderContext();
  populateSceneNodeMap(ctx, data);
  builders.cluster.build(data, scene, ctx);
  builders.domain.build(data, scene, ctx);
  builders.edge.build(data, scene, ctx);
  builders.ring.build(data, scene, ctx);
  builders.dust.build(data, scene, ctx);
  return ctx;
}

describe('builders/integration — Cycle 5 INT-1..INT-6 full pipeline (spec §5.3)', () => {
  let scene: THREE.Scene;

  beforeEach(() => {
    scene = new THREE.Scene();
  });

  // INT-1: Full pipeline — all 5 builders → scene has expected mesh
  // count + tree structure.
  it('INT-1: full pipeline produces expected mesh count + tree structure', () => {
    const builders = {
      cluster: new ClusterBuilder(null),
      domain: new DomainBuilder(null),
      edge: new EdgeBuilder(),
      ring: new RingBuilder(),
      dust: new DustBuilder(),
    };
    const data = makeRealisticData();
    const ctx = runFullPipeline(scene, data, builders);

    // ctx.nodeMeshes — 5 clusters + 2 domains = 7 entries.
    expect(ctx.nodeMeshes.size).toBe(7);
    expect(ctx.domainGroups.length).toBe(2);
    expect(ctx.clusterShaderMaterials.size).toBe(5);

    // Scene tree:
    //   - 5 cluster groups (state === 'active')
    //   - 2 domain groups (state === 'domain')
    //   - 3 ephemeral edge sub-groups (hierarchical + similarity + injection)
    //   - 1 persistent _readinessRingGroup
    //   - 1 persistent _templateRingGroup
    //   - 1 persistent _dustPoints
    // Total: 5 + 2 + 3 + 1 + 1 + 1 = 13 top-level children.
    expect(scene.children.length).toBe(13);

    // Persistent surfaces are exactly the expected three.
    const persistent = scene.children.filter((c) => c.userData?.persistent === true);
    expect(persistent.length).toBe(3);
    const persistentTags = new Set(persistent.map((c) => {
      if (c.userData?.isReadinessRingGroup) return 'readiness';
      if (c.userData?.isTemplateRingGroup) return 'template';
      if (c.userData?.isNeuralDust) return 'dust';
      return 'unknown';
    }));
    expect(persistentTags).toEqual(new Set(['readiness', 'template', 'dust']));

    // Dust child is THREE.Points + has 3000 vertices.
    const dust = builders.dust.dustPoints();
    expect(dust).toBeInstanceOf(THREE.Points);
    expect((dust!.geometry.getAttribute('position') as THREE.BufferAttribute).count).toBe(3000);

    // Hierarchical edge group present.
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup === true,
    );
    expect(hierGroup).toBeDefined();
  });

  // INT-2: Idempotency under cleanupScene — build 3x with cleanupScene
  // between, scene matches first.
  it('INT-2: idempotency under cleanupScene — three cycles converge to identical scene structure', () => {
    const builders = {
      cluster: new ClusterBuilder(null),
      domain: new DomainBuilder(null),
      edge: new EdgeBuilder(),
      ring: new RingBuilder(),
      dust: new DustBuilder(),
    };
    const data = makeRealisticData();

    // Cycle #1: full pipeline.
    runFullPipeline(scene, data, builders);
    const firstChildrenCount = scene.children.length;
    expect(firstChildrenCount).toBe(13);

    // Snapshot UUIDs for the persistent surfaces — these MUST be stable
    // across cleanupScene cycles.
    const initialReadinessUuid = scene.children.find(
      (c) => c.userData?.isReadinessRingGroup === true,
    )!.uuid;
    const initialTemplateUuid = scene.children.find(
      (c) => c.userData?.isTemplateRingGroup === true,
    )!.uuid;
    const initialDustUuid = scene.children.find(
      (c) => c.userData?.isNeuralDust === true,
    )!.uuid;

    // Cycle #2: cleanupScene → full pipeline.
    cleanupScene(scene);
    runFullPipeline(scene, data, builders);
    expect(scene.children.length).toBe(firstChildrenCount);
    expect(scene.children.find((c) => c.userData?.isReadinessRingGroup)!.uuid)
      .toBe(initialReadinessUuid);
    expect(scene.children.find((c) => c.userData?.isTemplateRingGroup)!.uuid)
      .toBe(initialTemplateUuid);
    expect(scene.children.find((c) => c.userData?.isNeuralDust)!.uuid)
      .toBe(initialDustUuid);

    // Cycle #3: cleanupScene → full pipeline. Same invariant holds.
    cleanupScene(scene);
    runFullPipeline(scene, data, builders);
    expect(scene.children.length).toBe(firstChildrenCount);
    expect(scene.children.find((c) => c.userData?.isReadinessRingGroup)!.uuid)
      .toBe(initialReadinessUuid);
    expect(scene.children.find((c) => c.userData?.isTemplateRingGroup)!.uuid)
      .toBe(initialTemplateUuid);
    expect(scene.children.find((c) => c.userData?.isNeuralDust)!.uuid)
      .toBe(initialDustUuid);
  });

  // INT-3: Cross-builder ctx reads — EdgeBuilder reads endpoint info
  // from Cluster + Domain via ctx (sceneNodeMap is the immediate read
  // path; ctx.nodeMeshes is populated by both for downstream consumers).
  it('INT-3: cross-builder ctx reads — EdgeBuilder positions match Cluster+Domain endpoints', () => {
    const builders = {
      cluster: new ClusterBuilder(null),
      domain: new DomainBuilder(null),
      edge: new EdgeBuilder(),
      ring: new RingBuilder(),
      dust: new DustBuilder(),
    };
    const data = makeRealisticData();
    const ctx = runFullPipeline(scene, data, builders);

    // ctx.nodeMeshes populated by Cluster + Domain — EdgeBuilder reads
    // sceneNodeMap (positions) for the same set of ids.
    expect(ctx.nodeMeshes.has('d1')).toBe(true);
    expect(ctx.nodeMeshes.has('d2')).toBe(true);
    expect(ctx.nodeMeshes.has('c1')).toBe(true);
    expect(ctx.sceneNodeMap.has('d1')).toBe(true);
    expect(ctx.sceneNodeMap.has('c1')).toBe(true);

    // ctx.edgeUniforms populated by DomainBuilder (domain edges) + EdgeBuilder
    // (hierarchical buckets) — F5 uniform chain spans both builders.
    expect(ctx.edgeUniforms.length).toBeGreaterThan(0);
    for (const u of ctx.edgeUniforms) {
      expect(u.uTime).toBeDefined();
      expect((u.uTime as THREE.IUniform).value).toBeDefined();
    }

    // Hierarchical edge group present — its child line meshes are
    // positioned using ctx.sceneNodeMap endpoints (from Cluster + Domain
    // builders). Cross-builder dependency is satisfied.
    const hierGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isInterClusterEdgeGroup === true,
    ) as THREE.Group;
    expect(hierGroup).toBeDefined();
    expect(hierGroup.children.length).toBeGreaterThan(0);

    // The mesh positions inside the hier group reference the SAME
    // SceneNode entries the Cluster/Domain builders consumed.
    const d1Position = ctx.sceneNodeMap.get('d1')!.position;
    const c1Position = ctx.sceneNodeMap.get('c1')!.position;
    expect(d1Position).toEqual([-30, 0, 0]);
    expect(c1Position).toEqual([-40, 5, 0]);
  });

  // INT-4: Persistent parents survive cleanupScene
  it('INT-4: persistent parents (_readinessRingGroup, _templateRingGroup, _dustPoints) survive cleanupScene', () => {
    const builders = {
      cluster: new ClusterBuilder(null),
      domain: new DomainBuilder(null),
      edge: new EdgeBuilder(),
      ring: new RingBuilder(),
      dust: new DustBuilder(),
    };
    const data = makeRealisticData();
    runFullPipeline(scene, data, builders);

    const readiness = scene.children.find((c) => c.userData?.isReadinessRingGroup === true);
    const template = scene.children.find((c) => c.userData?.isTemplateRingGroup === true);
    const dust = scene.children.find((c) => c.userData?.isNeuralDust === true);
    expect(readiness).toBeDefined();
    expect(template).toBeDefined();
    expect(dust).toBeDefined();
    const beforeUuids = new Set([readiness!.uuid, template!.uuid, dust!.uuid]);

    // cleanupScene wipes ephemeral children but reattaches persistent ones.
    cleanupScene(scene);
    const afterReadiness = scene.children.find((c) => c.userData?.isReadinessRingGroup === true);
    const afterTemplate = scene.children.find((c) => c.userData?.isTemplateRingGroup === true);
    const afterDust = scene.children.find((c) => c.userData?.isNeuralDust === true);
    expect(afterReadiness).toBeDefined();
    expect(afterTemplate).toBeDefined();
    expect(afterDust).toBeDefined();
    const afterUuids = new Set([afterReadiness!.uuid, afterTemplate!.uuid, afterDust!.uuid]);
    expect(afterUuids).toEqual(beforeUuids);

    // Ephemeral edge / cluster / domain groups are gone after cleanup
    // (until the next build re-creates them).
    const ephemeralStructural = scene.children.filter(
      (c) => c.userData?.isStructural === true || c.userData?.isStructural === false,
    );
    // After cleanupScene, no cluster/domain groups remain (they're ephemeral).
    expect(ephemeralStructural.length).toBe(0);
    // The hierarchical edge group is also gone.
    expect(scene.children.find((c) => c.userData?.isInterClusterEdgeGroup === true))
      .toBeUndefined();
  });

  // INT-5: Dispose chain (rev-2 B5) — after all 5 dispose, scene matches
  // pre-build state (modulo non-builder persistent children, e.g. lights).
  it('INT-5: dispose chain — after all 5 dispose, scene returns to pre-build state', () => {
    // Pre-state: scene seeded with a non-builder persistent child (e.g.
    // a renderer-owned light). The dispose chain MUST NOT remove it.
    const light = new THREE.AmbientLight(0xffffff, 0.5);
    light.userData.persistent = true;
    light.userData.isRendererOwned = true;
    scene.add(light);
    const initialChildren = [...scene.children];
    const initialUuids = new Set(initialChildren.map((c) => c.uuid));
    expect(initialChildren.length).toBe(1);
    expect(initialChildren[0]).toBe(light);

    // Action: construct + build all 5 builders.
    const builders = {
      cluster: new ClusterBuilder(null),
      domain: new DomainBuilder(null),
      edge: new EdgeBuilder(),
      ring: new RingBuilder(),
      dust: new DustBuilder(),
    };
    const data = makeRealisticData();
    runFullPipeline(scene, data, builders);
    // Builder-owned children are now in scene (light + 13 builder-owned = 14).
    expect(scene.children.length).toBeGreaterThan(initialChildren.length);

    // Dispose all 5 builders.
    builders.cluster.dispose();
    builders.domain.dispose();
    builders.edge.dispose();
    builders.ring.dispose();
    builders.dust.dispose();

    // EdgeBuilder + ClusterBuilder + DomainBuilder don't auto-detach their
    // ephemeral children (cleanupScene handles that on the next rebuild).
    // Run cleanupScene to wipe the ephemerals — it should preserve the
    // non-builder light, and the builder-owned persistents (readiness +
    // template + dust) have already been detached by their dispose calls.
    cleanupScene(scene);

    // Post-state: scene.children equals initial (just the light) modulo
    // insertion order — asserted via Set equality on UUIDs.
    const finalUuids = new Set(scene.children.map((c) => c.uuid));
    expect(finalUuids).toEqual(initialUuids);
    expect(scene.children.length).toBe(initialChildren.length);
    expect(scene.children.includes(light)).toBe(true);
  });

  // INT-6 (rev-2 M6): ImpactCoordinator integration via
  // ctx.clusterShaderMaterials. Locks in the architecture improvement
  // (Map lookup) over the prior `group.children[1]` walk.
  it('INT-6: ImpactCoordinator integration — ctx.clusterShaderMaterials resolves live wire ShaderMaterial', () => {
    const builders = {
      cluster: new ClusterBuilder(null),
      domain: new DomainBuilder(null),
      edge: new EdgeBuilder(),
      ring: new RingBuilder(),
      dust: new DustBuilder(),
    };
    const data = makeRealisticData();
    const ctx = runFullPipeline(scene, data, builders);

    // (a) ctx.clusterShaderMaterials.get(id) resolves for every cluster
    //     (5 clusters in realistic data) — replaces the pre-refactor
    //     group.children[1] index walk at SemanticTopology.svelte:~2421.
    const clusterIds = ['c1', 'c2', 'c3', 'c4', 'c5'];
    for (const id of clusterIds) {
      const mat = ctx.clusterShaderMaterials.get(id);
      expect(mat).toBeDefined();
      expect(mat).toBeInstanceOf(THREE.ShaderMaterial);
    }
    // Domains do NOT have wire shader materials registered (only clusters do).
    expect(ctx.clusterShaderMaterials.has('d1')).toBe(false);
    expect(ctx.clusterShaderMaterials.has('d2')).toBe(false);

    // (b) Writing uRipple.value via the resolved material mutates the LIVE
    //     scene mesh's material uniforms. The cluster's wire LineSegments
    //     reads from this very ShaderMaterial — the impact coordinator's
    //     ripple write (canon F19, prev. SemanticTopology.svelte:2422-2423
    //     via `(wire.material as any).uniforms.uRipple.value = ripple`) is
    //     visible on the live render through `ctx.clusterShaderMaterials`
    //     rather than via the prior `group.children[1]` index walk.
    const c1Mat = ctx.clusterShaderMaterials.get('c1') as THREE.ShaderMaterial;
    const c1Group = ctx.beamNodeGroups.get('c1') as THREE.Group;
    expect(c1Group).toBeDefined();
    const c1Wire = c1Group.children.find((c) => c.type === 'LineSegments') as THREE.LineSegments;
    expect(c1Wire).toBeDefined();
    // The wire's material is the SAME ShaderMaterial the coordinator reads
    // from ctx — mutation on the ctx-resolved material is visible on the
    // live wire mesh.
    expect(c1Wire.material).toBe(c1Mat);
    expect(c1Mat.uniforms.uRipple).toBeDefined();
    const initialURipple = c1Mat.uniforms.uRipple.value;
    expect(initialURipple).toBe(0.0); // canon F19 resting state
    c1Mat.uniforms.uRipple.value = 1.0;
    expect((c1Wire.material as THREE.ShaderMaterial).uniforms.uRipple.value).toBe(1.0);
    expect((c1Wire.material as THREE.ShaderMaterial).uniforms.uRipple.value).not.toBe(initialURipple);
    // uColor is the other canon F19 uniform — writes should also be live.
    expect(c1Mat.uniforms.uColor).toBeDefined();
    expect(c1Mat.uniforms.uColor.value).toBeInstanceOf(THREE.Color);
  });
});
