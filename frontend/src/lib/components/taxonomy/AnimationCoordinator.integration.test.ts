// frontend/src/lib/components/taxonomy/AnimationCoordinator.integration.test.ts
//
// Integration tests for AnimationCoordinator against a stub renderer that
// preserves the addAnimationCallback shape verbatim (push to internal array
// + return splice-unsubscribe). Tighter than the unit tests because it
// uses real Array semantics for the renderer's callback queue without
// requiring real WebGL (jsdom has no WebGL context).
//
// Spec: docs/superpowers/specs/2026-05-17-animation-coordinator-design.md §7
import { describe, it, expect } from 'vitest';
import type { TopologyRenderer } from './TopologyRenderer';
import { AnimationCoordinator } from './AnimationCoordinator';

// Stub renderer: preserves addAnimationCallback shape exactly per
// TopologyRenderer.ts:126-133 — push to internal array, return a
// splice-style unsubscribe.
function makeStubRenderer() {
  const _animateCallbacks: Array<() => void> = [];
  const renderer = {
    addAnimationCallback(cb: () => void): () => void {
      _animateCallbacks.push(cb);
      return () => {
        const idx = _animateCallbacks.indexOf(cb);
        if (idx >= 0) _animateCallbacks.splice(idx, 1);
      };
    },
    // Helper for tests — simulates one RAF frame
    _flushCallbacks(): void {
      for (const cb of _animateCallbacks) cb();
    },
    _callbackCount(): number {
      return _animateCallbacks.length;
    },
  };
  return renderer;
}

describe('AnimationCoordinator — stub-renderer integration', () => {
  it('INT-1 — constructor registers exactly one callback against stub renderer', () => {
    const renderer = makeStubRenderer();
    new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    expect(renderer._callbackCount()).toBe(1);
  });

  it('INT-2 — phase handlers invoked in PHASE_ORDER when stub flushes callbacks', () => {
    const renderer = makeStubRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const order: string[] = [];
    coordinator.register('camera', () => order.push('camera'));
    coordinator.register('ambient', () => order.push('ambient'));
    coordinator.register('breathing', () => order.push('breathing'));
    coordinator.register('physics', () => order.push('physics'));
    coordinator.register('impact', () => order.push('impact'));
    renderer._flushCallbacks();
    expect(order).toEqual(['impact', 'physics', 'breathing', 'ambient', 'camera']);
  });

  it('INT-3 — dispose cancels the stub renderer subscription (callback count returns to 0)', () => {
    const renderer = makeStubRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    expect(renderer._callbackCount()).toBe(1);
    coordinator.dispose();
    expect(renderer._callbackCount()).toBe(0);
  });

  it('INT-4 — after dispose, stub flush is no-op (cancelled callback not invoked)', () => {
    const renderer = makeStubRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    let invocations = 0;
    coordinator.register('impact', () => invocations++);
    coordinator.dispose();
    renderer._flushCallbacks(); // empty array after dispose
    expect(invocations).toBe(0);
  });

  it('INT-5 — within-impact strict ordering verified by registration order against stub flush', () => {
    const renderer = makeStubRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const order: string[] = [];
    // Mirrors the spec §3.3 registration order:
    coordinator.register('impact', () => order.push('beam'));
    coordinator.register('impact', () => order.push('envelope'));
    coordinator.register('impact', () => order.push('flash'));
    renderer._flushCallbacks();
    expect(order).toEqual(['beam', 'envelope', 'flash']);
  });
});
