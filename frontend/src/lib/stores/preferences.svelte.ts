import { getPreferences, patchPreferences } from '$lib/api/client';
import { toastStore } from './toast.svelte';

export interface ModelPrefs {
  analyzer: string;
  optimizer: string;
  scorer: string;
}

export interface PipelinePrefs {
  enable_explore: boolean;
  enable_scoring: boolean;
  enable_adaptation: boolean;
  force_sampling: boolean;
  force_passthrough: boolean;
  optimizer_effort: string;
  analyzer_effort: string;
  scorer_effort: string;
}

export interface ReadinessNotificationsPrefs {
  enabled: boolean;
  muted_domain_ids: string[];
}

export interface Preferences {
  schema_version: number;
  models: ModelPrefs;
  pipeline: PipelinePrefs;
  defaults: { strategy: string };
  domain_readiness_notifications: ReadinessNotificationsPrefs;
}

const DEFAULTS: Preferences = {
  schema_version: 1,
  models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
  pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: false, optimizer_effort: 'high', analyzer_effort: 'low', scorer_effort: 'low' },
  defaults: { strategy: 'auto' },
  // Default ON — see backend preferences.py for rationale. Users opt out via
  // the master bell in DomainReadinessPanel or per-row mutes.
  domain_readiness_notifications: { enabled: true, muted_domain_ids: [] },
};

/** User-visible toast surfaced when `toggleDomainMute()` rolls back. */
const MUTE_TOGGLE_ERROR_MESSAGE = 'Failed to update mute preference';

class PreferencesStore {
  prefs = $state<Preferences>(structuredClone(DEFAULTS));
  loading = $state(false);
  error = $state<string | null>(null);

  get models(): ModelPrefs { return this.prefs.models; }
  get pipeline(): PipelinePrefs { return this.prefs.pipeline; }
  get defaultStrategy(): string { return this.prefs.defaults.strategy; }

  get isLeanMode(): boolean {
    return !this.prefs.pipeline.enable_explore && !this.prefs.pipeline.enable_scoring;
  }

  async init(): Promise<void> {
    this.loading = true;
    this.error = null;
    try {
      const data = await getPreferences();
      this.prefs = data as Preferences;
    } catch {
      // Backend offline — use defaults
    } finally {
      this.loading = false;
    }
  }

  async update(patch: Record<string, any>): Promise<void> {
    this.error = null;
    try {
      const updated = await patchPreferences(patch);
      this.prefs = updated as Preferences;
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Failed to save';
    }
  }

  async setModel(phase: string, model: string): Promise<void> {
    await this.update({ models: { [phase]: model } });
  }

  async setPipelineToggle(key: string, value: boolean): Promise<void> {
    const patch: Record<string, boolean> = { [key]: value };
    // Enforce mutual exclusion: enabling one clears the other
    if (value) {
      if (key === 'force_sampling') patch['force_passthrough'] = false;
      if (key === 'force_passthrough') patch['force_sampling'] = false;
    }
    await this.update({ pipeline: patch });
  }

  async setDefaultStrategy(strategy: string): Promise<void> {
    await this.update({ defaults: { strategy } });
  }

  async setEffort(key: string, value: string): Promise<void> {
    await this.update({ pipeline: { [key]: value } });
  }

  /**
   * Optimistically toggle a domain's membership in
   * `domain_readiness_notifications.muted_domain_ids`, then persist via PATCH.
   *
   * Contract:
   *   - Optimistic: mutates local state *before* awaiting PATCH — UI updates
   *     immediately.
   *   - Rollback: on PATCH rejection, re-reads the CURRENT muted list and
   *     toggles `domainId` membership again. This inverse-toggle revert is
   *     scoped to the failed domain only, so concurrent toggles of other
   *     domains (which may have succeeded in the meantime) are preserved.
   *     Surfaces a `'deleted'` toast. Swallows the error (matches the
   *     rest-of-store pattern where `update()` also never re-throws).
   *   - No loading guard: toggling during an in-flight `init()` reload is
   *     harmless — the post-init `this.prefs = ...` assignment wins, which
   *     matches user intent ("server truth is most recent").
   */
  async toggleDomainMute(domainId: string): Promise<void> {
    const current = this.prefs.domain_readiness_notifications;
    const next = current.muted_domain_ids.includes(domainId)
      ? current.muted_domain_ids.filter((id) => id !== domainId)
      : [...current.muted_domain_ids, domainId];
    this.prefs.domain_readiness_notifications = {
      ...current,
      muted_domain_ids: next,
    };
    try {
      await patchPreferences({
        domain_readiness_notifications: { muted_domain_ids: next },
      });
    } catch {
      const live = this.prefs.domain_readiness_notifications;
      const reverted = live.muted_domain_ids.includes(domainId)
        ? live.muted_domain_ids.filter((id) => id !== domainId)
        : [...live.muted_domain_ids, domainId];
      this.prefs.domain_readiness_notifications = {
        ...live,
        muted_domain_ids: reverted,
      };
      toastStore.add('deleted', MUTE_TOGGLE_ERROR_MESSAGE);
    }
  }

  /** @internal Test-only: restore initial state */
  _reset() {
    this.prefs = structuredClone(DEFAULTS);
    this.loading = false;
    this.error = null;
  }
}

export const preferencesStore = new PreferencesStore();
