/**
 * projectStore — ADR-005 multi-project state (F1).
 *
 * Authoritative answer to "which project is the user looking at right now?"
 * This is NOT enslaved to GitHub connection state: it survives repo unlink
 * and no-GitHub sessions so users can keep working "in project X" without an
 * active session.  The plan's F1 spec mandates this explicitly — Legacy is a
 * first-class project, not a fallback/null.
 *
 * `currentProjectId`:
 *   - `null`    → "All projects" (global view, backend omits project_id)
 *   - `<uuid>`  → scoped view; pipeline attributes new prompts to this project
 *
 * Link-time flow (F5):
 *   1. `githubStore.linkRepo()` calls `POST /github/repos/link`
 *   2. Response carries `project_id` + `migration_candidates`
 *   3. Caller invokes `applyLinkResponse()` here → sets current + stashes
 *      candidates for the migration toast
 *   4. F5 UI reads `lastMigrationCandidates` to offer the sweep
 *
 * Persistence: `currentProjectId` round-trips through localStorage under
 * `synthesis:current_project_id`.  Projects list is a live API cache, never
 * persisted (stale counts would be worse than a brief loading flash).
 */

import { listProjects, type ProjectInfo, type MigrationCandidates } from '$lib/api/client';

const STORAGE_KEY = 'synthesis:current_project_id';

function loadInitial(): string | null {
  if (typeof localStorage === 'undefined') return null;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    // Stored as JSON so we can distinguish null ("All projects") from
    // missing.  Older clients may have written a bare string — tolerate.
    try {
      const parsed: unknown = JSON.parse(raw);
      if (parsed === null) return null;
      if (typeof parsed === 'string') return parsed || null;
      return null;
    } catch {
      return raw || null;
    }
  } catch {
    return null;
  }
}

function persist(id: string | null): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(id));
  } catch {
    // quota exceeded / disabled — best-effort, silent
  }
}

class ProjectStore {
  /** Current scope. `null` = "All projects" (global, omits project_id). */
  currentProjectId = $state<string | null>(loadInitial());

  /** Live list of project nodes, sorted Legacy-first alphabetically. */
  projects = $state<ProjectInfo[]>([]);

  /**
   * ADR-005 B4 — candidates surfaced by the most recent `linkRepo()`
   * response.  Drives the F5 post-link migration toast.  Nulled once the
   * toast is dismissed or acted on.
   */
  lastMigrationCandidates = $state<MigrationCandidates | null>(null);

  /** True while `refresh()` is in flight — lets UI show a quiet spinner. */
  loading = $state<boolean>(false);

  /** Last error from `refresh()` — surfaced in Navigator if non-null. */
  error = $state<string | null>(null);

  /** Human-readable label for the current project, or "All projects". */
  get currentLabel(): string {
    if (this.currentProjectId === null) return 'All projects';
    const hit = this.projects.find((p) => p.id === this.currentProjectId);
    return hit?.label ?? 'unknown';
  }

  /**
   * True when the current scope is the Legacy project.  Used by the
   * "prompt-in-Legacy-while-repo-linked" flow so the UI can hint that this
   * is an explicit override rather than incidental state.
   */
  get isLegacyScope(): boolean {
    if (this.currentProjectId === null) return false;
    const hit = this.projects.find((p) => p.id === this.currentProjectId);
    return (hit?.label ?? '').trim().toLowerCase() === 'legacy';
  }

  /** Update the current project and persist. No-op if unchanged. */
  setCurrent(id: string | null): void {
    if (this.currentProjectId === id) return;
    this.currentProjectId = id;
    persist(id);
  }

  /**
   * Fetch the live project list from `GET /api/projects`.
   * Swallows network errors into `error` so the UI can keep rendering.
   */
  async refresh(): Promise<void> {
    this.loading = true;
    try {
      this.projects = await listProjects();
      this.error = null;
    } catch (err: unknown) {
      this.error = err instanceof Error ? err.message : 'Failed to load projects';
    } finally {
      this.loading = false;
    }
  }

  /**
   * Hook for `githubStore.linkRepo()` response (ADR-005 B4).
   *
   * Auto-switches to the newly-linked project's scope and stashes the
   * migration candidates for the F5 toast.  Idempotent w.r.t. repeat calls
   * with the same payload.
   */
  applyLinkResponse(projectId: string | null, candidates: MigrationCandidates | null): void {
    if (projectId) this.setCurrent(projectId);
    this.lastMigrationCandidates = candidates;
  }

  /**
   * Count of opts eligible for migration from the last `linkRepo()` call.
   * Used by F5 to decide whether to render the migration toast at all.
   */
  eligibleForLegacyMigration(): number {
    return this.lastMigrationCandidates?.count ?? 0;
  }

  /** Dismiss the pending migration candidates (toast closed/acted on). */
  clearMigrationCandidates(): void {
    this.lastMigrationCandidates = null;
  }

  /** Test-only — reset to a clean slate. */
  _reset(): void {
    this.currentProjectId = null;
    this.projects = [];
    this.lastMigrationCandidates = null;
    this.loading = false;
    this.error = null;
    if (typeof localStorage !== 'undefined') {
      try {
        localStorage.removeItem(STORAGE_KEY);
      } catch {
        // ignore
      }
    }
  }
}

export const projectStore = new ProjectStore();
