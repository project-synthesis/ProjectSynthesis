import { describe, it, expect, beforeEach, vi } from 'vitest';
import { preferencesStore } from './preferences.svelte';
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
    it('DEFAULTS.domain_readiness_notifications is { enabled: false, muted_domain_ids: [] }', () => {
      // After _reset() the store mirrors DEFAULTS; shape must match the
      // new backend preferences section added in Cycle 3.
      preferencesStore._reset();
      expect(preferencesStore.prefs.domain_readiness_notifications).toEqual({
        enabled: false,
        muted_domain_ids: [],
      });
    });

    it('preferencesStore.prefs.domain_readiness_notifications returns that shape after _reset()', () => {
      // Mutate then reset to prove DEFAULTS are actually restored.
      preferencesStore.prefs.domain_readiness_notifications = {
        enabled: true,
        muted_domain_ids: ['backend'],
      };
      preferencesStore._reset();
      expect(preferencesStore.prefs.domain_readiness_notifications.enabled).toBe(false);
      expect(preferencesStore.prefs.domain_readiness_notifications.muted_domain_ids).toEqual([]);
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
