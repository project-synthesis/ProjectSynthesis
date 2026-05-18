// frontend/src/lib/components/taxonomy/ImpactCoordinator.integration.test.ts
//
// Sub-project C — 5 stub-renderer integration tests per spec §7 Cycle 1
// OPERATE.
//
// Stub renderer preserves addAnimationCallback shape verbatim; real
// EnvelopePool/BeamPool/ClusterPhysics are constructed against jsdom
// THREE (no WebGL).
import { describe, it, expect, beforeEach, vi } from 'vitest';
import * as THREE from 'three';
import { ImpactCoordinator } from './ImpactCoordinator';
import { AnimationCoordinator } from './AnimationCoordinator';
import { BeamPool } from './BeamPool';
import { EnvelopePool } from './EnvelopePool';
import { ClusterPhysics } from './ClusterPhysics';
import type { TopologyRenderer } from './TopologyRenderer';
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

describe('ImpactCoordinator — stub-renderer integration (spec §7 Cycle 1 OPERATE)', () => {
  it('INT-1: full chain — fire → beam.acquire → onImpact → envelope.acquire + flash + engulfed-marker', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    renderer.scene.add(beamPool.group);
    const envelopePool = new EnvelopePool();
    renderer.scene.add(envelopePool.group);
    const clusterPhysics = new ClusterPhysics();
    let flashCalled = false;
    let flashedId: string | null = null;
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: (id: string) => { flashCalled = true; flashedId = id; },
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      getSelectedId: () => null,
      isFlashActive: () => false,
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
    expect(coord.isEngulfed('n1')).toBe(true);
  });

  it('INT-2: clearEngulfed on click marks node; re-click after clearEngulfed re-marks', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      getSelectedId: () => 'n1',
      isFlashActive: () => false,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    const node = mkNode('n1');
    const group = new THREE.Group();
    // First click — engulfs n1
    coord.fire({ trigger: 'click', node, group });
    (acquireSpy.mock.calls[0][1] as { onImpact: () => void }).onImpact();
    expect(coord.isEngulfed('n1')).toBe(true);
    // Clear
    coord.clearEngulfed();
    expect(coord.isEngulfed('n1')).toBe(false);
    // Re-click — re-engulfs
    coord.fire({ trigger: 'click', node, group });
    (acquireSpy.mock.calls[1][1] as { onImpact: () => void }).onImpact();
    expect(coord.isEngulfed('n1')).toBe(true);
  });

  it('INT-3: dispose cancels impact-phase registration (verify stub renderer callback count goes back to baseline)', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    // After AnimationCoordinator constructor: 1 callback registered on
    // renderer (the AC's per-frame tick).
    const acBaseline = renderer._callbackCount();
    expect(acBaseline).toBe(1);
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      getSelectedId: () => null,
      isFlashActive: () => false,
      renderer: renderer as unknown as TopologyRenderer,
      animationCoordinator: ac,
    });
    // After ImpactCoordinator constructor: still 1 callback on renderer
    // (coordinator registers against AC's `impact` phase, not directly
    // against renderer). AC owns the renderer registration.
    expect(renderer._callbackCount()).toBe(1);
    // Capture AC's internal handler-count BEFORE coordinator.dispose().
    // We need a way to introspect AC's per-phase handler set. Reach into
    // private state via type assertion (acceptable in integration test).
    // AnimationCoordinator stores per-phase handler arrays in `_phases: Map<AnimationPhase, AnimationHandler[]>`
    // (verified at `AnimationCoordinator.ts:50`). The `_handlers` name from
    // earlier plan revisions was incorrect.
    const acAny = ac as unknown as { _phases?: Map<string, unknown[]> };
    const impactHandlersBefore = acAny._phases?.get('impact')?.length ?? 0;
    coord.dispose();
    const impactHandlersAfter = acAny._phases?.get('impact')?.length ?? 0;
    // dispose() removes the coordinator's single impact-phase handler.
    expect(impactHandlersAfter).toBe(impactHandlersBefore - 1);
  });

  it('INT-4: getter fallbacks — getSceneNode returns undefined uses request.node; getBeamGroup returns undefined uses request.group', () => {
    const renderer = makeStubRenderer();
    const ac = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const beamPool = new BeamPool();
    const envelopePool = new EnvelopePool();
    const clusterPhysics = new ClusterPhysics();
    const acquireSpy = vi.spyOn(beamPool, 'acquire');
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      // Both getters return undefined — fire() must fall back to request fields.
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      getSelectedId: () => null,
      isFlashActive: () => false,
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
    const coord = new ImpactCoordinator({
      beamPool, envelopePool, clusterPhysics,
      flashEmissive: () => {},
      getSceneNode: () => undefined,
      getBeamGroup: () => undefined,
      getNodeMesh: () => undefined,
      getSelectedId: () => null,
      isFlashActive: () => false,
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
