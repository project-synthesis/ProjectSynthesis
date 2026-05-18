// frontend/src/lib/components/taxonomy/builders/DustBuilder.test.ts
//
// Sub-project D Cycle 5 — 9 failing unit tests for DustBuilder per spec §5.1
// (DustBuilder.test.ts — 8 spec base + 1 rev-2 accessor case).
//
// DustBuilder is the simplest builder: a single 3000-point THREE.Points
// ambient backdrop (canon F10) that is constructed once on first build and
// persists across rebuilds. Positions are SceneData-independent (random in
// [-150, 150]^3); vertex colors are tinted toward each particle's nearest
// `state === 'domain'` anchor (T3.1) and lerp toward the fallback 0x88ccff
// when no anchor is within range.
//
// Spec: docs/superpowers/specs/2026-05-17-scene-builder-extraction-design.md

import { describe, it, expect, beforeEach } from 'vitest';
import * as THREE from 'three';
import type { SceneData, SceneNode } from '../TopologyData';
import { DustBuilder } from './DustBuilder';
import { createBuilderContext, type BuilderContext } from './BuilderContext';

const DUST_COUNT = 3000;
const DUST_RANGE = 150; // positions live in [-150, 150]^3

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

function makeData(nodes: SceneNode[]): SceneData {
  return { nodes, edges: [] } as SceneData;
}

function populateMap(ctx: BuilderContext, data: SceneData): void {
  for (const n of data.nodes) ctx.sceneNodeMap.set(n.id, n);
}

function findDust(scene: THREE.Scene): THREE.Points | undefined {
  return scene.children.find(
    (c) => c.type === 'Points' && (c as THREE.Points).userData?.isNeuralDust === true,
  ) as THREE.Points | undefined;
}

