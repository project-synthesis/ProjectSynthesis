// frontend/src/lib/components/taxonomy/ImpactCoordinator.test.ts
//
// Sub-project C (original) — mock-based unit tests for ImpactCoordinator.
// Sub-project E (Cycle 3 RED) — shrinks the IC API: deleted tests for the
// methods/fields that now live on SelectionController
// (`isEngulfed` / `clearEngulfed` / `_tick` idle-pulse formula /
// `SELECTION_EMISSIVE_FLOOR` export / `getSelectedId` + `isFlashActive` deps);
// added tests for the new `selectionController` dep + `onImpact` wiring (B2).
//
// Spec: docs/superpowers/specs/2026-05-18-selection-state-machine-design.md
import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import type { TopologyRenderer } from './TopologyRenderer';
import type { BeamPool } from './BeamPool';
import type { EnvelopePool } from './EnvelopePool';
import type { ClusterPhysics } from './ClusterPhysics';
import type { AnimationCoordinator, AnimationHandler } from './AnimationCoordinator';
import type { SceneNode } from './TopologyData';
import type { SelectionController } from './SelectionController';
import {
  ImpactCoordinator,
  TRIGGER_PRESETS,
  type ImpactCoordinatorDeps,
  type Trigger,
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

function makeStubAnimationCoordinator(): {
  ac: AnimationCoordinator;
  registerCalls: Array<{ phase: string; handler: AnimationHandler }>;
  unregisterCalls: number;
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
  };
}

function makeStubSelectionController(): {
  sc: SelectionController;
  onImpact: ReturnType<typeof vi.fn>;
} {
  const onImpact = vi.fn();
  const sc = { onImpact } as unknown as SelectionController;
  return { sc, onImpact };
}

function makeDeps(overrides: Partial<ImpactCoordinatorDeps> = {}) {
  const { ac } = makeStubAnimationCoordinator();
  const { sc } = makeStubSelectionController();
  const deps: ImpactCoordinatorDeps = {
    beamPool: { acquire: vi.fn() } as unknown as BeamPool,
    envelopePool: { acquire: vi.fn() } as unknown as EnvelopePool,
    clusterPhysics: { onBeamImpact: vi.fn() } as unknown as ClusterPhysics,
    flashEmissive: vi.fn(),
    getSceneNode: vi.fn(),
    getBeamGroup: vi.fn(),
    renderer: { camera: new THREE.PerspectiveCamera() } as unknown as TopologyRenderer,
    animationCoordinator: ac,
    selectionController: sc,
    ...overrides,
  };
  return deps;
}

describe('ImpactCoordinator — unit tests (Sub-project E Cycle 3 RED)', () => {
  // ── #1 ──
  it('#1 — constructor does NOT register an impact-phase handler (T3.4 idle pulse moved to SC)', () => {
    // Per spec §3.4: IC's `_tick` + `_removeTick` + the constructor's
    // `animationCoordinator.register('impact', ...)` are deleted; the T3.4
    // idle pulse now lives on SelectionController.
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({ animationCoordinator: stub.ac });
    new ImpactCoordinator(deps);
    expect(stub.registerCalls).toHaveLength(0);
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
  it('#8 — onImpact fires reactions in order: clusterPhysics → envelopePool → flashEmissive', () => {
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
  it('#12 — dispose is idempotent (no per-frame handler to cancel post-§3.4)', () => {
    // Per spec §3.4: IC no longer registers an impact-phase handler, so
    // dispose() has nothing AnimationCoordinator-side to cancel. The
    // idempotence guard remains (a second dispose is a no-op).
    const stub = makeStubAnimationCoordinator();
    const deps = makeDeps({ animationCoordinator: stub.ac });
    const coord = new ImpactCoordinator(deps);
    coord.dispose();
    coord.dispose(); // second call is safe
    expect(stub.unregisterCalls).toBe(0);
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

  // ──────────────────────────────────────────────────────────────────
  // Sub-project E Cycle 3 RED — selectionController dep + onImpact wiring
  // (B2 from selection-state-machine-design rev 2 spec).
  // ──────────────────────────────────────────────────────────────────

  // ── A ──
  it('A — ImpactCoordinatorDeps interface includes selectionController dep', () => {
    // Compile-time check: constructing with selectionController works without
    // ts errors. The dep is required (no optional `?`), so omitting it would
    // be a type error post-GREEN.
    const { sc } = makeStubSelectionController();
    const ic = new ImpactCoordinator({
      ...makeDeps(),
      selectionController: sc,
    });
    expect(ic).toBeDefined();
  });

  // ── B ──
  it('B — fire({trigger:"click"}) routes engulfed-marker through selectionController.onImpact', () => {
    const { sc, onImpact } = makeStubSelectionController();
    const deps = makeDeps({ selectionController: sc });
    const coord = new ImpactCoordinator(deps);
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    // Invoke the beam's onImpact callback — that's where the engulfed-marker
    // routes happen in the new wiring.
    const beamConfig = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    beamConfig.onImpact();
    expect(onImpact).toHaveBeenCalledWith('n1');
    expect(onImpact).toHaveBeenCalledTimes(1);
  });

  // ── C ──
  it.each(['entrance', 'post-growth', 'optimization'] as const)(
    'C — fire({trigger:"%s"}) does NOT call selectionController.onImpact',
    (trigger: Trigger) => {
      const { sc, onImpact } = makeStubSelectionController();
      const deps = makeDeps({ selectionController: sc });
      const coord = new ImpactCoordinator(deps);
      coord.fire({ trigger, node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
      const beamConfig = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
      beamConfig.onImpact();
      expect(onImpact).not.toHaveBeenCalled();
    },
  );

  // ── D ──
  it('D — fire onImpact callback still invokes flashEmissive dep', () => {
    // The `flashEmissive` dep is preserved in the interface (the wiring shifts
    // in SemanticTopology to `(id, color) => selectionController.flash(id, color)`,
    // but IC still calls it inside the beam onImpact callback per the §3.4
    // re-wiring contract).
    const flashEmissive = vi.fn();
    const { sc } = makeStubSelectionController();
    const deps = makeDeps({ flashEmissive, selectionController: sc });
    const coord = new ImpactCoordinator(deps);
    coord.fire({ trigger: 'click', node: makeMockNode({ id: 'n1' }), group: makeMockGroup() });
    const beamConfig = (deps.beamPool.acquire as ReturnType<typeof vi.fn>).mock.calls[0][1];
    beamConfig.onImpact();
    expect(flashEmissive).toHaveBeenCalledWith('n1', expect.any(THREE.Color));
  });
});
