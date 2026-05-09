import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { PreflightResponse } from '$lib/api/client';

vi.mock('$lib/api/client', () => ({
  applyUpdate: vi.fn(),
  getHealth: vi.fn(),
  getUpdatePreflight: vi.fn(),
  getUpdateStatus: vi.fn(),
}));
vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
}));

import * as client from '$lib/api/client';
import * as toastModule from '$lib/stores/toast.svelte';
import { updateStore } from './update.svelte';

const PREFLIGHT_OK: PreflightResponse = {
  can_apply: true,
  blocking_issues: [],
  warnings: [],
  dirty_files: [],
  user_customizations: [],
  commits_ahead_of_origin: 0,
  commits_behind_origin: 0,
  on_detached_head: false,
  in_flight_optimizations: 0,
  in_flight_trace_ids: [],
  will_auto_stash: false,
  target_tag: 'v0.4.99',
  target_tag_exists_locally: true,
};

describe('updateStore — load', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateStore._reset();
  });
  afterEach(() => {
    updateStore._reset();
  });

  it('populates fields from getUpdateStatus()', async () => {
    vi.mocked(client.getUpdateStatus).mockResolvedValue({
      current_version: '0.4.18',
      latest_version: '0.4.19',
      latest_tag: 'v0.4.19',
      update_available: true,
      changelog: 'release notes',
      changelog_entries: [{ category: 'Added', text: 'thing' }],
      detection_tier: 'gh',
    } as never);
    await updateStore.load();
    expect(updateStore.currentVersion).toBe('0.4.18');
    expect(updateStore.updateAvailable).toBe(true);
    expect(updateStore.changelogEntries).toHaveLength(1);
  });

  it('silently swallows errors from getUpdateStatus', async () => {
    vi.mocked(client.getUpdateStatus).mockRejectedValue(new Error('boom'));
    await expect(updateStore.load()).resolves.toBeUndefined();
    expect(updateStore.currentVersion).toBeNull();
  });
});

describe('updateStore — loadPreflight (race-guard)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateStore._reset();
    updateStore.latestTag = 'v0.4.19';
  });
  afterEach(() => updateStore._reset());

  it('no-op when latestTag is null', async () => {
    updateStore.latestTag = null;
    await updateStore.loadPreflight();
    expect(client.getUpdatePreflight).not.toHaveBeenCalled();
  });

  it('populates preflight on success and clears loading', async () => {
    vi.mocked(client.getUpdatePreflight).mockResolvedValue(PREFLIGHT_OK);
    await updateStore.loadPreflight();
    expect(updateStore.preflight).toEqual(PREFLIGHT_OK);
    expect(updateStore.preflightLoading).toBe(false);
    expect(updateStore.preflightError).toBeNull();
  });

  it('sets preflightError on rejection', async () => {
    vi.mocked(client.getUpdatePreflight).mockRejectedValue(new Error('500'));
    await updateStore.loadPreflight();
    expect(updateStore.preflightError).toBe('500');
    expect(updateStore.preflight).toBeNull();
  });

  it('discards stale response when a newer call supersedes', async () => {
    // First call: slow + stale; second call: fast + wins.
    let resolveFirst: (v: PreflightResponse) => void = () => {};
    const slow = new Promise<PreflightResponse>((res) => { resolveFirst = res; });
    const fastResult = { ...PREFLIGHT_OK, blocking_issues: ['fresh'] };
    vi.mocked(client.getUpdatePreflight)
      .mockReturnValueOnce(slow as never)
      .mockResolvedValueOnce(fastResult);

    const p1 = updateStore.loadPreflight();
    const p2 = updateStore.loadPreflight();
    await p2;
    // Now resolve the stale call after the fast one settled.
    resolveFirst({ ...PREFLIGHT_OK, blocking_issues: ['stale'] });
    await p1;

    // The stale write must not overwrite the fresh one.
    expect(updateStore.preflight?.blocking_issues).toEqual(['fresh']);
  });
});

describe('updateStore — receive (SSE)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateStore._reset();
  });
  afterEach(() => updateStore._reset());

  it('receive() updates fields and flips updateAvailable=true', () => {
    updateStore.receive({
      current_version: '0.4.18',
      latest_version: '0.4.19',
      latest_tag: 'v0.4.19',
      changelog: 'note',
      changelog_entries: [{ category: 'Added', text: 'x' }],
    });
    expect(updateStore.updateAvailable).toBe(true);
    expect(updateStore.latestTag).toBe('v0.4.19');
    expect(updateStore.changelogEntries).toHaveLength(1);
  });

  it('receiveStep() appends to history and updates updateStep', () => {
    updateStore.receiveStep({ step: 'preflight', status: 'running', detail: 'starting' });
    updateStore.receiveStep({ step: 'preflight', status: 'done' });
    expect(updateStore.updateStep).toBe('preflight');
    expect(updateStore.stepHistory).toHaveLength(2);
    expect(updateStore.stepHistory[1].status).toBe('done');
  });

  it('receiveStep() ignores malformed payload (missing step or status)', () => {
    updateStore.receiveStep({});
    updateStore.receiveStep({ step: 'preflight' });
    updateStore.receiveStep({ status: 'running' });
    expect(updateStore.stepHistory).toHaveLength(0);
  });

  it('receiveComplete() success flushes flags + emits success toast with conflict count', () => {
    updateStore.latestVersion = '0.4.19';
    updateStore.updating = true;
    updateStore.receiveComplete({
      success: true,
      version: '0.4.19',
      checks: [{ name: 'health', passed: true, detail: 'ok' }],
      stash_pop_conflicts: ['file1.txt'],
    });
    expect(updateStore.updateComplete).toBe(true);
    expect(updateStore.updateSuccess).toBe(true);
    expect(updateStore.updating).toBe(false);
    expect(updateStore.updateAvailable).toBe(false);
    expect(updateStore.stashPopConflicts).toEqual(['file1.txt']);
    expect(toastModule.addToast).toHaveBeenCalledWith(
      'created',
      expect.stringMatching(/Updated to v0\.4\.19.*1 stash conflict/),
    );
  });

  it('receiveComplete() failure path emits warning toast', () => {
    updateStore.receiveComplete({ success: false, checks: [], stash_pop_conflicts: [] });
    expect(updateStore.updateSuccess).toBe(false);
    expect(toastModule.addToast).toHaveBeenCalledWith(
      'deleted',
      expect.stringMatching(/Update completed with warnings/),
    );
  });
});

