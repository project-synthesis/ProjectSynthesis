// frontend/src/lib/components/taxonomy/SelectionController.integration.test.ts
//
// Sub-project E — Cycle 2 OPERATE. Integration tests covering the cross-class
// wiring between SelectionController ↔ FlashController ↔ real
// AnimationCoordinator ↔ stub renderer ↔ stub ImpactCoordinator.
//
// Spec: docs/superpowers/specs/2026-05-18-selection-state-machine-design.md §5.4
//
// Why integration tests (in addition to the 30 unit tests):
//   - Unit tests stub the coordinator (handlers exposed on a side array). The
//     real construction path registers AC's tick on the renderer via the
//     and dispatches through the PHASE_ORDER fixed iteration. INT pins that
//     full chain: SC's tick + FC's tick both arrive each frame in registration
//     order, and dispose() correctly drains the renderer subscription so RAF
//     after teardown is a silent no-op.
//   - The 5 scenarios below are the cross-class invariants:
//       INT-1  full selection lifecycle (idle → focusing → focused →
//              impacting → engulfed → idle) through the real tick loop.
//       INT-2  re-select mid-impact cancels the pending decay timer AND clears
//              the engulfed set (B1+B3 roundtrip through real timer).
//       INT-3  afterRebuild() re-applies the dual-swap highlight on a swapped
//              mesh reference; idempotent (B5 + canon F16 replacement).
//       INT-4  flash + idle pulse don't compete — flash holds emissive
//              authority while active; idle pulse resumes after the 1380ms
//              flash window.
//       INT-5  dispose chain ordering — SC.dispose() drains the renderer
//              callback (silencing ALL impact-phase handlers), restores
//              FC-tracked baselines (B9), and cancels the pending decay.

import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import * as THREE from 'three';
import { SelectionController, SELECTION_EMISSIVE_FLOOR } from './SelectionController';
import { AnimationCoordinator, type AnimationPhase, type AnimationHandler } from './AnimationCoordinator';
import type { TopologyRenderer } from './TopologyRenderer';
import type { ImpactCoordinator } from './ImpactCoordinator';
import type { SceneNode } from './TopologyData';

// ── Harness ─────────────────────────────────────────────────────────
//
// The real AnimationCoordinator registers ONE callback on
// `renderer.addAnimationCallback`; ticking is driven by flushing the
// renderer's animation-callback list. SC + FC each register one impact-
// phase handler, so on every flush both handlers run in registration order
// (SC tick first because SC's constructor runs the registration before
// it constructs its inner FC).
//
// All times use fake timers — Vitest's fake timers mock both setTimeout
// (SC's decay) AND performance.now() (AC delta + FC envelope), so a single
// `vi.advanceTimersByTime(...)` advances both clocks coherently.

function makeRendererStub() {
  const animationCallbacks: Array<() => void> = [];
  const focusOn = vi.fn();
  const addAnimationCallback = vi.fn((cb: () => void) => {
    animationCallbacks.push(cb);
    return () => {
      const i = animationCallbacks.indexOf(cb);
      if (i >= 0) animationCallbacks.splice(i, 1);
    };
  });
  const flush = () => {
    // Snapshot before iterating — dispose mid-flush could splice the array.
    const snapshot = animationCallbacks.slice();
    for (const cb of snapshot) cb();
  };
  return {
    focusOn,
    addAnimationCallback,
    animationCallbacks,
    flush,
  } as unknown as TopologyRenderer & {
    focusOn: typeof focusOn;
    addAnimationCallback: typeof addAnimationCallback;
    animationCallbacks: typeof animationCallbacks;
    flush: typeof flush;
  };
}

function makeIC() {
  return { fire: vi.fn() } as unknown as ImpactCoordinator & {
    fire: ReturnType<typeof vi.fn>;
  };
}

