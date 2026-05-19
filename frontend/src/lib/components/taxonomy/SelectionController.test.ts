// frontend/src/lib/components/taxonomy/SelectionController.test.ts
//
// Sub-project E — Cycle 2 RED. 29 tests pinning the SelectionController
// contract per spec §5.1 (docs/superpowers/specs/2026-05-18-selection-state-
// machine-design.md). Style anchor: FlashController.test.ts.
//
// Test catalog:
//   1-12 State transitions (idle → focusing → focused → impacting → engulfed
//        + cancel-via-idle (B1) + setTimeout decay (B3) + onComplete (B8) +
//        IC.fire click (B2))
//   13-17 Illegal transitions (onImpact gate + _transition dev-throw / prod-warn)
//   18-20 Engulfment gate (isEngulfed semantics across the lifecycle)
//   21-24 Highlight (B4 snapshot + B5 dual swap on apply/restore/afterRebuild)
//   25-26 T3.4 idle pulse (formula in engulfed; skip otherwise)
//   27-28 Dispose (cancel handler + clear timer + lenient post-dispose)
//   29    Flash bridge (delegates to FlashController.flash)

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import * as THREE from 'three';
import { SelectionController, SELECTION_EMISSIVE_FLOOR } from './SelectionController';
import type { TopologyRenderer } from './TopologyRenderer';
import type { AnimationCoordinator } from './AnimationCoordinator';
import type { ImpactCoordinator } from './ImpactCoordinator';
import type { SceneNode } from './TopologyData';

// ── Harness factories ──────────────────────────────────────────────
// Shape mirrors FlashController.test.ts so the assembled stubs match the
// real dep surface from `SelectionControllerDeps`. We stub each collaborator
// independently — SC owns the orchestration; the deps are opaque side effects.

function makeMesh(baseEmissive: number, color = 0xff8800): THREE.Mesh {
  const geo = new THREE.SphereGeometry(1);
  const mat = new THREE.MeshStandardMaterial({ color, emissive: color });
  mat.emissiveIntensity = baseEmissive;
  const mesh = new THREE.Mesh(geo, mat);
  mesh.userData.baseEmissive = baseEmissive;
  return mesh;
}

function makeCoordinator() {
  const handlers: Array<{ phase: string; fn: (d: number) => void }> = [];
  const register = vi.fn((phase: string, fn: (d: number) => void) => {
    const e = { phase, fn };
    handlers.push(e);
    return () => {
      const i = handlers.indexOf(e);
      if (i >= 0) handlers.splice(i, 1);
    };
  });
  return { register, handlers } as unknown as AnimationCoordinator & {
    handlers: typeof handlers;
  };
}

function makeRenderer() {
  const focusOn = vi.fn();
  return { focusOn } as unknown as TopologyRenderer & { focusOn: typeof focusOn };
}

function makeIC() {
  const fire = vi.fn();
  return { fire } as unknown as ImpactCoordinator & { fire: typeof fire };
}

function makeSceneNode(id: string): SceneNode {
  return {
    id,
    label: id,
    size: 10,
    color: 0xff8800,
    position: [0, 0, 0],
  } as unknown as SceneNode;
}

/**
 * Build a SelectionController and its bag of stubs in one shot. The harness
 * keeps the per-test boilerplate small: tests reach into `meshes`/`sceneNodes`/
 * `groups` to register fixtures, then drive SC.
 */
function makeHarness(opts?: { highlightColor?: number }) {
  const coord = makeCoordinator();
  const renderer = makeRenderer();
  const ic = makeIC();
  const meshes = new Map<string, THREE.Mesh>();
  const sceneNodes = new Map<string, SceneNode>();
  const groups = new Map<string, THREE.Group>();
  const sc = new SelectionController({
    renderer,
    animationCoordinator: coord,
    impactCoordinator: ic,
    getNodeMesh: (id) => meshes.get(id),
    getSceneNode: (id) => sceneNodes.get(id),
    getBeamGroup: (id) => groups.get(id),
    highlightColor: opts?.highlightColor ?? 0x00ffff,
  });
  return { sc, coord, renderer, ic, meshes, sceneNodes, groups };
}

