// frontend/src/lib/components/taxonomy/AnimationCoordinator.test.ts
//
// Runtime unit tests for AnimationCoordinator. Mock-based for fast feedback;
// real-Three integration in AnimationCoordinator.integration.test.ts.
//
// Spec: docs/superpowers/specs/2026-05-17-animation-coordinator-design.md §5.1
import { describe, it, expect, vi } from 'vitest';
import type { TopologyRenderer } from './TopologyRenderer';
import {
  AnimationCoordinator,
  type AnimationPhase,
  type AnimationHandler,
} from './AnimationCoordinator';

// Mock renderer: captures the addAnimationCallback registration + returns
// a splice-style unsubscribe (mirrors TopologyRenderer.addAnimationCallback
// at frontend/src/lib/components/taxonomy/TopologyRenderer.ts:126-133).
function makeMockRenderer() {
  const callbacks: Array<() => void> = [];
  const addAnimationCallback = vi.fn((cb: () => void) => {
    callbacks.push(cb);
    return () => {
      const idx = callbacks.indexOf(cb);
      if (idx >= 0) callbacks.splice(idx, 1);
    };
  });
  return {
    addAnimationCallback,
    callbacks,
    triggerTick(): void {
      // Manually invoke the captured tick callback once (simulates one RAF).
      for (const cb of callbacks) cb();
    },
  };
}

