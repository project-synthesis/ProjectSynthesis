# Phase 3: Frontend — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the full web UI — VS Code workbench layout, prompt editor with strategy picker, SSE-driven optimization with score display, history/GitHub navigators, command palette, and error states.

**Architecture:** SvelteKit 2 with Svelte 5 runes. Three stores (forge, editor, github) manage state. API client handles all backend calls including SSE consumption with reconnection. Layout shell uses CSS Grid for the workbench (activity bar + navigator + editor groups + inspector + status bar). Industrial cyberpunk design system: dark backgrounds (#06060c), sharp 1px neon contours (#00e5ff), no rounded corners, no drop shadows, no glow effects.

**Tech Stack:** SvelteKit 2, Svelte 5 (runes), Tailwind CSS 4, TypeScript

**Spec:** `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md` (Section 5)

**Brand Guidelines:** `.claude/skills/brand-guidelines/` — industrial cyberpunk, zero-effects directive, neon tube model

**Phase 2 Handoff:** `docs/superpowers/plans/handoffs/handoff-phase-2.json` (all_passed: true, 160 tests)

---

## Design System Reference (for all subagents)

Every frontend subagent MUST follow these rules. Include this section in every dispatch.

**Colors (CSS custom properties to define in app.css):**
- `--color-bg-primary: #06060c` — page background
- `--color-bg-secondary: #0c0c16` — panels
- `--color-bg-card: #11111e` — cards
- `--color-bg-hover: #16162a` — hover
- `--color-bg-input: #0a0a14` — inputs
- `--color-neon-cyan: #00e5ff` — primary accent
- `--color-neon-purple: #a855f7` — processed
- `--color-neon-green: #22ff88` — success
- `--color-neon-red: #ff3366` — danger
- `--color-neon-yellow: #fbbf24` — warning
- `--color-text-primary: #e4e4f0` — body text
- `--color-text-secondary: #8b8ba8` — secondary
- `--color-text-dim: #7a7a9e` — metadata
- `--color-border-subtle: rgba(74, 74, 106, 0.15)` — borders
- `--color-border-accent: rgba(0, 229, 255, 0.12)` — accent borders

**Typography:** Space Grotesk (body), Geist Mono (data/scores), Syne (headings). Load via Google Fonts.

**Rules:**
- ZERO glow, drop-shadow, text-shadow, blur — absolute non-negotiable
- All borders 1px solid, never 2px
- No rounded corners (use `rounded-none` or very small like `rounded-sm` for buttons only)
- Ultra-compact: sidebar headers h-8, status bar h-[24px], data rows h-5
- Body text `text-xs` (12px), compact labels `text-[10px]`, monospace for scores/data
- Hover: border intensifies, background tints, 200ms transition
- Dark backgrounds only — never light mode

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `frontend/src/app.css` | Global styles, CSS custom properties, font imports |
| `frontend/src/lib/api/client.ts` | API client — all backend calls, SSE consumption |
| `frontend/src/lib/stores/forge.svelte.ts` | Optimization state (input, result, progress, scores, feedback) |
| `frontend/src/lib/stores/editor.svelte.ts` | Tab management, active document |
| `frontend/src/lib/stores/github.svelte.ts` | Repo link state, OAuth |
| `frontend/src/lib/components/layout/ActivityBar.svelte` | Left icon bar (4 activities) |
| `frontend/src/lib/components/layout/Navigator.svelte` | Sidebar panel (varies by activity) |
| `frontend/src/lib/components/layout/EditorGroups.svelte` | Multi-tab center editor |
| `frontend/src/lib/components/layout/Inspector.svelte` | Right panel (scores, strategy) |
| `frontend/src/lib/components/layout/StatusBar.svelte` | Bottom bar (provider, repo, palette hint) |
| `frontend/src/lib/components/editor/PromptEdit.svelte` | Prompt textarea + strategy picker + Forge button |
| `frontend/src/lib/components/editor/ForgeArtifact.svelte` | Optimization result viewer |
| `frontend/src/lib/components/shared/DiffView.svelte` | Side-by-side diff |
| `frontend/src/lib/components/shared/CommandPalette.svelte` | Ctrl+K fuzzy finder |
| `frontend/src/lib/components/shared/ProviderBadge.svelte` | Provider indicator |
| `frontend/src/lib/components/shared/ScoreCard.svelte` | 5-dimension scores with deltas |

### Modify

| File | Changes |
|------|---------|
| `frontend/src/app.html` | Add font imports, meta tags |
| `frontend/src/routes/+layout.svelte` | Workbench shell layout |
| `frontend/src/routes/+page.svelte` | Wire stores and components |

---

## Chunk 1: Foundation (Styles, API Client, Stores)

### Task 1: Global Styles + App Shell

**Files:**
- Create: `frontend/src/app.css`
- Modify: `frontend/src/app.html`
- Modify: `frontend/src/routes/+layout.svelte`

- [ ] **Step 1: Create app.css with design system tokens**

Global CSS with all custom properties, font imports, base styles. Tailwind CSS 4 uses `@import "tailwindcss"` instead of directives.

- [ ] **Step 2: Update app.html with font links**

Add Google Fonts links for Space Grotesk, Syne, and Geist Mono.

- [ ] **Step 3: Update +layout.svelte with workbench grid**

CSS Grid layout: `grid-template-columns: 48px 240px 1fr 280px`, `grid-template-rows: 1fr 24px`. Activity bar | Navigator | Editor Groups | Inspector, with Status Bar spanning bottom.

- [ ] **Step 4: Verify dev server starts**

Run: `cd frontend && npm run dev -- --port 5199` — should start without errors

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app.css frontend/src/app.html frontend/src/routes/+layout.svelte
git commit -m "feat: add design system tokens and workbench layout shell"
```

---

### Task 2: API Client

**Files:**
- Create: `frontend/src/lib/api/client.ts`

- [ ] **Step 1: Implement API client**

The client handles all backend communication:

```typescript
// frontend/src/lib/api/client.ts
const BASE_URL = 'http://localhost:8000/api';

// Generic fetch wrapper
async function apiFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE_URL}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...options?.headers },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new ApiError(resp.status, body.detail || resp.statusText);
  }
  return resp.json();
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// Health
export const getHealth = () => apiFetch<HealthResponse>('/health');

