# Phase 4: Conversational Refinement — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add conversational refinement — users can iteratively improve prompts with suggestions, branching/rollback, and a timeline UI showing score progression.

**Architecture:** Each refinement turn is a fresh pipeline invocation (not multi-turn accumulation). `refine.md` replaces `optimize.md` during refinement. Suggestions generated via Haiku after each turn. Branching at the data layer — rollback creates a fork. Parts-based SSE streaming (status → prompt → scores → suggestions). Timeline UI with expandable turn cards.

**Tech Stack:** Python 3.12+ (backend), SvelteKit 2 / Svelte 5 runes (frontend)

**Spec:** `docs/superpowers/specs/2026-03-15-project-synthesis-redesign.md` (Section 13)

---

## File Structure

### Create (Backend)

| File | Responsibility |
|------|---------------|
| `backend/app/services/refinement_service.py` | Refinement sessions, version CRUD, branching, suggestion generation |
| `backend/app/routers/refinement.py` | `POST /api/refine`, `GET /api/refine/{opt_id}/versions`, `POST /api/refine/{opt_id}/rollback` |
| `backend/tests/test_refinement_service.py` | Refinement CRUD, branching tests |
| `backend/tests/test_refinement_pipeline.py` | Full refine flow tests |

### Create (Frontend)

| File | Responsibility |
|------|---------------|
| `frontend/src/lib/stores/refinement.svelte.ts` | Refinement state (turns, branches, suggestions, streaming) |
| `frontend/src/lib/components/refinement/RefinementTimeline.svelte` | Scrollable turn card list |
| `frontend/src/lib/components/refinement/RefinementTurnCard.svelte` | Single turn (header + expandable parts) |
| `frontend/src/lib/components/refinement/SuggestionChips.svelte` | Clickable suggestion pills |
| `frontend/src/lib/components/refinement/BranchSwitcher.svelte` | Branch navigation |
| `frontend/src/lib/components/refinement/ScoreSparkline.svelte` | Inline score progression chart |
| `frontend/src/lib/components/refinement/RefinementInput.svelte` | Text input for custom requests |

### Modify

| File | Changes |
|------|---------|
| `prompts/refine.md` | Real refinement optimizer template |
| `prompts/suggest.md` | Real suggestion generator template |
| `backend/app/main.py` | Include refinement router |
| `frontend/src/lib/api/client.ts` | Add refine endpoints |
| `frontend/src/lib/components/layout/EditorGroups.svelte` | Add refinement timeline split pane |

---

## Chunk 1: Backend (Templates + Service + Router)

### Task 1: Refinement Templates

**Files:**
- Modify: `prompts/refine.md`
- Modify: `prompts/suggest.md`

- [ ] **Step 1: Write refine.md**

The refinement optimizer template — replaces optimize.md during refinement turns:

```markdown
<original-prompt>
{{original_prompt}}
</original-prompt>

<current-prompt>
{{current_prompt}}
</current-prompt>

<refinement-request>
{{refinement_request}}
</refinement-request>

<codebase-context>
{{codebase_guidance}}
{{codebase_context}}
</codebase-context>

<adaptation>
{{adaptation_state}}
</adaptation>

<strategy>
{{strategy_instructions}}
</strategy>

## Instructions

You are an expert prompt engineer performing an iterative refinement.

The user has an existing optimized prompt (shown as "current prompt" above) and wants a specific improvement (shown as "refinement request"). The original raw prompt is provided for reference.

**Guidelines:**
- **Apply ONLY the refinement request.** Do not rewrite the entire prompt — modify only what the request asks for.
- **Preserve all existing improvements.** The current prompt has already been optimized. Keep everything that works.
- **Maintain the original intent.** The original prompt defines what the task should accomplish.
- **Be surgical.** Small, targeted changes are better than wholesale rewrites.

Summarize exactly what you changed and why.
```

- [ ] **Step 2: Write suggest.md**

```markdown
<optimized-prompt>
{{optimized_prompt}}
</optimized-prompt>

<scores>
{{scores}}
</scores>

<weaknesses>
{{weaknesses}}
</weaknesses>

<strategy>
Strategy used: {{strategy_used}}
</strategy>

## Instructions

Generate exactly 3 actionable refinement suggestions for the optimized prompt above.

Each suggestion should be a single, specific instruction the user could give to improve the prompt. Draw from three sources:

1. **Score-driven** — Target the lowest-scoring dimension. Example: "Improve specificity — currently 6.2/10"
2. **Analysis-driven** — Address a weakness detected by the analyzer. Example: "Add error handling constraints"
3. **Strategic** — Apply a technique from the strategy above. Example: "Add few-shot examples to demonstrate expected output"

Return exactly 3 suggestions. Each should be actionable in one sentence. Be specific, not vague.
```

- [ ] **Step 3: Commit**

```bash
git add prompts/refine.md prompts/suggest.md
git commit -m "feat: write refinement and suggestion prompt templates"
```

---

### Task 2: Refinement Service

**Files:**
- Create: `backend/app/services/refinement_service.py`
- Create: `backend/tests/test_refinement_service.py`

