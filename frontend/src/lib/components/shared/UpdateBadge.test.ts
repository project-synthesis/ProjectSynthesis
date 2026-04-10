import { describe, it, expect, vi, beforeEach } from 'vitest';
import { updateStore } from '$lib/stores/update.svelte';

vi.mock('$lib/api/client', () => ({
  getUpdateStatus: vi.fn().mockResolvedValue({ update_available: false, current_version: '0.3.20' }),
  applyUpdate: vi.fn().mockResolvedValue({ status: 'restarting', tag: 'v0.4.0' }),
  getHealth: vi.fn().mockResolvedValue({ version: '0.3.20', provider: 'mock' }),
}));

vi.mock('$lib/stores/toast.svelte', () => ({
  addToast: vi.fn(),
}));

describe('UpdateStore — Happy Paths', () => {
  beforeEach(() => {
    updateStore.updateAvailable = false;
    updateStore.updating = false;
    updateStore.updateComplete = false;
    updateStore.updateSuccess = null;
    updateStore.dialogOpen = false;
    updateStore.latestVersion = null;
    updateStore.latestTag = null;
    updateStore.changelog = null;
    updateStore.changelogEntries = null;
    updateStore.hideDetachedWarning = false;
    updateStore.validationChecks = [];
    localStorage.clear();
  });

  it('starts with no update available', () => {
    expect(updateStore.updateAvailable).toBe(false);
    expect(updateStore.updating).toBe(false);
    expect(updateStore.updateComplete).toBe(false);
  });

  it('receive() populates all update info', () => {
    updateStore.receive({
      current_version: '0.3.20-dev',
      latest_version: '0.4.0',
      latest_tag: 'v0.4.0',
      changelog: '## Added\n- Feature',
      changelog_entries: [{ category: 'Added', text: 'Feature' }],
    });
    expect(updateStore.updateAvailable).toBe(true);
    expect(updateStore.latestVersion).toBe('0.4.0');
    expect(updateStore.latestTag).toBe('v0.4.0');
    expect(updateStore.changelog).toBe('## Added\n- Feature');
    expect(updateStore.changelogEntries).toHaveLength(1);
  });

  it('receiveComplete() with success clears badge', () => {
    updateStore.updating = true;
    updateStore.updateAvailable = true;
    updateStore.receiveComplete({
      success: true,
      version: '0.4.0',
      checks: [
        { name: 'version', passed: true, detail: 'OK' },
        { name: 'tag', passed: true, detail: 'OK' },
        { name: 'migrations', passed: true, detail: 'OK' },
      ],
    });
    expect(updateStore.updating).toBe(false);
    expect(updateStore.updateComplete).toBe(true);
    expect(updateStore.updateSuccess).toBe(true);
    expect(updateStore.updateAvailable).toBe(false);
    expect(updateStore.validationChecks).toHaveLength(3);
  });

  it('dismissWarning persists and restores', () => {
    updateStore.dismissWarning(true);
    expect(updateStore.hideDetachedWarning).toBe(true);
    expect(localStorage.getItem('synthesis:dismiss_detached_head_warning')).toBe('true');

    updateStore.dismissWarning(false);
    expect(updateStore.hideDetachedWarning).toBe(false);
    expect(localStorage.getItem('synthesis:dismiss_detached_head_warning')).toBeNull();
  });
});

describe('UpdateStore — Unhappy Paths', () => {
  beforeEach(() => {
    updateStore.updateAvailable = false;
    updateStore.updating = false;
    updateStore.dialogOpen = false;
    updateStore.latestVersion = null;
    updateStore.latestTag = null;
    localStorage.clear();
  });

  it('receiveComplete() with failure keeps state', () => {
    updateStore.updating = true;
    updateStore.updateAvailable = true;
    updateStore.receiveComplete({
      success: false,
      version: '0.4.0',
      checks: [
        { name: 'version', passed: true, detail: 'OK' },
        { name: 'tag', passed: false, detail: 'HEAD detached' },
      ],
    });
    expect(updateStore.updateSuccess).toBe(false);
    expect(updateStore.updateComplete).toBe(true);
    expect(updateStore.validationChecks).toHaveLength(2);
  });

  it('receive() with null changelog still sets updateAvailable', () => {
    updateStore.receive({
      current_version: '0.3.20',
      latest_version: '0.4.0',
      latest_tag: 'v0.4.0',
      changelog: null,
      changelog_entries: null,
    });
    expect(updateStore.updateAvailable).toBe(true);
    expect(updateStore.changelog).toBeNull();
    expect(updateStore.changelogEntries).toBeNull();
  });

  it('startUpdate() does nothing when no latestTag', async () => {
    updateStore.latestTag = null;
    await updateStore.startUpdate();
    expect(updateStore.updating).toBe(false);
  });

  it('startUpdate() does nothing when already updating', async () => {
    updateStore.latestTag = 'v0.4.0';
    updateStore.updating = true;
    const { applyUpdate } = await import('$lib/api/client');
    await updateStore.startUpdate();
    expect(applyUpdate).not.toHaveBeenCalled();
  });
});

describe('UpdateStore — Edge Cases', () => {
  beforeEach(() => {
    updateStore.updateAvailable = false;
    updateStore.updating = false;
    updateStore.dialogOpen = false;
    localStorage.clear();
  });

  it('receive() called multiple times overwrites cleanly', () => {
    updateStore.receive({ latest_version: '0.4.0', latest_tag: 'v0.4.0' });
    updateStore.receive({ latest_version: '0.5.0', latest_tag: 'v0.5.0' });
    expect(updateStore.latestVersion).toBe('0.5.0');
    expect(updateStore.latestTag).toBe('v0.5.0');
  });

  it('receiveComplete() after receive() clears update state', () => {
    updateStore.receive({ latest_version: '0.4.0', latest_tag: 'v0.4.0' });
    expect(updateStore.updateAvailable).toBe(true);
    updateStore.updating = true;
    updateStore.receiveComplete({ success: true, version: '0.4.0', checks: [] });
    expect(updateStore.updateAvailable).toBe(false);
  });

  it('localStorage failure does not crash dismissWarning', () => {
    const orig = localStorage.setItem;
    localStorage.setItem = () => { throw new Error('quota exceeded'); };
    updateStore.dismissWarning(true);
    expect(updateStore.hideDetachedWarning).toBe(true);
    localStorage.setItem = orig;
  });
});