describe('DustBuilder — 9 unit tests per spec §5.1 (8 base + 1 rev-2 accessor)', () => {
  let scene: THREE.Scene;
  let ctx: BuilderContext;

  beforeEach(() => {
    scene = new THREE.Scene();
    ctx = createBuilderContext();
  });

  // #1 build(any data) adds _dustPoints to scene on first call
  it('#1 — build(any data) adds _dustPoints to scene on first call', () => {
    const data = makeData([]);
    populateMap(ctx, data);
    const db = new DustBuilder();
    db.build(data, scene, ctx);
    const dust = findDust(scene);
    expect(dust).toBeDefined();
    expect(dust!.parent).toBe(scene);
  });

  // #2 _dustPoints is userData.persistent = true
  it('#2 — _dustPoints is userData.persistent = true', () => {
    const data = makeData([]);
    populateMap(ctx, data);
    new DustBuilder().build(data, scene, ctx);
    const dust = findDust(scene);
    expect(dust).toBeDefined();
    expect(dust!.userData.persistent).toBe(true);
  });

  // #3 Subsequent build(...) calls are no-op (dust persists across rebuilds —
  // positions + colors are computed only on the first build for the builder's
  // lifetime; the same Points instance + same geometry is reused)
  it('#3 — subsequent build(...) calls are no-op (same Points instance reused)', () => {
    const data1 = makeData([makeNode('d1', { state: 'domain', color: '#ff0000', position: [10, 0, 0] })]);
    populateMap(ctx, data1);
    const db = new DustBuilder();
    db.build(data1, scene, ctx);
    const dustFirst = findDust(scene);
    expect(dustFirst).toBeDefined();
    const uuidFirst = dustFirst!.uuid;
    const geomFirst = dustFirst!.geometry;
    const matFirst = dustFirst!.material as THREE.PointsMaterial;
    // Capture the original first-position vertex so we can verify colors
    // were NOT recomputed on the second build.
    const posAttrFirst = geomFirst.getAttribute('position') as THREE.BufferAttribute;
    const colorAttrFirst = geomFirst.getAttribute('color') as THREE.BufferAttribute;
    const firstX = posAttrFirst.getX(0);
    const firstColorR = colorAttrFirst.getX(0);

    // Second build with completely different data — must be a no-op.
    const ctx2 = createBuilderContext();
    const data2 = makeData([makeNode('d2', { state: 'domain', color: '#00ff00', position: [-20, 0, 0] })]);
    populateMap(ctx2, data2);
    db.build(data2, scene, ctx2);

    const dustSecond = findDust(scene);
    expect(dustSecond).toBeDefined();
    // Same instance — no replacement.
    expect(dustSecond!.uuid).toBe(uuidFirst);
    expect(dustSecond!.geometry).toBe(geomFirst);
    expect(dustSecond!.material).toBe(matFirst);
    // Same buffer values — no recomputation.
    const posAttrSecond = dustSecond!.geometry.getAttribute('position') as THREE.BufferAttribute;
    const colorAttrSecond = dustSecond!.geometry.getAttribute('color') as THREE.BufferAttribute;
    expect(posAttrSecond.getX(0)).toBe(firstX);
    expect(colorAttrSecond.getX(0)).toBe(firstColorR);
    // Still exactly one Points child in scene.
    expect(scene.children.filter((c) => c.type === 'Points').length).toBe(1);
  });

  // #4 _dustPoints uses THREE.Points (not Mesh)
  it('#4 — _dustPoints uses THREE.Points (not Mesh)', () => {
    const data = makeData([]);
    populateMap(ctx, data);
    new DustBuilder().build(data, scene, ctx);
    const dust = findDust(scene);
    expect(dust).toBeDefined();
    expect(dust).toBeInstanceOf(THREE.Points);
    // Sanity — Points reports type 'Points', not 'Mesh'.
    expect(dust!.type).toBe('Points');
  });

  // #5 PointsMaterial uses AdditiveBlending
  it('#5 — PointsMaterial uses AdditiveBlending + transparent + vertexColors + depthWrite false', () => {
    const data = makeData([]);
    populateMap(ctx, data);
    new DustBuilder().build(data, scene, ctx);
    const dust = findDust(scene)!;
    const mat = dust.material as THREE.PointsMaterial;
    expect(mat).toBeInstanceOf(THREE.PointsMaterial);
    expect(mat.blending).toBe(THREE.AdditiveBlending);
    expect(mat.transparent).toBe(true);
    expect(mat.vertexColors).toBe(true);
    expect(mat.depthWrite).toBe(false);
  });

  // #6 3000 vertices in BufferGeometry position attribute
  it('#6 — 3000 vertices in BufferGeometry position attribute', () => {
    const data = makeData([]);
    populateMap(ctx, data);
    new DustBuilder().build(data, scene, ctx);
    const dust = findDust(scene)!;
    const posAttr = dust.geometry.getAttribute('position') as THREE.BufferAttribute;
    expect(posAttr).toBeDefined();
    expect(posAttr.itemSize).toBe(3);
    expect(posAttr.count).toBe(DUST_COUNT);
    // Color attribute also present + 3-wide + DUST_COUNT entries (T3.1).
    const colorAttr = dust.geometry.getAttribute('color') as THREE.BufferAttribute;
    expect(colorAttr).toBeDefined();
    expect(colorAttr.itemSize).toBe(3);
    expect(colorAttr.count).toBe(DUST_COUNT);
  });

  // #7 (rev-2 — Mi3): positions are SceneData-independent (random in
  // [-150, 150]^3 even with zero anchors); vertex colors lerp toward anchor
  // colors near each anchor and fall back to 0x88ccff far from any anchor.
  it('#7 — positions SceneData-independent ([-150,150]^3); colors read state===domain anchors for T3.1 tinting', () => {
    // (a) Pass SceneData with zero anchors — positions must still populate
    //     3000 random points in [-150, 150]^3 (positions are not data-driven).
    const emptyData = makeData([]);
    populateMap(ctx, emptyData);
    const db1 = new DustBuilder();
    db1.build(emptyData, scene, ctx);
    const dust1 = findDust(scene)!;
    const pos1 = dust1.geometry.getAttribute('position') as THREE.BufferAttribute;
    expect(pos1.count).toBe(DUST_COUNT);
    for (let i = 0; i < DUST_COUNT; i++) {
      const x = pos1.getX(i);
      const y = pos1.getY(i);
      const z = pos1.getZ(i);
      expect(x).toBeGreaterThanOrEqual(-DUST_RANGE);
      expect(x).toBeLessThanOrEqual(DUST_RANGE);
      expect(y).toBeGreaterThanOrEqual(-DUST_RANGE);
      expect(y).toBeLessThanOrEqual(DUST_RANGE);
      expect(z).toBeGreaterThanOrEqual(-DUST_RANGE);
      expect(z).toBeLessThanOrEqual(DUST_RANGE);
    }
    // Colors with NO anchors fall back to the default cool blue 0x88ccff.
    const c1 = dust1.geometry.getAttribute('color') as THREE.BufferAttribute;
    const fallback = new THREE.Color(0x88ccff);
    for (let i = 0; i < DUST_COUNT; i++) {
      expect(c1.getX(i)).toBeCloseTo(fallback.r, 5);
      expect(c1.getY(i)).toBeCloseTo(fallback.g, 5);
      expect(c1.getZ(i)).toBeCloseTo(fallback.b, 5);
    }

    // (b) Two state===domain anchors at known positions: red at (0,0,0) +
    //     green at (140, 140, 140). Every dust vertex's color is the closer
    //     anchor's hue, optionally lerped toward fallback by distance.
    //
    // The lerp formula in the source is:
    //   t = clamp((sqrt(distSq) - 30) / 50, 0, 1)
    //   final = lerp(anchorColor, fallback, t)
    //
    // Validation rule: for each vertex, find its nearest anchor + assert
    // the color matches `lerp(anchor.color, fallback, t)` for that distance.
    const scene2 = new THREE.Scene();
    const ctx2 = createBuilderContext();
    const anchor1 = makeNode('a1', { state: 'domain', position: [0, 0, 0], color: '#ff0000' });
    const anchor2 = makeNode('a2', { state: 'domain', position: [140, 140, 140], color: '#00ff00' });
    const data2 = makeData([anchor1, anchor2]);
    populateMap(ctx2, data2);
    const db2 = new DustBuilder();
    db2.build(data2, scene2, ctx2);
    const dust2 = findDust(scene2)!;
    const pos2 = dust2.geometry.getAttribute('position') as THREE.BufferAttribute;
    const col2 = dust2.geometry.getAttribute('color') as THREE.BufferAttribute;
    expect(pos2.count).toBe(DUST_COUNT);
    expect(col2.count).toBe(DUST_COUNT);

    // Pick the 50 vertices closest to anchor1 — their color should NOT
    // match the all-fallback case (some red tint is mixed in). Conversely
    // pick the 50 closest to anchor2 — their color should carry green.
    let red = 0;
    let green = 0;
    let totalSeparation = 0;
    for (let i = 0; i < DUST_COUNT; i++) {
      const x = pos2.getX(i);
      const y = pos2.getY(i);
      const z = pos2.getZ(i);
      const d1Sq = x * x + y * y + z * z;
      const d2Sq = (x - 140) * (x - 140) + (y - 140) * (y - 140) + (z - 140) * (z - 140);
      const r = col2.getX(i);
      const g = col2.getY(i);
      const nearestColor = d1Sq < d2Sq ? new THREE.Color('#ff0000') : new THREE.Color('#00ff00');
      const dist = Math.sqrt(Math.min(d1Sq, d2Sq));
      const t = Math.min(Math.max((dist - 30) / 50, 0), 1);
      const expected = nearestColor.clone().lerp(fallback, t);
      expect(r).toBeCloseTo(expected.r, 4);
      expect(col2.getY(i)).toBeCloseTo(expected.g, 4);
      expect(col2.getZ(i)).toBeCloseTo(expected.b, 4);
      if (d1Sq < d2Sq && r > fallback.r + 0.01) red++;
      if (d2Sq < d1Sq && g > fallback.g + 0.01) green++;
      // Sanity — at least some vertices ended up near each anchor.
      if (dist < 30) totalSeparation++;
    }
    // At least a handful of vertices should land close enough to each anchor
    // to register as tinted toward that anchor's color.
    expect(red).toBeGreaterThan(0);
    expect(green).toBeGreaterThan(0);
    expect(totalSeparation).toBeGreaterThan(0);
  });

  // #8 dispose() removes _dustPoints from scene + disposes geometry/material; idempotent
  it('#8 — dispose() removes dust from scene + disposes geometry/material; idempotent', () => {
    const data = makeData([]);
    populateMap(ctx, data);
    const db = new DustBuilder();
    db.build(data, scene, ctx);
    const dust = findDust(scene)!;
    const geom = dust.geometry;
    const mat = dust.material as THREE.PointsMaterial;
    let geomDisposed = 0;
    let matDisposed = 0;
    const originalGeomDispose = geom.dispose.bind(geom);
    const originalMatDispose = mat.dispose.bind(mat);
    geom.dispose = () => {
      geomDisposed++;
      originalGeomDispose();
    };
    mat.dispose = () => {
      matDisposed++;
      originalMatDispose();
    };

    db.dispose();
    expect(findDust(scene)).toBeUndefined();
    expect(geomDisposed).toBe(1);
    expect(matDisposed).toBe(1);

    // Idempotent — second dispose no-ops (no extra dispose calls, no throw).
    expect(() => db.dispose()).not.toThrow();
    expect(geomDisposed).toBe(1);
    expect(matDisposed).toBe(1);
    expect(findDust(scene)).toBeUndefined();
  });

  // #9 (rev-2 accessor B2) — dustPoints(): THREE.Points | null returns the
  // dust instance after build; null before build + null after dispose.
  it('#9 — dustPoints() accessor: null pre-build, Points post-build, null post-dispose', () => {
    const db = new DustBuilder();
    // Pre-build → null.
    expect(db.dustPoints()).toBeNull();

    // After build → live THREE.Points instance.
    const data = makeData([]);
    populateMap(ctx, data);
    db.build(data, scene, ctx);
    const dust = db.dustPoints();
    expect(dust).not.toBeNull();
    expect(dust).toBeInstanceOf(THREE.Points);
    expect(dust).toBe(findDust(scene));

    // After dispose → back to null.
    db.dispose();
    expect(db.dustPoints()).toBeNull();
  });
});
