# Live Pattern Intelligence Tier 1 — Implementation Plan

**Status:** Draft r2 — independent plan-reviewer pass complete (2026-04-24). All 5 BLOCKERs (B-1 S3 test dropped, B-2 Task 14 split, B-3+B-4 backend tests mock `engine.match_prompt` instead of seeding fixtures, B-5 style-attribute assertion) + 4 MAJORs addressed; 5 MINORs addressed. `match_level` enum corrected to `{"family","cluster"}` (was wrongly `{…,"candidate"}` in spec). Pending user approval.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-banner `PatternSuggestion.svelte` with a persistent `ContextPanel.svelte` sidebar that surfaces matched cluster, meta-patterns, and cross-cluster (GLOBAL) patterns as the user types; allow multi-pattern selection + apply.

**Architecture:** Backend receives two additive keys on `POST /api/clusters/match` response (`cross_cluster_patterns`, `match_level`) — no new Pydantic model, no schema migration. Frontend gets a new `ContextPanel.svelte` component that reads `clustersStore.suggestion` and round-trips selection through `forgeStore.appliedPatternIds`. `clustersStore` drops its `_skippedClusterId` gate and `dismissSuggestion()` method; `PatternSuggestion.svelte` + its test are deleted.

**Tech Stack:** Python 3.12 + FastAPI + SQLAlchemy (backend), SvelteKit 2 + Svelte 5 runes + Tailwind CSS 4 + Vitest + @testing-library/svelte (frontend).

**Spec:** [docs/superpowers/specs/2026-04-24-live-pattern-intelligence-tier-1-design.md](../specs/2026-04-24-live-pattern-intelligence-tier-1-design.md)

---

## Test Design Matrix (TDM)

Every requirement in the spec maps to one or more test cases. Each row: requirement ID → behavior under test → file → fixture/mock → primary assertion. Requirements map 1:1 to tasks — a task is RED-GREEN-REFACTOR for its row's test case(s).

### Backend TDM

All backend tests mock `engine.match_prompt()` via `AsyncMock` returning a hand-rolled `PatternMatch` — matches the existing pattern at `test_clusters_router.py:245-275`. Rationale: the test fixture's `app_client` has no embedding service (`conftest.py:114`: `embedding_service=None`) and no taxonomy engine seeded on `app.state`, so cosine-similarity-driven match fixtures aren't viable. `match_level` enum is `{"family", "cluster"}` — the engine's internal `"none"` short-circuits to `match=None` (matching.py:395-397 + clusters.py:696-697), never reaching the response dict.

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| B1 | `POST /api/clusters/match` response includes `cross_cluster_patterns` key | `backend/tests/test_clusters_router.py::test_match_response_includes_cross_cluster_patterns` | `AsyncMock(engine.match_prompt)` → `PatternMatch` with 2 `cross_cluster_patterns` MetaPattern rows | `response.json()["match"]["cross_cluster_patterns"]` has 2 items with mocked IDs, disjoint from `meta_patterns` |
| B2 | `POST /api/clusters/match` response includes `match_level` key | `backend/tests/test_clusters_router.py::test_match_response_includes_match_level` | `PatternMatch(..., match_level="cluster")` | `response.json()["match"]["match_level"] == "cluster"` |
| B3 | `match_level` takes one of `{family, cluster}` | `backend/tests/test_clusters_router.py::test_match_response_match_level_is_valid_enum` | `PatternMatch(..., match_level="family")` | Value is in `{"family", "cluster"}` |
| B4 | Existing match response contract unchanged (backwards compat) | `backend/tests/test_clusters_router.py::test_match_response_preserves_existing_fields` | Mock as B2 | `cluster.{id, label, domain, member_count}`, `meta_patterns`, `similarity` all present |
| B5 | Response serializes cleanly when `cross_cluster_patterns` is empty | `backend/tests/test_clusters_router.py::test_match_response_empty_cross_cluster_patterns` | `PatternMatch(..., cross_cluster_patterns=[])` | `response.json()["match"]["cross_cluster_patterns"] == []` |

### Frontend store TDM

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| S1 | `_skippedClusterId` state removed from `clustersStore` | `frontend/src/lib/stores/clusters.svelte.test.ts::test_skipped_cluster_id_state_gone` | — | `('_skippedClusterId' in clustersStore)` is `false` |
| S2 | `dismissSuggestion()` method removed | `frontend/src/lib/stores/clusters.svelte.test.ts::test_dismiss_suggestion_method_gone` | — | `typeof (clustersStore as any).dismissSuggestion === 'undefined'` |
| S7 | `_matchInFlight` is exposed as public state | `frontend/src/lib/stores/clusters.svelte.test.ts::test_match_inflight_exposed` | `mockFetch` with delayed response | During the fetch: `clustersStore._matchInFlight === true`; after resolution: `=== false` |
| S8 | `_matchError` is `'network'` when fetch fails and `null` on success | `frontend/src/lib/stores/clusters.svelte.test.ts::test_match_error_flag` | `mockFetch` returning reject then resolve | After reject: `_matchError === 'network'`; after success: `_matchError === null` |
| S9 | `_lastMatchedText` visible from outside (access-control change to allow component-level gating) | `frontend/src/lib/stores/clusters.svelte.test.ts::test_last_matched_text_readable` | Call `checkForPatterns` successfully | `clustersStore._lastMatchedText !== ''` |
| S4 | `ClusterMatch` type carries `cross_cluster_patterns` (array, default `[]`) | `frontend/src/lib/stores/clusters.svelte.test.ts::test_cluster_match_type_carries_cross_cluster_patterns` | Mock `matchPattern` response with `cross_cluster_patterns: [{ id, pattern_text, source_count }]` | `clustersStore.suggestion.cross_cluster_patterns.length === 1` |
| S5 | `ClusterMatch` type carries `match_level` (`'family' \| 'cluster'`, default `"cluster"`) | `frontend/src/lib/stores/clusters.svelte.test.ts::test_cluster_match_type_carries_match_level` | `mockFetch` response with `match_level: "family"` | `clustersStore.suggestion.match_level === "family"` |
| S6 | Legacy response missing new keys decodes with defaults | `frontend/src/lib/stores/clusters.svelte.test.ts::test_legacy_response_defaults` | Mock response with no `cross_cluster_patterns` + no `match_level` | `suggestion.cross_cluster_patterns === []`, `suggestion.match_level === "cluster"` |

### Frontend component TDM (`ContextPanel.svelte`)

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| C1 | Mounts with `null` suggestion → empty state copy visible | `frontend/src/lib/components/editor/ContextPanel.test.ts::test_empty_state_null_suggestion` | `clustersStore.suggestion = null`, `forgeStore.status = 'idle'` | `screen.getByText(/waiting for prompt/i)` is present |
| C2 | With a match → cluster label renders | `ContextPanel.test.ts::test_renders_cluster_label` | `clustersStore.suggestion = makeSuggestion({ cluster: { label: 'API endpoint patterns' } })` | `screen.getByText('API endpoint patterns')` present |
| C3 | Similarity rendered as integer percentage | `ContextPanel.test.ts::test_renders_similarity_percentage` | `suggestion.similarity = 0.842` | `screen.getByText(/84%/)` present |
| C4 | `match_level` rendered as sub-label | `ContextPanel.test.ts::test_renders_match_level` | `suggestion.match_level = 'family'` | `screen.getByText(/family/i)` present |
| C5 | Domain dot renders with `taxonomyColor(domain)` | `ContextPanel.test.ts::test_renders_domain_dot` | `suggestion.cluster.domain = 'backend'` | Dot element has `background-color` matching `taxonomyColor('backend')` |
| C6 | Meta-patterns section renders N rows | `ContextPanel.test.ts::test_renders_meta_pattern_rows` | `suggestion.meta_patterns = [mp1, mp2, mp3]` | 3 checkbox inputs with role `checkbox` in meta-patterns section |
| C7 | Global section renders cross-cluster patterns | `ContextPanel.test.ts::test_renders_global_pattern_rows` | `suggestion.cross_cluster_patterns = [gcp1, gcp2]` | Heading "GLOBAL" + 2 checkbox inputs in global section |
| C8 | Global section has `neon-purple` left-border class | `ContextPanel.test.ts::test_global_section_has_purple_left_border` | Same as C7 | Global section element has `border-left` matching `var(--color-neon-purple)` |
| C9 | Meta-pattern text truncates at 60 chars | `ContextPanel.test.ts::test_pattern_text_truncated` | `meta_patterns[0].pattern_text = 'x'.repeat(80)` | Rendered text ends in "…" and length ≤ 61 |
| C10 | Clicking a checkbox toggles `selectedPatternIds` | `ContextPanel.test.ts::test_checkbox_click_toggles_selection` | As C6 | After click on first checkbox: `input.checked === true`, selection counter shows `1/3 ✔` |
| C11 | Selection counter reads `N/M ✔` | `ContextPanel.test.ts::test_selection_counter_format` | After 2 toggles on a 3-row section | `screen.getByText('2/3 ✔')` present |
| C12 | Apply button disabled when selection empty | `ContextPanel.test.ts::test_apply_button_disabled_when_empty` | `suggestion` present, no toggles | Apply button has `disabled` attribute |
| C13 | Apply button label reflects selection count | `ContextPanel.test.ts::test_apply_button_label_count` | 2 toggles | Button text contains `"APPLY 2"` |
| C14 | Apply click populates `forgeStore.appliedPatternIds` | `ContextPanel.test.ts::test_apply_populates_forge_store` | 2 toggles → click | `forgeStore.appliedPatternIds.length === 2`, contains the two IDs |
| C15 | Apply click sets `forgeStore.appliedPatternLabel` to "cluster (N)" form | `ContextPanel.test.ts::test_apply_sets_label` | Cluster label `"API endpoint patterns"`, 2 toggles | `forgeStore.appliedPatternLabel === "API endpoint patterns (2)"` |
| C16 | Apply keeps panel visible with selections locked in | `ContextPanel.test.ts::test_apply_does_not_clear_selection` | Same as C14 | After click, first checkbox still `checked === true` |
| C17 | Collapse toggle collapses panel to 28 px rail | `ContextPanel.test.ts::test_collapse_toggle_narrows_panel` | Click `∨` | Panel root element's `data-collapsed === "true"` |
| C18 | Collapse state persists to `localStorage['synthesis:context_panel_open']` | `ContextPanel.test.ts::test_collapse_state_persists` | Collapse → read localStorage | `localStorage.getItem('synthesis:context_panel_open') === 'false'` |
| C19 | Panel hidden entirely when `forgeStore.status = 'synthesizing'` | `ContextPanel.test.ts::test_panel_hidden_during_synthesis` | Set `forgeStore.status = 'analyzing'` (a synthesizing state) | Panel element not in DOM OR has `aria-hidden="true"` |
| C20 | Empty-cluster state renders when match returns null | `ContextPanel.test.ts::test_empty_match_state` | `suggestion = null`, prompt ≥ 30 chars and a fetch attempt completed | Text `/no match/i` present |
| C21 | In-flight state fades previous match to 0.5 opacity | `ContextPanel.test.ts::test_inflight_fades_previous_match` | Previous suggestion + `inflight = true` | Panel body has `opacity: 0.5` style |
| C22 | Network error renders 1 px neon-red inset contour | `ContextPanel.test.ts::test_network_error_red_contour` | `suggestion` present + `errorState = true` | Header element has `box-shadow: inset 0 0 0 1px` containing `neon-red` token |
| C23 | Reduced-motion collapses transitions to 0.01 ms | `ContextPanel.test.ts::test_reduced_motion_respected` | `@media (prefers-reduced-motion: reduce)` via `window.matchMedia` mock | Panel has `transition-duration: 0.01ms` on computed style |
| C24 | Panel has `role="complementary"` + `aria-label` | `ContextPanel.test.ts::test_accessibility_role_label` | Any suggestion | Panel root has `role === "complementary"` and `aria-label === "Pattern context"` |
| C25 | Collapse button has `aria-expanded` + `aria-controls` | `ContextPanel.test.ts::test_accessibility_collapse_button_aria` | — | Button has `aria-expanded` + `aria-controls` attrs |