// Optimize (SSE)
export function optimizeSSE(
  prompt: string,
  strategy?: string,
  onEvent: (event: SSEEvent) => void,
  onError: (err: Error) => void,
  onComplete: () => void,
): AbortController { ... }

// Optimize (poll for reconnection)
export const getOptimization = (traceId: string) =>
  apiFetch<OptimizationResult>(`/optimize/${traceId}`);

// History
export const getHistory = (params?: HistoryParams) => ...

// Feedback
export const submitFeedback = (optimizationId: string, rating: string, comment?: string) => ...

// Providers
export const getProviders = () => apiFetch<ProvidersResponse>('/providers');

// Settings
export const getSettings = () => apiFetch<SettingsResponse>('/settings');

// GitHub
export const githubLogin = () => apiFetch<{ url: string }>('/github/auth/login');
export const githubMe = () => apiFetch<GitHubUser>('/github/auth/me');
export const githubLogout = () => apiFetch('/github/auth/logout', { method: 'POST' });
export const githubRepos = () => apiFetch<Repo[]>('/github/repos');
export const githubLink = (fullName: string) => ...
export const githubLinked = () => apiFetch<LinkedRepo>('/github/repos/linked');
export const githubUnlink = () => apiFetch('/github/repos/unlink', { method: 'DELETE' });
```

The SSE function uses `fetch` with `text/event-stream` reading, parses `data:` lines, and implements reconnection (poll `getOptimization` every 2s for 60s on drop).

- [ ] **Step 2: Commit**

```bash
git add frontend/src/lib/api/client.ts
git commit -m "feat: implement API client with SSE consumption and reconnection"
```

---

### Task 3: Stores

**Files:**
- Create: `frontend/src/lib/stores/forge.svelte.ts`
- Create: `frontend/src/lib/stores/editor.svelte.ts`
- Create: `frontend/src/lib/stores/github.svelte.ts`

- [ ] **Step 1: Implement forge store**

Svelte 5 runes store managing optimization state:
- `prompt` (string) — user input
- `strategy` (string | null) — selected strategy
- `status` ('idle' | 'analyzing' | 'optimizing' | 'scoring' | 'complete' | 'error')
- `result` (OptimizationResult | null) — final result
- `traceId` (string | null) — for SSE reconnection
- `error` (string | null)
- `feedback` ('thumbs_up' | 'thumbs_down' | null)
- `forge()` — triggers optimization via SSE
- `submitFeedback(rating)` — sends feedback

- [ ] **Step 2: Implement editor store**

Tab management:
- `tabs` — array of `{id, title, type: 'prompt' | 'result' | 'diff'}`
- `activeTabId` — current tab
- `openTab(tab)`, `closeTab(id)`, `setActive(id)`

- [ ] **Step 3: Implement github store**

GitHub state:
- `user` (GitHubUser | null)
- `linkedRepo` (LinkedRepo | null)
- `repos` (Repo[])
- `checkAuth()`, `login()`, `logout()`, `loadRepos()`, `linkRepo(name)`, `unlinkRepo()`

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/stores/
git commit -m "feat: implement forge, editor, and github stores"
```