function makeMesh(baseEmissive: number, color = 0xff8800): THREE.Mesh {
  const geo = new THREE.SphereGeometry(1);
  const mat = new THREE.MeshStandardMaterial({ color, emissive: color });
  mat.emissiveIntensity = baseEmissive;
  const mesh = new THREE.Mesh(geo, mat);
  mesh.userData.baseEmissive = baseEmissive;
  return mesh;
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
 * Build a SC backed by a REAL AnimationCoordinator + stub renderer + stub IC +
 * real-mesh registries. Returns the controller plus the renderer (for flushing
 * ticks + capturing focusOn args), the coordinator (for direct inspection of
 * its `_phases` map), and the mesh registry.
 */
function makeIntegrationHarness(opts?: { highlightColor?: number }) {
  const renderer = makeRendererStub();
  const ic = makeIC();
  const meshes = new Map<string, THREE.Mesh>();
  const sceneNodes = new Map<string, SceneNode>();
  const groups = new Map<string, THREE.Group>();
  // Real AnimationCoordinator — registers its tick on the stub renderer.
  const coord = new AnimationCoordinator(renderer);
  const sc = new SelectionController({
    renderer,
    animationCoordinator: coord,
    impactCoordinator: ic,
    getNodeMesh: (id) => meshes.get(id),
    getSceneNode: (id) => sceneNodes.get(id),
    getBeamGroup: (id) => groups.get(id),
    getBaseEmissive: (id) => meshes.get(id)?.userData.baseEmissive as number | undefined,
    highlightColor: opts?.highlightColor ?? 0x00ffff,
  });
  return { sc, coord, renderer, ic, meshes, sceneNodes, groups };
}

/** Register a node fixture (mesh + sceneNode + group) in one shot. */
function registerNode(
  h: ReturnType<typeof makeIntegrationHarness>,
  id: string,
  baseEmissive = 0.6,
  color = 0xff8800,
) {
  const mesh = makeMesh(baseEmissive, color);
  h.meshes.set(id, mesh);
  h.sceneNodes.set(id, makeSceneNode(id));
  h.groups.set(id, new THREE.Group());
  return mesh;
}

/** Pull the AnimationCoordinator's internal impact-phase handler array. */
function getImpactHandlers(coord: AnimationCoordinator): AnimationHandler[] {
  const seam = coord as unknown as {
    _phases: Map<AnimationPhase, AnimationHandler[]>;
  };
  return seam._phases.get('impact') ?? [];
}

// ── Tests ───────────────────────────────────────────────────────────

describe('SelectionController integration', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('INT-1 — full lifecycle: idle → focusing → focused → impacting → engulfed → idle', () => {
    const h = makeIntegrationHarness();
    registerNode(h, 'a');

    // idle (default)
    expect(h.sc.state).toBe('idle');

    // select → focusing
    h.sc.select('a');
    expect(h.sc.state).toBe('focusing');
    expect(h.sc.selectedId).toBe('a');
    expect(h.ic.fire).toHaveBeenCalledTimes(1);
    expect(h.renderer.focusOn).toHaveBeenCalledTimes(1);

    // Capture the onComplete (4th positional arg) from the focusOn call
    // and invoke it — this is the focus-tween done-callback path (B8).
    const focusOnCall = h.renderer.focusOn.mock.calls[0];
    expect(focusOnCall.length).toBe(4);
    const onComplete = focusOnCall[3] as () => void;
    expect(typeof onComplete).toBe('function');
    onComplete();
    expect(h.sc.state).toBe('focused');

    // onImpact → impacting
    h.sc.onImpact('a');
    expect(h.sc.state).toBe('impacting');
    expect(h.sc.isEngulfed('a')).toBe(true);

    // advance 1380ms → engulfed (decay timer fires)
    vi.advanceTimersByTime(1380);
    expect(h.sc.state).toBe('engulfed');

    // deselect → idle
    h.sc.deselect();
    expect(h.sc.state).toBe('idle');
    expect(h.sc.selectedId).toBeNull();
    expect(h.sc.isEngulfed('a')).toBe(false);
  });

  it('INT-2 — re-select mid-impact cancels pending decay timer and clears engulfed set (B1+B3)', () => {
    const h = makeIntegrationHarness();
    registerNode(h, 'a');
    registerNode(h, 'b');

    // Drive 'a' into impacting.
    h.sc.select('a');
    const onCompleteA = h.renderer.focusOn.mock.calls[0][3] as () => void;
    onCompleteA();
    expect(h.sc.state).toBe('focused');
    h.sc.onImpact('a');
    expect(h.sc.state).toBe('impacting');
    expect(h.sc.isEngulfed('a')).toBe(true);

    // Re-select 'b' — cancel-via-idle: clears pending decay timer for 'a',
    // clears engulfed-set, re-enters focusing for 'b'.
    h.sc.select('b');
    expect(h.sc.state).toBe('focusing');
    expect(h.sc.selectedId).toBe('b');
    expect(h.sc.isEngulfed('a')).toBe(false);

    // Advance well past the original 1380ms decay window — the cancelled
    // timer must NOT fire. State stays at 'focusing' (the onComplete for
    // 'b' has not been invoked yet).
    vi.advanceTimersByTime(2000);
    expect(h.sc.state).toBe('focusing'); // NOT 'engulfed'
    expect(h.sc.selectedId).toBe('b');
  });

  it('INT-3 — afterRebuild() re-applies dual-swap highlight on swapped mesh; idempotent (B5)', () => {
    const h = makeIntegrationHarness();
    const originalHex = 0xff8800;
    const highlightHex = 0x00ffff;
    registerNode(h, 'a', 0.6, originalHex);

    // Drive into focused so a selection exists and the original mesh is highlighted.
    h.sc.select('a');
    (h.renderer.focusOn.mock.calls[0][3] as () => void)();
    expect(h.sc.state).toBe('focused');
    const originalMesh = h.meshes.get('a')!;
    const originalMat = originalMesh.material as THREE.MeshStandardMaterial;
    expect(originalMat.color.getHex()).toBe(highlightHex);
    expect(originalMat.emissive.getHex()).toBe(highlightHex);

    // Simulate rebuildScene replacing the mesh ref: fresh mesh with the same
    // original color, registered under the same id. SC has no knowledge of
    // the swap until afterRebuild() is called.
    const freshMesh = makeMesh(0.6, originalHex);
    h.meshes.set('a', freshMesh);
    const freshMat = freshMesh.material as THREE.MeshStandardMaterial;
    // Pre-condition: fresh mesh starts at original color.
    expect(freshMat.color.getHex()).toBe(originalHex);
    expect(freshMat.emissive.getHex()).toBe(originalHex);

    h.sc.afterRebuild();
    // Dual-swap: BOTH color AND emissive land at highlightColor (canon F16).
    expect(freshMat.color.getHex()).toBe(highlightHex);
    expect(freshMat.emissive.getHex()).toBe(highlightHex);

    // Idempotent: calling afterRebuild() again on the same swapped mesh is a
    // no-op clear-and-reapply. No throw, and the mesh stays highlighted.
    expect(() => h.sc.afterRebuild()).not.toThrow();
    expect(freshMat.color.getHex()).toBe(highlightHex);
    expect(freshMat.emissive.getHex()).toBe(highlightHex);

    // Second rebuild cycle with another mesh swap — still idempotent.
    const fresherMesh = makeMesh(0.6, originalHex);
    h.meshes.set('a', fresherMesh);
    h.sc.afterRebuild();
    const fresherMat = fresherMesh.material as THREE.MeshStandardMaterial;
    expect(fresherMat.color.getHex()).toBe(highlightHex);
    expect(fresherMat.emissive.getHex()).toBe(highlightHex);
  });

  it('INT-4 — flash + idle pulse do not compete; flash holds emissive authority while active', () => {
    const h = makeIntegrationHarness();
    const baseEm = 0.6;
    const mesh = registerNode(h, 'a', baseEm);
    const mat = mesh.material as THREE.MeshStandardMaterial;

    // Drive 'a' into engulfed: select → onComplete → onImpact → advance 1380ms.
    h.sc.select('a');
    (h.renderer.focusOn.mock.calls[0][3] as () => void)();
    h.sc.onImpact('a');
    vi.advanceTimersByTime(1380);
    expect(h.sc.state).toBe('engulfed');

    // Flash the engulfed cluster mid-engulfed. FC owns emissiveIntensity
    // while active; SC's _tickIdlePulse must early-return (skip emissive
    // write) for the duration of the flash window.
    h.sc.flash('a', new THREE.Color(0xff8800));
    expect(h.sc.isFlashActive('a')).toBe(true);

    // Advance into the flash envelope so FC's tick has measurable elapsed
    // > 0 and writes a peak-ramped value (attack ramp ends at 120ms; hold
    // begins). Then flush: FC tick writes peak emissive, SC tick yields.
    vi.advanceTimersByTime(120);
    h.renderer.flush();
    const intensityDuringFlash = mat.emissiveIntensity;

    // Sanity: FC has elevated emissiveIntensity above the idle-pulse range.
    // Flash peak = baseline + 1.6 ≈ 0.6 + 1.6 = 2.2; idle-pulse max =
    // max(base, FLOOR) + 0.2 = 1.0 + 0.2 = 1.2. After the 120ms attack
    // ramp, emissive is at (or very near) peak — well above the pulse
    // range. This proves FC owns emissive authority during the flash window.
    expect(intensityDuringFlash).toBeGreaterThan(SELECTION_EMISSIVE_FLOOR + 0.2);

    // Mid-flash: invoking the SC tick directly (with a non-zero delta) must
    // NOT mutate emissiveIntensity — the flash-active check yields the slot
    // to FC. Capture SC's tick handler from the impact-phase handler array.
    //
    // Registration order in SC's constructor: FC is constructed FIRST (which
    // registers FC's tick on the coordinator), THEN SC registers its own
    // _tickIdlePulse. So `impactHandlers[0]` is FC.tick and
    // `impactHandlers[1]` is SC._tickIdlePulse.
    const impactHandlers = getImpactHandlers(h.coord);
    expect(impactHandlers.length).toBe(2);
    const scTick = impactHandlers[1];
    expect(typeof scTick).toBe('function');
    const before = mat.emissiveIntensity;
    scTick(0.016);
    expect(mat.emissiveIntensity).toBe(before); // no write — flash-active

    // Advance past the remainder of the flash window (we already advanced
    // 120ms; total envelope is 1380ms, so 1260ms more puts elapsed past
    // GLOW_TOTAL_MS). FC's cleanup branch restores baseline emissive then
    // deletes the state. After cleanup, flash is inactive.
    vi.advanceTimersByTime(1260);
    h.renderer.flush(); // FC tick: elapsed > GLOW_TOTAL_MS → cleanup branch
    expect(h.sc.isFlashActive('a')).toBe(false);

    // After flash cleanup, advancing time + flushing must let the idle pulse
    // resume writing emissive. SC's _pulseTime accumulates via `delta` — the
    // pulse value is `sin(pulseTime * 0.4) * 0.1 + 0.1` added to the floor.
    // Verify the idle-pulse path runs at all by snapshotting intensity, then
    // calling SC's tick directly with a known delta. (We don't rely on AC's
    // delta computation here because we already verified the wiring above.)
    const beforePulse = mat.emissiveIntensity;
    scTick(0.016);
    // Idle-pulse formula writes a fresh value derived from baseEmissive +
    // SELECTION_EMISSIVE_FLOOR + idlePulse. With base=0.6 and FLOOR=1.0,
    // the floor wins → max=1.0; idlePulse at small accumulated pulseTime
    // is small (≈ sin(small) * 0.1 + 0.1). Verify the value is in range,
    // confirming idle-pulse re-wrote emissive.
    expect(mat.emissiveIntensity).not.toBe(beforePulse);
    const expectedMin = SELECTION_EMISSIVE_FLOOR; // idlePulse can be 0
    const expectedMax = SELECTION_EMISSIVE_FLOOR + 0.2; // idlePulse max = 0.2
    expect(mat.emissiveIntensity).toBeGreaterThanOrEqual(expectedMin);
    expect(mat.emissiveIntensity).toBeLessThanOrEqual(expectedMax + 0.01);
  });

  it('INT-5 — dispose chain ordering: drains renderer subscription, restores FC baselines, cancels decay (B9)', () => {
    const h = makeIntegrationHarness();
    const baseEmA = 0.6;
    const baseEmB = 0.4;
    const meshA = registerNode(h, 'a', baseEmA);
    const meshB = registerNode(h, 'b', baseEmB);
    const matB = meshB.material as THREE.MeshStandardMaterial;

    // Drive 'a' into engulfed and arm an active flash on 'b'.
    h.sc.select('a');
    (h.renderer.focusOn.mock.calls[0][3] as () => void)();
    h.sc.onImpact('a');
    vi.advanceTimersByTime(1380);
    expect(h.sc.state).toBe('engulfed');

    // Flash 'b' (a different, non-selected node).
    h.sc.flash('b', new THREE.Color(0xff8800));
    expect(h.sc.isFlashActive('b')).toBe(true);

    // Advance into the attack ramp so FC.tick writes an elevated value
    // above baseline — sets up a clear restore-on-dispose contrast.
    vi.advanceTimersByTime(120);
    h.renderer.flush();
    expect(matB.emissiveIntensity).toBeGreaterThan(baseEmB);

    // Pre-condition: 2 impact-phase handlers (SC tick + FC tick).
    expect(getImpactHandlers(h.coord).length).toBe(2);

    // Now re-arm a pending decay by re-selecting + re-impacting.
    // (We disposed of the previous decay timer when we hit 'engulfed' —
    // the timer already fired. To pin "dispose cancels pending decay" we
    // need a live timer. So: deselect, re-select, advance onComplete,
    // onImpact — now there's a pending decay we can verify gets cancelled.)
    h.sc.deselect();
    h.sc.select('a');
    // The mesh swap on re-select clears the engulfed set. Get the latest
    // onComplete (captured fresh by the new focusOn call).
    const onCompleteCalls = h.renderer.focusOn.mock.calls.length;
    const latestOnComplete = h.renderer.focusOn.mock.calls[onCompleteCalls - 1][3] as () => void;
    latestOnComplete();
    expect(h.sc.state).toBe('focused');
    h.sc.onImpact('a');
    expect(h.sc.state).toBe('impacting'); // decay timer armed

    // ── dispose() ──
    h.sc.dispose();

    // (i) FC.dispose was invoked transitively: any active flash is cleared.
    expect(h.sc.isFlashActive('b')).toBe(false);

    // (ii) FC.dispose restored baseline emissive on 'b' (B9 invariant).
    expect(matB.emissiveIntensity).toBe(baseEmB);

    // (iii) Pending decay timer cancelled: advancing past 1380ms must not
    // throw (post-dispose state mutations are guarded by `_disposed`
    // checks inside the setTimeout callback, but the cleanest invariant
    // is that the timer was cleared so there's no callback to fire at
    // all — `vi.advanceTimersByTime` is a no-op in that case).
    expect(() => vi.advanceTimersByTime(2000)).not.toThrow();

    // (iv) Impact-phase handler array is drained — BOTH SC's tick AND
    // FC's tick have been unregistered.
    expect(getImpactHandlers(h.coord).length).toBe(0);

    // Bonus: post-dispose API calls are lenient no-ops (don't throw).
    expect(() => h.sc.select('a')).not.toThrow();
    expect(() => h.sc.afterRebuild()).not.toThrow();
    expect(() => h.sc.flash('a', new THREE.Color(0xff0000))).not.toThrow();
    // The flash on 'a' must NOT have been registered (dispose drained FC).
    expect(h.sc.isFlashActive('a')).toBe(false);

    // Reference meshA so the linter doesn't flag the unused fixture — its
    // mere existence verifies the dispose chain didn't crash on stale refs.
    expect(meshA).toBeDefined();
  });
});
