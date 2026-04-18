import { describe, it, expect, beforeEach, vi } from 'vitest';
import { preferencesStore } from './preferences.svelte';
import { toastStore } from './toast.svelte';
import { mockFetch } from '../test-utils';

describe('PreferencesStore', () => {
  beforeEach(() => {
    preferencesStore._reset();
  });

  it('starts with default values', () => {
    expect(preferencesStore.defaultStrategy).toBe('auto');
    expect(preferencesStore.loading).toBe(false);
    expect(preferencesStore.error).toBeNull();
  });

  describe('init', () => {
    it('loads preferences from API', async () => {
      mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: false },
          defaults: { strategy: 'chain-of-thought' },
        },
      }]);
      await preferencesStore.init();
      expect(preferencesStore.defaultStrategy).toBe('chain-of-thought');
      expect(preferencesStore.models.optimizer).toBe('opus');
    });
  });

  describe('setModel', () => {
    it('patches a model preference', async () => {
      const fetchMock = mockFetch([
        {
          match: '/api/preferences',
          response: {
            schema_version: 1,
            models: { analyzer: 'sonnet', optimizer: 'haiku', scorer: 'sonnet' },
            pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: false },
            defaults: { strategy: 'auto' },
          },
        },
      ]);
      await preferencesStore.setModel('optimizer', 'haiku');
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/preferences'),
        expect.objectContaining({ method: 'PATCH' }),
      );
    });
  });

  describe('setPipelineToggle', () => {
    it('patches a pipeline toggle', async () => {
      mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: false },
          defaults: { strategy: 'auto' },
        },
      }]);
      await preferencesStore.setPipelineToggle('enable_explore', true);
      expect(preferencesStore.pipeline.enable_explore).toBe(true);
    });
  });

  describe('setDefaultStrategy', () => {
    it('calls update with correct payload', async () => {
      mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: false },
          defaults: { strategy: 'few-shot' },
        },
      }]);
      await preferencesStore.setDefaultStrategy('few-shot');
      expect(preferencesStore.defaultStrategy).toBe('few-shot');
    });
  });

  describe('setPipelineToggle mutual exclusion', () => {
    it('setting force_sampling=true also sets force_passthrough=false', async () => {
      const fetchMock = mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: true, force_passthrough: false },
          defaults: { strategy: 'auto' },
        },
      }]);
      await preferencesStore.setPipelineToggle('force_sampling', true);
      const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
      expect(body.pipeline.force_sampling).toBe(true);
      expect(body.pipeline.force_passthrough).toBe(false);
    });

    it('setting force_passthrough=true also sets force_sampling=false', async () => {
      const fetchMock = mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: true },
          defaults: { strategy: 'auto' },
        },
      }]);
      await preferencesStore.setPipelineToggle('force_passthrough', true);
      const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
      expect(body.pipeline.force_passthrough).toBe(true);
      expect(body.pipeline.force_sampling).toBe(false);
    });

    it('setting force_sampling=false does not touch force_passthrough', async () => {
      const fetchMock = mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: { enable_explore: true, enable_scoring: true, enable_adaptation: true, force_sampling: false, force_passthrough: false },
          defaults: { strategy: 'auto' },
        },
      }]);
      await preferencesStore.setPipelineToggle('force_sampling', false);
      const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
      expect(body.pipeline.force_sampling).toBe(false);
      expect(body.pipeline).not.toHaveProperty('force_passthrough');
    });
  });

  describe('isLeanMode', () => {
    it('returns true when both explore and scoring are disabled', () => {
      preferencesStore.prefs.pipeline.enable_explore = false;
      preferencesStore.prefs.pipeline.enable_scoring = false;
      expect(preferencesStore.isLeanMode).toBe(true);
    });

    it('returns false when explore is enabled', () => {
      preferencesStore.prefs.pipeline.enable_explore = true;
      preferencesStore.prefs.pipeline.enable_scoring = false;
      expect(preferencesStore.isLeanMode).toBe(false);
    });

    it('returns false when scoring is enabled', () => {
      preferencesStore.prefs.pipeline.enable_explore = false;
      preferencesStore.prefs.pipeline.enable_scoring = true;
      expect(preferencesStore.isLeanMode).toBe(false);
    });

    it('returns false when both are enabled', () => {
      preferencesStore.prefs.pipeline.enable_explore = true;
      preferencesStore.prefs.pipeline.enable_scoring = true;
      expect(preferencesStore.isLeanMode).toBe(false);
    });
  });

  describe('domain readiness notifications', () => {
    it('DEFAULTS.domain_readiness_notifications is { enabled: true, muted_domain_ids: [] }', () => {
      // Default ON — PR #27 follow-up: notifications were user-unreachable
      // when default was false because no global UI toggle shipped. The
      // master bell in DomainReadinessPanel + per-row mutes cover opt-out.
      preferencesStore._reset();
      expect(preferencesStore.prefs.domain_readiness_notifications).toEqual({
        enabled: true,
        muted_domain_ids: [],
      });
    });

    it('preferencesStore.prefs.domain_readiness_notifications returns that shape after _reset()', () => {
      // Mutate then reset to prove DEFAULTS are actually restored.
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: false,
        muted_domain_ids: ['backend'],
      };
      preferencesStore._reset();
      expect(preferencesStore.prefs.domain_readiness_notifications.enabled).toBe(true);
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids).toEqual([]);
    });
  });

  describe('toggleDomainMute', () => {
    beforeEach(() => {
      toastStore._reset();
      // Simulate a "loaded" store so toggle is not a no-op.
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };
    });

    function respondWith(muted: string[]) {
      return mockFetch([{
        match: '/api/preferences',
        response: {
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: {
            enable_explore: true, enable_scoring: true, enable_adaptation: true,
            force_sampling: false, force_passthrough: false,
            optimizer_effort: 'high', analyzer_effort: 'low', scorer_effort: 'low',
          },
          defaults: { strategy: 'auto' },
          domain_readiness_notifications: { enabled: true, muted_domain_ids: muted },
        },
      }]);
    }

    it('adds domain_id to muted_domain_ids when absent (optimistic)', async () => {
      respondWith(['dom-1']);
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };
      const p = preferencesStore.toggleDomainMute('dom-1');
      // Optimistic: local state must reflect the new id BEFORE the PATCH resolves.
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['dom-1']);
      await p;
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['dom-1']);
    });

    it('removes domain_id from muted_domain_ids when present (optimistic)', async () => {
      respondWith([]);
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: ['dom-1', 'dom-2'],
      };
      const p = preferencesStore.toggleDomainMute('dom-1');
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['dom-2']);
      await p;
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['dom-2']);
    });

    it('allows toggle during an in-flight init() reload (no false no-op)', async () => {
      // The previous `this.loading` guard was semantically wrong: it only
      // covered the initial `init()` window yet silently blocked legitimate
      // toggles during SSE-triggered preferences reloads. Toggling while
      // `loading === true` must now PATCH and optimistically update.
      const fetchMock = respondWith(['dom-1']);
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };
      preferencesStore.loading = true;
      await preferencesStore.toggleDomainMute('dom-1');
      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['dom-1']);
      preferencesStore.loading = false;
    });

    it('persists via PATCH with a domain_readiness_notifications payload', async () => {
      const fetchMock = respondWith(['dom-1']);
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };
      await preferencesStore.toggleDomainMute('dom-1');
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/preferences'),
        expect.objectContaining({ method: 'PATCH' }),
      );
      const body = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string);
      expect(body).toEqual({
        domain_readiness_notifications: { muted_domain_ids: ['dom-1'] },
      });
    });

    it('rolls back local state and shows a toast on PATCH failure', async () => {
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('boom')));
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };
      await preferencesStore.toggleDomainMute('dom-1');
      // Rolled back.
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual([]);
      // Toast surfaced.
      expect(toastStore.toasts.length).toBeGreaterThan(0);
    });

    it('rollback of one failed toggle does not clobber a concurrent toggle of another domain', async () => {
      // Regression guard: snapshot-based rollback silently dropped concurrent
      // toggles on other domains. The inverse-toggle rollback must scope
      // the revert to the failed domain only.
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: [],
      };

      // Build a fetch mock whose first call (domain_a) hangs on a controllable
      // promise, and whose second call (domain_b) succeeds immediately.
      let rejectA: ((err: Error) => void) | null = null;
      const pendingA = new Promise<Response>((_resolve, reject) => {
        rejectA = reject;
      });
      const successResponse = {
        ok: true,
        status: 200,
        json: async () => ({
          schema_version: 1,
          models: { analyzer: 'sonnet', optimizer: 'opus', scorer: 'sonnet' },
          pipeline: {
            enable_explore: true, enable_scoring: true, enable_adaptation: true,
            force_sampling: false, force_passthrough: false,
            optimizer_effort: 'high', analyzer_effort: 'low', scorer_effort: 'low',
          },
          defaults: { strategy: 'auto' },
          domain_readiness_notifications: {
            enabled: true,
            muted_domain_ids: ['domain_a', 'domain_b'],
          },
        }),
        text: async () => '',
      } as unknown as Response;

      const fetchMock = vi.fn()
        .mockImplementationOnce(() => pendingA)
        .mockImplementationOnce(async () => successResponse);
      vi.stubGlobal('fetch', fetchMock);

      // 1. Toggle domain_a — PATCH pending.
      const pA = preferencesStore.toggleDomainMute('domain_a');
      // Optimistic: domain_a added immediately.
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['domain_a']);

      // 2. Toggle domain_b — PATCH succeeds.
      await preferencesStore.toggleDomainMute('domain_b');

      // 3. Both present before domain_a's rejection.
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['domain_a', 'domain_b']);

      // 4. Reject domain_a's PATCH.
      rejectA!(new Error('boom'));
      await pA;

      // 5. domain_a removed; domain_b preserved.
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids)
        .toEqual(['domain_b']);

      // 6. Exactly one error toast fired.
      expect(toastStore.toasts.length).toBe(1);
    });
  });

  describe('update error handling', () => {
    it('sets error state on failed PATCH', async () => {
      mockFetch([{
        match: '/api/preferences',
        response: { detail: 'Server error' },
        status: 500,
      }]);
      await preferencesStore.update({ defaults: { strategy: 'bad' } });
      expect(preferencesStore.error).toBeTruthy();
    });

    it('captures Error.message in error state', async () => {
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network failure')));
      await preferencesStore.update({ defaults: { strategy: 'bad' } });
      expect(preferencesStore.error).toBe('Network failure');
    });

    it('uses fallback message for non-Error throws', async () => {
      vi.stubGlobal('fetch', vi.fn().mockRejectedValue('string error'));
      await preferencesStore.update({ defaults: { strategy: 'bad' } });
      expect(preferencesStore.error).toBe('Failed to save');
    });
  });
});