---

## Chunk 2: Layout Components

### Task 4: Activity Bar + Navigator

**Files:**
- Create: `frontend/src/lib/components/layout/ActivityBar.svelte`
- Create: `frontend/src/lib/components/layout/Navigator.svelte`

- [ ] **Step 1: Implement ActivityBar**

Vertical icon strip (48px wide). 4 activities: Editor (pencil), History (clock), GitHub (git branch), Settings (gear). Uses SVG icons or Unicode. Active activity has cyan left border.

- [ ] **Step 2: Implement Navigator**

240px sidebar that switches content based on active activity:
- **Editor**: minimal or strategy list
- **History**: sortable/filterable list from `GET /api/history`
- **GitHub**: linked repo info, repo browser
- **Settings**: provider info, config values

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/layout/
git commit -m "feat: implement ActivityBar and Navigator components"
```

---

### Task 5: Editor Groups + Inspector + StatusBar

**Files:**
- Create: `frontend/src/lib/components/layout/EditorGroups.svelte`
- Create: `frontend/src/lib/components/layout/Inspector.svelte`
- Create: `frontend/src/lib/components/layout/StatusBar.svelte`

- [ ] **Step 1: Implement EditorGroups**

Tab bar at top (h-8), content area below. Renders the active tab's component:
- `prompt` → PromptEdit
- `result` → ForgeArtifact
- `diff` → DiffView

- [ ] **Step 2: Implement Inspector**

Right panel (280px). Shows:
- During idle: "Enter a prompt and forge"
- During optimization: progress indicator with current phase
- After optimization: ScoreCard component with 5-dimension scores + deltas

- [ ] **Step 3: Implement StatusBar**

Bottom strip (24px). Shows:
- Left: ProviderBadge + linked repo badge
- Right: "Ctrl+K" command palette hint

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/components/layout/
git commit -m "feat: implement EditorGroups, Inspector, and StatusBar"
```

---

## Chunk 3: Editor + Shared Components

### Task 6: PromptEdit + ForgeArtifact

**Files:**
- Create: `frontend/src/lib/components/editor/PromptEdit.svelte`
- Create: `frontend/src/lib/components/editor/ForgeArtifact.svelte`

- [ ] **Step 1: Implement PromptEdit**

The main editor component:
- Large textarea with `bg-[var(--color-bg-input)]`, `border: 1px solid var(--color-border-subtle)`, `text-xs font-sans`
- Strategy picker dropdown (select from available strategies + "auto")
- "Forge" button — cyan neon border, `font-display uppercase text-[11px]`, triggers `forge()` from forge store
- Progress indicator during optimization (phase name + spinner)

- [ ] **Step 2: Implement ForgeArtifact**