The service manages refinement sessions with version history, branching, and suggestion generation.

**Methods:**
- `create_initial_turn(optimization_id, prompt, scores, strategy)` — creates branch + v1 turn from initial optimization
- `create_refinement_turn(optimization_id, refinement_request, provider, db)` — runs full pipeline (analyze → refine → score → suggest), persists turn
- `get_versions(optimization_id, branch_id=None)` — list all turns for a branch
- `rollback(optimization_id, to_version)` — creates new branch forked from version N
- `get_branches(optimization_id)` — list all branches
- `_generate_suggestions(optimized_prompt, scores, weaknesses, strategy)` — Haiku call to suggest.md

Each refinement turn is a fresh pipeline invocation — not multi-turn accumulation.

**Tests (6):**
1. `test_create_initial_turn` — v1 created with scores
2. `test_create_refinement_turn` — v2 created with deltas from v1
3. `test_get_versions` — returns ordered list
4. `test_rollback_creates_fork` — new branch from version N
5. `test_get_branches` — lists branches
6. `test_suggestions_generated` — verify 3 suggestions produced

- [ ] **Steps: TDD cycle (write tests → fail → implement → pass → commit)**

```bash
git add backend/app/services/refinement_service.py backend/tests/test_refinement_service.py
git commit -m "feat: implement refinement service with version CRUD, branching, and suggestions"
```

---

### Task 3: Refinement Router + Pipeline Integration

**Files:**
- Create: `backend/app/routers/refinement.py`
- Create: `backend/tests/test_refinement_pipeline.py`
- Modify: `backend/app/main.py`

**Endpoints:**
- `POST /api/refine` — takes `{optimization_id, refinement_request}`, runs pipeline with refine.md, returns SSE stream with parts
- `GET /api/refine/{optimization_id}/versions` — returns version list
- `POST /api/refine/{optimization_id}/rollback` — takes `{to_version}`, creates fork

SSE events for refinement: same as optimize SSE plus `suggestions` event at end.

**Tests (4):**
1. `test_refine_sse` — mock provider, verify SSE events stream
2. `test_get_versions` — returns version list
3. `test_rollback` — creates fork
4. `test_refine_invalid_optimization` — 404

- [ ] **Steps: TDD cycle → commit**

```bash
git add backend/app/routers/refinement.py backend/tests/test_refinement_pipeline.py backend/app/main.py
git commit -m "feat: implement refinement router with SSE streaming"
```

---

## Chunk 2: Frontend (Store + Components)

### Task 4: Refinement Store + API Client Updates

**Files:**
- Create: `frontend/src/lib/stores/refinement.svelte.ts`
- Modify: `frontend/src/lib/api/client.ts`

Add to API client:
- `refineSSE(optimizationId, request, onEvent, onError, onComplete)` — SSE for refinement
- `getRefinementVersions(optimizationId)` — GET versions
- `rollbackRefinement(optimizationId, toVersion)` — POST rollback

Store manages: turns, branches, activeBranch, suggestions, streaming state, score progression.

- [ ] **Steps: implement → build verify → commit**

---

### Task 5: Refinement UI Components

**Files:**
- Create all 7 refinement components in `frontend/src/lib/components/refinement/`
- Modify: `frontend/src/lib/components/layout/EditorGroups.svelte`

Components (all must follow brand guidelines — zero glow, 1px borders, compact density):

1. **RefinementTimeline** — scrollable vertical list of turn cards
2. **RefinementTurnCard** — version badge, request text, overall score, expandable details (diff, full scores, changes summary)
3. **SuggestionChips** — 3 clickable pills, cyan border on hover
4. **BranchSwitcher** — left/right arrows with "Branch 1/2" label
5. **ScoreSparkline** — tiny SVG line chart of overall scores across versions
6. **RefinementInput** — text input + submit button for custom requests

**Wire into EditorGroups:** When forge store has a completed result and refinement is active, show a split pane: editor on top, refinement timeline on bottom.

- [ ] **Steps: implement → wire into live layout → build verify → commit**

---

### Task 6: Tests + Handoff

- [ ] **Step 1: Run full backend suite with coverage**
- [ ] **Step 2: Verify frontend build**
- [ ] **Step 3: Generate handoff-phase-4.json**
- [ ] **Step 4: Update orchestration protocol**
- [ ] **Step 5: Commit**

---

## Exit Conditions Checklist

| # | Condition | Task |
|---|-----------|------|
| 1 | POST /api/refine runs full pipeline with refine.md | Task 3 |
| 2 | Refinement timeline renders in editor groups | Task 5 |
| 3 | Turn cards show scores, deltas, expandable diffs | Task 5 |
| 4 | 3 suggestions generated per turn, clickable | Task 2, 5 |
| 5 | Rollback creates fork, branch switcher works | Task 2, 5 |
| 6 | Score sparkline shows progression | Task 5 |
| 7 | SSE parts stream correctly | Task 3 |
| 8 | All refinement tests pass | Task 6 |
| 9 | handoff-phase-4.json written | Task 6 |
