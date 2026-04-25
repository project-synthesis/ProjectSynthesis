/**
 * Update state management — tracks available updates, dialog state, and restart progress.
 *
 * v0.4.6 hardening:
 *   - ``loadPreflight()`` fetches GET /api/update/preflight before
 *     enabling the apply button so users see dirty files, in-flight
 *     optimizations, branch divergence, and customization counts.
 *   - ``stepHistory`` records ``update_step`` SSE events for the
 *     per-phase progress timeline (preflight / drain / fetch_tags /
 *     stash / checkout / deps / migrate / pop_stash / restart /
 *     validate). Replaces the unused ``updateStep`` placeholder.
 *   - ``stashPopConflicts`` + ``validationChecks`` are surfaced in the
 *     completion view (previously stored but never rendered).
 *   - ``startUpdate(force)`` accepts a force flag to bypass non-blocking
 *     warnings (commits ahead, in-flight optimizations).
 *   - ``retryHealthCheck()`` lets the user manually retry health
 *     polling after the 120s timeout instead of being stranded.
 */
import {
  applyUpdate,
  getHealth,
  getUpdatePreflight,
  getUpdateStatus,
  type PreflightResponse,
} from '$lib/api/client';
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

export interface UpdateStepEvent {
  step: string;
  status: 'running' | 'done' | 'warning' | 'failed';
  detail?: string;
  ts: number;
}

const LS_KEY = 'synthesis:dismiss_detached_head_warning';

