# GitHub Connection State & Visibility Fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 2 critical auth bugs, add unified connection state model, and surface project/repo visibility across all panels.

**Architecture:** Add a `connectionState` getter to `GitHubStore` (5 states: disconnected/expired/authenticated/linked/ready). All components read this instead of ad-hoc null checks. Add `reconnect()` method that clears stale state before starting device flow. Surface `repo_full_name` and project info from existing API data in Inspector, ForgeArtifact, StatusBar, and history rows.

**Tech Stack:** SvelteKit 2 (Svelte 5 runes), TypeScript, Vitest

**Spec:** `docs/superpowers/specs/2026-04-10-github-connection-state-design.md`

---

### Task 1: Store — connectionState getter + reconnect() + bug fixes

**Files:**
- Modify: `frontend/src/lib/stores/github.svelte.ts`
- Test: `frontend/src/lib/stores/github.svelte.test.ts`

- [ ] **Step 1: Write failing tests for connectionState**

Add to `github.svelte.test.ts` after line 206 (before the final `});`):

```typescript
  describe('connectionState (spec: unified state model)', () => {
    it('returns disconnected when no user, no linkedRepo, no authExpired', () => {
      expect(githubStore.connectionState).toBe('disconnected');
    });

    it('returns expired when authExpired is true', () => {
      githubStore.authExpired = true;
      expect(githubStore.connectionState).toBe('expired');
    });

    it('returns expired when authExpired is true even with linkedRepo', () => {
      githubStore.authExpired = true;
      githubStore.linkedRepo = { id: '1', full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      expect(githubStore.connectionState).toBe('expired');
    });

    it('returns authenticated when user set but no linkedRepo', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      expect(githubStore.connectionState).toBe('authenticated');
    });

    it('returns linked when user + linkedRepo but indexStatus is null', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      githubStore.linkedRepo = { id: '1', full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      expect(githubStore.connectionState).toBe('linked');
    });

    it('returns linked when user + linkedRepo + indexStatus building', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      githubStore.linkedRepo = { id: '1', full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      githubStore.indexStatus = { status: 'building', file_count: 0, indexed_at: null } as any;
      expect(githubStore.connectionState).toBe('linked');
    });

    it('returns ready when user + linkedRepo + indexStatus ready', () => {
      githubStore.user = { login: 'test', avatar_url: '', github_user_id: '1' } as any;
      githubStore.linkedRepo = { id: '1', full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      githubStore.indexStatus = { status: 'ready', file_count: 42, indexed_at: '2026-01-01' } as any;
      expect(githubStore.connectionState).toBe('ready');
    });
  });

  describe('reconnect (spec: clears state before device flow)', () => {
    it('clears authExpired, linkedRepo, error, and browsing state then calls login', async () => {
      const loginSpy = vi.spyOn(githubStore, 'login').mockResolvedValue(undefined);
      githubStore.authExpired = true;
      githubStore.linkedRepo = { id: '1', full_name: 'o/r', default_branch: 'main', branch: null, language: null } as any;
      githubStore.fileTree = [{ name: 'f', path: 'f', type: 'file' }] as any;
      githubStore.branches = ['main'];
      githubStore.indexStatus = { status: 'ready', file_count: 10, indexed_at: '2026-01-01' } as any;
      githubStore.error = 'stale error';
      await githubStore.reconnect();
      expect(githubStore.authExpired).toBe(false);
      expect(githubStore.linkedRepo).toBeNull();
      expect(githubStore.fileTree).toHaveLength(0);
      expect(githubStore.branches).toHaveLength(0);
      expect(githubStore.indexStatus).toBeNull();
      expect(githubStore.error).toBeNull();
      expect(loginSpy).toHaveBeenCalled();
      loginSpy.mockRestore();
    });
  });

  describe('checkAuth bug fix (spec: authExpired reset on null)', () => {
    it('resets authExpired on null return from githubMe', async () => {
      githubStore.authExpired = true;
      mockFetch([
        { match: '/github/auth/me', response: null, status: 200 },
      ]);
      await githubStore.checkAuth();
      expect(githubStore.authExpired).toBe(false);
      expect(githubStore.linkedRepo).toBeNull();
    });
  });

  describe('logout bug fix (spec: authExpired reset)', () => {
    it('resets authExpired on logout', async () => {
      githubStore.authExpired = true;
      mockFetch([
        { match: '/github/auth/logout', response: { ok: true } },
      ]);
      await githubStore.logout();
      expect(githubStore.authExpired).toBe(false);
    });
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm run test -- --run src/lib/stores/github.svelte.test.ts`
Expected: FAIL — `connectionState` and `reconnect` not defined

- [ ] **Step 3: Implement connectionState getter**

In `github.svelte.ts`, after line 41 (`indexStatus = $state<IndexStatus | null>(null);`), add:

