import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import BranchSwitcher from './BranchSwitcher.svelte';
import { mockRefinementBranch } from '$lib/test-utils';

describe('BranchSwitcher', () => {
  afterEach(() => {
    cleanup();
  });

  function makeBranches(count: number) {
    return Array.from({ length: count }, (_, i) =>
      mockRefinementBranch({ id: `branch-${i}`, optimization_id: 'opt-1' })
    );
  }

  it('renders nothing when only one branch exists', () => {
    const branches = makeBranches(1);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch: vi.fn() },
    });
    expect(screen.queryByLabelText('Branch navigation')).not.toBeInTheDocument();
  });

  it('renders navigation when two or more branches exist', () => {
    const branches = makeBranches(2);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch: vi.fn() },
    });
    expect(screen.getByLabelText('Branch navigation')).toBeInTheDocument();
  });

  it('displays current branch position (1/2)', () => {
    const branches = makeBranches(2);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch: vi.fn() },
    });
    expect(screen.getByText('Branch 1/2')).toBeInTheDocument();
  });

  it('displays correct branch position for second branch (2/3)', () => {
    const branches = makeBranches(3);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-1', onSwitch: vi.fn() },
    });
    expect(screen.getByText('Branch 2/3')).toBeInTheDocument();
  });

  it('prev button is disabled when at first branch', () => {
    const branches = makeBranches(2);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch: vi.fn() },
    });
    expect(screen.getByLabelText('Previous branch')).toBeDisabled();
  });

  it('next button is disabled when at last branch', () => {
    const branches = makeBranches(2);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-1', onSwitch: vi.fn() },
    });
    expect(screen.getByLabelText('Next branch')).toBeDisabled();
  });

  it('prev button is enabled when not at first branch', () => {
    const branches = makeBranches(3);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-1', onSwitch: vi.fn() },
    });
    expect(screen.getByLabelText('Previous branch')).not.toBeDisabled();
  });

  it('next button is enabled when not at last branch', () => {
    const branches = makeBranches(3);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch: vi.fn() },
    });
    expect(screen.getByLabelText('Next branch')).not.toBeDisabled();
  });

  it('calls onSwitch with previous branch id when prev is clicked', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const branches = makeBranches(3);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-1', onSwitch },
    });
    await user.click(screen.getByLabelText('Previous branch'));
    expect(onSwitch).toHaveBeenCalledWith('branch-0');
  });

  it('calls onSwitch with next branch id when next is clicked', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const branches = makeBranches(3);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch },
    });
    await user.click(screen.getByLabelText('Next branch'));
    expect(onSwitch).toHaveBeenCalledWith('branch-1');
  });

  it('does not call onSwitch when clicking a disabled prev button', async () => {
    const user = userEvent.setup();
    const onSwitch = vi.fn();
    const branches = makeBranches(2);
    render(BranchSwitcher, {
      props: { branches, activeBranchId: 'branch-0', onSwitch },
    });
    // The button is disabled so the click should not fire onSwitch
    await user.click(screen.getByLabelText('Previous branch'));
    expect(onSwitch).not.toHaveBeenCalled();
  });
});