/** Drive SC fully into 'focused' state: register fixtures, select, invoke onComplete. */
function driveToFocused(h: ReturnType<typeof makeHarness>, id = 'n1', baseEm = 0.6) {
  const mesh = makeMesh(baseEm);
  h.meshes.set(id, mesh);
  h.sceneNodes.set(id, makeSceneNode(id));
  h.groups.set(id, new THREE.Group());
  h.sc.select(id);
  // 4th positional arg to focusOn is the onComplete callback (B8 wiring).
  const onComplete = (
    h.renderer.focusOn.mock.calls[0][3] as () => void
  );
  onComplete();
  return { mesh };
}

/** Drive SC fully into 'engulfed' state (full lifecycle). */
function driveToEngulfed(h: ReturnType<typeof makeHarness>, id = 'n1', baseEm = 0.6) {
  const { mesh } = driveToFocused(h, id, baseEm);
  h.sc.onImpact(id);
  vi.advanceTimersByTime(1380);
  return { mesh };
}

// ── Tests ──────────────────────────────────────────────────────────

describe('SelectionController', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllEnvs();
  });

  // ── State transitions (12 tests) ─────────────────────────────────

  it('1. initial state is "idle"', () => {
    const h = makeHarness();
    expect(h.sc.state).toBe('idle');
    expect(h.sc.selectedId).toBeNull();
  });

  it('2. select(nodeId) from idle → focusing; renderer.focusOn called with 4 args incl. onComplete (B8)', () => {
    const h = makeHarness();
    h.meshes.set('n1', makeMesh(0.6));
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    h.sc.select('n1');
    expect(h.sc.state).toBe('focusing');
    expect(h.sc.selectedId).toBe('n1');
    expect(h.renderer.focusOn).toHaveBeenCalledTimes(1);
    const call = h.renderer.focusOn.mock.calls[0];
    // 4 positional args: target (Vector3-ish), distance (undefined), duration (undefined), onComplete (fn)
    expect(call.length).toBe(4);
    expect(call[1]).toBeUndefined();
    expect(call[2]).toBeUndefined();
    expect(typeof call[3]).toBe('function');
  });

  it('3. select(nodeId) invokes impactCoordinator.fire({trigger:"click", node, group}) (B2)', () => {
    const h = makeHarness();
    const mesh = makeMesh(0.6);
    const sceneNode = makeSceneNode('n1');
    const group = new THREE.Group();
    h.meshes.set('n1', mesh);
    h.sceneNodes.set('n1', sceneNode);
    h.groups.set('n1', group);
    h.sc.select('n1');
    expect(h.ic.fire).toHaveBeenCalledTimes(1);
    expect(h.ic.fire).toHaveBeenCalledWith({
      trigger: 'click',
      node: sceneNode,
      group,
    });
  });

  it('4. _onFocusAnimationComplete (via captured onComplete) transitions focusing → focused', () => {
    const h = makeHarness();
    h.meshes.set('n1', makeMesh(0.6));
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    h.sc.select('n1');
    expect(h.sc.state).toBe('focusing');
    const onComplete = h.renderer.focusOn.mock.calls[0][3] as () => void;
    onComplete();
    expect(h.sc.state).toBe('focused');
  });

  it('5. onImpact(id) from focused → impacting (when id matches selectedId)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('impacting');
  });

  it('6. onImpact starts setTimeout(GLOW_TOTAL_MS=1380); advance 1380ms → state is "engulfed" (B3)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('impacting');
    vi.advanceTimersByTime(1379);
    expect(h.sc.state).toBe('impacting');
    vi.advanceTimersByTime(1); // hits 1380ms exactly
    expect(h.sc.state).toBe('engulfed');
  });

  it('7. deselect() from engulfed → idle; highlight cleared', () => {
    const h = makeHarness();
    const { mesh } = driveToEngulfed(h, 'n1');
    const mat = mesh.material as THREE.MeshStandardMaterial;
    // Highlight currently applied (cyan) — deselect should restore original 0xff8800.
    expect(mat.color.getHex()).toBe(0x00ffff);
    h.sc.deselect();
    expect(h.sc.state).toBe('idle');
    expect(h.sc.selectedId).toBeNull();
    expect(mat.color.getHex()).toBe(0xff8800);
    expect(mat.emissive.getHex()).toBe(0xff8800);
  });

  it('8. select(otherId) from engulfed: cancel-via-idle → focusing; engulfed-set cleared (B1)', () => {
    const h = makeHarness();
    driveToEngulfed(h, 'n1');
    expect(h.sc.isEngulfed('n1')).toBe(true);
    // Register fixtures for the second selection.
    h.meshes.set('n2', makeMesh(0.6));
    h.sceneNodes.set('n2', makeSceneNode('n2'));
    h.groups.set('n2', new THREE.Group());
    h.sc.select('n2');
    expect(h.sc.state).toBe('focusing');
    expect(h.sc.selectedId).toBe('n2');
    // engulfed-set cleared per B1 cancel-via-idle semantics.
    expect(h.sc.isEngulfed('n1')).toBe(false);
  });

  it('9. select(otherId) from impacting: cancels pending decay timer (B1+B3 fake-timer roundtrip)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('impacting');
    // Register fixtures for the second selection BEFORE the cancel.
    h.meshes.set('n2', makeMesh(0.6));
    h.sceneNodes.set('n2', makeSceneNode('n2'));
    h.groups.set('n2', new THREE.Group());
    h.sc.select('n2'); // triggers cancel-via-idle → clears _decayTimer → re-arms as 'focusing'
    expect(h.sc.state).toBe('focusing');
    expect(h.sc.selectedId).toBe('n2');
    // The previously-pending decay timer (would have transitioned n1 to engulfed)
    // must NOT fire — advancing past the original 1380ms keeps state at 'focusing'
    // (the new selection's onComplete has not been invoked yet).
    vi.advanceTimersByTime(1380);
    expect(h.sc.state).toBe('focusing'); // NOT 'engulfed'
    expect(h.sc.selectedId).toBe('n2');
  });

  it('10. deselect() from focusing → idle', () => {
    const h = makeHarness();
    h.meshes.set('n1', makeMesh(0.6));
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    h.sc.select('n1');
    expect(h.sc.state).toBe('focusing');
    h.sc.deselect();
    expect(h.sc.state).toBe('idle');
    expect(h.sc.selectedId).toBeNull();
  });

  it('10b. select(otherId) from focused: cancel-via-idle → focusing (B1, direct focused → idle pin)', () => {
    // Adjacency-coverage closure: spec §3.1 lists `focused → idle` with two
    // triggers — `deselect()` (covered by #11 via select(null)) and
    // `select(otherNodeId)` cancel-via-idle. Tests #8 / #9 pin cancel-via-idle
    // from engulfed / impacting; this test pins the focused → idle leg of the
    // cancel-via-idle path explicitly.
    const h = makeHarness();
    driveToFocused(h, 'n1');
    expect(h.sc.state).toBe('focused');
    h.meshes.set('n2', makeMesh(0.6));
    h.sceneNodes.set('n2', makeSceneNode('n2'));
    h.groups.set('n2', new THREE.Group());
    h.sc.select('n2');
    // Cancel-via-idle: focused → idle → focusing.
    expect(h.sc.state).toBe('focusing');
    expect(h.sc.selectedId).toBe('n2');
  });

  it('11. select(null) is equivalent to deselect()', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    expect(h.sc.state).toBe('focused');
    h.sc.select(null);
    expect(h.sc.state).toBe('idle');
    expect(h.sc.selectedId).toBeNull();
  });

  it('12. same-id select(id) is a no-op (no re-transition, no extra IC.fire)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    expect(h.sc.state).toBe('focused');
    expect(h.ic.fire).toHaveBeenCalledTimes(1);
    h.sc.select('n1'); // same id
    // State stays at focused; focusOn + fire NOT called again.
    expect(h.sc.state).toBe('focused');
    expect(h.ic.fire).toHaveBeenCalledTimes(1);
    expect(h.renderer.focusOn).toHaveBeenCalledTimes(1);
  });

  // ── Illegal transitions (5 tests) ────────────────────────────────

  it('13. onImpact(id) from idle is a no-op (state stays idle)', () => {
    const h = makeHarness();
    expect(h.sc.state).toBe('idle');
    h.sc.onImpact('n1'); // no selection — must not throw, must not transition
    expect(h.sc.state).toBe('idle');
  });

  it('14. onImpact(id) with non-matching id (id !== selectedId) is no-op', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n2'); // stale beam from a prior selection
    expect(h.sc.state).toBe('focused');
  });

  it('15. onImpact(id) when state is impacting or engulfed (not focused) is no-op', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('impacting');
    // Second onImpact while impacting — must not re-start decay or transition.
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('impacting');
    // Drive to engulfed.
    vi.advanceTimersByTime(1380);
    expect(h.sc.state).toBe('engulfed');
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('engulfed');
  });

  it('16. forced internal _transition("engulfed") from idle throws in dev mode', () => {
    // `import.meta.env.DEV` defaults to `true` under Vitest (default mode is dev);
    // forced illegal transition should throw.
    const h = makeHarness();
    expect(h.sc.state).toBe('idle');
    // Test seam: access private _transition via typed cast for illegal-edge coverage.
    const seam = h.sc as unknown as { _transition: (s: string) => void };
    expect(() => {
      seam._transition('engulfed');
    }).toThrow(/illegal transition/i);
  });

  it('17. forced illegal transition logs console.warn OR throws — message format matches /illegal transition/ in both branches (DEV=true throws, DEV=false warns)', () => {
    // Production-vs-dev branch: spec _transition throws Error in dev mode, calls
    // console.warn in production mode. `import.meta.env.DEV` is fixed at module
    // load under Vitest (the default mode is dev), so `vi.stubEnv('DEV', false)`
    // may not flip it at runtime for SC. This test pins the INVARIANT —
    // SOMETHING must be raised (either an error throw or a warn call), and
    // whatever it is, the message must contain "illegal transition".
    vi.stubEnv('DEV', false);
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    let threwError: Error | null = null;
    try {
      const h = makeHarness();
      // Test seam: access private _transition via typed cast.
      const seam = h.sc as unknown as { _transition: (s: string) => void };
      try {
        seam._transition('engulfed');
      } catch (e) {
        threwError = e as Error;
      }
      // Either a throw happened (dev branch) OR warn was called (prod branch).
      const warned = warnSpy.mock.calls.length > 0;
      expect(threwError !== null || warned).toBe(true);
      if (threwError !== null) {
        expect(threwError.message).toMatch(/illegal transition/i);
      }
      if (warned) {
        expect(warnSpy).toHaveBeenCalledWith(
          expect.stringMatching(/illegal transition/i),
        );
      }
    } finally {
      warnSpy.mockRestore();
    }
  });

  // ── Engulfment gate (3 tests) ────────────────────────────────────

  it('18. isEngulfed(id) false after select(id) (before any impact)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    expect(h.sc.isEngulfed('n1')).toBe(false);
  });

  it('19. isEngulfed(id) true after onImpact(id) (during both impacting AND engulfed)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n1');
    expect(h.sc.state).toBe('impacting');
    expect(h.sc.isEngulfed('n1')).toBe(true);
    vi.advanceTimersByTime(1380);
    expect(h.sc.state).toBe('engulfed');
    expect(h.sc.isEngulfed('n1')).toBe(true);
  });

  it('20. isEngulfed(id) cleared on new select(otherId)', () => {
    const h = makeHarness();
    driveToEngulfed(h, 'n1');
    expect(h.sc.isEngulfed('n1')).toBe(true);
    h.meshes.set('n2', makeMesh(0.6));
    h.sceneNodes.set('n2', makeSceneNode('n2'));
    h.groups.set('n2', new THREE.Group());
    h.sc.select('n2');
    expect(h.sc.isEngulfed('n1')).toBe(false);
  });

  // ── Highlight (4 tests, B4 + B5) ─────────────────────────────────

  it('21. select(id) writes highlightColor to BOTH mat.color AND mat.emissive (B5 dual swap)', () => {
    const h = makeHarness();
    const mesh = makeMesh(0.6, 0xff8800);
    h.meshes.set('n1', mesh);
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    const mat = mesh.material as THREE.MeshStandardMaterial;
    expect(mat.color.getHex()).toBe(0xff8800);
    expect(mat.emissive.getHex()).toBe(0xff8800);
    h.sc.select('n1');
    expect(mat.color.getHex()).toBe(0x00ffff);
    expect(mat.emissive.getHex()).toBe(0x00ffff);
  });

  it('22. _applyHighlight snapshots mat.color.getHex() BEFORE writing highlight color (B4 — verified via deselect roundtrip)', () => {
    const h = makeHarness();
    const originalHex = 0xff8800;
    const mesh = makeMesh(0.6, originalHex);
    h.meshes.set('n1', mesh);
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    const mat = mesh.material as THREE.MeshStandardMaterial;
    h.sc.select('n1');
    // Highlight applied — color/emissive are cyan.
    expect(mat.color.getHex()).toBe(0x00ffff);
    h.sc.deselect();
    // Snapshot must have captured original 0xff8800 BEFORE writing 0x00ffff —
    // restoring on deselect lands at original, not cyan.
    expect(mat.color.getHex()).toBe(originalHex);
    expect(mat.emissive.getHex()).toBe(originalHex);
  });

  it('23. afterRebuild() re-applies highlight to selected mesh (idempotent across multiple cycles)', () => {
    const h = makeHarness();
    const originalHex = 0xff8800;
    const mesh = makeMesh(0.6, originalHex);
    h.meshes.set('n1', mesh);
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    const mat = mesh.material as THREE.MeshStandardMaterial;
    h.sc.select('n1');
    expect(mat.color.getHex()).toBe(0x00ffff);
    // Simulate rebuildScene: replace the mesh with a fresh one carrying the
    // same original color (the dispose() in rebuildScene would have wiped the
    // old one). SC.afterRebuild() must re-paint the new mesh cyan.
    const freshMesh = makeMesh(0.6, originalHex);
    h.meshes.set('n1', freshMesh);
    const freshMat = freshMesh.material as THREE.MeshStandardMaterial;
    expect(freshMat.color.getHex()).toBe(originalHex);
    expect(freshMat.emissive.getHex()).toBe(originalHex);
    h.sc.afterRebuild();
    expect(freshMat.color.getHex()).toBe(0x00ffff);
    expect(freshMat.emissive.getHex()).toBe(0x00ffff);
    // Second rebuild cycle — idempotent.
    const fresher = makeMesh(0.6, originalHex);
    h.meshes.set('n1', fresher);
    h.sc.afterRebuild();
    const fresherMat = fresher.material as THREE.MeshStandardMaterial;
    expect(fresherMat.color.getHex()).toBe(0x00ffff);
    expect(fresherMat.emissive.getHex()).toBe(0x00ffff);
  });

  it('24. deselect() restores _highlightedColor snapshot to BOTH mat.color AND mat.emissive', () => {
    const h = makeHarness();
    const originalHex = 0xaa3344; // pick a non-default hex to make the snapshot unambiguous
    const mesh = makeMesh(0.6, originalHex);
    h.meshes.set('n1', mesh);
    h.sceneNodes.set('n1', makeSceneNode('n1'));
    h.groups.set('n1', new THREE.Group());
    const mat = mesh.material as THREE.MeshStandardMaterial;
    h.sc.select('n1');
    expect(mat.color.getHex()).toBe(0x00ffff);
    expect(mat.emissive.getHex()).toBe(0x00ffff);
    h.sc.deselect();
    expect(mat.color.getHex()).toBe(originalHex);
    expect(mat.emissive.getHex()).toBe(originalHex);
  });

  // ── Idle pulse (2 tests) ─────────────────────────────────────────

  it('25. T3.4 idle pulse formula in "engulfed" writes correct emissive (delta=0.016)', () => {
    const h = makeHarness();
    const baseEm = 0.6;
    const { mesh } = driveToEngulfed(h, 'n1', baseEm);
    expect(h.sc.state).toBe('engulfed');
    const mat = mesh.material as THREE.MeshStandardMaterial;
    // Locate the impact-phase handler that SC registered (its own _tickIdlePulse).
    // SC constructs an FC internally which also registers an impact-phase handler;
    // SC's own handler is the one we want. Both run on each tick, so invoking
    // every impact-phase handler with delta=0.016 lets the pulse formula write.
    const delta = 0.016;
    for (const h0 of h.coord.handlers) {
      if (h0.phase === 'impact') h0.fn(delta);
    }
    // Pulse formula: idlePulse = sin(pulseTime * 0.4) * 0.1 + 0.1, with
    // pulseTime accumulated to `delta`. emissiveIntensity =
    // max(baseEmissive, SELECTION_EMISSIVE_FLOOR) + idlePulse.
    const expectedIdle = Math.sin(delta * 0.4) * 0.1 + 0.1;
    const expectedEmissive =
      Math.max(baseEm, SELECTION_EMISSIVE_FLOOR) + expectedIdle;
    expect(mat.emissiveIntensity).toBeCloseTo(expectedEmissive, 3);
  });

  it('26. T3.4 idle pulse skips when state is not "engulfed" (no emissive write)', () => {
    const h = makeHarness();
    const baseEm = 0.6;
    const { mesh } = driveToFocused(h, 'n1', baseEm);
    expect(h.sc.state).toBe('focused');
    const mat = mesh.material as THREE.MeshStandardMaterial;
    const beforeIntensity = mat.emissiveIntensity;
    // Invoke every impact-phase handler with delta=0.016.
    for (const h0 of h.coord.handlers) {
      if (h0.phase === 'impact') h0.fn(0.016);
    }
    // No write because state !== 'engulfed'.
    expect(mat.emissiveIntensity).toBe(beforeIntensity);
  });

  // ── Dispose (2 tests) ────────────────────────────────────────────

  it('27. dispose() cancels handler + clears state + clears pending decay timer; idempotent', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.onImpact('n1'); // arms the decay timer
    expect(h.sc.state).toBe('impacting');
    const handlersBefore = h.coord.handlers.length;
    expect(handlersBefore).toBeGreaterThan(0);
    h.sc.dispose();
    // Handler arrays drained — SC's impact-phase tick AND FC's impact-phase tick
    // both unregister, so the array shrinks back to zero.
    expect(h.coord.handlers.length).toBe(0);
    // Pending decay timer cancelled — advancing 1380ms must NOT throw and must
    // NOT transition (impossible to inspect transitions post-dispose without
    // crashing, but the absence of an exception proves the timer was cleared).
    expect(() => vi.advanceTimersByTime(1380)).not.toThrow();
    // Idempotent.
    expect(() => h.sc.dispose()).not.toThrow();
  });

  it('28. post-dispose() API calls are lenient no-ops (do not throw)', () => {
    const h = makeHarness();
    driveToFocused(h, 'n1');
    h.sc.dispose();
    expect(() => h.sc.select('n1')).not.toThrow();
    expect(() => h.sc.select('n2')).not.toThrow();
    expect(() => h.sc.deselect()).not.toThrow();
    expect(() => h.sc.onImpact('n1')).not.toThrow();
    expect(() => h.sc.afterRebuild()).not.toThrow();
    expect(() => h.sc.flash('n1', new THREE.Color(0xff8800))).not.toThrow();
  });

  // ── Flash bridge (1 test) ────────────────────────────────────────

  it('29. flash(id, color) delegates to FlashController.flash() (verified via SC owned FC instance)', () => {
    const h = makeHarness();
    // SC constructs its own FC instance internally in GREEN. The bridge test
    // verifies the call delegates: spy on the private `_flash.flash` and assert
    // the call shape pass-through.
    const inner = (h.sc as unknown as { _flash: { flash: (id: string, c: THREE.Color) => void } })._flash;
    expect(inner).toBeDefined();
    const flashSpy = vi.spyOn(inner, 'flash');
    const color = new THREE.Color(0xff8800);
    h.sc.flash('n1', color);
    expect(flashSpy).toHaveBeenCalledTimes(1);
    expect(flashSpy).toHaveBeenCalledWith('n1', color);
  });
});
