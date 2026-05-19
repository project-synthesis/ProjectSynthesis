// frontend/src/lib/components/taxonomy/ImpactCoordinator.integration.test.ts
//
// Sub-project C — stub-renderer integration tests (originally 5).
// Sub-project E Cycle 3 OPERATE — migrated to the post-shrink IC API:
//   - `getSelectedId` / `isFlashActive` deps removed (SC owns those).
//   - `coord.isEngulfed(id)` removed; engulfed-set lives on SelectionController.
//   - `coord.clearEngulfed()` removed; cancellation flows through
//     `SelectionController.select` (cancel-via-idle path).
//   - INT-1 verifies the new contract: IC's onImpact body routes the
//     impacted-node id through `deps.selectionController.onImpact(id)`
//     for 'click' triggers (the only preset with `marksEngulfed: true`).
//   - INT-2 verifies the same routing fires for repeated clicks — IC has
//     no internal cancel-via-clear path anymore; the SC owns
//     "stale selection → engulfed-set cleanup" via cancel-via-idle inside
//     `SelectionController.select`. The behavior previously exercised by
//     this integration test ("clear-then-re-mark") is covered by
//     SelectionController.test.ts #25-#26 + SelectionController.integration
//     INT-4 + the SC's own `select(other)` mid-decay zombie-transition guard.
//   - INT-3 verifies the post-shrink dispose semantics: IC's constructor no
//     longer registers an `impact`-phase handler (per IC.test.ts #1, the
//     T3.4 idle pulse moved to SelectionController). dispose() now only
//     toggles the `_disposed` flag — subsequent `fire()` calls become
//     no-ops.
// T3.4 idle-pulse integration test deleted entirely: behavior migrated
// to SelectionController._tickIdlePulse, covered by SC unit tests
// #25-#26 + SC integration INT-4.
//
// Stub renderer preserves addAnimationCallback shape verbatim; real
// EnvelopePool/BeamPool/ClusterPhysics are constructed against jsdom
// THREE (no WebGL).
import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import { ImpactCoordinator } from './ImpactCoordinator';
import { AnimationCoordinator } from './AnimationCoordinator';
import { BeamPool } from './BeamPool';
import { EnvelopePool } from './EnvelopePool';
import { ClusterPhysics } from './ClusterPhysics';
import type { TopologyRenderer } from './TopologyRenderer';
import type { SelectionController } from './SelectionController';
import type { SceneNode } from './TopologyData';

function makeStubRenderer() {
  const _animateCallbacks: Array<() => void> = [];
  return {
    addAnimationCallback(cb: () => void) {
      _animateCallbacks.push(cb);
      return () => {
        const idx = _animateCallbacks.indexOf(cb);
        if (idx >= 0) _animateCallbacks.splice(idx, 1);
      };
    },
    camera: new THREE.PerspectiveCamera(),
    scene: new THREE.Scene(),
    _flushCallbacks: () => { for (const cb of _animateCallbacks) cb(); },
    _callbackCount: () => _animateCallbacks.length,
  };
}

function makeSelectionController(): {
  sc: SelectionController;
  onImpact: ReturnType<typeof vi.fn>;
} {
  const onImpact = vi.fn();
  const sc = { onImpact } as unknown as SelectionController;
  return { sc, onImpact };
}

function mkNode(id: string, size = 5): SceneNode {
  // Cast via `unknown` — partial SceneNode test fixture omits the many
  // bookkeeping fields (opacity/persistence/visible/coherence/avgScore/
  // domain/memberCount/isSubDomain/template_count). ImpactCoordinator only
  // reads id/size/color/state, so missing fields are safe for these tests.
  // The cast pattern mirrors the `renderer as unknown as TopologyRenderer`
  // idiom used 5x in this same file.
  return {
    id, label: id, color: '#ff00ff', state: 'active', size,
    position: [0, 0, 0], members: [],
  } as unknown as SceneNode;
}