/** Step labels in their canonical sequential order — used for the timeline. */
const STEP_ORDER = [
  'preflight',
  'drain',
  'fetch_tags',
  'stash',
  'checkout',
  'deps',
  'migrate',
  'pop_stash',
  'restart',
  'validate',
] as const;

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

  // Pre-flight
  preflight = $state<PreflightResponse | null>(null);
  preflightLoading = $state(false);
  preflightError = $state<string | null>(null);

  // Update progress
  updating = $state(false);
  updateStep = $state<string | null>(null);
  stepHistory = $state<UpdateStepEvent[]>([]);
  updateComplete = $state(false);
  updateSuccess = $state<boolean | null>(null);
  validationChecks = $state<ValidationCheck[]>([]);
  stashPopConflicts = $state<string[]>([]);
  pollTimeout = $state(false);

  // Dismissable warning
  hideDetachedWarning = $state(false);

  private _pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor() {
    try {
      this.hideDetachedWarning = localStorage.getItem(LS_KEY) === 'true';
    } catch {
      /* SSR or no localStorage */
    }
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

  /** Fetch the pre-flight readiness probe — must be called before
   * showing the apply dialog so dirty files / divergence / in-flight
   * optimizations are surfaced before the user clicks.
   *
   * Race guard: if the user opens the dialog twice in rapid succession,
   * a stale response from the slower call would otherwise overwrite a
   * fresher one. The generation counter discards out-of-order
   * responses (only the latest call's response is applied to state). */
  private _preflightGen = 0;

  async loadPreflight(): Promise<void> {
    if (!this.latestTag) return;
    this._preflightGen += 1;
    const myGen = this._preflightGen;
    this.preflightLoading = true;
    this.preflightError = null;
    try {
      const result = await getUpdatePreflight(this.latestTag);
      if (myGen !== this._preflightGen) {
        // A newer call superseded us — drop this response.
        return;
      }
      this.preflight = result;
    } catch (err) {
      if (myGen !== this._preflightGen) return;
      this.preflightError = err instanceof Error ? err.message : 'Pre-flight failed';
      this.preflight = null;
    } finally {
      if (myGen === this._preflightGen) {
        this.preflightLoading = false;
      }
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

  /** Handle SSE update_step event — per-phase progress timeline. */
  receiveStep(data: Record<string, unknown>): void {
    const step = data.step as string;
    const status = data.status as UpdateStepEvent['status'];
    const detail = data.detail as string | undefined;
    if (!step || !status) return;
    this.updateStep = step;
    this.stepHistory = [
      ...this.stepHistory,
      { step, status, detail, ts: Date.now() },
    ];
  }

  /** Handle SSE update_complete event (Phase 2 — after restart). */
  receiveComplete(data: Record<string, unknown>): void {
    this.updating = false;
    this.updateComplete = true;
    this.updateSuccess = data.success as boolean;
    this.validationChecks = (data.checks as ValidationCheck[]) ?? [];
    this.stashPopConflicts = (data.stash_pop_conflicts as string[]) ?? [];
    this.updateAvailable = false;
    this._stopPolling();

    if (this.updateSuccess) {
      const conflictNote =
        this.stashPopConflicts.length > 0
          ? ` (${this.stashPopConflicts.length} stash conflict(s) — see dialog)`
          : '';
      addToast(
        'created',
        `Updated to v${(data.version as string) ?? this.latestVersion}${conflictNote}`,
      );
    } else {
      addToast(
        'deleted',
        'Update completed with warnings — check validation results',
      );
    }
  }

  /** Trigger the update — calls POST /api/update/apply then polls health.
   *
   * @param force Bypass non-blocking pre-flight warnings (commits-ahead,
   *   in-flight optimizations remaining after drain). Blocking issues —
   *   non-prompt uncommitted changes, invalid tag — are always enforced.
   */
  async startUpdate(force: boolean = false): Promise<void> {
    if (!this.latestTag || this.updating) return;

    // Guard — preflight must allow apply (or user must opt into force).
    if (this.preflight && !this.preflight.can_apply && !force) {
      addToast(
        'deleted',
        'Pre-flight blocked: ' + this.preflight.blocking_issues.join(' | '),
      );
      return;
    }

    this.updating = true;
    this.updateStep = null;
    this.stepHistory = [];
    this.updateComplete = false;
    this.pollTimeout = false;
    this.dialogOpen = false;

    try {
      await applyUpdate(this.latestTag, force);
      this._startPolling();
    } catch (err) {
      this.updating = false;
      const msg = err instanceof Error ? err.message : 'Update failed';
      addToast('deleted', msg);
    }
  }

  /** Manual health-poll restart after the 120s auto-timeout. */
  retryHealthCheck(): void {
    if (!this.updating) {
      this.updating = true;
      this.pollTimeout = false;
    }
    this._startPolling();
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
    } catch {
      /* SSR */
    }
  }

  /** Convenience: known step labels in canonical order — frontend
   * timeline component renders one row per step, marking each as
   * pending / running / done / warning / failed based on stepHistory. */
  get stepOrder(): readonly string[] {
    return STEP_ORDER;
  }

  /** True when the preflight is fresh enough that the apply button
   * should reflect its can_apply gate. */
  get canApply(): boolean {
    return this.preflight ? this.preflight.can_apply : true;
  }

  private _startPolling(): void {
    let elapsed = 0;
    this.pollTimeout = false;
    this._pollTimer = setInterval(async () => {
      elapsed += 2000;
      try {
        const h = await getHealth();
        if (
          h.version &&
          this.latestVersion &&
          h.version.startsWith(this.latestVersion.split('-')[0])
        ) {
          this._stopPolling();
          return;
        }
      } catch {
        // Backend still down — keep polling
      }
      if (elapsed > 120_000 && this._pollTimer) {
        this._stopPolling();
        this.updating = false;
        this.pollTimeout = true;
        addToast(
          'deleted',
          'Update may have failed. Try ./init.sh restart or click Retry.',
        );
      }
    }, 2000);
  }

  private _stopPolling(): void {
    if (this._pollTimer) {
      clearInterval(this._pollTimer);
      this._pollTimer = null;
    }
  }

  /** Test helper — reset all state (frontend tests rely on a clean store). */
  _reset(): void {
    this._stopPolling();
    this.updateAvailable = false;
    this.currentVersion = null;
    this.latestVersion = null;
    this.latestTag = null;
    this.changelog = null;
    this.changelogEntries = null;
    this.detectionTier = null;
    this.dialogOpen = false;
    this.preflight = null;
    this.preflightLoading = false;
    this.preflightError = null;
    this.updating = false;
    this.updateStep = null;
    this.stepHistory = [];
    this.updateComplete = false;
    this.updateSuccess = null;
    this.validationChecks = [];
    this.stashPopConflicts = [];
    this.pollTimeout = false;
  }
}

export const updateStore = new UpdateStore();