describe('AnimationCoordinator', () => {
  it('#1 — constructor registers exactly one addAnimationCallback with arity-0 function', () => {
    const renderer = makeMockRenderer();
    new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    expect(renderer.addAnimationCallback).toHaveBeenCalledTimes(1);
    const arg = renderer.addAnimationCallback.mock.calls[0][0];
    expect(typeof arg).toBe('function');
    expect(arg.length).toBe(0); // arity-0 () => void
  });

  it('#2 — registered impact handler invoked once per tick with delta near 0.016', async () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handler = vi.fn();
    coordinator.register('impact', handler);
    // Wait ~16ms then trigger
    await new Promise((r) => setTimeout(r, 16));
    renderer.triggerTick();
    expect(handler).toHaveBeenCalledTimes(1);
    const delta = handler.mock.calls[0][0];
    expect(typeof delta).toBe('number');
    expect(delta).toBeGreaterThan(0.001);
    expect(delta).toBeLessThan(0.1);
  });

  it('#3 — handlers in 5 phases called in phase order: impact → physics → breathing → ambient → camera', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const order: AnimationPhase[] = [];
    coordinator.register('camera', () => order.push('camera'));
    coordinator.register('ambient', () => order.push('ambient'));
    coordinator.register('breathing', () => order.push('breathing'));
    coordinator.register('physics', () => order.push('physics'));
    coordinator.register('impact', () => order.push('impact'));
    renderer.triggerTick();
    expect(order).toEqual(['impact', 'physics', 'breathing', 'ambient', 'camera']);
  });

  it('#4 — three handlers in same phase called in registration order (FIFO)', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const order: string[] = [];
    coordinator.register('impact', () => order.push('A'));
    coordinator.register('impact', () => order.push('B'));
    coordinator.register('impact', () => order.push('C'));
    renderer.triggerTick();
    expect(order).toEqual(['A', 'B', 'C']);
  });

  it('#5 — handler not invoked after unsubscribe', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handler = vi.fn();
    const unsub = coordinator.register('impact', handler);
    unsub();
    renderer.triggerTick();
    expect(handler).not.toHaveBeenCalled();
  });

  it('#6 — register with unknown phase throws with phase name in message', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    expect(() => coordinator.register('bogus' as AnimationPhase, () => {})).toThrow(/bogus/);
  });

  it('#7 — register after dispose returns no-op unsubscribe; does NOT throw; handler not invoked', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    coordinator.dispose();
    const handler = vi.fn();
    let unsub: (() => void) | undefined;
    expect(() => {
      unsub = coordinator.register('impact', handler);
    }).not.toThrow();
    expect(typeof unsub).toBe('function');
    renderer.triggerTick();
    expect(handler).not.toHaveBeenCalled();
  });

  it('#8 — tick after dispose is no-op (no handlers called; no exception)', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handler = vi.fn();
    coordinator.register('impact', handler);
    coordinator.dispose();
    expect(() => renderer.triggerTick()).not.toThrow();
    expect(handler).not.toHaveBeenCalled();
  });

  it('#9 — dispose is idempotent (called twice, no exception)', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    expect(() => {
      coordinator.dispose();
      coordinator.dispose();
    }).not.toThrow();
  });

  it('#10 — handler exception isolated: throwing handler does not kill remaining handlers in tick', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const handlerB = vi.fn();
    const handlerC = vi.fn();
    coordinator.register('impact', () => {
      throw new Error('boom');
    });
    coordinator.register('physics', handlerB);
    coordinator.register('camera', handlerC);
    expect(() => renderer.triggerTick()).not.toThrow();
    expect(handlerB).toHaveBeenCalledTimes(1);
    expect(handlerC).toHaveBeenCalledTimes(1);
    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it('#11 — second tick reflects elapsed time in delta', async () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handler = vi.fn();
    coordinator.register('impact', handler);
    renderer.triggerTick(); // first tick: delta near 0
    await new Promise((r) => setTimeout(r, 30));
    renderer.triggerTick(); // second tick: delta ~0.030
    expect(handler).toHaveBeenCalledTimes(2);
    const secondDelta = handler.mock.calls[1][0];
    expect(secondDelta).toBeGreaterThan(0.020);
    expect(secondDelta).toBeLessThan(0.100);
  });

  it('#12 — 100 handlers across 5 phases all invoked per tick', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handlers: ReturnType<typeof vi.fn>[] = [];
    const phases: AnimationPhase[] = ['impact', 'physics', 'breathing', 'ambient', 'camera'];
    for (let i = 0; i < 100; i++) {
      const h = vi.fn();
      handlers.push(h);
      coordinator.register(phases[i % 5], h);
    }
    renderer.triggerTick();
    for (const h of handlers) expect(h).toHaveBeenCalledTimes(1);
  });

  it('#13 — same delta value passed to all handlers in one tick (delta computed once per _tick)', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const deltas: number[] = [];
    const phases: AnimationPhase[] = ['impact', 'physics', 'breathing', 'ambient', 'camera'];
    for (const phase of phases) {
      coordinator.register(phase, (dt) => deltas.push(dt));
    }
    renderer.triggerTick();
    expect(deltas.length).toBe(5);
    // All deltas in the same tick should be the SAME number (computed once).
    expect(deltas[0]).toBe(deltas[1]);
    expect(deltas[1]).toBe(deltas[2]);
    expect(deltas[2]).toBe(deltas[3]);
    expect(deltas[3]).toBe(deltas[4]);
  });

  it('#14 — register from inside a handler: coordinator does not throw; new handler invoked at LEAST on the next tick', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handlerB = vi.fn();
    coordinator.register('impact', () => {
      coordinator.register('impact', handlerB);
    });
    // First tick: handlerA runs and registers handlerB
    expect(() => renderer.triggerTick()).not.toThrow();
    // Whether handlerB also runs THIS tick is V8-defined for array mutation
    // during for-of iteration — implementation-detail per spec §3.1 JSDoc.
    // Test pins ONLY: no exception during tick 1; handlerB invoked at least
    // once by the end of tick 2 (the strictly-load-bearing contract). Allow
    // 1 OR 2 total invocations (1 = handlerB runs only on tick 2; 2 = V8
    // sees the appended handler during tick 1's iteration AND on tick 2).
    const callsAfterTick1 = handlerB.mock.calls.length;
    renderer.triggerTick(); // second tick: handlerB must have been invoked by now
    const callsAfterTick2 = handlerB.mock.calls.length;
    expect(callsAfterTick2).toBeGreaterThan(callsAfterTick1);
    expect(callsAfterTick2).toBeGreaterThanOrEqual(1);
    expect(callsAfterTick2).toBeLessThanOrEqual(2);
  });

  it('#15 — dispose from inside a handler: subsequent handlers in tick do NOT run', () => {
    const renderer = makeMockRenderer();
    const coordinator = new AnimationCoordinator(renderer as unknown as TopologyRenderer);
    const handlerB = vi.fn();
    const handlerC = vi.fn();
    coordinator.register('impact', () => coordinator.dispose());
    coordinator.register('impact', handlerB);
    coordinator.register('physics', handlerC);
    expect(() => renderer.triggerTick()).not.toThrow();
    expect(handlerB).not.toHaveBeenCalled();
    expect(handlerC).not.toHaveBeenCalled();
  });
});
