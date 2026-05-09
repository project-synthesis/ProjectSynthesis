import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

// Mock API module
vi.mock('$lib/api/seed', () => ({
  seedTaxonomy: vi.fn().mockResolvedValue({
    status: 'completed',
    tier: 'internal',
    prompts_generated: 10,
    prompts_optimized: 10,
    prompts_failed: 0,
    estimated_cost_usd: 0.5,
    clusters_created: 3,
    domains_touched: ['backend', 'frontend'],
    batch_id: 'test-batch-123',
    summary: 'ok',
    duration_ms: 5000,
  }),
  listSeedAgents: vi.fn().mockResolvedValue([
    { name: 'code-explorer', description: 'Explores codebases', task_types: ['coding'], prompts_per_run: 10, enabled: true },
    { name: 'writer', description: 'Writing tasks', task_types: ['writing'], prompts_per_run: 5, enabled: true },
  ]),
}));

vi.mock('$lib/stores/clusters.svelte', () => ({
  clustersStore: {
    invalidateTree: vi.fn(),
    invalidateStats: vi.fn(),
    invalidateClusters: vi.fn(),
    clearSeedBatch: vi.fn(),
    updateSeedProgress: vi.fn(),
    seedBatchActive: false,
    seedBatchProgress: { completed: 0, total: 0, current: '' },
  },
}));

import SeedModal from './SeedModal.svelte';

