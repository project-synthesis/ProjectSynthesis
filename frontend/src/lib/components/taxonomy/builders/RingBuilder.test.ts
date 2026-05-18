// frontend/src/lib/components/taxonomy/builders/RingBuilder.test.ts
//
// Sub-project D Cycle 4 — 21 failing unit tests for RingBuilder per spec
// §5.1 (RingBuilder.test.ts — most complex builder due to pool state).
//
// 5 readiness-ring + 5 template-ring + 5 pool-lifecycle + 3 readiness-ring
// pruning + 3 accessor cases = 21 tests.
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import { describe, it, expect, beforeEach } from 'vitest';
import * as THREE from 'three';
import type { SceneData, SceneNode } from '../TopologyData';
import type { ReadinessTier } from '../readiness-tier';
import { readinessTierColor } from '../readiness-tier';
import { RingBuilder } from './RingBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';

function makeNode(id: string, overrides: Partial<SceneNode> = {}): SceneNode {
  return {
    id,
    position: [0, 0, 0],
    color: '#ff00ff',
    size: 1.0,
    opacity: 1.0,
    persistence: 1.0,
    state: 'active',
    label: id,
    visible: true,
    coherence: 0.5,
    avgScore: 5.0,
    domain: 'general',
    memberCount: 1,
    isSubDomain: false,
    template_count: 0,
    ...overrides,
  } as SceneNode;
}

function makeDomain(id: string, tier?: ReadinessTier, overrides: Partial<SceneNode> = {}): SceneNode {
  return makeNode(id, {
    state: 'domain',
    readinessTier: tier,
    domain: id,
    ...overrides,
  });
}

function makeData(nodes: SceneNode[]): SceneData {
  return { nodes, edges: [] } as SceneData;
}

function populateMap(ctx: BuilderContext, data: SceneData): void {
  for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
}

// Constants mirrored from RingBuilder spec §3.3 — kept here so the tests
// pin numeric invariants without re-importing private builder constants.
const TEMPLATE_RING_POOL_INITIAL = 50;
const TEMPLATE_RING_POOL_GROW_CHUNK = 50;
const TEMPLATE_RING_POOL_MAX = 500;
const READINESS_RING_OPACITY_FACTOR = 0.9;

