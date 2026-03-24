# Passthrough UI Revision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revise the passthrough-mode UI so the Navigator pipeline section morphs in-place (MODELS→CONTEXT, EFFORT→SCORING), the PassthroughGuide modal feature matrix reflects v0.3.1 capabilities, and the deprecated `preparePassthrough()` API function is removed.

**Architecture:** Three files change. Navigator.svelte replaces `{#if !routing.isPassthrough}` hide blocks with `{#if}/{:else}` morph blocks that render CONTEXT (4 read-only indicators + 1 live toggle) and SCORING (1 read-only indicator) in passthrough mode. PassthroughGuide.svelte updates 3 stale `COMPARISON` rows and the step 1 description. client.ts removes the deprecated `preparePassthrough` function and its type.

**Tech Stack:** SvelteKit 2 (Svelte 5 runes), TypeScript, Vitest + @testing-library/svelte

**Spec:** `docs/superpowers/specs/2026-03-24-passthrough-ui-revision-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `frontend/src/lib/components/layout/Navigator.svelte` | Settings sidebar — pipeline section | Replace 3 hide blocks with morph blocks (CONTEXT + SCORING) |
| `frontend/src/lib/components/layout/Navigator.test.ts` | Navigator tests | Add tests for CONTEXT/SCORING sections, update existing passthrough tests |
| `frontend/src/lib/components/shared/PassthroughGuide.svelte` | Passthrough protocol modal | Fix 3 feature matrix rows, update step 1 description |
| `frontend/src/lib/components/shared/PassthroughGuide.test.ts` | PassthroughGuide tests | Update feature matrix assertion for new values |
| `frontend/src/lib/api/client.ts` | API client | Remove `preparePassthrough()` and `PassthroughPrepareResult` |
| `frontend/src/lib/api/client.test.ts` | API client tests | Remove `preparePassthrough` test and import |

---

### Task 1: PassthroughGuide Modal — Fix Feature Matrix and Step 1

**Files:**
- Modify: `frontend/src/lib/components/shared/PassthroughGuide.svelte:25-31,81-111`
- Modify: `frontend/src/lib/components/shared/PassthroughGuide.test.ts:107-113`

- [ ] **Step 1: Write failing test for updated feature matrix values**

In `frontend/src/lib/components/shared/PassthroughGuide.test.ts`, add a new test after the existing `renders feature matrix table` test (line 114):

```typescript
  it('shows updated passthrough capabilities in feature matrix', () => {
    passthroughGuide.show(false);
    render(PassthroughGuide);
    // Score phase: Heuristic / Hybrid (was just "Heuristic")
    expect(screen.getByText('Heuristic / Hybrid')).toBeInTheDocument();
    // Codebase explore: Roots + index (was "Roots only")
    expect(screen.getByText('Roots + index')).toBeInTheDocument();
    // Pattern injection: no longer dimmed cross — should NOT have dimmed marker
    const cells = screen.getAllByRole('cell');
    const patternRow = cells.find(c => c.textContent?.includes('Pattern injection'));
    // Pattern injection passthrough column should not contain cross mark
    expect(screen.queryByText('Roots only')).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/components/shared/PassthroughGuide.test.ts --reporter=verbose 2>&1 | tail -20`
Expected: FAIL — "Heuristic / Hybrid" not found (current value is "Heuristic")

- [ ] **Step 3: Update COMPARISON array and STEPS in PassthroughGuide.svelte**

In `frontend/src/lib/components/shared/PassthroughGuide.svelte`, update the `STEPS` array step 1 description (line 30):

```typescript
      description:
        'Strategy template, scoring rubric, workspace context, codebase context, applied patterns, and adaptation state are assembled into a single optimized instruction.',
```

Update three rows in the `COMPARISON` array:

Line 84 — Score phase:
```typescript
    { feature: 'Score phase', internal: 'LLM', sampling: 'LLM', passthrough: 'Heuristic / Hybrid' },
```

Line 85 — Codebase explore:
```typescript
    { feature: 'Codebase explore', internal: '\u2713', sampling: '\u2713', passthrough: 'Roots + index' },
```

Lines 86-92 — Pattern injection (remove `passthroughDim`, change cross to checkmark):
```typescript
    { feature: 'Pattern injection', internal: '\u2713', sampling: '\u2713', passthrough: '\u2713' },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/components/shared/PassthroughGuide.test.ts --reporter=verbose 2>&1 | tail -20`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/shared/PassthroughGuide.svelte frontend/src/lib/components/shared/PassthroughGuide.test.ts
git commit -m "fix(frontend): update PassthroughGuide feature matrix for v0.3.1 capabilities"
```

---

### Task 2: Navigator — Morph MODELS → CONTEXT Section

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte:446-470` (MODELS block only — feature toggles block at 477-502 stays as-is)
- Modify: `frontend/src/lib/components/layout/Navigator.test.ts`

- [ ] **Step 1: Write failing tests for CONTEXT section in passthrough mode**

In `frontend/src/lib/components/layout/Navigator.test.ts`, add after the existing passthrough tests (around line 756):

```typescript
  // ── Settings — passthrough CONTEXT section ─────────────────────────────────

  it('shows CONTEXT section with read-only indicators in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('Context')).toBeInTheDocument();
    expect(screen.getByText('heuristic')).toBeInTheDocument();
    expect(screen.getByText('auto-injected')).toBeInTheDocument();
  });

  it('shows "via index" when GitHub repo is linked in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    (githubStore as any).linkedRepo = { full_name: 'owner/repo' };
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('via index')).toBeInTheDocument();
  });

  it('shows "no repo" when no GitHub repo linked in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    (githubStore as any).linkedRepo = null;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('no repo')).toBeInTheDocument();
  });

  it('shows Adaptation toggle in CONTEXT section in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByRole('switch', { name: /Toggle Adaptation/i })).toBeInTheDocument();
  });

  it('hides Models section in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.queryByText('Models')).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/components/layout/Navigator.test.ts --reporter=verbose 2>&1 | tail -30`
Expected: FAIL — "Context" sub-heading not found

- [ ] **Step 3: Replace MODELS hide block and feature toggles hide block with morph**

In `Navigator.svelte`, replace lines 446-470 (the `{#if !routing.isPassthrough}` block around MODELS only) with a morph block. The feature toggles block at lines 477-502 stays as-is — it already correctly hides Explore/Scoring/Adaptation in passthrough mode via its own `{#if !routing.isPassthrough}` guard:

```svelte
        <!-- Models / Context — morphs by tier -->
        {#if routing.isPassthrough}
        <div class="sub-section">
          <span class="sub-heading" style="color: var(--color-neon-yellow);">Context</span>
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label" style="color: var(--color-text-dim);">Analysis</span>
              <span class="data-value" style="color: var(--color-neon-yellow); font-size: 10px;">heuristic</span>
            </div>
            <div class="data-row">
              <span class="data-label" style="color: var(--color-text-dim);">Codebase</span>
              <span class="data-value" style="color: var(--color-neon-yellow); font-size: 10px; {!githubStore.linkedRepo ? 'opacity: 0.4;' : ''}">
                {githubStore.linkedRepo ? 'via index' : 'no repo'}
              </span>
            </div>
            <div class="data-row">
              <span class="data-label" style="color: var(--color-text-dim);">Patterns</span>
              <span class="data-value" style="color: var(--color-neon-yellow); font-size: 10px;">auto-injected</span>
            </div>
            <div class="data-row">
              <span class="data-label">Adaptation</span>
              <button
                class="toggle-track"
                class:toggle-track--on={preferencesStore.pipeline.enable_adaptation}
                onclick={() => preferencesStore.setPipelineToggle('enable_adaptation', !preferencesStore.pipeline.enable_adaptation)}
                role="switch"
                aria-checked={preferencesStore.pipeline.enable_adaptation}
                aria-label="Toggle Adaptation"
              >
                <span class="toggle-thumb"></span>
              </button>
            </div>
          </div>
        </div>
        {:else}
        <div class="sub-section">
          <span class="sub-heading">Models</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', phase: 'analyzer' },
              { label: 'Optimizer', phase: 'optimizer' },
              { label: 'Scorer', phase: 'scorer' },
            ] as { label, phase }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.models[phase as keyof typeof preferencesStore.models]}
                  onchange={(e) => preferencesStore.setModel(phase, (e.target as HTMLSelectElement).value)}
                >
                  <option value="opus">opus</option>
                  <option value="sonnet">sonnet</option>
                  <option value="haiku">haiku</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}
```

**Important:** Do NOT touch the feature toggles block at lines 477-502 (Explore/Scoring/Adaptation toggles + LEAN MODE badge). It stays as-is — its existing `{#if !routing.isPassthrough}` guard already hides it in passthrough mode. In passthrough, the Adaptation toggle lives in the CONTEXT section above; in internal/sampling, it remains in its original Pipeline location.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/components/layout/Navigator.test.ts --reporter=verbose 2>&1 | tail -30`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte frontend/src/lib/components/layout/Navigator.test.ts
git commit -m "feat(frontend): morph Navigator MODELS→CONTEXT section in passthrough mode"
```

---

### Task 3: Navigator — Morph EFFORT → SCORING Section

**Files:**
- Modify: `frontend/src/lib/components/layout/Navigator.svelte:545-570`
- Modify: `frontend/src/lib/components/layout/Navigator.test.ts`

- [ ] **Step 1: Write failing tests for SCORING section in passthrough mode**

In `frontend/src/lib/components/layout/Navigator.test.ts`, add after the CONTEXT tests:

```typescript
  // ── Settings — passthrough SCORING section ─────────────────────────────────

  it('shows SCORING section with heuristic mode in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('Scoring')).toBeInTheDocument();
    // The SCORING section shows "Mode" label with "heuristic" value
    const modeLabels = screen.getAllByText('heuristic');
    expect(modeLabels.length).toBeGreaterThanOrEqual(1);
  });

  it('hides Effort section in passthrough mode', () => {
    forgeStore.provider = null;
    preferencesStore.prefs.pipeline.force_passthrough = true;
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.queryByText('Effort')).not.toBeInTheDocument();
  });

  it('shows Effort section in internal mode', () => {
    defaultFetchHandlers();
    render(Navigator, { props: { active: 'settings' } });
    expect(screen.getByText('Effort')).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/lib/components/layout/Navigator.test.ts --reporter=verbose 2>&1 | tail -30`
Expected: FAIL — "Scoring" sub-heading not found in passthrough mode

- [ ] **Step 3: Replace EFFORT hide block with morph**

In `Navigator.svelte`, replace the EFFORT section (lines 545-570, the `{#if !routing.isPassthrough}` block) with:

```svelte
        <!-- Effort / Scoring — morphs by tier -->
        {#if routing.isPassthrough}
        <div class="sub-section">
          <span class="sub-heading" style="color: var(--color-neon-yellow);">Scoring</span>
          <div class="card-terminal">
            <div class="data-row">
              <span class="data-label" style="color: var(--color-text-dim);">Mode</span>
              <span class="data-value" style="color: var(--color-neon-yellow); font-size: 10px;">heuristic</span>
            </div>
          </div>
        </div>
        {:else}
        <div class="sub-section">
          <span class="sub-heading">Effort</span>
          <div class="card-terminal">
            {#each [
              { label: 'Analyzer', key: 'analyzer_effort' },
              { label: 'Optimizer', key: 'optimizer_effort' },
              { label: 'Scorer', key: 'scorer_effort' },
            ] as { label, key }}
              <div class="data-row">
                <span class="data-label">{label}</span>
                <select
                  class="pref-select"
                  value={preferencesStore.pipeline[key as keyof typeof preferencesStore.pipeline] as string}
                  onchange={(e) => preferencesStore.setEffort(key, (e.target as HTMLSelectElement).value)}
                >
                  <option value="low">low</option>
                  <option value="medium">medium</option>
                  <option value="high">high</option>
                  <option value="max">max</option>
                </select>
              </div>
            {/each}
          </div>
        </div>
        {/if}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/components/layout/Navigator.test.ts --reporter=verbose 2>&1 | tail -30`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/layout/Navigator.svelte frontend/src/lib/components/layout/Navigator.test.ts
git commit -m "feat(frontend): morph Navigator EFFORT→SCORING section in passthrough mode"
```

---

### Task 4: Remove Deprecated `preparePassthrough()` API Function

**Files:**
- Modify: `frontend/src/lib/api/client.ts:273-285`
- Modify: `frontend/src/lib/api/client.test.ts:21,387-402`

- [ ] **Step 1: Verify no other imports of `preparePassthrough` exist**

Run: `cd frontend && grep -r "preparePassthrough" src/ --include="*.ts" --include="*.svelte" | grep -v "client.ts" | grep -v "client.test.ts"`
Expected: No output (no other files reference it)

- [ ] **Step 2: Remove `PassthroughPrepareResult` interface and `preparePassthrough` function from client.ts**

In `frontend/src/lib/api/client.ts`, remove lines 273-285:

```typescript
// DELETE: the entire PassthroughPrepareResult interface and preparePassthrough function
export interface PassthroughPrepareResult {
  trace_id: string;
  optimization_id: string;
  assembled_prompt: string;
  strategy_requested: string;
}

/** @deprecated Use unified POST /api/optimize — backend routes to passthrough via SSE */
export const preparePassthrough = (prompt: string, strategy: string | null) =>
  apiFetch<PassthroughPrepareResult>('/optimize/passthrough', {
    method: 'POST',
    body: JSON.stringify({ prompt, strategy: strategy || undefined }),
  });
```

- [ ] **Step 3: Remove `preparePassthrough` import and test block from client.test.ts**

In `frontend/src/lib/api/client.test.ts`:

Remove `preparePassthrough` from the import statement (line 21).

Remove the entire test block (lines 387-402):
```typescript
// DELETE:
// ── preparePassthrough ──────────────────────────────────────────

describe('preparePassthrough', () => {
  it('sends POST /optimize/passthrough', async () => {
    const prepResp = { trace_id: 'trace-1', optimization_id: 'opt-1', assembled_prompt: 'Full prompt', strategy_requested: 'auto' };
    const mock = mockFetch([{ match: '/optimize/passthrough', response: prepResp }]);
    const result = await preparePassthrough('My prompt', 'auto');
    expect(result.trace_id).toBe('trace-1');
    const [url, opts] = mock.mock.calls[0];
    expect(url).toContain('/optimize/passthrough');
    expect((opts as RequestInit).method).toBe('POST');
    const body = JSON.parse((opts as RequestInit).body as string);
    expect(body.prompt).toBe('My prompt');
    expect(body.strategy).toBe('auto');
  });
});
```

- [ ] **Step 4: Run all frontend tests to verify nothing breaks**

Run: `cd frontend && npx vitest run --reporter=verbose 2>&1 | tail -30`
Expected: All PASS — no references to removed function

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api/client.ts frontend/src/lib/api/client.test.ts
git commit -m "chore(frontend): remove deprecated preparePassthrough() API function"
```

---

### Task 5: Final Verification

**Files:** None — verification only.

- [ ] **Step 1: Run all frontend tests**

Run: `cd frontend && npx vitest run --reporter=verbose 2>&1 | tail -40`
Expected: All PASS

- [ ] **Step 2: Run svelte-check for type errors**

Run: `cd frontend && npx svelte-check 2>&1 | tail -20`
Expected: 0 errors

- [ ] **Step 3: Visual verification checklist**

Start the app (`./init.sh start`) and verify in browser:

1. Toggle Force passthrough ON — MODELS section morphs to CONTEXT section with: Analysis (heuristic), Codebase (via index or no repo), Patterns (auto-injected), Adaptation (live toggle)
2. EFFORT section morphs to SCORING section with: Mode (heuristic)
3. CONTEXT and SCORING headers are neon yellow
4. Adaptation toggle is interactive — clicking it changes the value
5. Toggle Force passthrough OFF — MODELS, feature toggles, and EFFORT sections reappear as before
6. Open PassthroughGuide modal (? button or toggle enable) — feature matrix shows "Heuristic / Hybrid", "Roots + index", and checkmark for Pattern injection
7. Step 1 description mentions "codebase context, applied patterns"

- [ ] **Step 4: Commit any remaining fixes if needed**

```bash
git add -A && git commit -m "fix(frontend): address verification findings"
```