```typescript
  /** Unified connection state — single source of truth for all UI components. */
  get connectionState(): 'disconnected' | 'expired' | 'authenticated' | 'linked' | 'ready' {
    if (this.authExpired) return 'expired';
    if (!this.user) return 'disconnected';
    if (!this.linkedRepo) return 'authenticated';
    if (!this.indexStatus || this.indexStatus.status === 'building') return 'linked';
    return 'ready';
  }
```

- [ ] **Step 4: Implement reconnect() method**

In `github.svelte.ts`, after `logout()` (after line 163), add:

```typescript
  /** Clear stale auth state and start device flow for re-authentication.
   *  Clears linkedRepo so the Navigator template falls to the device flow branch. */
  async reconnect() {
    this.authExpired = false;
    this.linkedRepo = null;
    this.fileTree = [];
    this.branches = [];
    this.indexStatus = null;
    this.error = null;
    await this.login();
  }
```

- [ ] **Step 5: Fix checkAuth null path**

In `github.svelte.ts`, replace line 77-79:
```typescript
      } else {
        this.user = null;
      }
```
with:
```typescript
      } else {
        this.user = null;
        this.linkedRepo = null;
        this.authExpired = false;
      }
```

- [ ] **Step 6: Fix checkAuth catch path**

In `github.svelte.ts`, replace lines 80-84:
```typescript
    } catch (err) {
      this.user = null;
      // Detect token revoked/expired (backend validates with GitHub on /auth/me)
      this._handleAuthError(err);
    }
```
with:
```typescript
    } catch {
      // Network error — githubMe uses tryFetch so 401s return null, not throw.
      // Only DNS/CORS failures reach here. Clear user, leave authExpired unchanged.
      this.user = null;
    }
```

- [ ] **Step 7: Fix logout authExpired reset**

In `github.svelte.ts`, in `logout()` after line 157 (`this.user = null;`), add:
```typescript
      this.authExpired = false;
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `cd frontend && npm run test -- --run src/lib/stores/github.svelte.test.ts`
Expected: ALL PASS

- [ ] **Step 9: Type check**

Run: `cd frontend && npm run check`
Expected: 0 ERRORS

- [ ] **Step 10: Commit**

```bash
git add frontend/src/lib/stores/github.svelte.ts frontend/src/lib/stores/github.svelte.test.ts
git commit -m "feat: unified GitHubConnectionState model + reconnect + auth bug fixes"
```

---

### Task 2: Types — add repo_full_name to OptimizationResult + linked_at to LinkedRepo

**Files:**
- Modify: `frontend/src/lib/api/client.ts`

- [ ] **Step 1: Add repo_full_name to OptimizationResult**

In `client.ts`, after line 65 (`heuristic_flags: string[];`), add:
```typescript
  repo_full_name?: string | null;
```

- [ ] **Step 2: Add linked_at to LinkedRepo**

In `client.ts`, after line 148 (`project_label?: string | null;`), add:
```typescript
  linked_at?: string | null;
```

- [ ] **Step 3: Type check**

Run: `cd frontend && npm run check`
Expected: 0 ERRORS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api/client.ts
git commit -m "feat: add repo_full_name to OptimizationResult, linked_at to LinkedRepo"
```

---

