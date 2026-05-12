// frontend/src/lib/components/probes/TopicProbeProgressView.test.ts
//
// Cycle 11 RED — Topic Probe active-run view contract.
//
// The view must be source-agnostic — it renders identical UI whether
// progress comes from SSE events or polled `GET /api/runs/{id}` snapshots.
// The contract:
//   - per-prompt strip cells fill as `probe_prompt_completed` events arrive
//   - emergent taxonomy nodes flash in with `forge-spark` animation
//   - SSE-driven vs poll-driven progress render the same DOM
//
// Tests assert structural behavior, not animation timing precision.
//
// Dynamic import — the component doesn't exist yet in RED state; static
// imports would crash suite collection.

import { describe, it, expect, afterEach, vi } from 'vitest';
import { cleanup, render } from '@testing-library/svelte';

async function loadComponent() {
  // Runtime-computed path so Vite's static analyzer can't resolve it
  // at suite-collection time — keeps the file from crashing before
  // its tests run (RED behavior: individual test failures, not whole-
  // suite collection errors).
  const path = ['.', 'TopicProbeProgressView.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

/**
 * Helper: emit the same DOM CustomEvent the SSE bridge dispatches into
 * `window` for `probe_*` events. The view subscribes to the bridge and
 * updates its strip + taxonomy mini-view from these events.
 */
function emitProbeEvent(type: string, detail: Record<string, unknown>): void {
  window.dispatchEvent(new CustomEvent(type, { detail }));
}

describe('TopicProbeProgressView', () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  // ── Test 6: per-prompt strip cells fill on SSE events ───────────────
  it('test_per_prompt_strip_cells_fill_on_sse_events', async () => {
    const TopicProbeProgressView = await loadComponent();
    const runId = 'run-abc123';
    const { container } = render(TopicProbeProgressView, {
      props: {
        runId,
        nPrompts: 5,
        source: 'sse' as const,
      },
    });

    // The strip renders 5 cells in their resting/pending state.
    const cellsBefore = container.querySelectorAll(
      '[data-test="probe-strip-cell"]',
    );
    expect(cellsBefore.length).toBe(5);
    // Pending cells have no `data-state="completed"` (or equivalent
    // aria-state). They are at most in `pending` / unset.
    cellsBefore.forEach((cell) => {
      expect(cell.getAttribute('data-state')).not.toBe('completed');
    });

    // Emit two probe_prompt_completed events targeting this run.
    emitProbeEvent('probe_prompt_completed', {
      run_id: runId,
      prompt_index: 0,
      overall_score: 8.1,
    });
    emitProbeEvent('probe_prompt_completed', {
      run_id: runId,
      prompt_index: 1,
      overall_score: 7.4,
    });

    // Wait for reactivity to flush.
    await vi.waitFor(() => {
      const cells = container.querySelectorAll('[data-test="probe-strip-cell"]');
      expect(cells[0].getAttribute('data-state')).toBe('completed');
      expect(cells[1].getAttribute('data-state')).toBe('completed');
      // Third cell remains pending until its event arrives.
      expect(cells[2].getAttribute('data-state')).not.toBe('completed');
    });
  });

  // ── Test 7: taxonomy mini-view nodes flash in with forge-spark ───────
  it('test_taxonomy_mini_view_nodes_flash_in_with_forge_spark', async () => {
    const TopicProbeProgressView = await loadComponent();
    const runId = 'run-tax123';
    const { container } = render(TopicProbeProgressView, {
      props: {
        runId,
        nPrompts: 3,
        source: 'sse' as const,
      },
    });

    // Mini-view starts with no emergent nodes.
    const before = container.querySelectorAll(
      '[data-test="taxonomy-mini-node"]',
    );
    expect(before.length).toBe(0);

    // Emit a taxonomy_changed event signaling a new sub-domain emerged
    // during this run. The payload includes `run_id` so the view can
    // attribute the change.
    emitProbeEvent('taxonomy_changed', {
      run_id: runId,
      trigger: 'sub_domain_created',
      sub_domain_label: 'backend: cache-invalidation',
    });

    await vi.waitFor(() => {
      const nodes = container.querySelectorAll(
        '[data-test="taxonomy-mini-node"]',
      );
      expect(nodes.length).toBe(1);
      const node = nodes[0] as HTMLElement;
      // The new node carries the forge-spark animation declaration.
      // Either inline style or a class that maps to `animation: forge-spark`.
      const inline = node.getAttribute('style') ?? '';
      const className = node.className;
      const animationDeclared =
        /forge-spark\s+250ms\s+ease-out/.test(inline) ||
        /forge-spark/.test(className) ||
        /forge-spark/.test(getComputedStyle(node).animation ?? '');
      expect(animationDeclared).toBe(true);
    });
  });

  // ── Test 8: progress source-agnostic SSE vs poll ────────────────────
  it('test_progress_source_agnostic_sse_vs_poll', async () => {
    const TopicProbeProgressView = await loadComponent();
    const runId = 'run-cmp123';

    // Render two instances side-by-side with different sources but the
    // same underlying progress snapshot. The poll-source consumer reads
    // from an explicit `snapshot` prop instead of waiting for SSE events.
    const sseInstance = render(TopicProbeProgressView, {
      props: {
        runId,
        nPrompts: 4,
        source: 'sse' as const,
        // Pre-populated progress (e.g., after store hydration).
        initialCompleted: [
          { prompt_index: 0, overall_score: 8.0 },
          { prompt_index: 1, overall_score: 7.2 },
        ],
      },
    });

    const pollInstance = render(TopicProbeProgressView, {
      props: {
        runId,
        nPrompts: 4,
        source: 'poll' as const,
        initialCompleted: [
          { prompt_index: 0, overall_score: 8.0 },
          { prompt_index: 1, overall_score: 7.2 },
        ],
      },
    });

    // Both must render 4 strip cells, 2 of which are completed.
    const sseCells = sseInstance.container.querySelectorAll(
      '[data-test="probe-strip-cell"]',
    );
    const pollCells = pollInstance.container.querySelectorAll(
      '[data-test="probe-strip-cell"]',
    );
    expect(sseCells.length).toBe(4);
    expect(pollCells.length).toBe(4);

    // Same completed-state pattern across both sources.
    const sseStates = Array.from(sseCells).map((c) => c.getAttribute('data-state'));
    const pollStates = Array.from(pollCells).map((c) => c.getAttribute('data-state'));
    expect(sseStates).toEqual(pollStates);
    // First two completed, last two pending.
    expect(sseStates[0]).toBe('completed');
    expect(sseStates[1]).toBe('completed');
    expect(sseStates[2]).not.toBe('completed');
    expect(sseStates[3]).not.toBe('completed');
  });
});
