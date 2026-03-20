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

  describe('isLeanMode', () => {
    it('returns true when explore and scoring are disabled', () => {
      preferencesStore.prefs.pipeline.enable_explore = false;
      preferencesStore.prefs.pipeline.enable_scoring = false;
      expect(preferencesStore.isLeanMode).toBe(true);
    });
  });
});