### Task 3: Navigator — auth-expired banner + connection badge + project_label null state

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte`
- Test: `frontend/src/lib/components/layout/Navigator.test.ts`

- [ ] **Step 1: Add auth-expired banner inside linkedRepo branch**

In `Navigator.svelte`, after line 593 (`{#if githubTab === 'info'}`), add:
```svelte
            <!-- Auth-expired banner with reconnect (inside linkedRepo branch) -->
            {#if githubStore.connectionState === 'expired'}
              <div class="auth-expired-banner">
                <span class="error-note" style="margin: 0;">GitHub session expired</span>
                <button
                  class="action-btn action-btn--primary"
                  onclick={() => githubStore.reconnect()}
                >Reconnect</button>
              </div>
            {/if}
```

- [ ] **Step 2: Add connection status badge to GitHub panel header**

In `Navigator.svelte`, replace lines 568-570:
```svelte
      <header class="panel-header">
        <span class="section-heading">GitHub</span>
      </header>
```
with:
```svelte
      <header class="panel-header">
        <span class="section-heading">GitHub</span>
        {#if githubStore.connectionState === 'ready'}
          <span class="connection-badge" style="color: var(--color-text-dim)">connected</span>
        {:else if githubStore.connectionState === 'linked'}
          <span class="connection-badge" style="color: var(--color-neon-cyan)">indexing</span>
        {:else if githubStore.connectionState === 'expired'}
          <span class="connection-badge" style="color: var(--color-neon-red)">expired</span>
        {:else if githubStore.connectionState === 'authenticated'}
          <span class="connection-badge" style="color: var(--color-neon-yellow)">no repo</span>
        {/if}
      </header>
```

- [ ] **Step 3: Fix project_label null state**

In `Navigator.svelte`, replace lines 611-616:
```svelte
              {#if githubStore.linkedRepo.project_label}
                <div class="data-row">
                  <span class="data-label">Project</span>
                  <span class="data-value font-mono">{githubStore.linkedRepo.project_label}</span>
                </div>
              {/if}
```
with:
```svelte
              <div class="data-row">
                <span class="data-label">Project</span>
                <span class="data-value font-mono">{githubStore.linkedRepo.project_label ?? '(pending)'}</span>
              </div>
```

- [ ] **Step 4: Remove dead-code reconnect button**

In `Navigator.svelte`, remove lines 671-678 (the `{#if githubStore.authExpired}` block inside `{:else if githubStore.user}`). This code is permanently unreachable because `_handleAuthError()` sets `user = null` alongside `authExpired = true`, making the `{:else if githubStore.user}` branch always false when `authExpired` is true. The working reconnect is now in the `{#if githubStore.linkedRepo}` branch (added in Step 1).

Replace lines 671-678:
```svelte
          {#if githubStore.authExpired}
            <p class="error-note">GitHub session expired.</p>
            <button
              class="action-btn action-btn--primary"
              onclick={() => { githubStore.authExpired = false; githubStore.logout(); githubStore.login(); }}
            >
              Reconnect GitHub
            </button>
          {:else if !repoPickerOpen}
```
with:
```svelte
          {#if !repoPickerOpen}
```

- [ ] **Step 5: Add CSS for auth-expired-banner and connection-badge**

In the `<style>` section of `Navigator.svelte`, add:
```css
  .auth-expired-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 6px 8px;
    margin-bottom: 6px;
    border: 1px solid var(--color-neon-red);
    background: transparent;
  }
  .connection-badge {
    font-family: var(--font-mono);
    font-size: 10px;
    margin-left: auto;
  }
```

- [ ] **Step 6: Type check**

Run: `cd frontend && npm run check`
Expected: 0 ERRORS

- [ ] **Step 7: Run full test suite**

Run: `cd frontend && npm run test`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte
git commit -m "fix: auth-expired reconnect banner + connection badge + project_label null state"
```

---

### Task 4: Navigator — history rows with project labels

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte`

- [ ] **Step 1: Add project label cache on mount**

In `Navigator.svelte`, find the existing `projects` state at line 160:
```typescript
let projects = $state<ProjectInfo[]>([]);
```

After it, add a derived project label map and a mount-time loader:
```typescript
  const projectLabelMap = $derived<Record<string, string>>(
    Object.fromEntries(projects.map(p => [p.id, p.label]))
  );
```

Then add a guarded `$effect` to load projects on mount (after the existing effects around line 230):
```typescript
  // Load project labels for history row badges (one-time, matches settingsLoaded pattern)
  let projectsLoaded = false;
  $effect(() => {
    if (projectsLoaded) return;
    projectsLoaded = true;
    listProjects().then(p => { projects = p; }).catch(() => {});
  });
```

Note: `listProjects` is already imported (line 17) and `projects` is already declared (line 160).

- [ ] **Step 2: Add project badge to history rows**

In `Navigator.svelte`, in the history row at line 535 (inside `<div class="history-meta">`), add before the strategy badge:
```svelte
                  {#if item.project_id && projectLabelMap[item.project_id]}
                    <span class="row-project font-mono" use:tooltip={`Project: ${projectLabelMap[item.project_id]}`}>
                      {projectLabelMap[item.project_id].slice(0, 2).toUpperCase()}
                    </span>
                  {/if}
```

- [ ] **Step 3: Add CSS for row-project badge**

```css
  .row-project {
    font-size: 9px;
    color: var(--color-text-dim);
    border: 1px solid var(--color-border-subtle);
    padding: 0 3px;
    white-space: nowrap;
  }
```

- [ ] **Step 4: Type check + test**

Run: `cd frontend && npm run check && npm run test`
Expected: 0 ERRORS, ALL PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte
git commit -m "feat: project labels on history rows"
```

---

### Task 5: StatusBar — connection-state-aware GitHub indicator

**Files:**
- Modify: `frontend/src/lib/components/layout/StatusBar.svelte`

- [ ] **Step 1: Replace project badge with connection-aware indicator**

In `StatusBar.svelte`, replace lines 109-111:
```svelte
    {#if githubStore.linkedRepo}
      <span class="status-project" use:tooltip={`Project: ${githubStore.linkedRepo.full_name}`}>{githubStore.linkedRepo.full_name.split('/')[1]}</span>
    {/if}
```
with:
```svelte
    {#if githubStore.connectionState === 'ready'}
      <span class="status-github" style="color: var(--color-text-dim)"
        use:tooltip={`GitHub: ${githubStore.linkedRepo?.full_name}`}
      >{githubStore.linkedRepo?.full_name.split('/')[1]}</span>
    {:else if githubStore.connectionState === 'linked'}
      <span class="status-github" style="color: var(--color-neon-cyan)">indexing...</span>
    {:else if githubStore.connectionState === 'expired'}
      <span class="status-github" style="color: var(--color-neon-red)">session expired</span>
    {:else if githubStore.connectionState === 'authenticated'}
      <span class="status-github" style="color: var(--color-neon-yellow)">no repo</span>
    {/if}
```

- [ ] **Step 2: Update CSS — rename status-project to status-github**

In the `<style>` section, rename `.status-project` to `.status-github` (keep same styles).

- [ ] **Step 3: Type check + test**

Run: `cd frontend && npm run check && npm run test`
Expected: 0 ERRORS, ALL PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/layout/StatusBar.svelte
git commit -m "feat: connection-state-aware GitHub indicator in StatusBar"
```

---

### Task 6: Inspector — project breadcrumb + repo context row

**Files:**
- Modify: `frontend/src/lib/components/layout/Inspector.svelte`

- [ ] **Step 1: Change project_ids display to show label for single-project clusters**

In `Inspector.svelte`, add a project label cache. In the `<script>` section (near the imports at the top), add:
```typescript
  import { listProjects } from '$lib/api/client';
  let projectLabels = $state<Record<string, string>>({});
  let projectsLoaded = false;
  $effect(() => {
    if (projectsLoaded) return;
    projectsLoaded = true;
    listProjects().then(ps => {
      projectLabels = Object.fromEntries(ps.map(p => [p.id, p.label]));
    }).catch(() => {});
  });
```

Then replace lines 215-220:
```svelte
            {#if family.project_ids && family.project_ids.length > 1}
              <div class="meta-row">
                <span class="meta-label">Projects</span>
                <span class="meta-value meta-value--cyan">{family.project_ids.length}</span>
              </div>
            {/if}
```
with:
```svelte
            {#if family.project_ids && family.project_ids.length > 0}
              <div class="meta-row">
                <span class="meta-label">{family.project_ids.length === 1 ? 'Project' : 'Projects'}</span>
                <span class="meta-value meta-value--cyan">
                  {#if family.project_ids.length === 1}
                    {projectLabels[family.project_ids[0]] ?? family.project_ids.length}
                  {:else}
                    {family.project_ids.length}
                  {/if}
                </span>
              </div>
            {/if}
```

- [ ] **Step 2: Add repo context row in optimization detail**

In `Inspector.svelte`, after the Provider meta-row (after line 434), add:
```svelte
          {#if activeResult?.repo_full_name}
            <div class="meta-row">
              <span class="meta-label">Repo</span>
              <span class="meta-value font-mono">{activeResult.repo_full_name}</span>
            </div>
          {/if}
```

- [ ] **Step 3: Type check + test**

Run: `cd frontend && npm run check && npm run test`
Expected: 0 ERRORS, ALL PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/layout/Inspector.svelte
git commit -m "feat: project breadcrumb for single-project clusters + repo context in optimization detail"
```

---

### Task 7: ForgeArtifact — repo context line

**Files:**
- Modify: `frontend/src/lib/components/editor/ForgeArtifact.svelte`

- [ ] **Step 1: Add repo context below header**

In `ForgeArtifact.svelte`, after line 127 (the closing `</div>` of `artifact-header`), before line 129 (`<!-- Prompt display -->`), add:
```svelte
  {#if result?.repo_full_name}
    <div class="artifact-repo-context font-mono">{result.repo_full_name}</div>
  {/if}
```

- [ ] **Step 2: Add CSS**

```css
  .artifact-repo-context {
    font-size: 10px;
    color: var(--color-text-dim);
    padding: 0 8px 4px;
  }
```

- [ ] **Step 3: Type check + test**

Run: `cd frontend && npm run check && npm run test`
Expected: 0 ERRORS, ALL PASS

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/editor/ForgeArtifact.svelte
git commit -m "feat: repo context line in ForgeArtifact"
```

---

### Task 8: Final validation + push

- [ ] **Step 1: Full type check**

Run: `cd frontend && npm run check`
Expected: 0 ERRORS, 0 WARNINGS

- [ ] **Step 2: Full test suite**

Run: `cd frontend && npm run test`
Expected: ALL PASS

- [ ] **Step 3: Backend tests (verify no regression)**

Run: `cd backend && source .venv/bin/activate && python3 -m pytest tests/ -k "github" -q`
Expected: ALL PASS

- [ ] **Step 4: Push**

```bash
git push origin main
```
