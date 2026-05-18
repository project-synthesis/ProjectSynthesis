// frontend/src/lib/components/taxonomy/ImpactCoordinator.test.ts
//
// Sub-project C — 22 mock-based unit tests for ImpactCoordinator per spec §5.1.
// Mocks for BeamPool, EnvelopePool, ClusterPhysics, AnimationCoordinator, TopologyRenderer.
//
// Spec: docs/superpowers/specs/2026-05-17-impact-coordinator-design.md
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as THREE from 'three';
import type { TopologyRenderer } from './TopologyRenderer';
import type { BeamPool } from './BeamPool';
import type { EnvelopePool } from './EnvelopePool';
import type { ClusterPhysics } from './ClusterPhysics';
import type { AnimationCoordinator, AnimationHandler } from './AnimationCoordinator';
import type { SceneNode } from './TopologyData';
import {
  ImpactCoordinator,
  TRIGGER_PRESETS,
  SELECTION_EMISSIVE_FLOOR,
  type ImpactCoordinatorDeps,
} from './ImpactCoordinator';

function makeMockNode(overrides: Partial<SceneNode> = {}): SceneNode {
  return {
    id: 'cluster-a',
    label: 'Cluster A',
    color: '#ff00ff',
    state: 'active',
    size: 5,
    position: [0, 0, 0],
    members: [],
    ...overrides,
  } as SceneNode;
}

function makeMockGroup(): THREE.Group {
  return new THREE.Group();
}

function makeMockMesh(baseEmissive = 0.5): THREE.Mesh {
  const geometry = new THREE.SphereGeometry(1, 8, 6);
  const material = new THREE.MeshStandardMaterial({ emissiveIntensity: baseEmissive });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.userData.baseEmissive = baseEmissive;
  return mesh;
}

function makeStubAnimationCoordinator(): {
  ac: AnimationCoordinator;
  registerCalls: Array<{ phase: string; handler: AnimationHandler }>;
  unregisterCalls: number;
  triggerImpactTick: (delta: number) => void;
} {
  const registerCalls: Array<{ phase: string; handler: AnimationHandler }> = [];
  let unregisterCalls = 0;
  const ac = {
    register: vi.fn((phase: string, handler: AnimationHandler) => {
      registerCalls.push({ phase, handler });
      return () => { unregisterCalls++; };
    }),
  } as unknown as AnimationCoordinator;
  return {
    ac,
    registerCalls,
    get unregisterCalls() { return unregisterCalls; },
    triggerImpactTick(delta: number) {
      const impact = registerCalls.find((r) => r.phase === 'impact');
      if (impact) impact.handler(delta);
    },
  };
}

function makeDeps(overrides: Partial<ImpactCoordinatorDeps> = {}) {
  const { ac } = makeStubAnimationCoordinator();
  const deps: ImpactCoordinatorDeps = {
    beamPool: { acquire: vi.fn() } as unknown as BeamPool,
    envelopePool: { acquire: vi.fn() } as unknown as EnvelopePool,
    clusterPhysics: { onBeamImpact: vi.fn() } as unknown as ClusterPhysics,
    flashEmissive: vi.fn(),
    getSceneNode: vi.fn(),
    getBeamGroup: vi.fn(),
    getNodeMesh: vi.fn(),
    getSelectedId: vi.fn(() => null),
    isFlashActive: vi.fn(() => false),
    renderer: { camera: new THREE.PerspectiveCamera() } as unknown as TopologyRenderer,
    animationCoordinator: ac,
    ...overrides,
  };
  return deps;
}

