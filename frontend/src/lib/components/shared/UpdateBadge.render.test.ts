/**
 * UpdateBadge component render tests.
 *
 * The pre-existing UpdateBadge.test.ts only exercises the underlying
 * updateStore — the component itself sits at 0% coverage. This file
 * covers the four exclusive top-level branches (updating / pollTimeout
 * / completed-with-conflicts / available), the progress timeline, the
 * completion dialog, and the click-to-open interaction.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';

vi.mock('$lib/actions/tooltip', () => ({
  tooltip: () => ({ destroy() {} }),
}));
vi.mock('$lib/api/client', () => ({
  applyUpdate: vi.fn(),
  getHealth: vi.fn(),
  getUpdatePreflight: vi.fn().mockResolvedValue({
    can_apply: true, blocking_issues: [], warnings: [], dirty_files: [],
    user_customizations: [], commits_ahead_of_origin: 0, commits_behind_origin: 0,
    on_detached_head: false, in_flight_optimizations: 0, in_flight_trace_ids: [],
    will_auto_stash: false, target_tag: 'v0.4.99', target_tag_exists_locally: true,
  }),
  getUpdateStatus: vi.fn(),
}));
vi.mock('$lib/stores/toast.svelte', () => ({ addToast: vi.fn() }));

import UpdateBadge from './UpdateBadge.svelte';
import { updateStore } from '$lib/stores/update.svelte';

beforeEach(() => {
  updateStore._reset();
});
afterEach(() => {
  cleanup();
  updateStore._reset();
});

describe('UpdateBadge — top-level branch selection', () => {
  it('renders the "updating" badge when updateStore.updating=true', () => {
    updateStore.updating = true;
    updateStore.updateStep = 'preflight';
    render(UpdateBadge);
    const btn = screen.getAllByRole('button')[0];
    expect(btn.className).toMatch(/updating/);
    expect(btn.textContent).toMatch(/Pre-flight/);
  });

  it('falls back to "Restarting…" when updating but updateStep is null', () => {
    updateStore.updating = true;
    updateStore.updateStep = null;
    render(UpdateBadge);
    expect(screen.getAllByRole('button')[0].textContent).toMatch(/Restarting/);
  });

  it('renders the "Retry" badge when pollTimeout=true', () => {
    updateStore.pollTimeout = true;
    render(UpdateBadge);
    const btn = screen.getByRole('button');
    expect(btn.className).toMatch(/timeout/);
    expect(btn.textContent).toMatch(/Retry/);
  });

  it('renders the "warning" badge after completion with stash-pop conflicts', () => {
    updateStore.updateComplete = true;
    updateStore.stashPopConflicts = ['frontend/src/lib/components/foo.svelte'];
    render(UpdateBadge);
    const btn = screen.getByRole('button');
    expect(btn.className).toMatch(/warning/);
    expect(btn.textContent).toMatch(/1 conflict/);
  });

  it('pluralizes correctly when multiple stash-pop conflicts present', () => {
    updateStore.updateComplete = true;
    updateStore.stashPopConflicts = ['a.svelte', 'b.ts', 'c.md'];
    render(UpdateBadge);
    expect(screen.getByRole('button').textContent).toMatch(/3 conflicts/);
  });

  it('renders the "available" badge with version when no other branch active', () => {
    updateStore.latestVersion = '0.4.19';
    updateStore.updateAvailable = true;
    render(UpdateBadge);
    const btn = screen.getByRole('button');
    expect(btn.className).toMatch(/available/);
    expect(btn.textContent).toMatch(/v0\.4\.19/);
  });
});

describe('UpdateBadge — progress timeline rendering when updating', () => {
  it('renders the canonical step labels in the progress timeline', () => {
    updateStore.updating = true;
    updateStore.updateStep = 'preflight';
    updateStore.stepHistory = [
      { step: 'preflight', status: 'running', detail: 'starting', ts: 1 },
    ];
    render(UpdateBadge);
    expect(screen.getByText('Update in progress')).toBeInTheDocument();
    // The badge button itself contains "Pre-flight" text — getAllByText picks
    // up multiple matches across button + timeline rows.
    expect(screen.getAllByText('Pre-flight').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Drain in-flight')).toBeInTheDocument();
    expect(screen.getByText('Validate health')).toBeInTheDocument();
  });

  it('renders the step "detail" line when present in stepHistory', () => {
    updateStore.updating = true;
    updateStore.updateStep = 'embedding';
    updateStore.stepHistory = [
      { step: 'preflight', status: 'done', detail: '4 files dirty', ts: 1 },
    ];
    render(UpdateBadge);
    expect(screen.getByText(/4 files dirty/)).toBeInTheDocument();
  });
});

describe('UpdateBadge — completion dialog', () => {
  it('renders the success completion dialog when complete + dialogOpen', () => {
    updateStore.updateComplete = true;
    updateStore.dialogOpen = true;
    updateStore.updateSuccess = true;
    updateStore.currentVersion = '0.4.18';
    updateStore.latestVersion = '0.4.19';
    updateStore.validationChecks = [
      { name: 'health endpoint', passed: true, detail: 'ok' },
    ];
    render(UpdateBadge);
    expect(screen.getByText('Update applied')).toBeInTheDocument();
    expect(screen.getByText(/health endpoint/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Acknowledge/ })).toBeInTheDocument();
  });

  it('renders the warning completion dialog when updateSuccess=false', () => {
    updateStore.updateComplete = true;
    updateStore.dialogOpen = true;
    updateStore.updateSuccess = false;
    updateStore.currentVersion = '0.4.18';
    updateStore.latestVersion = '0.4.19';
    render(UpdateBadge);
    expect(screen.getByText('Update completed with warnings')).toBeInTheDocument();
  });

  it('renders the stash-pop conflict section when conflicts present', () => {
    updateStore.updateComplete = true;
    updateStore.dialogOpen = true;
    updateStore.updateSuccess = true;
    updateStore.currentVersion = '0.4.18';
    updateStore.latestVersion = '0.4.19';
    updateStore.stashPopConflicts = ['conf.ts', 'merge.svelte'];
    render(UpdateBadge);
    expect(screen.getByText('Stash-pop conflicts')).toBeInTheDocument();
    expect(screen.getByText(/conf\.ts/)).toBeInTheDocument();
    expect(screen.getByText(/merge\.svelte/)).toBeInTheDocument();
  });

  it('Acknowledge button closes the completion dialog', async () => {
    const user = userEvent.setup();
    updateStore.updateComplete = true;
    updateStore.dialogOpen = true;
    updateStore.updateSuccess = true;
    updateStore.currentVersion = '0.4.18';
    updateStore.latestVersion = '0.4.19';
    render(UpdateBadge);
    const ack = screen.getByRole('button', { name: /Acknowledge/ });
    await user.click(ack);
    expect(updateStore.updateComplete).toBe(false);
    expect(updateStore.dialogOpen).toBe(false);
  });
});

describe('UpdateBadge — interaction wiring', () => {
  it('clicking the available badge opens the dialog', async () => {
    const user = userEvent.setup();
    updateStore.latestVersion = '0.4.19';
    updateStore.latestTag = 'v0.4.19';
    updateStore.updateAvailable = true;
    render(UpdateBadge);
    await user.click(screen.getByRole('button'));
    expect(updateStore.dialogOpen).toBe(true);
  });

  it('Retry badge flips updating back on and clears pollTimeout', async () => {
    // userEvent uses real timers internally for click dispatch; fake-timers
    // would deadlock the click. Manually clear the polling interval after.
    const user = userEvent.setup();
    updateStore.pollTimeout = true;
    updateStore.latestTag = 'v0.4.19';
    render(UpdateBadge);
    await user.click(screen.getByRole('button', { name: /Retry/ }));
    expect(updateStore.updating).toBe(true);
    expect(updateStore.pollTimeout).toBe(false);
    updateStore._reset();
  });
});