### Integration + cleanup TDM

| Req | Behavior under test | File | Fixture/Mock | Primary assertion |
|-----|---------------------|------|--------------|-------------------|
| I1 | `PromptEdit.svelte` no longer mounts `<PatternSuggestion />` | `frontend/src/lib/components/editor/PromptEdit.test.ts::test_no_pattern_suggestion_child` | Render PromptEdit | Query for `PatternSuggestion` component in DOM returns null |
| I2 | `PromptEdit.svelte` still renders the applied-chip below textarea when `forgeStore.appliedPatternIds` is populated | `frontend/src/lib/components/editor/PromptEdit.test.ts::test_applied_chip_still_rendered` | `forgeStore.appliedPatternIds = ['mp-1', 'mp-2']` | Chip element with text matching `/2 patterns/` present |
| I3 | `EditorGroups.svelte` renders a slot for `ContextPanel` at >= 1400 px viewport | `frontend/src/lib/components/layout/EditorGroups.test.ts::test_context_panel_slot_renders_wide_viewport` | Mock `window.innerWidth = 1500` | `ContextPanel` test-id in DOM |
| I4 | `EditorGroups.svelte` collapses ContextPanel to rail at < 1400 px | `EditorGroups.test.ts::test_context_panel_collapses_on_narrow_viewport` | `window.innerWidth = 1280` | Panel has `data-collapsed === "true"` |

---

## File Structure

| File | Role | Change type |
|------|------|-------------|
| `backend/app/routers/clusters.py` (lines 700-719) | Populates new keys in `match_dict` | Modify |
| `backend/tests/test_clusters_router.py` | 5 new test cases (B1–B5) | Modify |
| `frontend/src/lib/api/clusters.ts` (lines 124-135) | Adds `cross_cluster_patterns` + `match_level` to `ClusterMatchResponse.match` | Modify |
| `frontend/src/lib/stores/clusters.svelte.ts` (lines 123-227) | Drops `_skippedClusterId` + `dismissSuggestion()`; extends `ClusterMatch` type | Modify |
| `frontend/src/lib/stores/clusters.svelte.test.ts` (around lines 150-170) | Removes `dismissSuggestion` / `_skippedClusterId` tests; adds S1–S6 | Modify |
| `frontend/src/lib/test-utils.ts` (lines 111-123) | Extends `mockClusterMatch()` with the two new fields (optional overrides) | Modify |
| `frontend/src/lib/components/editor/ContextPanel.svelte` | Main panel component (~220 lines) | Create |
| `frontend/src/lib/components/editor/ContextPanel.test.ts` | 25 test cases (C1–C25) | Create |
| `frontend/src/lib/components/editor/PromptEdit.svelte` (lines 128-131) | Removes `<PatternSuggestion />` mount | Modify |
| `frontend/src/lib/components/editor/PromptEdit.test.ts` | I1 + I2 updates | Modify |
| `frontend/src/lib/components/editor/PatternSuggestion.svelte` | — | Delete |
| `frontend/src/lib/components/editor/PatternSuggestion.test.ts` | — | Delete |
| `frontend/src/lib/components/layout/EditorGroups.svelte` | Add ContextPanel slot in the third column | Modify |
| `frontend/src/lib/components/layout/EditorGroups.test.ts` | I3 + I4 | Modify (or Create if absent) |

---

## Task sequence

Tasks are ordered for incremental green (no step leaves the repo broken). Each task is a small RED-GREEN-REFACTOR cycle plus commit. Expected duration per step: 2-5 minutes.

---

### Task 1 — Backend: add `match_level` to match response (B2 + B3)

**Files:**
- Modify: `backend/app/routers/clusters.py:700-719`
- Test: `backend/tests/test_clusters_router.py`

- [ ] **Step 1.1: Write the failing test**

Append to `backend/tests/test_clusters_router.py` (inside `class TestClusterMatch` — mirror the existing `test_match_cluster` pattern at line 244 that mocks `engine.match_prompt`):

```python
    @pytest.mark.asyncio
    async def test_match_response_includes_match_level(self, app_client):
        """B2+B3: match response exposes match_level ∈ {'family', 'cluster'}.

        Mocks engine.match_prompt() — the test fixture has no embedding
        service (conftest.py:114 sets embedding_service=None), so a real
        similarity match against a seeded cluster isn't achievable.
        """
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "API endpoint patterns"
        mock_cluster.domain = "backend"
        mock_cluster.member_count = 5
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#a855f7"

        mock_result = PatternMatch(
            cluster=mock_cluster, meta_patterns=[], similarity=0.85,
            match_level="cluster",
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "this is a test prompt text"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        body = resp.json()
        assert body["match"] is not None
        assert "match_level" in body["match"], "match_level key missing from response"
        assert body["match"]["match_level"] in {"family", "cluster"}
```

(`MagicMock` + `AsyncMock` are already imported at the top of the existing test file — check `test_clusters_router.py:1-20` to confirm before adding imports.)

- [ ] **Step 1.2: Run the test to verify it fails**

Run: `cd backend && source .venv/bin/activate && pytest tests/test_clusters_router.py::TestClusterMatch::test_match_response_includes_match_level -v 2>&1 | tail -12`