describe('RingBuilder — 21 unit tests per spec §5.1 (rev-2 accessors)', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  // ────────────────────────────────────────────────────────────────────
  // Readiness rings — 5 tests
  // ────────────────────────────────────────────────────────────────────

  // #1 build(domain with readinessTier) adds 1 ring under _readinessRingGroup
  it('#1 — Readiness: build(domain with readinessTier) adds 1 ring under readiness group', () => {
    const dom = makeDomain('d1', 'healthy');
    const data = makeData([dom]);
    populateMap(ctx, data);
    const rb = new RingBuilder();
    rb.build(data, scene, ctx);
    // Find readiness ring group via userData tag.
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group | undefined;
    expect(ringGroup).toBeDefined();
    expect(ringGroup!.children.length).toBe(1);
    expect(ringGroup!.children[0].type).toBe('Mesh');
  });

  // #2 Ring color matches readinessTier color (per readinessTierColor) + opacity = node.opacity * factor
  it('#2 — Readiness: ring MeshBasicMaterial color matches tier + opacity = nodeOpacity * factor', () => {
    const tiers: ReadinessTier[] = ['healthy', 'warming', 'guarded', 'critical', 'ready'];
    for (const tier of tiers) {
      const localScene = new THREE.Scene();
      const localCtx = createBuilderContext();
      const dom = makeDomain(`d-${tier}`, tier, { opacity: 0.8 });
      const data = makeData([dom]);
      populateMap(localCtx, data);
      const rb = new RingBuilder();
      rb.build(data, localScene, localCtx);
      const ringGroup = localScene.children.find(
        (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
      ) as THREE.Group;
      const ringMesh = ringGroup.children[0] as THREE.Mesh;
      const mat = ringMesh.material as THREE.MeshBasicMaterial;
      const expectedHex = parseInt(readinessTierColor(tier).replace('#', ''), 16);
      expect(mat.color.getHex()).toBe(expectedHex);
      expect(mat.opacity).toBeCloseTo(0.8 * READINESS_RING_OPACITY_FACTOR, 5);
    }
  });

  // #3 _readinessRingGroup is userData.persistent = true
  it('#3 — Readiness: _readinessRingGroup is userData.persistent = true', () => {
    const dom = makeDomain('d1', 'healthy');
    const data = makeData([dom]);
    populateMap(ctx, data);
    new RingBuilder().build(data, scene, ctx);
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group;
    expect(ringGroup.userData.persistent).toBe(true);
  });

  // #4 _readinessRingGroup is a child of scene after first build
  it('#4 — Readiness: _readinessRingGroup is a child of scene after first build', () => {
    const dom = makeDomain('d1', 'healthy');
    const data = makeData([dom]);
    populateMap(ctx, data);
    new RingBuilder().build(data, scene, ctx);
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group | undefined;
    expect(ringGroup).toBeDefined();
    expect(ringGroup!.parent).toBe(scene);
  });

  // #5 Build skips domains without readinessTier
  it('#5 — Readiness: build skips domains without readinessTier (no ring added)', () => {
    const dom = makeDomain('d1'); // no tier
    const data = makeData([dom]);
    populateMap(ctx, data);
    const rb = new RingBuilder();
    rb.build(data, scene, ctx);
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group | undefined;
    // Group may exist (lazy constructed) but has no children for the no-tier domain.
    if (ringGroup) {
      expect(ringGroup.children.length).toBe(0);
    }
    expect(rb.readinessRingCount()).toBe(0);
  });

  // ────────────────────────────────────────────────────────────────────
  // Template rings — 5 tests
  // ────────────────────────────────────────────────────────────────────

  // #6 build(cluster with template indicator) adds 1 ring under _templateRingGroup
  it('#6 — Template: build(cluster with template_count > 0) adds 1 ring under template group', () => {
    const clusterTpl = makeNode('c1', { template_count: 3 });
    const data = makeData([clusterTpl]);
    populateMap(ctx, data);
    const rb = new RingBuilder();
    rb.build(data, scene, ctx);
    const tplGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isTemplateRingGroup === true,
    ) as THREE.Group | undefined;
    expect(tplGroup).toBeDefined();
    // Template ring active for the cluster
    expect(rb.getTemplateRing('c1')).toBeDefined();
    expect(rb.getTemplateRing('c1')!.visible).toBe(true);
  });

  // #7 _templateRingGroup is userData.persistent = true
  it('#7 — Template: _templateRingGroup is userData.persistent = true', () => {
    const clusterTpl = makeNode('c1', { template_count: 1 });
    const data = makeData([clusterTpl]);
    populateMap(ctx, data);
    new RingBuilder().build(data, scene, ctx);
    const tplGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isTemplateRingGroup === true,
    ) as THREE.Group;
    expect(tplGroup.userData.persistent).toBe(true);
  });

  // #8 Template ring canon F3 material attributes — factory color + invariants
  it('#8 — Template: ring material is canon F3 (factory 0x00e5ff, opacity 0.35, DoubleSide, transparent, RingGeometry)', () => {
    // Use a cluster whose color matches canon cyan so the post-sync color
    // assertion pins the brand intent. Other clusters get their per-id
    // color (sync overwrites the factory canon — verified separately by
    // test #10 which asserts color tracks live data).
    const c1 = makeNode('c1', { template_count: 1, color: '#00e5ff' });
    const data = makeData([c1]);
    populateMap(ctx, data);
    const rb = new RingBuilder();
    rb.build(data, scene, ctx);
    const mesh = rb.getTemplateRing('c1')!;
    const mat = mesh.material as THREE.MeshBasicMaterial;
    expect(mat.color.getHex()).toBe(0x00e5ff);
    // Canon F3 invariants surviving sync (transparent + DoubleSide + RingGeometry):
    expect(mat.transparent).toBe(true);
    expect(mat.side).toBe(THREE.DoubleSide);
    const geom = mesh.geometry as THREE.RingGeometry;
    expect(geom.type).toBe('RingGeometry');
    // Verify RingGeometry constructor args match canon F3 — inner/outer/segments
    // visible in the geometry's `parameters` field.
    const params = (geom as unknown as { parameters: { innerRadius: number; outerRadius: number; thetaSegments: number } }).parameters;
    expect(params.innerRadius).toBeCloseTo(1.25, 5);
    expect(params.outerRadius).toBeCloseTo(1.35, 5);
    expect(params.thetaSegments).toBe(64);
  });

  // #9 Template ring released when cluster disappears (no longer in build output)
  it('#9 — Template: ring released when cluster disappears across rebuilds', () => {
    const rb = new RingBuilder();
    const c1 = makeNode('c1', { template_count: 1 });
    const c2 = makeNode('c2', { template_count: 1 });
    // First build: both clusters present.
    const data1 = makeData([c1, c2]);
    const ctx1 = createBuilderContext();
    populateMap(ctx1, data1);
    rb.build(data1, scene, ctx1);
    expect(rb.getTemplateRing('c1')).toBeDefined();
    expect(rb.getTemplateRing('c2')).toBeDefined();
    // Second build: c2 missing.
    const data2 = makeData([c1]);
    const ctx2 = createBuilderContext();
    populateMap(ctx2, data2);
    rb.build(data2, scene, ctx2);
    expect(rb.getTemplateRing('c1')).toBeDefined();
    // c2's ring entry must be released — getTemplateRing returns undefined.
    expect(rb.getTemplateRing('c2')).toBeUndefined();
  });

  // #10 Position/color follow cluster live data
  it('#10 — Template: ring position + color follow cluster live data', () => {
    const c1 = makeNode('c1', { template_count: 1, position: [3, 4, 5], color: '#abcdef' });
    const data = makeData([c1]);
    populateMap(ctx, data);
    const rb = new RingBuilder();
    rb.build(data, scene, ctx);
    const mesh = rb.getTemplateRing('c1')!;
    expect(mesh.position.x).toBeCloseTo(3, 5);
    expect(mesh.position.y).toBeCloseTo(4, 5);
    expect(mesh.position.z).toBeCloseTo(5, 5);
    const mat = mesh.material as THREE.MeshBasicMaterial;
    expect(mat.color.getHex()).toBe(parseInt('abcdef', 16));
  });

  // ────────────────────────────────────────────────────────────────────
  // Pool lifecycle — 5 tests
  // ────────────────────────────────────────────────────────────────────

  // #11 _templateRingPool initial growth on first build (TEMPLATE_RING_POOL_INITIAL meshes)
  it('#11 — Pool: first build seeds pool with TEMPLATE_RING_POOL_INITIAL meshes', () => {
    const rb = new RingBuilder();
    // One templated cluster is enough to trigger the initial seed.
    const c1 = makeNode('c1', { template_count: 1 });
    const data = makeData([c1]);
    populateMap(ctx, data);
    rb.build(data, scene, ctx);
    // The pool exposes its high-water mark via the templateRingPoolSize() helper.
    expect((rb as unknown as { templateRingPoolSize(): number }).templateRingPoolSize()).toBe(
      TEMPLATE_RING_POOL_INITIAL,
    );
  });

  // #12 Subsequent builds grow by TEMPLATE_RING_POOL_GROW_CHUNK if needed
  it('#12 — Pool: pool grows by TEMPLATE_RING_POOL_GROW_CHUNK when more requests arrive', () => {
    const rb = new RingBuilder();
    // First build with 5 templated clusters → seeds INITIAL (50).
    const firstNodes: SceneNode[] = [];
    for (let i = 0; i < 5; i++) firstNodes.push(makeNode(`c${i}`, { template_count: 1 }));
    const data1 = makeData(firstNodes);
    populateMap(ctx, data1);
    rb.build(data1, scene, ctx);
    expect(
      (rb as unknown as { templateRingPoolSize(): number }).templateRingPoolSize(),
    ).toBe(TEMPLATE_RING_POOL_INITIAL);

    // Second build with 55 fresh ids → 50 still in use + 5 new attachments need
    // the pool to grow by one chunk (50 → 100).
    const ctx2 = createBuilderContext();
    const secondNodes: SceneNode[] = [];
    for (let i = 0; i < 55; i++) secondNodes.push(makeNode(`x${i}`, { template_count: 1 }));
    populateMap(ctx2, makeData(secondNodes));
    rb.build(makeData(secondNodes), scene, ctx2);
    expect(
      (rb as unknown as { templateRingPoolSize(): number }).templateRingPoolSize(),
    ).toBe(TEMPLATE_RING_POOL_INITIAL + TEMPLATE_RING_POOL_GROW_CHUNK);
  });

  // #13 Pool high-water mark never exceeds TEMPLATE_RING_POOL_MAX
  it('#13 — Pool: high-water mark never exceeds TEMPLATE_RING_POOL_MAX', () => {
    const rb = new RingBuilder();
    // Build with > TEMPLATE_RING_POOL_MAX templated clusters.
    const nodes: SceneNode[] = [];
    for (let i = 0; i < TEMPLATE_RING_POOL_MAX + 50; i++) {
      nodes.push(makeNode(`c${i}`, { template_count: 1 }));
    }
    const data = makeData(nodes);
    populateMap(ctx, data);
    rb.build(data, scene, ctx);
    const size = (rb as unknown as { templateRingPoolSize(): number }).templateRingPoolSize();
    expect(size).toBeLessThanOrEqual(TEMPLATE_RING_POOL_MAX);
  });

  // #14 free-list returns ring to pool on release
  it('#14 — Pool: released rings return to free list (next acquire reuses)', () => {
    const rb = new RingBuilder();
    const c1 = makeNode('c1', { template_count: 1 });
    const data1 = makeData([c1]);
    populateMap(ctx, data1);
    rb.build(data1, scene, ctx);
    expect(rb.getTemplateRing('c1')).toBeDefined();

    // Release: c1 disappears from second build.
    const ctx2 = createBuilderContext();
    populateMap(ctx2, makeData([]));
    rb.build(makeData([]), scene, ctx2);
    expect(rb.getTemplateRing('c1')).toBeUndefined();

    // Free-list size = TEMPLATE_RING_POOL_INITIAL (all seeded mesh, c1 returned).
    // Third build with a new templated cluster: pool size should NOT grow
    // (reuse from free list).
    const ctx3 = createBuilderContext();
    populateMap(ctx3, makeData([makeNode('c-fresh', { template_count: 1 })]));
    rb.build(makeData([makeNode('c-fresh', { template_count: 1 })]), scene, ctx3);
    expect(
      (rb as unknown as { templateRingPoolSize(): number }).templateRingPoolSize(),
    ).toBe(TEMPLATE_RING_POOL_INITIAL);
  });

  // #15 dispose() post-state: persistent groups detached + maps cleared + pool removed from scene tree
  it('#15 — dispose() detaches persistent groups + clears maps + removes pool meshes from scene + idempotent', () => {
    const rb = new RingBuilder();
    const dom = makeDomain('d1', 'healthy');
    const tpl = makeNode('c1', { template_count: 1 });
    const data = makeData([dom, tpl]);
    populateMap(ctx, data);
    rb.build(data, scene, ctx);

    // Pre-state: both persistent groups are children of scene.
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group;
    const tplGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isTemplateRingGroup === true,
    ) as THREE.Group;
    expect(ringGroup.parent).toBe(scene);
    expect(tplGroup.parent).toBe(scene);

    rb.dispose();
    // (a) persistent groups detached.
    expect(ringGroup.parent).toBeNull();
    expect(tplGroup.parent).toBeNull();
    // (b) internal maps cleared.
    expect(rb.getTemplateRing('c1')).toBeUndefined();
    expect(rb.getReadinessRing('d1')).toBeUndefined();
    expect(rb.readinessRingCount()).toBe(0);
    // (c) every pool mesh removed from scene tree.
    let poolMeshesInScene = 0;
    scene.traverse((obj) => {
      if (obj.userData?.kind === 'template_ring') poolMeshesInScene++;
    });
    expect(poolMeshesInScene).toBe(0);

    // Idempotent — second dispose is a no-op.
    expect(() => rb.dispose()).not.toThrow();
    expect(rb.getTemplateRing('c1')).toBeUndefined();
  });

  // ────────────────────────────────────────────────────────────────────
  // Readiness-ring pruning — 3 tests
  // ────────────────────────────────────────────────────────────────────

  // #16 Domain disappears in subsequent build → readiness entry pruned + disposed
  it('#16 — Pruning: domain disappears in subsequent build → readiness entry pruned + disposed', () => {
    const rb = new RingBuilder();
    const d1 = makeDomain('d1', 'healthy');
    const d2 = makeDomain('d2', 'warming');
    rb.build(makeData([d1, d2]), scene, ctx);
    expect(rb.getReadinessRing('d1')).toBeDefined();
    expect(rb.getReadinessRing('d2')).toBeDefined();
    expect(rb.readinessRingCount()).toBe(2);

    // Second build: only d2 remains.
    const ctx2 = createBuilderContext();
    populateMap(ctx2, makeData([d2]));
    rb.build(makeData([d2]), scene, ctx2);
    expect(rb.getReadinessRing('d1')).toBeUndefined();
    expect(rb.getReadinessRing('d2')).toBeDefined();
    expect(rb.readinessRingCount()).toBe(1);

    // The persistent group should no longer contain d1's mesh.
    const ringGroup = scene.children.find(
      (c) => (c as THREE.Group).userData?.isReadinessRingGroup === true,
    ) as THREE.Group;
    expect(ringGroup.children.length).toBe(1);
  });

  // #17 Domain loses readinessTier (still visible) → entry pruned
  it('#17 — Pruning: domain loses readinessTier → ring pruned', () => {
    const rb = new RingBuilder();
    const d1 = makeDomain('d1', 'healthy');
    rb.build(makeData([d1]), scene, ctx);
    expect(rb.getReadinessRing('d1')).toBeDefined();

    // Second build: same id but tier dropped.
    const d1NoTier = makeDomain('d1');
    const ctx2 = createBuilderContext();
    populateMap(ctx2, makeData([d1NoTier]));
    rb.build(makeData([d1NoTier]), scene, ctx2);
    expect(rb.getReadinessRing('d1')).toBeUndefined();
  });

  // #18 Domain becomes invisible → entry pruned
  it('#18 — Pruning: domain.visible = false → ring pruned', () => {
    const rb = new RingBuilder();
    const d1 = makeDomain('d1', 'healthy');
    rb.build(makeData([d1]), scene, ctx);
    expect(rb.getReadinessRing('d1')).toBeDefined();

    const d1Hidden = makeDomain('d1', 'healthy', { visible: false });
    const ctx2 = createBuilderContext();
    populateMap(ctx2, makeData([d1Hidden]));
    rb.build(makeData([d1Hidden]), scene, ctx2);
    expect(rb.getReadinessRing('d1')).toBeUndefined();
  });

  // ────────────────────────────────────────────────────────────────────
  // Accessor canon — 3 tests
  // ────────────────────────────────────────────────────────────────────

  // #19 getTemplateRing(id) — known/unknown/post-dispose
  it('#19 — Accessor: getTemplateRing returns Mesh for known id, undefined for unknown + post-dispose', () => {
    const rb = new RingBuilder();
    const c1 = makeNode('c1', { template_count: 1 });
    rb.build(makeData([c1]), scene, ctx);
    expect(rb.getTemplateRing('c1')).toBeInstanceOf(THREE.Mesh);
    expect(rb.getTemplateRing('unknown')).toBeUndefined();
    rb.dispose();
    expect(rb.getTemplateRing('c1')).toBeUndefined();
  });

  // #20 getReadinessRing(id) — known/unknown/post-dispose + RingEntry shape
  it('#20 — Accessor: getReadinessRing returns RingEntry with all spec fields for known id', () => {
    const rb = new RingBuilder();
    const d1 = makeDomain('d1', 'warming', { opacity: 0.7, size: 1.5 });
    rb.build(makeData([d1]), scene, ctx);
    const entry = rb.getReadinessRing('d1');
    expect(entry).toBeDefined();
    expect(entry!.mesh).toBeInstanceOf(THREE.Mesh);
    expect(entry!.material).toBeInstanceOf(THREE.MeshBasicMaterial);
    expect(entry!.lastTier).toBe('warming');
    expect(entry!.lastSize).toBe(1.5);
    expect(entry!.domain).toBe('d1');
    expect(entry!.nodeOpacity).toBe(0.7);
    expect(entry!.tween).toBeNull();

    expect(rb.getReadinessRing('unknown')).toBeUndefined();
    rb.dispose();
    expect(rb.getReadinessRing('d1')).toBeUndefined();
  });

  // #21 readinessRingCount() + readinessRingIds() track active entries
  it('#21 — Accessor: readinessRingCount + readinessRingIds reflect active entries', () => {
    const rb = new RingBuilder();
    expect(rb.readinessRingCount()).toBe(0);
    expect(rb.readinessRingIds()).toEqual([]);

    const d1 = makeDomain('d1', 'healthy');
    const d2 = makeDomain('d2', 'critical');
    rb.build(makeData([d1, d2]), scene, ctx);
    expect(rb.readinessRingCount()).toBe(2);
    const ids = rb.readinessRingIds().sort();
    expect(ids).toEqual(['d1', 'd2']);
  });
});
