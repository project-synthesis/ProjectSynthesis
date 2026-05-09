/**
 * Cold-boot tier-guide trigger contract.
 *
 * Pre-2026-05-09 the tier-onboarding modal trigger was fragmented across
 * 5 call sites (cold-boot effect, two SSE handler branches, two Settings
 * toggles), each reading ``routing.tier`` at slightly different moments.
 * This test pins the unified contract: a single ``$effect`` watches
 * ``routing.tier`` and fires the right guide when the resolved tier
 * changes (cold boot OR mid-session).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, cleanup, waitFor } from '@testing-library/svelte';

// Stub heavy children — the test only cares about the trigger path.
vi.mock('$lib/components/editor/PromptEdit.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/ForgeArtifact.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/PassthroughView.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/ContextPanel.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/shared/DiffView.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/refinement/RefinementTimeline.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/taxonomy/SemanticTopology.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/taxonomy/DomainLifecycleTimeline.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/taxonomy/DomainReadinessAggregate.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/taxonomy/PatternDensityHeatmap.svelte', () => ({ default: () => ({ destroy: () => {} }) }));

// SSE stub: never fires. We exercise the cold-boot path only.
vi.mock('$lib/stores/sse-health.svelte', async () => {
  const actual = await vi.importActual<typeof import('$lib/stores/sse-health.svelte')>(
    '$lib/stores/sse-health.svelte',
  );
  return {
    ...actual,
    sseHealthStore: {
      ...actual.sseHealthStore,
      connect: vi.fn(),
      disconnect: vi.fn(),
    },
  };
});

vi.mock('$lib/api/client', async () => {
  const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
  return {
    ...actual,
    getHealth: vi.fn(),
    getOptimization: vi.fn(),
  };
});

import Page from './+page.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { preferencesStore } from '$lib/stores/preferences.svelte';
import { internalGuide } from '$lib/stores/internal-guide.svelte';
import { samplingGuide } from '$lib/stores/sampling-guide.svelte';
import { passthroughGuide } from '$lib/stores/passthrough-guide.svelte';
import * as client from '$lib/api/client';

function resetGuides(): void {
  internalGuide.close();
  internalGuide.resetDismissal();
  samplingGuide.close();
  samplingGuide.resetDismissal();
  passthroughGuide.close();
  passthroughGuide.resetDismissal();
}

describe('app/+page.svelte — tier-guide trigger', () => {
  beforeEach(() => {
    editorStore._reset();
    forgeStore._reset();
    preferencesStore._reset();
    resetGuides();
    vi.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
    resetGuides();
  });

  it('cold boot with VS Code closed → InternalGuide opens', async () => {
    // VS-Code-closed: sampling_capable=false, mcp_connected=false, provider exists.
    // Resolved tier should be 'internal'; InternalGuide should auto-open.
    const showSpy = vi.spyOn(internalGuide, 'show');
    vi.mocked(client.getHealth).mockResolvedValue({
      provider: 'claude_cli',
      version: '0.1.0',
      sampling_capable: false,
      mcp_disconnected: false,
    } as never);

    render(Page);

    await waitFor(() => {
      expect(showSpy).toHaveBeenCalledWith(true);
    }, { timeout: 2000 });
    showSpy.mockRestore();
  });

  it('cold boot with VS Code open + sampling capable → SamplingGuide opens', async () => {
    // Cold-boot reconcileToggles auto-enables force_sampling when the
    // health response carries sampling_capable=true. The single $effect
    // watches routing.tier; once force_sampling persists, tier flips
    // to 'sampling' and the effect fires SamplingGuide.
    vi.mocked(client.getHealth).mockResolvedValue({
      provider: 'claude_cli',
      version: '0.1.0',
      sampling_capable: true,
      mcp_disconnected: false,
    } as never);

    render(Page);

    await waitFor(() => {
      expect(samplingGuide.open).toBe(true);
    }, { timeout: 2000 });
    expect(passthroughGuide.open).toBe(false);
  });

  it('cold boot with no provider AND no MCP → PassthroughGuide opens', async () => {
    // Last-resort fallback: no provider detected, no sampling capability.
    // routing.tier resolves to 'passthrough'; the effect fires
    // PassthroughGuide.
    vi.mocked(client.getHealth).mockResolvedValue({
      provider: null,
      version: '0.1.0',
      sampling_capable: false,
      mcp_disconnected: false,
    } as never);

    render(Page);

    await waitFor(() => {
      expect(passthroughGuide.open).toBe(true);
    }, { timeout: 2000 });
    expect(internalGuide.open).toBe(false);
    expect(samplingGuide.open).toBe(false);
  });

  it('previously-dismissed guide is not auto-reopened on cold boot', async () => {
    // Dismissal persists across cold boots — show(true) silently skips.
    // This pins the existing contract; users can clear localStorage to
    // force-open OR use the Settings toggle (which calls show(false)).
    internalGuide.dismiss();
    vi.mocked(client.getHealth).mockResolvedValue({
      provider: 'claude_cli',
      version: '0.1.0',
      sampling_capable: false,
      mcp_disconnected: false,
    } as never);

    render(Page);

    // Give the effect time to fire; assert it didn't re-open.
    await new Promise((r) => setTimeout(r, 400));
    expect(internalGuide.open).toBe(false);
  });
});
