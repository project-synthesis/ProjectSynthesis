import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';

// Mock the heavy editor sub-components — the integration test only cares
// about the Observatory tab being registered + mounting the shell.
vi.mock('$lib/components/editor/PromptEdit.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/ForgeArtifact.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/PassthroughView.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/editor/ContextPanel.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/shared/DiffView.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/refinement/RefinementTimeline.svelte', () => ({ default: () => ({ destroy: () => {} }) }));
vi.mock('$lib/components/taxonomy/SemanticTopology.svelte', () => ({ default: () => ({ destroy: () => {} }) }));

// Stub Observatory child components so we only verify the shell mount.
vi.mock('$lib/components/taxonomy/DomainLifecycleTimeline.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));
vi.mock('$lib/components/taxonomy/DomainReadinessAggregate.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));
vi.mock('$lib/components/taxonomy/PatternDensityHeatmap.svelte', () => ({
  default: () => ({ destroy: () => {} }),
}));

// Mock backend client calls fired by Page on mount (health, SSE bootstrap).
vi.mock('$lib/api/client', async () => {
  const actual = await vi.importActual<typeof import('$lib/api/client')>('$lib/api/client');
  return {
    ...actual,
    getHealth: vi.fn().mockResolvedValue({
      provider: 'claude-cli',
      version: '0.1.0',
      sampling_capable: false,
      mcp_disconnected: true,
    }),
    getOptimization: vi.fn(),
  };
});

import Page from './+page.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';

describe('app/+page.svelte — Observatory tab integration', () => {
  beforeEach(() => {
    editorStore._reset();
    forgeStore._reset();
    vi.clearAllMocks();
  });
  afterEach(() => cleanup());

  it('registers OBSERVATORY tab in the tablist (I1)', () => {
    render(Page);
    expect(screen.getByRole('tab', { name: /observatory/i })).toBeTruthy();
  });

  it('clicking the Observatory tab mounts TaxonomyObservatory (I2)', async () => {
    const userEvent = (await import('@testing-library/user-event')).default;
    const { container } = render(Page);
    const user = userEvent.setup();
    await user.click(screen.getByRole('tab', { name: /observatory/i }));
    expect(container.querySelector('[data-test="taxonomy-observatory"]')).not.toBeNull();
  });
});