describe('ImpactCoordinator — 22 unit tests per spec §5.1', () => {
  // ── #1 ──
  it('#1 — constructor registers exactly one impact-phase handler', () => {
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({ animationCoordinator: stub.ac });
    new ImpactCoordinator(deps);
    expect(stub.registerCalls).toHaveLength(1);
    expect(stub.registerCalls[0].phase).toBe('impact');
    expect(typeof stub.registerCalls[0].handler).toBe('function');
  });

  // ── #2 ──
  it('#2 — fire(click) uses click preset: radius = Math.max(node.size * 0.04, 0.1); sustainMs = 800; no kineticDisplacement', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    const node = makeMockNode({ size: 5 });
    const group = makeMockGroup();
    coord.fire({ trigger: 'click', node, group });

    expect(deps.beamPool.acquire).toHaveBeenCalledTimes(1);
    const [_group, config] = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(config.radius).toBeCloseTo(Math.max(5 * 0.04, 0.1));
    expect(config.sustainMs).toBe(800);
    // Trigger the onImpact to verify kineticDisplacement gate
    config.onImpact();
    expect(deps.clusterPhysics.onBeamImpact).not.toHaveBeenCalled();
  });

  // ── #3 ──
  it('#3 — fire(entrance) uses entrance preset: kineticDisplacement off, sustainMs = 1500 + sizeFactor*500, applySizeFactor true', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    const node = makeMockNode({ size: 25 }); // sizeFactor = 25/50 = 0.5 (clamped at floor)
    const group = makeMockGroup();
    coord.fire({ trigger: 'entrance', node, group });

    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(config.sustainMs).toBeCloseTo(1500 + 0.5 * 500);
    config.onImpact();
    expect(deps.clusterPhysics.onBeamImpact).not.toHaveBeenCalled();
  });

  // ── #4 ──
  it('#4 — fire(post-growth) applies 2.0× thickness; sustainMs = 3500 + sizeFactor*500', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    const node = makeMockNode({ size: 50 }); // sizeFactor = 1.0
    const group = makeMockGroup();
    coord.fire({ trigger: 'post-growth', node, group });

    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(config.radius).toBeCloseTo(50 * 0.04 * 1.0 * 2.0);
    expect(config.sustainMs).toBeCloseTo(3500 + 1.0 * 500);
  });

  // ── #5 ──
  it('#5 — fire(optimization) uses 800ms sustain (no bonus) + kineticDisplacement on', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    const node = makeMockNode({ size: 50 });
    const group = makeMockGroup();
    coord.fire({ trigger: 'optimization', node, group });

    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(config.sustainMs).toBe(800);
    config.onImpact();
    expect(deps.clusterPhysics.onBeamImpact).toHaveBeenCalledTimes(1);
  });

  // ── #6 ──
  it('#6 — fire resolves fresh node via getSceneNode (sceneData drift)', () => {
    const freshNode = makeMockNode({ size: 10, color: '#00ff00' });
    const deps = makeDeps({ getSceneNode: vi.fn(() => freshNode) });
    const coord = new ImpactCoordinator(deps);
    const staleNode = makeMockNode({ size: 5, color: '#ff0000' });
    coord.fire({ trigger: 'click', node: staleNode, group: makeMockGroup() });

    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    expect(config.radius).toBeCloseTo(Math.max(10 * 0.04, 0.1));
    config.onImpact();
    expect(deps.envelopePool.acquire).toHaveBeenCalledWith(
      expect.anything(),
      10, // freshNode.size, not staleNode.size
      expect.anything(),
      expect.anything(),
    );
  });

  // ── #7 ──
  it('#7 — fire resolves current group via getBeamGroup (rebuildScene drift)', () => {
    const currentGroup = makeMockGroup();
    const deps = makeDeps({ getBeamGroup: vi.fn(() => currentGroup) });
    const coord = new ImpactCoordinator(deps);
    const staleGroup = makeMockGroup();
    coord.fire({ trigger: 'click', node: makeMockNode(), group: staleGroup });

    const calledGroup = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(calledGroup).toBe(currentGroup);
    expect(calledGroup).not.toBe(staleGroup);
  });

  // ── #8 ──
  it('#8 — onImpact fires reactions in order: clusterPhysics → envelopePool → flashEmissive → engulfed-marker', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    const calls: string[] = [];
    (deps.clusterPhysics.onBeamImpact as ReturnType<typeof vi.fn>).mockImplementation(() => calls.push('physics'));
    (deps.envelopePool.acquire as ReturnType<typeof vi.fn>).mockImplementation(() => calls.push('envelope'));
    (deps.flashEmissive as ReturnType<typeof vi.fn>).mockImplementation(() => calls.push('flash'));
    // 'post-growth' has kineticDisplacement=true so we see physics call
    const node = makeMockNode({ id: 'n1' });
    coord.fire({ trigger: 'post-growth', node, group: makeMockGroup() });
    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    config.onImpact();
    expect(calls).toEqual(['physics', 'envelope', 'flash']);
    // post-growth's marksEngulfed is false so isEngulfed stays false
    expect(coord.isEngulfed('n1')).toBe(false);
  });

  // ── #9 ──
  it('#9 — click trigger adds node id to _selectionEngulfed', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    const node = makeMockNode({ id: 'click-node' });
    coord.fire({ trigger: 'click', node, group: makeMockGroup() });
    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    config.onImpact();
    expect(coord.isEngulfed('click-node')).toBe(true);
  });

  // ── #10 ──
  it('#10 — non-click triggers do NOT add to _selectionEngulfed', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    for (const trigger of ['entrance', 'post-growth', 'optimization'] as const) {
      const node = makeMockNode({ id: `n-${trigger}` });
      coord.fire({ trigger, node, group: makeMockGroup() });
      const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls.at(-1)![1];
      config.onImpact();
      expect(coord.isEngulfed(`n-${trigger}`)).toBe(false);
    }
  });

  // ── #11 ──
  it('#11 — fire after dispose is a lenient no-op', () => {
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    coord.dispose();
    coord.fire({ trigger: 'click', node: makeMockNode(), group: makeMockGroup() });
    expect(deps.beamPool.acquire).not.toHaveBeenCalled();
  });

  // ── #12 ──
  it('#12 — dispose is idempotent + cancels impact-phase handler', () => {
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({ animationCoordinator: stub.ac });
    const coord = new ImpactCoordinator(deps);
    coord.dispose();
    coord.dispose(); // second call is safe
    expect(stub.unregisterCalls).toBe(1);
  });

  // ── #13 ──
  it('#13 — fire envelope passes freshNode.size DIRECTLY (no floor, no Math.max — canon F19 anti-regression)', () => {
    const tinyNode = makeMockNode({ size: 2 }); // would have been floored to 8 pre-canon-fix
    const deps = makeDeps();
    const coord = new ImpactCoordinator(deps);
    coord.fire({ trigger: 'click', node: tinyNode, group: makeMockGroup() });
    const config = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    config.onImpact();
    expect(deps.envelopePool.acquire).toHaveBeenCalledWith(
      expect.anything(),
      2, // verbatim freshNode.size — no Math.max(2, 8) inflation
      expect.anything(),
      expect.anything(),
    );
  });

  // ── #14 ──
  it('#14 — _tick skips emissive write when getSelectedId returns null', () => {
    const mesh = makeMockMesh(0.5);
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({
      animationCoordinator: stub.ac,
      getSelectedId: () => null,
      getNodeMesh: () => mesh,
    });
    new ImpactCoordinator(deps);
    stub.triggerImpactTick(0.016);
    expect((mesh.material as THREE.MeshStandardMaterial).emissiveIntensity).toBe(0.5);
  });

  // ── #15 ──
  it('#15 — _tick skips emissive write when selected node is not engulfed', () => {
    const mesh = makeMockMesh(0.5);
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({
      animationCoordinator: stub.ac,
      getSelectedId: () => 'unengulfed-node',
      getNodeMesh: () => mesh,
    });
    new ImpactCoordinator(deps);
    stub.triggerImpactTick(0.016);
    expect((mesh.material as THREE.MeshStandardMaterial).emissiveIntensity).toBe(0.5);
  });

  // ── #16 ──
  it('#16 — clearEngulfed() resets _pulseTime AND clears engulfed set; subsequent click re-populates', () => {
    const mesh = makeMockMesh(0.5);
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({
      animationCoordinator: stub.ac,
      getSelectedId: () => 'n1',
      getNodeMesh: () => mesh,
    });
    const coord = new ImpactCoordinator(deps);
    // Engulf n1 via click
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1].onImpact();
    expect(coord.isEngulfed('n1')).toBe(true);
    // Run one tick to advance _pulseTime
    stub.triggerImpactTick(0.5);
    const emissiveAfterFirstTick = (mesh.material as THREE.MeshStandardMaterial).emissiveIntensity;
    // Clear and re-engulf
    coord.clearEngulfed();
    expect(coord.isEngulfed('n1')).toBe(false);
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[1][1].onImpact();
    expect(coord.isEngulfed('n1')).toBe(true);
    // After clearEngulfed, _pulseTime = 0; first tick advances to delta=0.5
    // sine(0.5 * 0.4) = sine(0.2) ≈ 0.1987; idlePulse = 0.1987*0.1 + 0.1 = 0.1199
    stub.triggerImpactTick(0.5);
    const emissiveAfterReEngulf = (mesh.material as THREE.MeshStandardMaterial).emissiveIntensity;
    // The two values should match (same _pulseTime starting state)
    expect(emissiveAfterReEngulf).toBeCloseTo(emissiveAfterFirstTick, 5);
  });

  // ── #17 ──
  it('#17 — _tick writes T3.4 idle pulse formula on engulfed selected node', () => {
    const mesh = makeMockMesh(0.5);
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({
      animationCoordinator: stub.ac,
      getSelectedId: () => 'n1',
      getNodeMesh: () => mesh,
    });
    const coord = new ImpactCoordinator(deps);
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1].onImpact();
    stub.triggerImpactTick(0.016);
    const t = 0.016;
    const expectedPulse = Math.sin(t * 0.4) * 0.1 + 0.1;
    const expected = Math.max(0.5, SELECTION_EMISSIVE_FLOOR) + expectedPulse;
    expect((mesh.material as THREE.MeshStandardMaterial).emissiveIntensity).toBeCloseTo(expected, 5);
  });

  // ── #18 ──
  it('#18 — _tick skips emissive write when isFlashActive returns true (flash owns ramp)', () => {
    const mesh = makeMockMesh(0.5);
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({
      animationCoordinator: stub.ac,
      getSelectedId: () => 'n1',
      getNodeMesh: () => mesh,
      isFlashActive: () => true,
    });
    const coord = new ImpactCoordinator(deps);
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1].onImpact();
    (mesh.material as THREE.MeshStandardMaterial).emissiveIntensity = 999; // sentinel
    stub.triggerImpactTick(0.016);
    expect((mesh.material as THREE.MeshStandardMaterial).emissiveIntensity).toBe(999);
  });

  // ── #19 ──
  it('#19 — _tick uses real-time delta accumulator (framerate-independent)', () => {
    const mesh1 = makeMockMesh(0.5);
    const mesh2 = makeMockMesh(0.5);
    const stub1 = makeStubAnimationCoordinator();
    const stub2 = makeStubAnimationCoordinator();
    const deps1 = makeDeps({ animationCoordinator: stub1.ac, getSelectedId: () => 'n1', getNodeMesh: () => mesh1 });
    const deps2 = makeDeps({ animationCoordinator: stub2.ac, getSelectedId: () => 'n1', getNodeMesh: () => mesh2 });
    const c1 = new ImpactCoordinator(deps1);
    const c2 = new ImpactCoordinator(deps2);
    [c1, c2].forEach((c, i) => {
      const d = i === 0 ? deps1 : deps2;
      c.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
      (d.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1].onImpact();
    });
    // c1: 1 tick of delta=0.5; c2: 50 ticks of delta=0.01 (both total 0.5s)
    stub1.triggerImpactTick(0.5);
    for (let i = 0; i < 50; i++) stub2.triggerImpactTick(0.01);
    const e1 = (mesh1.material as THREE.MeshStandardMaterial).emissiveIntensity;
    const e2 = (mesh2.material as THREE.MeshStandardMaterial).emissiveIntensity;
    expect(e1).toBeCloseTo(e2, 5);
  });

  // ── #20 ──
  it('#20 — fire falls back to request.node when getSceneNode returns undefined; to request.group when getBeamGroup returns undefined', () => {
    const deps = makeDeps({
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
    });
    const coord = new ImpactCoordinator(deps);
    const node = makeMockNode({ size: 7, color: '#abcdef' });
    const group = makeMockGroup();
    coord.fire({ trigger: 'click', node, group });

    const [calledGroup, config] = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(calledGroup).toBe(group); // fell back to request.group
    expect(config.radius).toBeCloseTo(Math.max(7 * 0.04, 0.1)); // fell back to request.node.size
  });

  // ── #21 ──
  it('#21 — _tick skips when getNodeMesh returns undefined (mid-rebuild race)', () => {
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({
      animationCoordinator: stub.ac,
      getSelectedId: () => 'n1',
      getNodeMesh: () => undefined,
    });
    const coord = new ImpactCoordinator(deps);
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1].onImpact();
    expect(() => stub.triggerImpactTick(0.016)).not.toThrow();
  });

  // ── #22 ──
  it('#22 — TRIGGER_PRESETS has exactly 4 keys with correct shape', () => {
    const keys = Object.keys(TRIGGER_PRESETS).sort();
    expect(keys).toEqual(['click', 'entrance', 'optimization', 'post-growth'].sort());
    for (const trigger of keys) {
      const preset = TRIGGER_PRESETS[trigger as keyof typeof TRIGGER_PRESETS];
      expect(preset).toHaveProperty('sustainMs');
      expect(preset).toHaveProperty('thicknessMultiplier');
      expect(preset).toHaveProperty('applySizeFactor');
      expect(preset).toHaveProperty('applyClickRadiusFloor');
      expect(preset).toHaveProperty('kineticDisplacement');
      expect(preset).toHaveProperty('sizeFactorSustainBonus');
      expect(preset).toHaveProperty('marksEngulfed');
    }
    // click is the only one that marks engulfed
    expect(TRIGGER_PRESETS.click.marksEngulfed).toBe(true);
    expect(TRIGGER_PRESETS.entrance.marksEngulfed).toBe(false);
    expect(TRIGGER_PRESETS['post-growth'].marksEngulfed).toBe(false);
    expect(TRIGGER_PRESETS.optimization.marksEngulfed).toBe(false);
  });
});
