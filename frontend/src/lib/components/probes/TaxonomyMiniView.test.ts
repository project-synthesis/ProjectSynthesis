// frontend/src/lib/components/probes/TaxonomyMiniView.test.ts
//
// Cycle 11 RED — emergent-taxonomy mini view contract.
//
// The mini view is a ~200px slim tree that renders emergent taxonomy
// nodes (domains + sub-domains) discovered during a Topic Probe run.
// Two invariants:
//   - new nodes enter with `animation: forge-spark 250ms ease-out`
//   - nodes created within the last 30s carry a `NEW` chip badge
//
// The component does NOT subscribe to SSE on its own — its parent
// (TopicProbeProgressView) passes a `nodes` prop and the mini view is
// pure render. Tests pass nodes directly.
//
// Dynamic import — the component doesn't exist yet in RED state; static
// imports would crash suite collection.

import { describe, it, expect, afterEach } from 'vitest';
import { cleanup, render } from '@testing-library/svelte';

async function loadComponent() {
  // Compute the path at runtime so Vite's static analyzer can't
  // resolve it at suite-collection time. The component doesn't exist
  // in RED state — we want individual tests to FAIL with a clear
  // module-not-found error, not the entire suite to crash on import.
  const path = ['.', 'TaxonomyMiniView.svelte'].join('/');
  const mod = await import(/* @vite-ignore */ path);
  return mod.default;
}

/** Minimal mini-view node shape (matches the prop contract). */
interface MiniNode {
  id: string;
  label: string;
  kind: 'domain' | 'sub_domain';
  created_at: string; // ISO timestamp
  /** True iff the node is freshly emergent in the current run. */
  is_new?: boolean;
}

describe('TaxonomyMiniView', () => {
  afterEach(() => cleanup());

  // ── Test 13: emergent node entry animation = forge-spark 250ms ──────
  it('test_emergent_node_entry_animation_250ms_forge_spark', async () => {
    const TaxonomyMiniView = await loadComponent();
    const now = new Date().toISOString();
    const nodes: MiniNode[] = [
      {
        id: 'sd-1',
        label: 'backend: cache-invalidation',
        kind: 'sub_domain',
        created_at: now,
        is_new: true,
      },
    ];
    const { container } = render(TaxonomyMiniView, { props: { nodes } });

    const nodeEl = container.querySelector('[data-test="mini-node"]') as HTMLElement;
    expect(nodeEl).not.toBeNull();

    // The new node carries the `forge-spark 250ms ease-out` animation —
    // either inline or via a CSS class declaration in the bundle.
    const inline = nodeEl.getAttribute('style') ?? '';
    const className = nodeEl.className;
    const computedAnimation = getComputedStyle(nodeEl).animation ?? '';

    const animationDeclared =
      /forge-spark\s+250ms\s+ease-out/.test(inline) ||
      /forge-spark/.test(className) ||
      /forge-spark/.test(computedAnimation);
    expect(animationDeclared).toBe(true);
  });

  // ── Test 14: NEW chip on under-30s old nodes; absent on older ────────
  it('test_new_chip_on_under_30s_old_nodes', async () => {
    const TaxonomyMiniView = await loadComponent();
    const now = Date.now();
    const recent = new Date(now - 15_000).toISOString(); // 15s ago
    const old = new Date(now - 60_000).toISOString();    // 60s ago
    const nodes: MiniNode[] = [
      { id: 'sd-recent', label: 'recent-domain', kind: 'sub_domain', created_at: recent, is_new: true },
      { id: 'sd-old', label: 'old-domain', kind: 'sub_domain', created_at: old, is_new: false },
    ];

    const { container } = render(TaxonomyMiniView, { props: { nodes } });

    // Both nodes render.
    const nodeEls = container.querySelectorAll('[data-test="mini-node"]');
    expect(nodeEls.length).toBe(2);

    // The recent one carries a `NEW` chip badge.
    const recentEl = container.querySelector('[data-node-id="sd-recent"]') as HTMLElement;
    expect(recentEl).not.toBeNull();
    const recentChip = recentEl.querySelector('[data-test="new-chip"]');
    expect(recentChip).not.toBeNull();
    expect(recentChip?.textContent?.trim().toUpperCase()).toBe('NEW');

    // The older one does NOT.
    const oldEl = container.querySelector('[data-node-id="sd-old"]') as HTMLElement;
    expect(oldEl).not.toBeNull();
    const oldChip = oldEl.querySelector('[data-test="new-chip"]');
    expect(oldChip).toBeNull();
  });
});
