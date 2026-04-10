/**
 * Update state management — tracks available updates, dialog state, and restart progress.
 */
import { getUpdateStatus, applyUpdate, getHealth } from '$lib/api/client';
import { addToast } from '$lib/stores/toast.svelte';

export interface ChangelogEntry {
  category: string;
  text: string;
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail: string;
}

const LS_KEY = 'synthesis:dismiss_detached_head_warning';

class UpdateStore {
  // Version info
  updateAvailable = $state(false);
  currentVersion = $state<string | null>(null);
  latestVersion = $state<string | null>(null);
  latestTag = $state<string | null>(null);
  changelog = $state<string | null>(null);
  changelogEntries = $state<ChangelogEntry[] | null>(null);
  detectionTier = $state<string | null>(null);

  // Dialog
  dialogOpen = $state(false);

  // Update progress
  updating = $state(false);
  updateStep = $state<string | null>(null);
  updateComplete = $state(false);
  updateSuccess = $state<boolean | null>(null);
  validationChecks = $state<ValidationCheck[]>([]);

  // Dismissable warning
  hideDetachedWarning = $state(false);

  private _pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    try {
      this.hideDetachedWarning = localStorage.getItem(LS_KEY) === 'true';
    } catch { /* SSR or no localStorage */ }
  }

  /** Populate from GET /api/update/status on page load. */
  async load(): Promise<void> {
    try {
      const s = await getUpdateStatus();
      this.currentVersion = s.current_version;
      this.latestVersion = s.latest_version;
      this.latestTag = s.latest_tag;
      this.updateAvailable = s.update_available;
      this.changelog = s.changelog;
      this.changelogEntries = s.changelog_entries;
      this.detectionTier = s.detection_tier;
    } catch {
      // Silently fail — update info is optional
    }
  }

  /** Handle SSE update_available event. */
  receive(data: Record<string, unknown>): void {
    this.currentVersion = (data.current_version as string) ?? this.currentVersion;
    this.latestVersion = (data.latest_version as string) ?? null;
    this.latestTag = (data.latest_tag as string) ?? null;
    this.changelog = (data.changelog as string) ?? null;
    this.changelogEntries = (data.changelog_entries as ChangelogEntry[]) ?? null;
    this.updateAvailable = true;
  }

  /** Handle SSE update_complete event (Phase 2 — after restart). */
  receiveComplete(data: Record<string, unknown>): void {
    this.updating = false;
    this.updateComplete = true;
    this.updateSuccess = data.success as boolean;
    this.validationChecks = (data.checks as ValidationCheck[]) ?? [];
    this.updateAvailable = false;
    this._stopPolling();

    if (this.updateSuccess) {
      addToast('created', `Updated to v${(data.version as string) ?? this.latestVersion}`);
    } else {
      addToast('deleted', 'Update completed with warnings — check validation results');
    }
  }

  /** Trigger the update — calls POST /api/update/apply then polls health. */
  async startUpdate(): Promise<void> {
    if (!this.latestTag || this.updating) return;

    this.updating = true;
    this.dialogOpen = false;

    try {
      await applyUpdate(this.latestTag);
      this._startPolling();
    } catch (err) {
      this.updating = false;
      const msg = err instanceof Error ? err.message : 'Update failed';
      addToast('deleted', msg);
    }
  }

  /** Toggle the "don't show again" checkbox. */
  dismissWarning(dismissed: boolean): void {
    this.hideDetachedWarning = dismissed;
    try {
      if (dismissed) {
        localStorage.setItem(LS_KEY, 'true');
      } else {
        localStorage.removeItem(LS_KEY);
      }
    } catch { /* SSR */ }
  }

  private _startPolling(): void {
    let elapsed = 0;
    this._pollTimer = setInterval(async () => {
      elapsed += 2000;
      try {
        const h = await getHealth();
        if (h.version && this.latestVersion && h.version.startsWith(this.latestVersion.split('-')[0])) {
          this._stopPolling();
          return; // success — skip timeout check below
        }
      } catch {
        // Backend still down — keep polling
      }
      if (elapsed > 120_000 && this._pollTimer) {
        this._stopPolling();
        this.updating = false;
        addToast('deleted', 'Update may have failed. Try ./init.sh restart in the terminal.');
      }
    }, 2000);
  }

  private _stopPolling(): void {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }
}

export const updateStore = new UpdateStore();