Expected: FAIL — the response's `match` dict won't contain the `match_level` key (because the router doesn't yet populate it). Specific failure: `AssertionError: match_level key missing from response`.

- [ ] **Step 1.3: Populate the new key in the router**

In `backend/app/routers/clusters.py`, within `match_cluster()` after the existing `match_dict["similarity"] = result.similarity` line (line 717), add:

```python
        match_dict["similarity"] = result.similarity
        match_dict["match_level"] = result.match_level
```

(The `result.match_level` field already exists on `PatternMatch` per `backend/app/services/taxonomy/matching.py:99-107` — no backend service change needed.)

- [ ] **Step 1.4: Run the test to verify it passes**

Run: `pytest tests/test_clusters_router.py::TestClusterMatch::test_match_response_includes_match_level -v 2>&1 | tail -6`
Expected: PASS.

- [ ] **Step 1.5: REFACTOR — no cleanup needed, but verify other router tests still green**

Run: `pytest tests/test_clusters_router.py -q 2>&1 | tail -6`
Expected: all PASS.

- [ ] **Step 1.6: Commit**

```bash
git add backend/app/routers/clusters.py backend/tests/test_clusters_router.py
git commit -m "feat(clusters): expose match_level on /api/clusters/match response"
```

---

### Task 2 — Backend: add `cross_cluster_patterns` to match response (B1 + B5)

**Files:**
- Modify: `backend/app/routers/clusters.py:700-719`
- Test: `backend/tests/test_clusters_router.py`

- [ ] **Step 2.1: Write the failing test for non-empty cross-cluster patterns (B1)**

Append inside `class TestClusterMatch`:

```python
    @pytest.mark.asyncio
    async def test_match_response_includes_cross_cluster_patterns(self, app_client):
        """B1: cross_cluster_patterns surfaces from PatternMatch, disjoint from meta_patterns."""
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "JWT validation patterns"
        mock_cluster.domain = "security"
        mock_cluster.member_count = 4
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#ff2255"

        # Meta-pattern on the target cluster.
        meta_mp = MagicMock()
        meta_mp.id = "mp-local"
        meta_mp.pattern_text = "Validate token signature algorithm"
        meta_mp.source_count = 3

        # Cross-cluster (globally-promoted) patterns from sibling clusters.
        gp1 = MagicMock(); gp1.id = "gp-1"; gp1.pattern_text = "Universal A"; gp1.source_count = 5
        gp2 = MagicMock(); gp2.id = "gp-2"; gp2.pattern_text = "Universal B"; gp2.source_count = 4

        mock_result = PatternMatch(
            cluster=mock_cluster,
            meta_patterns=[meta_mp],
            similarity=0.88,
            match_level="cluster",
            cross_cluster_patterns=[gp1, gp2],
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "validate jwt for incoming api requests"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        body = resp.json()
        assert "cross_cluster_patterns" in body["match"], "key missing"
        assert len(body["match"]["cross_cluster_patterns"]) == 2

        # Disjointness guarantee (engine already enforces this per matching.py:404-457,
        # but the router must not accidentally merge them).
        meta_ids = {p["id"] for p in body["match"]["meta_patterns"]}
        cross_ids = {p["id"] for p in body["match"]["cross_cluster_patterns"]}
        assert meta_ids.isdisjoint(cross_ids)
```

- [ ] **Step 2.2: Write the failing test for empty cross-cluster patterns (B5)**

Append inside `class TestClusterMatch`:

```python
    @pytest.mark.asyncio
    async def test_match_response_empty_cross_cluster_patterns(self, app_client):
        """B5: cross_cluster_patterns is always present; [] when engine returns no globals."""
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "Solo cluster"
        mock_cluster.domain = "backend"
        mock_cluster.member_count = 2
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#b44aff"

        mock_result = PatternMatch(
            cluster=mock_cluster, meta_patterns=[], similarity=0.7,
            match_level="cluster", cross_cluster_patterns=[],
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "build a backend service"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        assert resp.json()["match"]["cross_cluster_patterns"] == []
```

- [ ] **Step 2.3: Run both tests to verify they fail**

Run: `pytest tests/test_clusters_router.py::TestClusterMatch::test_match_response_includes_cross_cluster_patterns tests/test_clusters_router.py::TestClusterMatch::test_match_response_empty_cross_cluster_patterns -v 2>&1 | tail -15`
Expected: both FAIL. B1 fails on `assert "cross_cluster_patterns" in body["match"]`; B5 fails on `KeyError: 'cross_cluster_patterns'`.

- [ ] **Step 2.4: Wire `cross_cluster_patterns` through the router**

In `backend/app/routers/clusters.py`, extend the `match_dict` population (right after the `match_level` line from Task 1):

```python
        match_dict["match_level"] = result.match_level
        match_dict["cross_cluster_patterns"] = [
            {"id": p.id, "pattern_text": p.pattern_text, "source_count": p.source_count}
            for p in (result.cross_cluster_patterns or [])
        ]
```

(`result.cross_cluster_patterns` is already populated by `engine.match_prompt()` per `matching.py:404-457`. Default `field(default_factory=list)` in `PatternMatch` means legacy engine paths that don't set it still decode cleanly.)

- [ ] **Step 2.5: Run the two tests to verify they pass**

Run: `pytest tests/test_clusters_router.py::TestClusterMatch::test_match_response_includes_cross_cluster_patterns tests/test_clusters_router.py::TestClusterMatch::test_match_response_empty_cross_cluster_patterns -v 2>&1 | tail -10`
Expected: both PASS.

- [ ] **Step 2.6: Run the whole router test file to confirm no regression**

Run: `pytest tests/test_clusters_router.py -q 2>&1 | tail -6`
Expected: all PASS.

- [ ] **Step 2.7: Commit**

```bash
git add backend/app/routers/clusters.py backend/tests/test_clusters_router.py
git commit -m "feat(clusters): expose cross_cluster_patterns on /api/clusters/match response"
```

---

### Task 3 — Backend regression: verify existing match-response fields unchanged (B4)

**Files:**
- Test only: `backend/tests/test_clusters_router.py`

- [ ] **Step 3.1: Write the regression test**

Append inside `class TestClusterMatch`:

```python
    @pytest.mark.asyncio
    async def test_match_response_preserves_existing_fields(self, app_client):
        """B4: additive delta must not remove or rename any pre-existing field.

        Locks the contract so a future refactor of match_dict assembly can't
        silently break consumers.
        """
        from app.main import app
        from app.services.taxonomy.matching import PatternMatch

        mock_cluster = MagicMock()
        mock_cluster.id = "c1"
        mock_cluster.label = "Test cluster"
        mock_cluster.domain = "backend"
        mock_cluster.member_count = 3
        mock_cluster.task_type = "coding"
        mock_cluster.usage_count = 0
        mock_cluster.avg_score = 0.0
        mock_cluster.created_at = None
        mock_cluster.color_hex = "#b44aff"

        mock_result = PatternMatch(
            cluster=mock_cluster, meta_patterns=[], similarity=0.75,
            match_level="cluster", cross_cluster_patterns=[],
        )
        mock_engine = MagicMock()
        mock_engine.match_prompt = AsyncMock(return_value=mock_result)
        app.state.taxonomy_engine = mock_engine

        try:
            resp = await app_client.post(
                "/api/clusters/match",
                json={"prompt_text": "write a function that validates email"},
            )
        finally:
            del app.state.taxonomy_engine

        assert resp.status_code == 200
        match = resp.json()["match"]
        assert match is not None

        # Pre-existing top-level keys must still be present.
        assert "cluster" in match
        assert "meta_patterns" in match
        assert "similarity" in match

        # Pre-existing cluster sub-keys.
        cl = match["cluster"]
        for key in ("id", "label", "domain", "member_count"):
            assert key in cl, f"missing pre-existing key: {key}"
```

- [ ] **Step 3.2: Run the test to verify it passes immediately**

Run: `pytest tests/test_clusters_router.py::TestClusterMatch::test_match_response_preserves_existing_fields -v 2>&1 | tail -6`
Expected: PASS (regression guard — if it fails after Tasks 1-2, the additive delta silently broke a pre-existing key).

- [ ] **Step 3.3: Commit**

```bash
git add backend/tests/test_clusters_router.py
git commit -m "test(clusters): regression guard — match response preserves pre-existing fields"
```

---

### Task 4 — Frontend types: extend `ClusterMatchResponse.match` (S4 + S5 + S6)

**Files:**
- Modify: `frontend/src/lib/api/clusters.ts:124-135`
- Modify: `frontend/src/lib/test-utils.ts:111-123`

- [ ] **Step 4.1: Write the failing type-decode tests (use the existing `mockFetch` pattern)**

The existing test file imports `mockFetch` from `$lib/test-utils` and uses it throughout (see lines 4, 38, 49, etc.). **Use the same pattern — don't mix in `vi.spyOn(clusterApi)`** for consistency. Append to `frontend/src/lib/stores/clusters.svelte.test.ts` at the end:

```typescript
describe('ClusterMatch type extensions', () => {
  beforeEach(() => {
    clustersStore._reset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('carries cross_cluster_patterns through the store (S4)', async () => {
    mockFetch([
      {
        match: {
          cluster: { id: 'c1', label: 'L1', domain: 'backend', member_count: 3 },
          meta_patterns: [{ id: 'mp1', pattern_text: 'p', source_count: 1 }],
          similarity: 0.9,
          cross_cluster_patterns: [{ id: 'gp1', pattern_text: 'g', source_count: 5 }],
          match_level: 'cluster',
        },
      },
    ]);
    clustersStore.checkForPatterns('x'.repeat(60));
    await vi.advanceTimersByTimeAsync(900);
    expect(clustersStore.suggestion?.cross_cluster_patterns).toEqual([
      { id: 'gp1', pattern_text: 'g', source_count: 5 },
    ]);
  });

  it('carries match_level through the store (S5)', async () => {
    mockFetch([
      {
        match: {
          cluster: { id: 'c1', label: 'L1', domain: 'backend', member_count: 3 },
          meta_patterns: [{ id: 'mp1', pattern_text: 'p', source_count: 1 }],
          similarity: 0.9,
          cross_cluster_patterns: [],
          match_level: 'family',
        },
      },
    ]);
    clustersStore.checkForPatterns('x'.repeat(60));
    await vi.advanceTimersByTimeAsync(900);
    expect(clustersStore.suggestion?.match_level).toBe('family');
  });

  it('applies defaults when legacy response omits the new keys (S6)', async () => {
    mockFetch([
      {
        match: {
          cluster: { id: 'c1', label: 'L1', domain: 'backend', member_count: 3 },
          meta_patterns: [{ id: 'mp1', pattern_text: 'p', source_count: 1 }],
          similarity: 0.9,
          // cross_cluster_patterns + match_level intentionally omitted
        },
      },
    ]);
    clustersStore.checkForPatterns('x'.repeat(60));
    await vi.advanceTimersByTimeAsync(900);
    expect(clustersStore.suggestion?.cross_cluster_patterns).toEqual([]);
    expect(clustersStore.suggestion?.match_level).toBe('cluster');
  });
});
```

(`mockFetch`, `mockClusterMatch`, `mockMetaPattern` are already imported at line 4. `vi` is imported for `useFakeTimers` — if not already, add `import { vi } from 'vitest';` at the top.)

- [ ] **Step 4.2: Run the test — expect FAIL**

Run: `cd frontend && npm run test -- clusters.svelte.test.ts 2>&1 | tail -20`
Expected: FAILs because `cross_cluster_patterns` is not a property on `ClusterMatch`.

- [ ] **Step 4.3: Extend the API type**

Replace `ClusterMatchResponse` in `frontend/src/lib/api/clusters.ts` (lines 124-135) with:

```typescript
export interface ClusterMatchResponse {
  match: {
    cluster: {
      id: string;
      label: string;
      domain: string;
      member_count: number;
    };
    meta_patterns: MetaPatternItem[];
    similarity: number;
    // Added in Tier 1 — defensively defaulted for legacy responses
    cross_cluster_patterns?: MetaPatternItem[];
    match_level?: 'family' | 'cluster';
  } | null;
}
```

- [ ] **Step 4.4: Apply defaults in the store when decoding**

In `frontend/src/lib/stores/clusters.svelte.ts`, find the `if (resp.match && resp.match.meta_patterns.length > 0)` branch of `checkForPatterns` (around line 188). Replace its body with:

```typescript
        if (resp.match && resp.match.meta_patterns.length > 0) {
          // Defensive defaults for legacy responses (backwards compat).
          this.suggestion = {
            ...resp.match,
            cross_cluster_patterns: resp.match.cross_cluster_patterns ?? [],
            match_level: resp.match.match_level ?? 'cluster',
          };
          this.suggestionVisible = true;
        }
```

- [ ] **Step 4.5: Extend the `ClusterMatch` exported type**

In the same file, find the `export type ClusterMatch = NonNullable<ClusterMatchResponse['match']>;` line (around line 29) and replace with:

```typescript
export type ClusterMatch = NonNullable<ClusterMatchResponse['match']> & {
  // Narrowed from optional to required after store-side defaulting
  cross_cluster_patterns: MetaPatternItem[];
  match_level: 'family' | 'cluster';
};
```

Also import `MetaPatternItem` at the top of the file if it isn't already:

```typescript
import type { MetaPatternItem } from '$lib/api/clusters';
```

- [ ] **Step 4.6: Extend `mockClusterMatch()` in test-utils**

In `frontend/src/lib/test-utils.ts`, replace the `mockClusterMatch` function:

```typescript
export function mockClusterMatch(overrides: Record<string, unknown> = {}) {
  return {
    cluster: {
      id: 'fam-1',
      label: 'API endpoint patterns',
      domain: 'backend',
      member_count: 3,
    },
    meta_patterns: [mockMetaPattern()],
    similarity: 0.85,
    cross_cluster_patterns: [],
    match_level: 'cluster' as const,
    ...overrides,
  };
}
```

- [ ] **Step 4.7: Run the three S4-S6 tests to verify they pass**

Run: `npm run test -- clusters.svelte.test.ts -t 'ClusterMatch type' 2>&1 | tail -12`
Expected: 3 PASS.

- [ ] **Step 4.8: Run `npm run check` to verify TS types are clean**

Run: `cd frontend && npm run check 2>&1 | tail -5`
Expected: 0 errors.

- [ ] **Step 4.9: Commit**

```bash
git add frontend/src/lib/api/clusters.ts frontend/src/lib/stores/clusters.svelte.ts frontend/src/lib/stores/clusters.svelte.test.ts frontend/src/lib/test-utils.ts
git commit -m "feat(clusters): extend ClusterMatch type with cross_cluster_patterns + match_level"
```

---

### Task 5 — Frontend store: drop `_skippedClusterId` + `dismissSuggestion()` (S1 + S2)

**Files:**
- Modify: `frontend/src/lib/stores/clusters.svelte.ts`
- Modify: `frontend/src/lib/stores/clusters.svelte.test.ts`

**Note**: the plan originally included an S3 "re-match same cluster after skip" test, but validation surfaced that it would pass in the pre-removal state (different text always re-matches anyway). Without the deleted `dismissSuggestion()` method available to explicitly set `_skippedClusterId`, there's no clean way to prove the gate removal end-to-end. S1+S2 are sufficient: they directly assert the state/method are gone, which is the contract. Behavioural surfacing is covered implicitly by all later tests — if a future commit re-introduces a skip gate, Tasks 6-14 tests that rely on `suggestion` being populated after multiple `checkForPatterns` will fail.

- [ ] **Step 5.1: Write the failing removal tests**

Append to `clusters.svelte.test.ts`:

```typescript
describe('skipped-cluster state removal (Tier 1)', () => {
  beforeEach(() => {
    clustersStore._reset();
  });

  it('has no _skippedClusterId field (S1)', () => {
    expect('_skippedClusterId' in clustersStore).toBe(false);
  });

  it('has no dismissSuggestion method (S2)', () => {
    expect(typeof (clustersStore as any).dismissSuggestion).toBe('undefined');
  });
});
```

- [ ] **Step 5.2: Delete the `dismissSuggestion` + `_skippedClusterId` tests from the file**

In `clusters.svelte.test.ts`, find and delete any `describe('dismissSuggestion', ...)` or tests that reference `_skippedClusterId` or call `dismissSuggestion()`. These were ~around lines 158-162 but scan the full file — remove any hit.

- [ ] **Step 5.3: Run the removal tests to verify they fail**

Run: `npm run test -- clusters.svelte.test.ts -t 'skipped-cluster state removal' 2>&1 | tail -12`
Expected: 2 FAILs — `_skippedClusterId` still present, `dismissSuggestion` still defined.

- [ ] **Step 5.4: Remove `_skippedClusterId` from the store**

In `frontend/src/lib/stores/clusters.svelte.ts`:

1. Delete the line: `private _skippedClusterId: string | null = null;  // prevent re-showing skipped suggestion` (around line 125).
2. Inside `checkForPatterns`, delete the three lines referencing `_skippedClusterId`:
   - `if (this._skippedClusterId === resp.match.cluster.id) return;` (around line 190)
3. Delete the entire `dismissSuggestion()` method (around lines 221-227).
4. In `_reset()` (or wherever exists near line 410-411), remove any `this._skippedClusterId = null;` line.

After edits, the `checkForPatterns` "if match" branch reads:

```typescript
        if (resp.match && resp.match.meta_patterns.length > 0) {
          // Defensive defaults for legacy responses (backwards compat).
          this.suggestion = {
            ...resp.match,
            cross_cluster_patterns: resp.match.cross_cluster_patterns ?? [],
            match_level: resp.match.match_level ?? 'cluster',
          };
          this.suggestionVisible = true;
        } else {
          this.suggestion = null;
          this.suggestionVisible = false;
        }
```

- [ ] **Step 5.5: Run the removal tests to verify they pass**

Run: `npm run test -- clusters.svelte.test.ts -t 'skipped-cluster state removal' 2>&1 | tail -12`
Expected: 3 PASS.

- [ ] **Step 5.6: Run the whole file to confirm nothing else broke**

Run: `npm run test -- clusters.svelte.test.ts 2>&1 | tail -10`
Expected: all tests pass (the old `dismissSuggestion` tests were deleted in Step 5.2).

- [ ] **Step 5.7: Run `npm run check`**

Run: `cd frontend && npm run check 2>&1 | tail -5`
Expected: 0 TS errors.

- [ ] **Step 5.8: Commit**

```bash
git add frontend/src/lib/stores/clusters.svelte.ts frontend/src/lib/stores/clusters.svelte.test.ts
git commit -m "refactor(clusters): drop _skippedClusterId gate + dismissSuggestion()"
```

---

### Task 6 — `ContextPanel.svelte` skeleton: empty-state rendering (C1)

**Files:**
- Create: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Create: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 6.1: Write the failing empty-state test**

Create `frontend/src/lib/components/editor/ContextPanel.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, cleanup } from '@testing-library/svelte';
import ContextPanel from './ContextPanel.svelte';
import { clustersStore } from '$lib/stores/clusters.svelte';
import { forgeStore } from '$lib/stores/forge.svelte';
import { mockClusterMatch, mockMetaPattern } from '$lib/test-utils';
// `mockMetaPattern` is used from Task 8 onward; import up front so later
// test additions don't need to edit imports each time.

describe('ContextPanel', () => {
  beforeEach(() => {
    clustersStore._reset();
    forgeStore.status = 'idle';
    forgeStore.appliedPatternIds = null;
    forgeStore.appliedPatternLabel = null;
    localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  describe('empty state', () => {
    it('renders "waiting for prompt" when suggestion is null (C1)', () => {
      clustersStore.suggestion = null;
      clustersStore.suggestionVisible = false;
      render(ContextPanel);
      expect(screen.getByText(/waiting for prompt/i)).toBeTruthy();
    });
  });
});
```

- [ ] **Step 6.2: Run the test — expect failure because the component doesn't exist**

Run: `cd frontend && npm run test -- ContextPanel.test.ts 2>&1 | tail -6`
Expected: FAIL — cannot resolve `./ContextPanel.svelte`.

- [ ] **Step 6.3: Create the minimal `ContextPanel.svelte`**

Create `frontend/src/lib/components/editor/ContextPanel.svelte`:

```svelte
<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';

  const hasSuggestion = $derived(clustersStore.suggestion !== null);
</script>

<aside
  class="context-panel"
  role="complementary"
  aria-label="Pattern context"
>
  <header class="panel-header">
    <span class="panel-title">CONTEXT</span>
  </header>

  {#if !hasSuggestion}
    <div class="empty-state">
      <p class="empty-copy">Start typing to see related clusters and patterns.</p>
      <p class="empty-sub">Waiting for prompt — at least 30 characters.</p>
    </div>
  {/if}
</aside>

<style>
  .context-panel {
    display: flex;
    flex-direction: column;
    width: 240px;
    height: 100%;
    background: var(--color-bg-secondary);
    border-left: 1px solid var(--color-border-subtle);
    font-family: var(--font-sans);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 24px;
    padding: 0 6px;
    font-family: var(--font-display);
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
    border-bottom: 1px solid var(--color-border-subtle);
  }

  .empty-state {
    padding: 12px 6px;
    color: var(--color-text-secondary);
    font-size: 11px;
  }

  .empty-copy { margin: 0 0 4px 0; }
  .empty-sub { margin: 0; color: var(--color-text-dim); font-size: 10px; }
</style>
```

- [ ] **Step 6.4: Run the test to verify PASS**

Run: `npm run test -- ContextPanel.test.ts 2>&1 | tail -6`
Expected: 1 PASS.

- [ ] **Step 6.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel skeleton with empty-state rendering"
```

---

### Task 7 — Cluster-identity row (C2, C3, C4, C5)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 7.1: Write the 4 failing tests**

Append inside the outer `describe('ContextPanel', ...)` block:

```typescript
  describe('cluster identity row', () => {
    function makeSuggestion(overrides: Record<string, unknown> = {}) {
      return mockClusterMatch(overrides);
    }

    it('renders the cluster label (C2)', () => {
      clustersStore.suggestion = makeSuggestion({
        cluster: { id: 'c1', label: 'API endpoint patterns', domain: 'backend', member_count: 5 },
      });
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText('API endpoint patterns')).toBeTruthy();
    });

    it('renders similarity as an integer percentage (C3)', () => {
      clustersStore.suggestion = makeSuggestion({ similarity: 0.842 });
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText(/84%/)).toBeTruthy();
    });

    it('renders the match_level (C4)', () => {
      clustersStore.suggestion = makeSuggestion({ match_level: 'family' });
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      expect(screen.getByText(/family/i)).toBeTruthy();
    });

    it('renders a domain dot styled with taxonomyColor (C5)', () => {
      clustersStore.suggestion = makeSuggestion({
        cluster: { id: 'c1', label: 'x', domain: 'backend', member_count: 2 },
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const dot = container.querySelector('[data-test="domain-dot"]') as HTMLElement | null;
      expect(dot).not.toBeNull();
      // taxonomyColor('backend') resolves to a neon-violet hex; assert non-empty bg.
      expect(dot!.style.backgroundColor || dot!.getAttribute('style')).toMatch(/#|rgb/);
    });
  });
```

- [ ] **Step 7.2: Run the tests to verify they fail**

Run: `npm run test -- ContextPanel.test.ts 2>&1 | tail -15`
Expected: 4 FAIL with "unable to find element".

- [ ] **Step 7.3: Add the cluster-identity row to the component**

In `ContextPanel.svelte`, after the `import`s, add:

```svelte
<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { taxonomyColor } from '$lib/utils/colors';

  const suggestion = $derived(clustersStore.suggestion);
  const hasSuggestion = $derived(suggestion !== null);
</script>
```

Then inside the `<aside>`, replace the current `{#if !hasSuggestion}` block with:

```svelte
  {#if !hasSuggestion}
    <div class="empty-state">
      <p class="empty-copy">Start typing to see related clusters and patterns.</p>
      <p class="empty-sub">Waiting for prompt — at least 30 characters.</p>
    </div>
  {:else if suggestion}
    <section class="identity-row" aria-label="Matched cluster">
      <div class="identity-primary">
        <span
          class="domain-dot"
          data-test="domain-dot"
          style="background-color: {taxonomyColor(suggestion.cluster.domain)};"
        ></span>
        <span class="cluster-label">{suggestion.cluster.label}</span>
      </div>
      <div class="identity-meta">
        <span class="similarity">matched {Math.round(suggestion.similarity * 100)}%</span>
        <span class="meta-sep">·</span>
        <span class="match-level">{suggestion.match_level}</span>
      </div>
    </section>
  {/if}
```

Add to the `<style>` block:

```css
  .identity-row {
    padding: 4px 6px;
    border-bottom: 1px solid var(--color-border-subtle);
  }
  .identity-primary {
    display: flex;
    align-items: center;
    gap: 6px;
    height: 20px;
    color: var(--color-text-primary);
  }
  .domain-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    flex-shrink: 0;
  }
  .cluster-label {
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .identity-meta {
    height: 18px;
    display: flex;
    align-items: center;
    gap: 4px;
    font-family: var(--font-mono);
    font-size: 10px;
    color: var(--color-text-dim);
  }
  .meta-sep { color: var(--color-text-dim); }
  .match-level { font-variant: tabular-nums; }
```

- [ ] **Step 7.4: Run the 4 tests to verify PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'cluster identity row' 2>&1 | tail -10`
Expected: 4 PASS.

- [ ] **Step 7.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel renders cluster identity row"
```

---

### Task 8 — Meta-patterns section + checkbox toggles (C6, C9, C10, C11)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 8.1: Write the 4 failing tests**

Append to the outer `describe('ContextPanel', ...)`:

```typescript
  describe('meta-patterns section', () => {
    it('renders one checkbox per meta-pattern (C6)', () => {
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const meta = container.querySelector('[data-test="meta-section"]') as HTMLElement;
      expect(meta.querySelectorAll('input[type="checkbox"]').length).toBe(3);
    });

    it('truncates pattern text longer than 60 chars (C9)', () => {
      const long = 'a'.repeat(80);
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [mockMetaPattern({ id: 'mp1', pattern_text: long, source_count: 1 })],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const row = container.querySelector('[data-test="pattern-row"]') as HTMLElement;
      const txt = row.querySelector('.pattern-text')!.textContent ?? '';
      expect(txt.endsWith('…') || txt.endsWith('...')).toBe(true);
      // Truncation contract: slice(0, n-1) + '…' = exactly n chars for any input > n.
      expect(txt.length).toBe(60);
    });

    it('toggles selection on checkbox click (C10)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      expect((checkboxes[0] as HTMLInputElement).checked).toBe(true);
      expect(screen.getByText('1/3 ✔')).toBeTruthy();
    });

    it('renders "N/M ✔" selection counter (C11)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch({
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      expect(screen.getByText('2/3 ✔')).toBeTruthy();
    });
  });
```

- [ ] **Step 8.2: Run the tests to verify they fail**

Run: `npm run test -- ContextPanel.test.ts -t 'meta-patterns section' 2>&1 | tail -15`
Expected: 4 FAIL.

- [ ] **Step 8.3: Implement the section in the component**

In `ContextPanel.svelte`, inside `<script>`, add state + helpers:

```svelte
<script lang="ts">
  import { clustersStore } from '$lib/stores/clusters.svelte';
  import { forgeStore } from '$lib/stores/forge.svelte';
  import { taxonomyColor } from '$lib/utils/colors';

  const suggestion = $derived(clustersStore.suggestion);
  const hasSuggestion = $derived(suggestion !== null);

  let selectedIds = $state<Set<string>>(new Set());

  // Seed selection from forgeStore on mount / when suggestion changes.
  $effect(() => {
    const initial = forgeStore.appliedPatternIds ?? [];
    selectedIds = new Set(initial);
  });

  function toggle(id: string) {
    const next = new Set(selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    selectedIds = next;
  }

  function truncate(text: string, n: number): string {
    return text.length <= n ? text : text.slice(0, n - 1) + '…';
  }

  const metaPatterns = $derived(suggestion?.meta_patterns ?? []);
  const metaSelectedCount = $derived(
    metaPatterns.filter((p) => selectedIds.has(p.id)).length,
  );
</script>
```

Then inside `{:else if suggestion}` after the `identity-row` section, add:

```svelte
      <section class="pattern-section" data-test="meta-section" aria-label="Meta-patterns">
        <header class="section-heading">
          <span class="section-title">META-PATTERNS</span>
          <span class="section-count" class:section-count--active={metaSelectedCount > 0}>
            {metaSelectedCount}/{metaPatterns.length}{metaSelectedCount > 0 ? ' ✔' : ''}
          </span>
        </header>
        <ul class="pattern-list">
          {#each metaPatterns as p (p.id)}
            <li class="pattern-row" data-test="pattern-row">
              <label class="pattern-label">
                <input
                  type="checkbox"
                  checked={selectedIds.has(p.id)}
                  onchange={() => toggle(p.id)}
                  aria-describedby="pattern-{p.id}-text"
                />
                <span id="pattern-{p.id}-text" class="pattern-text">{truncate(p.pattern_text, 60)}</span>
              </label>
            </li>
          {/each}
        </ul>
      </section>
```

Append to `<style>`:

```css
  .pattern-section { border-bottom: 1px solid var(--color-border-subtle); }
  .section-heading {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 20px;
    padding: 0 6px;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-text-dim);
  }
  .section-count {
    font-family: var(--font-mono);
    font-size: 10px;
  }
  .section-count--active { color: var(--color-neon-cyan); }
  .pattern-list { list-style: none; padding: 0; margin: 0; }
  .pattern-row { height: 20px; border-top: 1px solid var(--color-border-subtle); padding: 0 6px; }
  .pattern-label { display: flex; align-items: center; gap: 6px; height: 20px; cursor: pointer; }
  .pattern-label input[type="checkbox"] {
    appearance: none;
    width: 10px;
    height: 10px;
    margin: 0;
    border: 1px solid var(--color-border-subtle);
    background: transparent;
    cursor: pointer;
  }
  .pattern-label input[type="checkbox"]:hover {
    border-color: var(--color-neon-cyan);
  }
  .pattern-label input[type="checkbox"]:checked {
    border-color: var(--color-neon-cyan);
    background: color-mix(in srgb, var(--color-neon-cyan) 12%, transparent);
  }
  .pattern-text {
    font-size: 11px;
    color: var(--color-text-primary);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
```

- [ ] **Step 8.4: Run the 4 tests to verify PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'meta-patterns section' 2>&1 | tail -12`
Expected: 4 PASS.

- [ ] **Step 8.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel meta-patterns section with checkbox selection"
```

---

### Task 9 — Global section + purple left border (C7, C8)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 9.1: Write the 2 failing tests**

Append:

```typescript
  describe('global section', () => {
    it('renders GLOBAL heading and cross-cluster pattern rows (C7)', () => {
      clustersStore.suggestion = mockClusterMatch({
        cross_cluster_patterns: [
          mockMetaPattern({ id: 'gp1', pattern_text: 'Universal practice A', source_count: 5 }),
          mockMetaPattern({ id: 'gp2', pattern_text: 'Universal practice B', source_count: 4 }),
        ],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      expect(screen.getByText('GLOBAL')).toBeTruthy();
      const global = container.querySelector('[data-test="global-section"]') as HTMLElement;
      expect(global.querySelectorAll('input[type="checkbox"]').length).toBe(2);
    });

    it('global section has neon-purple left border (C8)', () => {
      clustersStore.suggestion = mockClusterMatch({
        cross_cluster_patterns: [mockMetaPattern({ id: 'gp1', pattern_text: 'P', source_count: 5 })],
      });
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const global = container.querySelector('[data-test="global-section"]') as HTMLElement;
      const style = getComputedStyle(global).borderLeft;
      // jsdom resolves CSS variables — the computed value should at least include "1px" + a color.
      expect(style).toMatch(/1px/);
    });
  });
```

- [ ] **Step 9.2: Run the tests — expect FAIL**

Run: `npm run test -- ContextPanel.test.ts -t 'global section' 2>&1 | tail -8`
Expected: 2 FAIL.

- [ ] **Step 9.3: Implement the global section**

In the `<script>` of `ContextPanel.svelte`, add:

```svelte
  const globalPatterns = $derived(suggestion?.cross_cluster_patterns ?? []);
  const globalSelectedCount = $derived(
    globalPatterns.filter((p) => selectedIds.has(p.id)).length,
  );
```

After the meta-patterns `<section>` block, add:

```svelte
      {#if globalPatterns.length > 0}
        <section class="pattern-section pattern-section--global" data-test="global-section" aria-label="Global patterns">
          <header class="section-heading">
            <span class="section-title">GLOBAL</span>
            <span class="section-count" class:section-count--active={globalSelectedCount > 0}>
              {globalSelectedCount}/{globalPatterns.length}{globalSelectedCount > 0 ? ' ✔' : ''}
            </span>
          </header>
          <ul class="pattern-list">
            {#each globalPatterns as p (p.id)}
              <li class="pattern-row" data-test="pattern-row">
                <label class="pattern-label">
                  <input
                    type="checkbox"
                    checked={selectedIds.has(p.id)}
                    onchange={() => toggle(p.id)}
                    aria-describedby="pattern-{p.id}-text"
                  />
                  <span id="pattern-{p.id}-text" class="pattern-text">{truncate(p.pattern_text, 60)}</span>
                </label>
              </li>
            {/each}
          </ul>
        </section>
      {/if}
```

Append to `<style>`:

```css
  .pattern-section--global {
    border-left: 1px solid var(--color-neon-purple);
  }
```

- [ ] **Step 9.4: Run the 2 tests to verify PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'global section' 2>&1 | tail -6`
Expected: 2 PASS.

- [ ] **Step 9.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel global section with neon-purple left border"
```

---

### Task 10 — Apply button (C12, C13, C14, C15, C16)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 10.1: Write the 5 failing tests**

Append to `ContextPanel.test.ts`:

```typescript
  describe('apply button', () => {
    function mountWithThreeMeta() {
      clustersStore.suggestion = mockClusterMatch({
        cluster: { id: 'c1', label: 'API endpoint patterns', domain: 'backend', member_count: 3 },
        meta_patterns: [
          mockMetaPattern({ id: 'mp1', pattern_text: 'A', source_count: 1 }),
          mockMetaPattern({ id: 'mp2', pattern_text: 'B', source_count: 1 }),
          mockMetaPattern({ id: 'mp3', pattern_text: 'C', source_count: 1 }),
        ],
      });
      clustersStore.suggestionVisible = true;
    }

    it('apply button disabled when selection is empty (C12)', () => {
      mountWithThreeMeta();
      render(ContextPanel);
      const btn = screen.getByRole('button', { name: /apply/i });
      expect((btn as HTMLButtonElement).disabled).toBe(true);
    });

    it('apply button label reflects selection count (C13)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      expect(screen.getByRole('button', { name: /apply 2/i })).toBeTruthy();
    });

    it('apply click populates forgeStore.appliedPatternIds (C14)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[2] as HTMLElement);
      await user.click(screen.getByRole('button', { name: /apply 2/i }));
      expect(forgeStore.appliedPatternIds?.sort()).toEqual(['mp1', 'mp3']);
    });

    it('apply click sets appliedPatternLabel to "cluster (N)" (C15)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(checkboxes[1] as HTMLElement);
      await user.click(screen.getByRole('button', { name: /apply 2/i }));
      expect(forgeStore.appliedPatternLabel).toBe('API endpoint patterns (2)');
    });

    it('apply click does not clear selection — panel remains visible with checkboxes locked (C16)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      mountWithThreeMeta();
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const checkboxes = container.querySelectorAll('[data-test="meta-section"] input[type="checkbox"]');
      await user.click(checkboxes[0] as HTMLElement);
      await user.click(screen.getByRole('button', { name: /apply 1/i }));
      expect((checkboxes[0] as HTMLInputElement).checked).toBe(true);
    });
  });
```

- [ ] **Step 10.2: Run the tests — expect all FAIL**

Run: `npm run test -- ContextPanel.test.ts -t 'apply button' 2>&1 | tail -12`
Expected: 5 FAIL.

- [ ] **Step 10.3: Implement the apply button**

In `ContextPanel.svelte` `<script>`, add:

```svelte
  const totalSelected = $derived(metaSelectedCount + globalSelectedCount);

  function apply() {
    if (totalSelected === 0 || !suggestion) return;
    forgeStore.appliedPatternIds = Array.from(selectedIds);
    forgeStore.appliedPatternLabel = `${suggestion.cluster.label} (${totalSelected})`;
  }
```

After the `{#if globalPatterns.length > 0}` block, before the closing `{/if}` of `{:else if suggestion}`, add:

```svelte
      <footer class="apply-footer">
        <button
          type="button"
          class="apply-btn"
          disabled={totalSelected === 0}
          onclick={apply}
        >
          APPLY {totalSelected}
        </button>
      </footer>
```

Append to `<style>`:

```css
  .apply-footer {
    padding: 4px 6px;
    display: flex;
    justify-content: flex-end;
  }
  .apply-btn {
    height: 20px;
    padding: 0 8px;
    font-family: var(--font-display);
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--color-neon-cyan);
    background: transparent;
    border: 1px solid var(--color-neon-cyan);
    cursor: pointer;
    transition: all 180ms cubic-bezier(0.16, 1, 0.3, 1);
  }
  .apply-btn:hover:not(:disabled) {
    transform: translateY(-1px);
    background: color-mix(in srgb, var(--color-neon-cyan) 6%, transparent);
  }
  .apply-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
```

- [ ] **Step 10.4: Run the 5 tests to verify PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'apply button' 2>&1 | tail -10`
Expected: 5 PASS.

- [ ] **Step 10.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel APPLY button populates forgeStore.appliedPatternIds"
```

---

### Task 11 — Collapse / expand + localStorage persistence (C17, C18)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 11.1: Write the 2 failing tests**

Append:

```typescript
  describe('collapse / expand', () => {
    it('collapse toggle narrows panel (C17)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const user = userEvent.setup();
      const panel = container.querySelector('[data-test="context-panel"]') as HTMLElement;
      expect(panel.getAttribute('data-collapsed')).toBe('false');
      await user.click(screen.getByRole('button', { name: /collapse/i }));
      expect(panel.getAttribute('data-collapsed')).toBe('true');
    });

    it('collapse state persists to localStorage (C18)', async () => {
      const userEvent = (await import('@testing-library/user-event')).default;
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      const user = userEvent.setup();
      await user.click(screen.getByRole('button', { name: /collapse/i }));
      expect(localStorage.getItem('synthesis:context_panel_open')).toBe('false');
    });
  });
```

- [ ] **Step 11.2: Run tests — expect FAIL**

Run: `npm run test -- ContextPanel.test.ts -t 'collapse / expand' 2>&1 | tail -6`
Expected: 2 FAIL.

- [ ] **Step 11.3: Implement collapse + persistence**

In `ContextPanel.svelte` `<script>`, add:

```svelte
  const STORAGE_KEY = 'synthesis:context_panel_open';

  let isOpen = $state<boolean>(
    typeof localStorage !== 'undefined'
      ? (localStorage.getItem(STORAGE_KEY) ?? 'true') !== 'false'
      : true,
  );

  function toggleCollapse() {
    isOpen = !isOpen;
    try {
      localStorage.setItem(STORAGE_KEY, String(isOpen));
    } catch {
      /* ignore — private browsing etc. */
    }
  }
```

Replace the `<aside>` tag with:

```svelte
<aside
  class="context-panel"
  class:context-panel--collapsed={!isOpen}
  role="complementary"
  aria-label="Pattern context"
  data-test="context-panel"
  data-collapsed={!isOpen}
>
```

Replace the `<header>` contents:

```svelte
  <header class="panel-header">
    <span class="panel-title">CONTEXT</span>
    <button
      type="button"
      class="collapse-btn"
      onclick={toggleCollapse}
      aria-expanded={isOpen}
      aria-controls="context-panel-body"
      aria-label={isOpen ? 'Collapse pattern context' : 'Expand pattern context'}
    >
      {isOpen ? '∨' : '∧'}
    </button>
  </header>
  <div id="context-panel-body" class="panel-body" hidden={!isOpen}>
```

At the very end of the `<aside>` body, close the new `<div>`:

```svelte
  </div>
</aside>
```

Append to `<style>`:

```css
  .context-panel--collapsed { width: 28px; }
  .collapse-btn {
    height: 20px;
    padding: 0 4px;
    background: transparent;
    border: none;
    color: var(--color-text-dim);
    cursor: pointer;
    font-family: var(--font-mono);
  }
  .collapse-btn:hover { color: var(--color-text-primary); }
```

- [ ] **Step 11.4: Run the 2 tests to verify PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'collapse / expand' 2>&1 | tail -6`
Expected: 2 PASS.

- [ ] **Step 11.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel collapse/expand with localStorage persistence"
```

---

### Task 12 — Hide panel during synthesis (C19)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 12.1: Write the failing test**

```typescript
  describe('synthesis gating', () => {
    it('hides panel when forgeStore.status === "analyzing" (C19)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      forgeStore.status = 'analyzing';
      const { container } = render(ContextPanel);
      const panel = container.querySelector('[data-test="context-panel"]');
      expect(panel === null || panel.getAttribute('aria-hidden') === 'true').toBe(true);
    });
  });
```

- [ ] **Step 12.2: Run — expect FAIL**

Run: `npm run test -- ContextPanel.test.ts -t 'synthesis gating' 2>&1 | tail -6`
Expected: FAIL.

- [ ] **Step 12.3: Implement**

In `<script>`:

```svelte
  const SYNTHESIS_STATES = new Set(['analyzing', 'optimizing', 'scoring', 'forging']);
  const isSynthesizing = $derived(SYNTHESIS_STATES.has(forgeStore.status));
```

Wrap the `<aside>` in `{#if !isSynthesizing}`:

```svelte
{#if !isSynthesizing}
  <aside
    class="context-panel"
    ...
```

Close with `{/if}` at the very bottom (before `<style>`).

- [ ] **Step 12.4: Run — expect PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'synthesis gating' 2>&1 | tail -6`
Expected: PASS.

- [ ] **Step 12.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel hides during synthesis"
```

---

### Task 13 — Accessibility + reduced-motion (C23, C24, C25)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 13.1: Write 3 failing tests**

```typescript
  describe('accessibility', () => {
    it('panel has role=complementary + aria-label (C24)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const panel = container.querySelector('[role="complementary"]');
      expect(panel).not.toBeNull();
      expect(panel!.getAttribute('aria-label')).toBe('Pattern context');
    });

    it('collapse button has aria-expanded and aria-controls (C25)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      render(ContextPanel);
      const btn = screen.getByRole('button', { name: /collapse|expand/i });
      expect(btn.getAttribute('aria-expanded')).toMatch(/true|false/);
      expect(btn.getAttribute('aria-controls')).toBe('context-panel-body');
    });

    it('respects prefers-reduced-motion (C23)', () => {
      // jsdom doesn't emulate media queries fully; we assert the @media rule
      // exists by checking the style tag content.
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      const { container } = render(ContextPanel);
      const html = container.innerHTML + Array.from(document.querySelectorAll('style')).map(s => s.textContent).join('\n');
      expect(html).toContain('prefers-reduced-motion');
    });
  });
```

- [ ] **Step 13.2: Run — expect FAIL on C23 only (C24/C25 should pass from Task 11)**

Run: `npm run test -- ContextPanel.test.ts -t 'accessibility' 2>&1 | tail -8`
Expected: 1 FAIL (C23).

- [ ] **Step 13.3: Add reduced-motion CSS**

In the `<style>` block append:

```css
  @media (prefers-reduced-motion: reduce) {
    .context-panel,
    .apply-btn,
    .collapse-btn {
      transition-duration: 0.01ms !important;
      animation-duration: 0.01ms !important;
    }
  }
```

- [ ] **Step 13.4: Run — expect PASS**

Run: `npm run test -- ContextPanel.test.ts -t 'accessibility' 2>&1 | tail -6`
Expected: 3 PASS.

- [ ] **Step 13.5: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel accessibility + prefers-reduced-motion"
```

---

### Task 14a — Store: expose `_matchInFlight`, `_matchError`, make `_lastMatchedText` public (S7 + S8 + S9)

**Files:**
- Modify: `frontend/src/lib/stores/clusters.svelte.ts`
- Modify: `frontend/src/lib/stores/clusters.svelte.test.ts`

Rationale: Task 14 originally jammed store-shape changes into the component task without a preceding failing-test gate. Splitting so the store change earns its own RED-GREEN cycle.

- [ ] **Step 14a.1: Write 3 failing store tests**

Append to `clusters.svelte.test.ts`:

```typescript
describe('transient fetch flags (Tier 1)', () => {
  beforeEach(() => {
    clustersStore._reset();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('exposes _matchInFlight as public state (S7)', async () => {
    // Delayed-resolve fetch so we can observe the in-flight transition.
    let resolveFetch!: (value: unknown) => void;
    (globalThis.fetch as any) = vi.fn(() => new Promise((r) => { resolveFetch = r; }));

    expect(clustersStore._matchInFlight).toBe(false);
    clustersStore.checkForPatterns('x'.repeat(60));
    await vi.advanceTimersByTimeAsync(900);
    expect(clustersStore._matchInFlight).toBe(true);

    resolveFetch({
      ok: true,
      status: 200,
      json: async () => ({ match: null }),
    });
    await vi.runAllTimersAsync();
    expect(clustersStore._matchInFlight).toBe(false);
  });

  it('captures _matchError="network" on rejection, clears on success (S8)', async () => {
    (globalThis.fetch as any) = vi.fn().mockRejectedValueOnce(new TypeError('network failure'));
    clustersStore.checkForPatterns('x'.repeat(60));
    await vi.advanceTimersByTimeAsync(900);
    await vi.runAllTimersAsync();
    expect(clustersStore._matchError).toBe('network');

    mockFetch([{ match: null }]);
    clustersStore.checkForPatterns('y'.repeat(60));
    await vi.advanceTimersByTimeAsync(900);
    await vi.runAllTimersAsync();
    expect(clustersStore._matchError).toBeNull();
  });

  it('_lastMatchedText is readable after a successful match (S9)', async () => {
    mockFetch([
      { match: { cluster: { id: 'c', label: 'L', domain: 'backend', member_count: 1 }, meta_patterns: [{ id: 'mp', pattern_text: 'p', source_count: 1 }], similarity: 0.9, cross_cluster_patterns: [], match_level: 'cluster' } },
    ]);
    clustersStore.checkForPatterns('hello world this is a long enough prompt');
    await vi.advanceTimersByTimeAsync(900);
    expect(clustersStore._lastMatchedText).not.toBe('');
  });
});
```

- [ ] **Step 14a.2: Run — expect 3 FAIL**

Run: `cd frontend && npm run test -- clusters.svelte.test.ts -t 'transient fetch flags' 2>&1 | tail -12`
Expected: 3 FAIL — `_matchInFlight` / `_matchError` undefined, and `_lastMatchedText` inaccessible (it's `private`).

- [ ] **Step 14a.3: Add the flags + change visibility**

In `frontend/src/lib/stores/clusters.svelte.ts`:

1. Change `private _lastMatchedText = '';` (line 124) to:
   ```typescript
   _lastMatchedText = $state('');
   ```
   Drop the `private` modifier.
2. Add near the top of the class, next to `_lastMatchedText`:
   ```typescript
   _matchInFlight = $state(false);
   _matchError: 'network' | null = $state(null);
   ```
3. Inside `checkForPatterns()`, rewrite the `setTimeout` body to set the flags:

   ```typescript
       this._debounceTimer = setTimeout(async () => {
         if (this._matchAbort) this._matchAbort.abort();
         this._matchAbort = new AbortController();
         this._matchInFlight = true;
         this._matchError = null;
         const signal = this._matchAbort.signal;
         try {
           const resp = await matchPattern(trimmed, signal, projectStore.currentProjectId);
           this._lastMatchedText = trimmed;
           if (resp.match && resp.match.meta_patterns.length > 0) {
             this.suggestion = {
               ...resp.match,
               cross_cluster_patterns: resp.match.cross_cluster_patterns ?? [],
               match_level: resp.match.match_level ?? 'cluster',
             };
             this.suggestionVisible = true;
           } else {
             this.suggestion = null;
             this.suggestionVisible = false;
           }
         } catch (err) {
           if (err instanceof DOMException && err.name === 'AbortError') return;
           this._matchError = 'network';
           console.warn('Pattern match failed:', err);
         } finally {
           this._matchInFlight = false;
         }
       }, debounceMs);
   ```

   **This replaces the `resp.match && …` body from Task 4 Step 4.4** — same logic, now wrapped in the flags.

4. In `_reset()`:

   ```typescript
       this._matchInFlight = false;
       this._matchError = null;
   ```

- [ ] **Step 14a.4: Run the 3 S7/S8/S9 tests to verify PASS**

Run: `npm run test -- clusters.svelte.test.ts -t 'transient fetch flags' 2>&1 | tail -8`
Expected: 3 PASS.

- [ ] **Step 14a.5: Run the whole store test file to catch any regression from the `_lastMatchedText` visibility change**

Run: `npm run test -- clusters.svelte.test.ts 2>&1 | tail -8`
Expected: all PASS.

- [ ] **Step 14a.6: Commit**

```bash
git add frontend/src/lib/stores/clusters.svelte.ts frontend/src/lib/stores/clusters.svelte.test.ts
git commit -m "feat(clusters): expose transient fetch flags (_matchInFlight, _matchError, _lastMatchedText)"
```

---

### Task 14b — `ContextPanel.svelte` edge-case rendering (C20, C21, C22)

**Files:**
- Modify: `frontend/src/lib/components/editor/ContextPanel.svelte`
- Modify: `frontend/src/lib/components/editor/ContextPanel.test.ts`

- [ ] **Step 14b.1: Write 3 failing tests**

```typescript
  describe('edge cases', () => {
    it('renders "no match" state when suggestion is null after a fetch attempt (C20)', () => {
      clustersStore.suggestion = null;
      clustersStore.suggestionVisible = false;
      clustersStore._lastMatchedText = 'x'.repeat(60);  // signal "we attempted"
      render(ContextPanel);
      expect(screen.queryByText(/no (similar|match)/i)).toBeTruthy();
    });

    it('in-flight state fades prior match body to 0.5 opacity (C21)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      clustersStore._matchInFlight = true;
      const { container } = render(ContextPanel);
      const body = container.querySelector('[data-test="panel-body"]') as HTMLElement;
      // jsdom exposes inline style; assert the attribute string.
      expect(body.getAttribute('style') || '').toMatch(/opacity:\s*0\.5/);
    });

    it('network error draws a red-contour class on the header (C22)', () => {
      clustersStore.suggestion = mockClusterMatch();
      clustersStore.suggestionVisible = true;
      clustersStore._matchError = 'network';
      const { container } = render(ContextPanel);
      const header = container.querySelector('[data-test="panel-header"]') as HTMLElement;
      expect(header.classList.contains('panel-header--error')).toBe(true);
    });
  });
```

- [ ] **Step 14b.2: Run — expect 3 FAIL**

Run: `npm run test -- ContextPanel.test.ts -t 'edge cases' 2>&1 | tail -8`
Expected: 3 FAIL — no "no match" copy, no in-flight opacity, no error class.

- [ ] **Step 14b.3: Wire flags into `ContextPanel.svelte`**

In `<script>`:

```svelte
  const inFlight = $derived(clustersStore._matchInFlight);
  const errorState = $derived(clustersStore._matchError !== null);
  const attemptedMatch = $derived(clustersStore._lastMatchedText !== '');
```

Replace the `{#if !hasSuggestion}` block with:

```svelte
  {#if !hasSuggestion}
    <div class="empty-state">
      {#if attemptedMatch}
        <p class="empty-copy">No similar clusters found — the optimizer will treat this prompt standalone.</p>
      {:else}
        <p class="empty-copy">Start typing to see related clusters and patterns.</p>
        <p class="empty-sub">Waiting for prompt — at least 30 characters.</p>
      {/if}
    </div>
```

Update the `<header>` to the full:

```svelte
  <header
    class="panel-header"
    data-test="panel-header"
    class:panel-header--error={errorState}
  >
    <span class="panel-title">CONTEXT</span>
    <button …>…</button>
  </header>
```

Update the `<div id="context-panel-body" …>` to:

```svelte
  <div
    id="context-panel-body"
    class="panel-body"
    data-test="panel-body"
    style="opacity: {inFlight ? 0.5 : 1};"
    hidden={!isOpen}
  >
```

Append to `<style>`:

```css
  .panel-header--error {
    box-shadow: inset 0 0 0 1px var(--color-neon-red);
  }
  .panel-body {
    transition: opacity 150ms cubic-bezier(0.16, 1, 0.3, 1);
  }
```

- [ ] **Step 14b.4: Run the 3 C20/C21/C22 tests**

Run: `npm run test -- ContextPanel.test.ts -t 'edge cases' 2>&1 | tail -8`
Expected: 3 PASS.

- [ ] **Step 14b.5: Run all ContextPanel tests to confirm no regression**

Run: `npm run test -- ContextPanel.test.ts 2>&1 | tail -8`
Expected: all PASS (~25 tests including C1-C25).

- [ ] **Step 14b.6: Commit**

```bash
git add frontend/src/lib/components/editor/ContextPanel.svelte frontend/src/lib/components/editor/ContextPanel.test.ts
git commit -m "feat(editor): ContextPanel edge cases — empty match, in-flight, network error"
```

---

### Task 15 — `EditorGroups.svelte`: add ContextPanel slot (I3, I4)

**Files:**
- Modify: `frontend/src/lib/components/layout/EditorGroups.svelte`
- Modify or Create: `frontend/src/lib/components/layout/EditorGroups.test.ts`

- [ ] **Step 15.1: Read the existing `EditorGroups.test.ts` to understand the mock setup**

Run: `head -30 frontend/src/lib/components/layout/EditorGroups.test.ts`

The existing file (150+ lines) uses `vi.mock()` factories to stub child components as no-ops — see lines 7-16 (`PromptEdit`, `ForgeArtifact`, `PassthroughView`, `DiffView`, `RefinementTimeline`, `SemanticTopology`). **Critical: do NOT add `vi.mock('$lib/components/editor/ContextPanel.svelte', ...)`**. The new tests below need to query the real `ContextPanel` DOM, so leaving it un-mocked is intentional. If a later test in this file wants to stub it, add the mock at that describe-block's scope only.

- [ ] **Step 15.2: Write 2 failing tests**

Append:

```typescript
  it('renders ContextPanel slot at >= 1400 px (I3)', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1500 });
    const { container } = render(EditorGroups);
    expect(container.querySelector('[data-test="context-panel"]')).not.toBeNull();
  });

  it('collapses ContextPanel to rail at < 1400 px (I4)', () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 });
    const { container } = render(EditorGroups);
    const panel = container.querySelector('[data-test="context-panel"]');
    expect(panel?.getAttribute('data-collapsed')).toBe('true');
  });
```

- [ ] **Step 15.3: Run — expect FAIL**

Run: `npm run test -- EditorGroups.test.ts 2>&1 | tail -6`
Expected: FAIL — no ContextPanel yet in EditorGroups.

- [ ] **Step 15.4: Mount ContextPanel in EditorGroups**

Open `frontend/src/lib/components/layout/EditorGroups.svelte`. Add the import at the top:

```svelte
<script lang="ts">
  import ContextPanel from '$lib/components/editor/ContextPanel.svelte';
</script>
```

Find the layout JSX — specifically where the prompt editor column ends and the Inspector column begins. Insert the panel between them:

```svelte
  <ContextPanel />
```

(The existing layout CSS grid should make room naturally; if an explicit column is needed, add a `.context-column { width: 240px; }` grid column slot and reduce the Inspector column by 240 px at viewports ≥ 1400 px.)

For the narrow-viewport behavior, add to `<script>`:

```svelte
  let innerWidth = $state(typeof window !== 'undefined' ? window.innerWidth : 1920);
  $effect(() => {
    if (typeof window === 'undefined') return;
    const handler = () => { innerWidth = window.innerWidth; };
    window.addEventListener('resize', handler);
    return () => window.removeEventListener('resize', handler);
  });
  const narrowViewport = $derived(innerWidth < 1400);
```

Then in the template, force-collapse via CSS when narrow:

```svelte
<div class="workbench" class:workbench--narrow={narrowViewport}>
  ...
  <ContextPanel />
</div>
```

Add CSS:

```css
  .workbench--narrow :global([data-test="context-panel"]) {
    width: 28px;
  }
  .workbench--narrow :global([data-test="context-panel"][data-collapsed="false"]) {
    /* override — narrow viewport always renders rail */
    width: 28px;
  }
```

(For the data-collapsed attribute check the tests rely on, add a workbench-level set:)

In the `<ContextPanel />` line, bind to force-collapse when narrow. The cleanest way is to extend `ContextPanel.svelte` to accept an optional `forceCollapsed: boolean` prop. For this task, do so:

In `ContextPanel.svelte` `<script>`:

```svelte
  interface Props {
    forceCollapsed?: boolean;
  }
  let { forceCollapsed = false }: Props = $props();
```

Replace `data-collapsed={!isOpen}` with:

```svelte
  data-collapsed={forceCollapsed || !isOpen}
```

And in the class binding:

```svelte
  class:context-panel--collapsed={forceCollapsed || !isOpen}
```

Then in `EditorGroups.svelte`:

```svelte
  <ContextPanel forceCollapsed={narrowViewport} />
```

- [ ] **Step 15.5: Run EditorGroups tests — expect PASS**

Run: `npm run test -- EditorGroups.test.ts 2>&1 | tail -6`
Expected: 2 PASS.

- [ ] **Step 15.6: Run ContextPanel tests to verify the new `forceCollapsed` prop didn't break anything**

Run: `npm run test -- ContextPanel.test.ts 2>&1 | tail -6`
Expected: all PASS (25 tests).

- [ ] **Step 15.7: Commit**

```bash
git add frontend/src/lib/components/layout/EditorGroups.svelte frontend/src/lib/components/layout/EditorGroups.test.ts frontend/src/lib/components/editor/ContextPanel.svelte
git commit -m "feat(layout): EditorGroups mounts ContextPanel with narrow-viewport rail"
```

---

### Task 16 — Remove `<PatternSuggestion />` from `PromptEdit.svelte` (I1, I2)

**Files:**
- Modify: `frontend/src/lib/components/editor/PromptEdit.svelte`
- Modify: `frontend/src/lib/components/editor/PromptEdit.test.ts`

- [ ] **Step 16.1: Write the 2 failing tests**

In `frontend/src/lib/components/editor/PromptEdit.test.ts`, append or replace:

```typescript
  it('does not render PatternSuggestion child (I1)', () => {
    const { container } = render(PromptEdit);
    // PatternSuggestion used a `.suggestion-banner` class — its absence confirms removal.
    expect(container.querySelector('.suggestion-banner')).toBeNull();
  });

  it('still renders applied-chip when forgeStore.appliedPatternIds populated (I2)', () => {
    forgeStore.appliedPatternIds = ['mp1', 'mp2'];
    forgeStore.appliedPatternLabel = 'Cluster L (2)';
    const { container } = render(PromptEdit);
    const chip = container.querySelector('.applied-chip');
    expect(chip).not.toBeNull();
    expect(chip!.textContent).toMatch(/2 patterns/);
  });
```

Delete any existing test in this file that mounts `PatternSuggestion` or asserts its presence.

- [ ] **Step 16.2: Run — expect FAIL on I1**

Run: `npm run test -- PromptEdit.test.ts 2>&1 | tail -6`
Expected: I1 fails (banner still mounted), I2 passes.

- [ ] **Step 16.3: Remove the PatternSuggestion import + mount from PromptEdit**

In `frontend/src/lib/components/editor/PromptEdit.svelte`:

1. Delete line 7: `import PatternSuggestion from './PatternSuggestion.svelte';`
2. Delete the `<PatternSuggestion onApply={...} />` block (lines 128-131).

The surrounding editor-area content now begins directly with `<textarea>`.

- [ ] **Step 16.4: Run PromptEdit tests — expect PASS**

Run: `npm run test -- PromptEdit.test.ts 2>&1 | tail -6`
Expected: 2 PASS.

- [ ] **Step 16.5: Commit**

```bash
git add frontend/src/lib/components/editor/PromptEdit.svelte frontend/src/lib/components/editor/PromptEdit.test.ts
git commit -m "refactor(editor): drop PatternSuggestion mount from PromptEdit"
```

---

### Task 17 — Delete legacy `PatternSuggestion.svelte` + its test

**Files:**
- Delete: `frontend/src/lib/components/editor/PatternSuggestion.svelte`
- Delete: `frontend/src/lib/components/editor/PatternSuggestion.test.ts`

- [ ] **Step 17.1: Verify no other file imports PatternSuggestion**

Run: `grep -rn "PatternSuggestion" frontend/src/ 2>&1 | head`
Expected: zero matches (Task 16 removed the last import).

- [ ] **Step 17.2: Delete the files**

Run: `rm frontend/src/lib/components/editor/PatternSuggestion.svelte frontend/src/lib/components/editor/PatternSuggestion.test.ts`

- [ ] **Step 17.3: Run the full frontend test suite to confirm no broken references**

Run: `cd frontend && npm run test 2>&1 | tail -10`
Expected: all tests pass.

- [ ] **Step 17.4: Run svelte-check**

Run: `npm run check 2>&1 | tail -5`
Expected: 0 errors.

- [ ] **Step 17.5: Commit**

```bash
git add -u
git commit -m "chore(editor): delete legacy PatternSuggestion component + test"
```

---

### Task 18 — Final regression + lint pass

**Files:** — None modified; verification only.

- [ ] **Step 18.1: Full backend test suite**

Run: `cd backend && source .venv/bin/activate && pytest --cov=app -q 2>&1 | tail -10`
Expected: all PASS, coverage ≥ 90%.

- [ ] **Step 18.2: Full frontend test suite**

Run: `cd frontend && npm run test 2>&1 | tail -10`
Expected: all PASS.

- [ ] **Step 18.3: `npm run check`**

Run: `npm run check 2>&1 | tail -5`
Expected: 0 errors.

- [ ] **Step 18.4: Ruff**

Run: `cd backend && source .venv/bin/activate && ruff check app/ tests/ 2>&1 | tail -5`
Expected: "All checks passed!"

- [ ] **Step 18.5: Brand-guideline grep**

Run:
```bash
grep -rnE 'box-shadow.*(blur|spread)' frontend/src/lib/components/editor/ContextPanel.svelte && echo FAIL || echo "no blur/spread: OK"
grep -rnE 'glow|radiance|bloom' frontend/src/lib/components/editor/ContextPanel.svelte && echo FAIL || echo "no forbidden words: OK"
grep -rnE 'border:\s*2px' frontend/src/lib/components/editor/ContextPanel.svelte && echo FAIL || echo "no 2px borders: OK"
grep -rnE '@keyframes.*pulse' frontend/src/lib/components/editor/ContextPanel.svelte && echo FAIL || echo "no pulse animations: OK"
```
Expected: 4 × "OK" lines.

- [ ] **Step 18.6: Commit a reproducible verification log (optional)**

Skip if 18.1-18.5 all green. Otherwise open a new task and fix.

---

## Commit log summary

Expected commit sequence on the feat branch (one per task; Task 14 was split into 14a + 14b post-review, so the sequence is 18 functional commits):

1. `feat(clusters): expose match_level on /api/clusters/match response`
2. `feat(clusters): expose cross_cluster_patterns on /api/clusters/match response`
3. `test(clusters): regression guard — match response preserves pre-existing fields`
4. `feat(clusters): extend ClusterMatch type with cross_cluster_patterns + match_level`
5. `refactor(clusters): drop _skippedClusterId gate + dismissSuggestion()`
6. `feat(editor): ContextPanel skeleton with empty-state rendering`
7. `feat(editor): ContextPanel renders cluster identity row`
8. `feat(editor): ContextPanel meta-patterns section with checkbox selection`
9. `feat(editor): ContextPanel global section with neon-purple left border`
10. `feat(editor): ContextPanel APPLY button populates forgeStore.appliedPatternIds`
11. `feat(editor): ContextPanel collapse/expand with localStorage persistence`
12. `feat(editor): ContextPanel hides during synthesis`
13. `feat(editor): ContextPanel accessibility + prefers-reduced-motion`
14a. `feat(clusters): expose transient fetch flags (_matchInFlight, _matchError, _lastMatchedText)`
14b. `feat(editor): ContextPanel edge cases — empty match, in-flight, network error`
15. `feat(layout): EditorGroups mounts ContextPanel with narrow-viewport rail`
16. `refactor(editor): drop PatternSuggestion mount from PromptEdit`
17. `chore(editor): delete legacy PatternSuggestion component + test`
18. (no commit — verification only)

18 functional commits. Each passes its own tests + all prior tests. Each leaves the repo in a shippable state.

---

## Verification plan

End-to-end manual check after all tasks land:

1. `./init.sh restart`
2. Open `/app` in a browser with a seeded taxonomy (≥ 1 mature cluster in `security` or `backend` domain — run `POST /api/seed` with the `coding-implementation` agent first if empty).
3. Type **"Write a Python function that validates JWT tokens against RFC 7519 with retry logic"** (70 chars) and wait 1 s.
4. Confirm `ContextPanel` populates within 1.2 s with the `security` or `backend` cluster's patterns.
5. Toggle 2 meta-patterns + 1 global pattern → click **APPLY** → confirm:
   - The applied-chip below the textarea reads "3 patterns from \"<cluster label>\"".
   - The checkboxes remain checked.
6. Click **SYNTHESIZE** → confirm `ContextPanel` disappears while the pipeline runs.
7. After completion → confirm `ContextPanel` re-renders.
8. Click the `∨` header control → panel collapses to a 28 px rail.
9. Reload the page → rail state persists.
10. DevTools → simulate `prefers-reduced-motion: reduce` → confirm panel transitions are effectively instant.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-24-live-pattern-intelligence-tier-1-plan.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