describe('updateStore — startUpdate gate logic', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateStore._reset();
  });
  afterEach(() => updateStore._reset());

  it('no-op when latestTag null', async () => {
    await updateStore.startUpdate();
    expect(client.applyUpdate).not.toHaveBeenCalled();
  });

  it('no-op when already updating', async () => {
    updateStore.latestTag = 'v0.4.19';
    updateStore.updating = true;
    await updateStore.startUpdate();
    expect(client.applyUpdate).not.toHaveBeenCalled();
  });

  it('blocks when preflight.can_apply=false and force=false', async () => {
    updateStore.latestTag = 'v0.4.19';
    updateStore.preflight = { ...PREFLIGHT_OK, can_apply: false, blocking_issues: ['dirty repo'] };
    await updateStore.startUpdate();
    expect(client.applyUpdate).not.toHaveBeenCalled();
    expect(toastModule.addToast).toHaveBeenCalledWith(
      'deleted',
      expect.stringMatching(/Pre-flight blocked.*dirty repo/),
    );
  });

  it('force=true bypasses preflight gate', async () => {
    updateStore.latestTag = 'v0.4.19';
    updateStore.preflight = { ...PREFLIGHT_OK, can_apply: false, blocking_issues: ['x'] };
    vi.mocked(client.applyUpdate).mockResolvedValue({
      status: 'started',
      tag: 'v0.4.19',
      message: 'ok',
      stash_pop_conflicts: [],
    } as never);
    // Use fake timers so _startPolling doesn't actually fire setInterval.
    vi.useFakeTimers();
    await updateStore.startUpdate(true);
    vi.useRealTimers();
    expect(client.applyUpdate).toHaveBeenCalledWith('v0.4.19', true);
    updateStore._reset();
  });

  it('applyUpdate failure resets updating and emits toast', async () => {
    updateStore.latestTag = 'v0.4.19';
    updateStore.preflight = PREFLIGHT_OK;
    vi.mocked(client.applyUpdate).mockRejectedValue(new Error('500: server'));
    await updateStore.startUpdate();
    expect(updateStore.updating).toBe(false);
    expect(toastModule.addToast).toHaveBeenCalledWith(
      'deleted',
      expect.stringContaining('500: server'),
    );
  });
});

describe('updateStore — convenience getters and dismissWarning', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateStore._reset();
    localStorage.removeItem('synthesis:dismiss_detached_head_warning');
  });
  afterEach(() => updateStore._reset());

  it('canApply defaults to true when preflight not loaded yet', () => {
    expect(updateStore.canApply).toBe(true);
  });

  it('canApply mirrors preflight.can_apply once loaded', () => {
    updateStore.preflight = { ...PREFLIGHT_OK, can_apply: false };
    expect(updateStore.canApply).toBe(false);
  });

  it('stepOrder returns the canonical 10-step sequence', () => {
    expect(updateStore.stepOrder).toEqual([
      'preflight', 'drain', 'fetch_tags', 'stash', 'checkout',
      'deps', 'migrate', 'pop_stash', 'restart', 'validate',
    ]);
  });

  it('dismissWarning(true) writes localStorage; dismissWarning(false) clears it', () => {
    updateStore.dismissWarning(true);
    expect(localStorage.getItem('synthesis:dismiss_detached_head_warning')).toBe('true');
    expect(updateStore.hideDetachedWarning).toBe(true);
    updateStore.dismissWarning(false);
    expect(localStorage.getItem('synthesis:dismiss_detached_head_warning')).toBeNull();
    expect(updateStore.hideDetachedWarning).toBe(false);
  });

  it('retryHealthCheck resets pollTimeout and resumes updating flag', () => {
    updateStore.updating = false;
    updateStore.pollTimeout = true;
    vi.useFakeTimers();
    updateStore.retryHealthCheck();
    vi.useRealTimers();
    expect(updateStore.updating).toBe(true);
    expect(updateStore.pollTimeout).toBe(false);
    updateStore._reset();
  });
});
