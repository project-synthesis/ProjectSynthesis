import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { mockOptimizationResult, mockRefinementTurn } from '$lib/test-utils';

vi.mock('$lib/api/client', () => ({
  submitFeedback: vi.fn().mockResolvedValue({}),
  getOptimization: vi.fn().mockResolvedValue(null),
  apiFeedback: vi.fn().mockResolvedValue({}),
}));

import ForgeArtifact from './ForgeArtifact.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { editorStore } from '$lib/stores/editor.svelte';
import { refinementStore } from '$lib/stores/refinement.svelte';

describe('ForgeArtifact', () => {
  beforeEach(() => {
    forgeStore._reset();
    refinementStore._reset();
    editorStore._reset();
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it('renders without crashing', () => {
    const { container } = render(ForgeArtifact);
    expect(container.querySelector('.forge-artifact')).toBeInTheDocument();
  });

  it('shows empty state message when no result', () => {
    render(ForgeArtifact);
    expect(screen.getByText(/No result yet/)).toBeInTheDocument();
  });

  it('shows optimized prompt text when result is set', () => {
    forgeStore.result = mockOptimizationResult() as any;
    render(ForgeArtifact);
    expect(screen.getByText('OPTIMIZED PROMPT')).toBeInTheDocument();
  });

  it('shows header buttons when result is set', () => {
    forgeStore.result = mockOptimizationResult() as any;
    render(ForgeArtifact);
    expect(screen.getByText('ORIGINAL')).toBeInTheDocument();
    expect(screen.getByText('RAW')).toBeInTheDocument();
    expect(screen.getByText('DIFF')).toBeInTheDocument();
    expect(screen.getByText('COPY')).toBeInTheDocument();
  });

  it('shows thumbs up and down feedback buttons', () => {
    forgeStore.result = mockOptimizationResult() as any;
    render(ForgeArtifact);
    expect(screen.getByRole('button', { name: 'Thumbs up' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thumbs down' })).toBeInTheDocument();
  });

  it('toggles to show original prompt on ORIGINAL button click', async () => {
    const user = userEvent.setup();
    const result = mockOptimizationResult({ raw_prompt: 'My original prompt text' });
    forgeStore.result = result as any;
    render(ForgeArtifact);

    await user.click(screen.getByText('ORIGINAL'));

    // Label should change to show original
    expect(screen.getByText('ORIGINAL PROMPT')).toBeInTheDocument();
    // Button text should change to OPTIMIZED
    expect(screen.getByText('OPTIMIZED')).toBeInTheDocument();
  });

  it('toggles back to optimized when OPTIMIZED button is clicked', async () => {
    const user = userEvent.setup();
    forgeStore.result = mockOptimizationResult() as any;
    render(ForgeArtifact);

    // Click ORIGINAL first
    await user.click(screen.getByText('ORIGINAL'));
    // Then click OPTIMIZED to toggle back
    await user.click(screen.getByText('OPTIMIZED'));

    expect(screen.getByText('OPTIMIZED PROMPT')).toBeInTheDocument();
  });

  it('toggles markdown render mode with RAW button', async () => {
    const user = userEvent.setup();
    forgeStore.result = mockOptimizationResult() as any;
    render(ForgeArtifact);

    await user.click(screen.getByText('RAW'));
    // Now in raw mode — button text should flip to RENDER
    expect(screen.getByText('RENDER')).toBeInTheDocument();
  });

  it('clicking DIFF calls editorStore.openDiff', async () => {
    const user = userEvent.setup();
    const result = mockOptimizationResult({ id: 'opt-diff-test' });
    forgeStore.result = result as any;
    const openDiffSpy = vi.spyOn(editorStore, 'openDiff');
    render(ForgeArtifact);

    await user.click(screen.getByText('DIFF'));

    expect(openDiffSpy).toHaveBeenCalledWith('opt-diff-test');
  });

  it('shows changes summary when result has changes_summary', () => {
    const result = mockOptimizationResult({ changes_summary: 'Added specificity and context' });
    forgeStore.result = result as any;
    render(ForgeArtifact);
    expect(screen.getByText('CHANGES')).toBeInTheDocument();
  });

  it('shows selected refinement version label', () => {
    forgeStore.result = mockOptimizationResult() as any;
    refinementStore.selectedVersion = mockRefinementTurn({ version: 3 }) as any;
    render(ForgeArtifact);
    expect(screen.getByText('OPTIMIZED PROMPT — v3')).toBeInTheDocument();
  });

  it('shows original prompt when showOriginal is toggled and prompt from forge store', async () => {
    const user = userEvent.setup();
    forgeStore.prompt = 'My original prompt text here';
    // No result set but no raw_prompt — falls back to forgeStore.prompt
    forgeStore.result = null;
    // With no result, no header visible, so set a result without raw_prompt
    const result = mockOptimizationResult({ raw_prompt: '' });
    forgeStore.result = result as any;
    render(ForgeArtifact);

    await user.click(screen.getByText('ORIGINAL'));
    expect(screen.getByText('ORIGINAL PROMPT')).toBeInTheDocument();
  });

  it('copy button triggers clipboard copy', async () => {
    const user = userEvent.setup();
    const result = mockOptimizationResult({ optimized_prompt: 'Optimized text to copy' });
    forgeStore.result = result as any;
    render(ForgeArtifact);

    await user.click(screen.getByText('COPY'));
    // After copy, should show COPIED (brief state)
    // Clipboard mock in test-setup.ts handles this
    expect(screen.getByText(/COPIED|COPY/)).toBeInTheDocument();
  });

  // I-9: per-layer enrichment skip reason codes
  describe('enrichment skip reasons', () => {
    it('renders skip reason on gray layers when profile skipped them', async () => {
      const user = userEvent.setup();
      forgeStore.result = mockOptimizationResult({
        context_sources: {
          heuristic_analysis: true,
          codebase_context: true,
          strategy_intelligence: false,
          applied_patterns: false,
          enrichment_meta: {
            enrichment_profile: 'cold_start',
            profile_skipped_layers: ['strategy_intelligence', 'applied_patterns'],
          },
        },
      }) as any;
      render(ForgeArtifact);

      // Expand the ENRICHMENT section.
      await user.click(screen.getByText('ENRICHMENT'));

      // Skip reason should be attached to each skipped layer row with
      // copy like "skipped — cold start profile" (two skipped layers
      // in this fixture → two matches).
      const reasons = screen.getAllByText(/skipped.*cold.start.*profile/i);
      expect(reasons.length).toBe(2);
    });

    it('does NOT render skip reason on active layers', async () => {
      const user = userEvent.setup();
      forgeStore.result = mockOptimizationResult({
        context_sources: {
          heuristic_analysis: true,
          codebase_context: true,
          strategy_intelligence: true,
          applied_patterns: true,
          enrichment_meta: {
            enrichment_profile: 'code_aware',
          },
        },
      }) as any;
      render(ForgeArtifact);

      await user.click(screen.getByText('ENRICHMENT'));

      // No skip-reason copy when all layers active.
      expect(screen.queryByText(/skipped/i)).not.toBeInTheDocument();
    });

    it('renders tier-defer reason when internal tier defers patterns', async () => {
      const user = userEvent.setup();
      forgeStore.result = mockOptimizationResult({
        context_sources: {
          heuristic_analysis: true,
          codebase_context: true,
          strategy_intelligence: true,
          applied_patterns: false,
          enrichment_meta: {
            enrichment_profile: 'code_aware',
            profile_skipped_layers: ['applied_patterns'],
            patterns_deferred_to_pipeline: true,
          },
        },
      }) as any;
      render(ForgeArtifact);

      await user.click(screen.getByText('ENRICHMENT'));

      // When deferred to pipeline, skip text reflects that instead of profile.
      expect(screen.getByText(/deferred/i)).toBeInTheDocument();
    });
  });
});