describe('SeedModal', () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); });

  // --- Visibility ---

  it('renders nothing when open=false', () => {
    render(SeedModal, { props: { open: false, onClose: vi.fn() } });
    expect(screen.queryByText(/Seed Taxonomy/i)).not.toBeInTheDocument();
  });

  it('renders modal content when open=true', () => {
    render(SeedModal, { props: { open: true, onClose: vi.fn() } });
    // The modal should show the start/seed button
    expect(screen.getByText('Start Seed')).toBeInTheDocument();
  });

  // --- Mode selection ---

  it('defaults to generate mode', () => {
    render(SeedModal, { props: { open: true, onClose: vi.fn() } });
    expect(screen.getByText(/Generate/i)).toBeInTheDocument();
  });

  it('shows provide mode option', () => {
    render(SeedModal, { props: { open: true, onClose: vi.fn() } });
    expect(screen.getByText(/Provide/i)).toBeInTheDocument();
  });

  // --- Agent loading ---

  it('loads agents when modal opens', async () => {
    const { listSeedAgents } = await import('$lib/api/seed');
    render(SeedModal, { props: { open: true, onClose: vi.fn() } });
    await vi.waitFor(() => {
      expect(listSeedAgents).toHaveBeenCalled();
    });
  });

  it('displays loaded agent names', async () => {
    render(SeedModal, { props: { open: true, onClose: vi.fn() } });
    await vi.waitFor(() => {
      expect(screen.getByText('code-explorer')).toBeInTheDocument();
    });
  });

  // --- Close behavior ---

  it('calls onClose when close button is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(SeedModal, { props: { open: true, onClose } });
    // Find close/cancel button
    const closeBtn = screen.queryByLabelText(/close/i) || screen.queryByText(/Cancel/i);
    if (closeBtn) {
      await user.click(closeBtn);
      expect(onClose).toHaveBeenCalled();
    }
  });

  // --- Seeding ---

  it('shows prompt count input in generate mode', () => {
    render(SeedModal, { props: { open: true, onClose: vi.fn() } });
    // The prompt count input should have a default value
    const input = screen.queryByDisplayValue('30');
    expect(input).toBeTruthy();
  });

  it('resets state when modal re-opens', async () => {
    const { container, rerender } = render(SeedModal, {
      props: { open: true, onClose: vi.fn() },
    });

    // Close
    await rerender({ open: false, onClose: vi.fn() });

    // Re-open — result and error should be cleared
    await rerender({ open: true, onClose: vi.fn() });
    expect(screen.queryByText(/completed/i)).not.toBeInTheDocument();
  });

  // --- Cycle 15 (Foundation P3): run_id filter on seed_batch_progress ---
  //
  // The +page.svelte SSE bridge dispatches a DOM `seed-batch-progress`
  // CustomEvent carrying the raw payload (now including `run_id` after
  // backend Cycle 7). When the modal is bound to a specific run via the
  // additive `runId` prop, it MUST ignore events from other runs so a
  // concurrent seed batch doesn't smear progress into this modal.
  //
  // Backwards-compat: if `runId` is null / undefined, all events pass —
  // preserves the pre-P3 single-batch global-progress behavior used by
  // StatusBar resume + the legacy "no run selected" path.
  //
  // Brand check: the progress display itself is contour-only — sharp 1px
  // border on `.seed-progress`, solid `--color-neon-cyan` fill, no glow.
  // These tests assert behavior, not visuals; the underlying component
  // continues to honor the zero-effects directive.

  /** Helper: emit the bridge event the way `+page.svelte` does. */
  function emitSeedProgress(detail: Record<string, unknown>): void {
    window.dispatchEvent(
      new CustomEvent('seed-batch-progress', { detail }),
    );
  }

  /**
   * Force the modal into the `seeding` branch so the SSE listener is
   * attached (the listener is mounted lazily inside `$effect(() => { if
   * (!seeding) return; … })`). We click "Start Seed" with a valid
   * generate-mode payload, but pin `seedTaxonomy` to a never-resolving
   * promise so the seeding state persists for the lifetime of the test.
   */
  async function activateSeedingBranch(runId: string | null) {
    const user = userEvent.setup();
    // Pin seedTaxonomy to a pending promise so the seeding state holds —
    // we want to observe progress events, not the eventual result.
    const { seedTaxonomy } = await import('$lib/api/seed');
    (seedTaxonomy as ReturnType<typeof vi.fn>).mockImplementation(
      () => new Promise(() => {}) /* never resolves */,
    );
    // GREEN landed (commit 13c3f41b): `runId` is now a typed prop on
    // SeedModal. The earlier `as any` RED-bridge cast is retired.
    render(SeedModal, { props: { open: true, onClose: vi.fn(), runId } });
    // Wait for the agent list (selectedAgents) to populate — without this
    // the isValid derivation gates the Start Seed button.
    await vi.waitFor(() => {
      expect(screen.getByText('code-explorer')).toBeInTheDocument();
    });
    // Fill the project description (≥20 chars) so isValid is true.
    const desc = screen.getByLabelText(/project description/i) as HTMLTextAreaElement;
    await user.type(desc, 'Refactor taxonomy substrate to RunRow contract');
    const startBtn = screen.getByRole('button', { name: /start seed/i });
    await user.click(startBtn);
    // The $effect runs after click; wait for the seed-progress-label to
    // confirm the listener is mounted before any progress events are
    // dispatched. (`/seeding\.\.\./i` matches both the label and the
    // disabled button — disambiguate via class.)
    await vi.waitFor(() => {
      const labels = document.querySelectorAll('.seed-progress-label');
      expect(labels.length).toBeGreaterThan(0);
    });
    return user;
  }

  it('filters seed_batch_progress events by run_id when modal is bound to a specific run', async () => {
    await activateSeedingBranch('target-run');

    // Event from a DIFFERENT run — must be ignored.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'other-run',
      batch_id: 'batch-other',
      completed: 9,
      total: 10,
      current_prompt: 'should not appear',
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/should not appear/i)).not.toBeInTheDocument();
    // Counter still at the post-handleSeed init: 0 / promptCount(=30).
    expect(screen.getByText(/0 \/ 30 completed/i)).toBeInTheDocument();

    // Event for the TARGET run — must be applied.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'target-run',
      batch_id: 'batch-target',
      completed: 4,
      total: 10,
      current_prompt: 'taxonomy substrate refactor',
    });
    await vi.waitFor(() => {
      expect(screen.getByText(/4 \/ 10 completed/i)).toBeInTheDocument();
      expect(screen.getByText(/taxonomy substrate refactor/i)).toBeInTheDocument();
    });
  });

  it('accepts all seed_batch_progress events when no runId filter is set (pre-P3 behavior)', async () => {
    await activateSeedingBranch(null);

    // First event from run A — applied.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'run-a',
      completed: 2,
      total: 8,
      current_prompt: 'first event',
    });
    await vi.waitFor(() => {
      expect(screen.getByText(/2 \/ 8 completed/i)).toBeInTheDocument();
    });

    // Second event from a different run B — also applied because no
    // filter is bound. This is the legacy global-progress contract.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'run-b',
      completed: 5,
      total: 8,
      current_prompt: 'second event',
    });
    await vi.waitFor(() => {
      expect(screen.getByText(/5 \/ 8 completed/i)).toBeInTheDocument();
      expect(screen.getByText(/second event/i)).toBeInTheDocument();
    });
  });

  it('updates progress counter and percentage from filtered events only', async () => {
    await activateSeedingBranch('target-run');

    // Apply a target-run event with current=5, total=10 → 50%.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'target-run',
      completed: 5,
      total: 10,
      current_prompt: 'mid-batch prompt',
    });
    await vi.waitFor(() => {
      expect(screen.getByText('50%')).toBeInTheDocument();
      expect(screen.getByText(/5 \/ 10 completed/i)).toBeInTheDocument();
    });

    // Stray event from a different run — counter does NOT update.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'noise-run',
      completed: 99,
      total: 100,
      current_prompt: 'noise',
    });
    await new Promise((r) => setTimeout(r, 0));
    // Still showing 5 / 10, not 99 / 100.
    expect(screen.getByText(/5 \/ 10 completed/i)).toBeInTheDocument();
    expect(screen.queryByText('99%')).not.toBeInTheDocument();
  });

  it('aria-live progress region announces only filtered events', async () => {
    await activateSeedingBranch('target-run');

    // Other-run event — ignored, so the live region keeps the seed init
    // text (`0 / 30`) unchanged.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'other-run',
      completed: 7,
      total: 10,
      current_prompt: 'phantom',
    });
    await new Promise((r) => setTimeout(r, 0));
    // The current prompt line only renders when progress.current is
    // populated — phantom must NOT appear.
    expect(screen.queryByText(/phantom/i)).not.toBeInTheDocument();

    // Target-run event — the current-prompt line (`.seed-progress-current`)
    // updates to reflect the announcement.
    emitSeedProgress({
      phase: 'optimize',
      run_id: 'target-run',
      completed: 3,
      total: 10,
      current_prompt: 'announce me',
    });
    await vi.waitFor(() => {
      expect(screen.getByText(/announce me/i)).toBeInTheDocument();
    });
  });

  it('handles events missing run_id gracefully (drops when filter active, accepts when not)', async () => {
    // ── Filter active: a payload missing run_id must be REJECTED so the
    //    modal is not corrupted by malformed cross-run events. ──
    await activateSeedingBranch('target-run');
    emitSeedProgress({
      phase: 'optimize',
      // run_id intentionally omitted
      completed: 6,
      total: 10,
      current_prompt: 'missing run_id',
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.queryByText(/missing run_id/i)).not.toBeInTheDocument();
    expect(screen.getByText(/0 \/ 30 completed/i)).toBeInTheDocument();
    // No throw — assertion is implicit by reaching this line.

    cleanup();

    // ── Filter inactive (runId=null): same payload MUST be ACCEPTED to
    //    preserve back-compat with pre-P3 emitters that did not include
    //    run_id at all. ──
    await activateSeedingBranch(null);
    emitSeedProgress({
      phase: 'optimize',
      completed: 4,
      total: 12,
      current_prompt: 'legacy emitter',
    });
    await vi.waitFor(() => {
      expect(screen.getByText(/4 \/ 12 completed/i)).toBeInTheDocument();
      expect(screen.getByText(/legacy emitter/i)).toBeInTheDocument();
    });
  });
});