Result viewer:
- Shows optimized prompt text
- Changes summary section
- Toggle to DiffView
- Copy button
- Feedback thumbs up/down buttons

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/components/editor/
git commit -m "feat: implement PromptEdit and ForgeArtifact components"
```

---

### Task 7: Shared Components (ScoreCard, DiffView, ProviderBadge, CommandPalette)

**Files:**
- Create: `frontend/src/lib/components/shared/ScoreCard.svelte`
- Create: `frontend/src/lib/components/shared/DiffView.svelte`
- Create: `frontend/src/lib/components/shared/ProviderBadge.svelte`
- Create: `frontend/src/lib/components/shared/CommandPalette.svelte`

- [ ] **Step 1: Implement ScoreCard**

5-dimension score display:
- Each dimension: label (text-[10px]), score (font-mono), delta (green for positive, red for negative)
- Overall score prominently displayed
- Original vs optimized scores side by side
- Use `var(--color-neon-green)` for positive deltas, `var(--color-neon-red)` for negative

- [ ] **Step 2: Implement DiffView**

Side-by-side diff of original vs optimized:
- Two columns: "Original" (left) and "Optimized" (right)
- Line-by-line comparison with additions/removals highlighted
- Simple word-level diff (split by lines, highlight differences)

- [ ] **Step 3: Implement ProviderBadge**

Small badge showing active provider:
- "CLI" (cyan border), "API" (purple border), "MCP" (green border), or "None" (red border)
- `text-[10px] font-mono` in a 1px bordered pill

- [ ] **Step 4: Implement CommandPalette**

Modal fuzzy finder:
- Opens on Ctrl+K (global keydown listener)
- Input field at top
- Fuzzy-matched action list: "New Prompt", "Forge", "View History", "Link Repo", "Toggle Diff", "Copy Result"
- Arrow keys + Enter to select
- Escape to close
- Dark overlay background

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/components/shared/
git commit -m "feat: implement ScoreCard, DiffView, ProviderBadge, and CommandPalette"
```

---

## Chunk 4: Wiring + Error States + Verification

### Task 8: Page Wiring + Error States

**Files:**
- Modify: `frontend/src/routes/+page.svelte`
- Modify: `frontend/src/routes/+layout.svelte`

- [ ] **Step 1: Wire +page.svelte**

Import all stores and layout components. Initialize stores on mount (check health, check GitHub auth, load provider info).

The page renders the workbench layout with all components connected to stores.

- [ ] **Step 2: Add error state banners**

Error states (from spec Section 10):
- Backend unreachable: "Cannot connect to backend. Check that services are running." + retry
- Optimization failed: "Optimization failed at [phase]. [message]." + retry
- No provider: "No provider configured. Set up Claude CLI or add an API key in Settings."
- GitHub auth failed: "GitHub authentication expired. Re-connect your account."
- Rate limited: "Rate limit reached. Try again in [N] seconds."

Implement as a global error banner component at the top of the workbench.

- [ ] **Step 3: Add SSE reconnection logic**

In the forge store's `forge()` method, implement reconnection:
- Store `traceId` from first SSE event
- On connection drop, poll `GET /api/optimize/{traceId}` every 2s for 60s
- On success, populate result
- On timeout, show "Optimization may still be running. Check history."

- [ ] **Step 4: Verify build**

Run: `cd frontend && npm run build` — should succeed
Run: `cd frontend && npx svelte-check` — should have no errors (warnings OK)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/
git commit -m "feat: wire page with stores, components, error states, and SSE reconnection"
```

---

### Task 9: Verify + Handoff

- [ ] **Step 1: Verify dev server starts**

```bash
cd frontend && npm run dev -- --port 5199
```

Open http://localhost:5199 in browser — workbench layout should render.

- [ ] **Step 2: Run type check**

```bash
cd frontend && npx svelte-check --tsconfig ./tsconfig.json
```

- [ ] **Step 3: Generate handoff**

Write `docs/superpowers/plans/handoffs/handoff-phase-3.json` with verification results.

- [ ] **Step 4: Update orchestration protocol**

Update Phase 3 row from `Pending` to `Complete`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/handoffs/handoff-phase-3.json docs/superpowers/plans/2026-03-15-redesign-orchestration-protocol.md
git commit -m "docs: write Phase 3 handoff artifact"
```

---

## Exit Conditions Checklist

| # | Condition | Task |
|---|-----------|------|
| 1 | SvelteKit dev server starts on 5199 | Task 1, 9 |
| 2 | VS Code workbench layout renders | Task 1, 4, 5 |
| 3 | Prompt editor accepts input, strategy picker works | Task 6 |
| 4 | Forge button triggers optimization, progress shows, result displays | Task 6, 8 |
| 5 | Diff view shows original vs optimized | Task 7 |
| 6 | Inspector shows 5-dimension scores with deltas | Task 5, 7 |
| 7 | History navigator shows past optimizations | Task 4 |
| 8 | GitHub navigator shows linked repo | Task 4 |
| 9 | Command palette opens on Ctrl+K | Task 7 |
| 10 | Feedback thumbs up/down works | Task 6 |
| 11 | SSE reconnection with trace_id polling | Task 8 |
| 12 | Frontend error states | Task 8 |
| 13 | All frontend checks pass | Task 9 |
| 14 | handoff-phase-3.json written | Task 9 |
