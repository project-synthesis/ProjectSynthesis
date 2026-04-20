/**
 * Tests for ADR-005 F1 — projectStore.
 *
 * Verifies current-project persistence, link-response ingestion, and
 * migration-candidate lifecycle.  Pure unit tests against a mocked
 * `listProjects()` — no real HTTP.
 */

import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock the api module BEFORE importing the store so the store picks up the
// mocked `listProjects`.  Vitest hoists vi.mock automatically.
vi.mock('$lib/api/client', () => ({
  listProjects: vi.fn(),
}));

import { projectStore } from './project.svelte';
import * as api from '$lib/api/client';

const STORAGE_KEY = 'synthesis:current_project_id';

describe('projectStore', () => {
  beforeEach(() => {
    projectStore._reset();
    localStorage.clear();
    vi.resetAllMocks();
  });

  describe('currentProjectId persistence', () => {
    it('defaults to null ("All projects")', () => {
      expect(projectStore.currentProjectId).toBeNull();
    });

    it('setCurrent updates state and persists to localStorage', () => {
      projectStore.setCurrent('proj-abc');
      expect(projectStore.currentProjectId).toBe('proj-abc');
      expect(localStorage.getItem(STORAGE_KEY)).toBe(JSON.stringify('proj-abc'));
    });

    it('setCurrent with null persists null (explicit All projects)', () => {
      projectStore.setCurrent('proj-abc');
      projectStore.setCurrent(null);
      expect(projectStore.currentProjectId).toBeNull();
      expect(localStorage.getItem(STORAGE_KEY)).toBe(JSON.stringify(null));
    });

    it('setCurrent is a no-op when unchanged', () => {
      projectStore.setCurrent('proj-abc');
      const before = localStorage.getItem(STORAGE_KEY);
      projectStore.setCurrent('proj-abc');
      expect(localStorage.getItem(STORAGE_KEY)).toBe(before);
    });
  });

  describe('refresh()', () => {
    it('populates projects on success', async () => {
      const mockProjects = [
        { id: 'legacy', label: 'Legacy', member_count: 3 },
        { id: 'proj-1', label: 'MyRepo', member_count: 7 },
      ];
      vi.mocked(api.listProjects).mockResolvedValue(mockProjects);

      await projectStore.refresh();
      expect(projectStore.projects).toEqual(mockProjects);
      expect(projectStore.error).toBeNull();
      expect(projectStore.loading).toBe(false);
    });

    it('captures error message on failure without clearing existing list', async () => {
      const seed = [{ id: 'legacy', label: 'Legacy', member_count: 1 }];
      vi.mocked(api.listProjects).mockResolvedValueOnce(seed);
      await projectStore.refresh();

      vi.mocked(api.listProjects).mockRejectedValueOnce(new Error('boom'));
      await projectStore.refresh();
      expect(projectStore.error).toBe('boom');
      expect(projectStore.projects).toEqual(seed);
      expect(projectStore.loading).toBe(false);
    });
  });

  describe('applyLinkResponse()', () => {
    it('switches scope and stashes candidates', () => {
      const candidates = { count: 5, from_project_id: 'legacy', since: '2026-04-12T00:00:00Z' };
      projectStore.applyLinkResponse('proj-new', candidates);

      expect(projectStore.currentProjectId).toBe('proj-new');
      expect(projectStore.lastMigrationCandidates).toEqual(candidates);
      expect(projectStore.eligibleForLegacyMigration()).toBe(5);
    });

    it('stashes null candidates without clobbering current when project_id is null', () => {
      projectStore.setCurrent('proj-keep');
      projectStore.applyLinkResponse(null, null);
      expect(projectStore.currentProjectId).toBe('proj-keep');
      expect(projectStore.lastMigrationCandidates).toBeNull();
      expect(projectStore.eligibleForLegacyMigration()).toBe(0);
    });

    it('clearMigrationCandidates() dismisses the pending toast state', () => {
      projectStore.applyLinkResponse('p', { count: 2, from_project_id: 'legacy', since: null });
      projectStore.clearMigrationCandidates();
      expect(projectStore.lastMigrationCandidates).toBeNull();
      expect(projectStore.eligibleForLegacyMigration()).toBe(0);
    });
  });

  describe('derived labels', () => {
    it('currentLabel falls back to "All projects" when scope is null', () => {
      expect(projectStore.currentLabel).toBe('All projects');
    });

    it('currentLabel reads from loaded projects when scoped', async () => {
      vi.mocked(api.listProjects).mockResolvedValue([
        { id: 'legacy', label: 'Legacy', member_count: 0 },
        { id: 'proj-1', label: 'MyRepo', member_count: 4 },
      ]);
      await projectStore.refresh();
      projectStore.setCurrent('proj-1');
      expect(projectStore.currentLabel).toBe('MyRepo');
    });

    it('isLegacyScope true only when current scope matches a project labeled "legacy"', async () => {
      vi.mocked(api.listProjects).mockResolvedValue([
        { id: 'legacy', label: 'Legacy', member_count: 0 },
        { id: 'proj-1', label: 'MyRepo', member_count: 4 },
      ]);
      await projectStore.refresh();

      projectStore.setCurrent('legacy');
      expect(projectStore.isLegacyScope).toBe(true);

      projectStore.setCurrent('proj-1');
      expect(projectStore.isLegacyScope).toBe(false);

      projectStore.setCurrent(null);
      expect(projectStore.isLegacyScope).toBe(false);
    });
  });
});