describe('ImpactCoordinator — stub-renderer integration (Sub-project E Cycle 3 OPERATE)', () => {
  it('INT-1: full chain — fire(click) → beam.acquire → onImpact → envelope.acquire + flash + selectionController.onImpact(id)', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    renderer.scene.add(beamPool.group);
    const envelopePool = new EnvelopePool();
    renderer.scene.add(envelopePool.group);
    const clusterPhysics = new ClusterPhysics();
    let flashCalled = false;
    let flashedId: string | null = null;
    const { sc, onImpact } = makeSelectionController();
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: (id: string) => { flashCalled = true; flashedId = id; },
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      selectionController: sc,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    const node = mkNode('n1');
    const group = new THREE.Group();
    coord.fire({ trigger: 'click', node, group });
    // After fire(), the beam was acquired against the stub renderer's
    // pool. BeamPool tracks beams in `_beams: PlasmaBeam[]` with per-beam
    // `state` (verified in `BeamPool.ts:10`). An acquired beam transitions
    // from 'idle' to 'firing'. Verify at least one non-idle beam exists.
    const beams = (beamPool as unknown as { _beams: Array<{ state: string }> })._beams;
    expect(beams.filter((b) => b.state !== 'idle').length).toBeGreaterThan(0);
    // Triggering the beam's onImpact (synthesized — we don't have a
    // real timed beam loop) fires the F19 reactions in order.
    // Simulate by extracting the most recent acquire's onImpact callback
    // and invoking it.
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    coord.fire({ trigger: 'click', node, group });
    const call = acquireSpy.mock.calls.at(-1);
    if (call) {
      const config = call[1] as { onImpact?: () => void };
      config.onImpact?.();
    }
    expect(flashCalled).toBe(true);
    expect(flashedId).toBe('n1');
    // Post-shrink contract: IC routes engulfed-marker through SC.onImpact
    // for 'click' triggers (`marksEngulfed: true`). SC owns the engulfed set.
    expect(onImpact).toHaveBeenCalledWith('n1');
    expect(onImpact).toHaveBeenCalledTimes(1);
  });

  it('INT-2: repeated click fires selectionController.onImpact each time (IC is stateless — no internal clear API needed)', () => {
    // Sub-project E Cycle 3 OPERATE — the original test verified IC's
    // own `clearEngulfed` semantics. That method no longer exists; the SC
    // owns engulfed-set lifecycle via cancel-via-idle inside `select(other)`.
    // The IC-level contract is "every click fires SC.onImpact" — verify that.
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    const { sc, onImpact } = makeSelectionController();
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      selectionController: sc,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    const node = mkNode('n1');
    const group = new THREE.Group();
    // First click — SC.onImpact called once with 'n1'.
    coord.fire({ trigger: 'click', node, group });
    (acquireSpy.mock.calls[0][1] as { onImpact: () => void }).onImpact();
    expect(onImpact).toHaveBeenCalledTimes(1);
    expect(onImpact).toHaveBeenLastCalledWith('n1');
    // Second click on the same node — SC.onImpact called a second time.
    // SC owns dedupe/idempotency at its layer; IC unconditionally routes.
    coord.fire({ trigger: 'click', node, group });
    (acquireSpy.mock.calls[1][1] as { onImpact: () => void }).onImpact();
    expect(onImpact).toHaveBeenCalledTimes(2);
    expect(onImpact).toHaveBeenLastCalledWith('n1');
  });

  it('INT-3: post-shrink dispose — IC constructor does not register an impact-phase handler; dispose makes fire() a no-op', () => {
    // Sub-project E Cycle 3 OPERATE — IC's `_tick`/`_removeTick`/the
    // constructor's `animationCoordinator.register('impact', ...)` are
    // deleted; the T3.4 idle pulse now lives on SelectionController.
    // The integration-level invariant is therefore:
    //   1. AC handler count for `impact` phase stays at 0 across IC ctor.
    //   2. dispose() flips the `_disposed` flag so subsequent fire() no-ops.
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    // After AnimationCoordinator constructor: 1 callback registered on
    // renderer (the AC's per-frame tick).
    const acBaseline = renderer._callbackCount();
    expect(acBaseline).toBe(1);
    // AnimationCoordinator stores per-phase handler arrays in
    // `_phases: Map<AnimationPhase, AnimationHandler[]>`.
    const acAny = ac as unknown as { _phases?: Map<string, unknown[]> };
    const impactHandlersBeforeCtor = acAny._phases?.get('impact')?.length ?? 0;
    const { sc } = makeSelectionController();
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      selectionController: sc,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    // After IC ctor: NO new impact-phase handlers added (T3.4 lives on SC now).
    const impactHandlersAfterCtor = acAny._phases?.get('impact')?.length ?? 0;
    expect(impactHandlersAfterCtor).toBe(impactHandlersBeforeCtor);
    // Renderer callback count unchanged (AC owns the renderer registration).
    expect(renderer._callbackCount()).toBe(1);
    // dispose() flips _disposed; subsequent fire() short-circuits.
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    coord.dispose();
    const node = mkNode('n1');
    const group = new THREE.Group();
    coord.fire({ trigger: 'click', node, group });
    expect(acquireSpy).not.toHaveBeenCalled();
    // Idempotent dispose.
    expect(() => coord.dispose()).not.toThrow();
  });

  it('INT-4: getter fallbacks — getSceneNode returns undefined uses request.node; getBeamGroup returns undefined uses request.group', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    const { sc } = makeSelectionController();
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      // Both getters return undefined — fire() must fall back to request fields.
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      selectionController: sc,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    const node = mkNode('fallback-id', 11);
    const group = new THREE.Group();
    coord.fire({ trigger: 'click', node, group });
    // The acquire call's group argument must be the caller-supplied request.group
    // (fallback path), not anything resolved from getBeamGroup.
    const [calledGroup, calledConfig] = acquireSpy.mock.calls[0];
    expect(calledGroup).toBe(group);
    // The radius computation used freshNode.size = node.size = 11 (no override).
    expect((calledConfig as { radius: number }).radius).toBeCloseTo(Math.max(11 * 0.04, 0.1));
  });

  it('INT-5: trigger-preset radius formula verifiable end-to-end (click on size=2 node yields beam.radius ≈ 0.1 via Math.max floor)', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    const { sc } = makeSelectionController();
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      selectionController: sc,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    const tinyNode = mkNode('tiny', 2);
    const group = new THREE.Group();
    coord.fire({ trigger: 'click', node: tinyNode, group });
    const config = acquireSpy.mock.calls[0][1] as { radius: number };
    // size=2 × 0.04 = 0.08; Math.max(0.08, 0.1) = 0.1 floor.
    expect(config.radius).toBeCloseTo(0.1, 5);
  });
});
