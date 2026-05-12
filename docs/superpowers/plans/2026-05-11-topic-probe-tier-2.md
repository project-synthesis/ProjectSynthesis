# v0.4.22 — Topic Probe Tier 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Each protocol cycle below dispatches **5 fresh implementer subagents** (RED → GREEN → REFACTOR → INTEGRATE → OPERATE) followed by **2 independent validators** (spec-compliance reviewer + code-quality reviewer), iterated until **ZERO inconsistencies** per spec § 10 + user mandate. APPROVED-WITH-MINOR triggers re-dispatch — NOT proceed-with-notes. Gate-only cycles (14, 16) dispatch ONLY the 2 validators (no RED→GREEN code change). Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md` (14 validation rounds; final APPROVED at commit `9b843791`).

**Goal:** Ship save-as-suite + replay + UI + regression alarm + topic-only mode + 202+polling + audit-hook RAISE flip as v0.4.22, building on Foundation P3 (`RunRow`) + P4 (long-handler restructure) substrates.

**Architecture:** New immutable `ValidationSuite` ORM (`source_run_id` FK → `run_row`, frozen `prompts_snapshot` + `baseline_scores`, soft-delete via `retired_at`). Replays write `RunRow(mode='replay_run', suite_id=...)` via a new `ReplayRunGenerator` conforming to the canonical `RunGenerator` Protocol; regression alarm joins `MAX(started_at) replay_run per suite_id` against frozen baselines. Save-as-suite restricted to `mode='topic_probe'` runs only. `RunOrchestrator` gains a new `dispatch_async()` entry point that awaits the initial INSERT and spawns shielded background tasks for 202+polling callers; existing `run()` becomes a thin wrapper that gates on a per-call `asyncio.Event` set by the spawned task at terminal status. `WRITE_QUEUE_AUDIT_HOOK_RAISE` default flips `False → True` after the 7-day v0.4.21 soak window closes (≥2026-05-18).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async + aiosqlite, Alembic, Pydantic v2, MCP SDK, asyncio (Lock + ContextVar + shielded tasks), pytest + pytest-asyncio. Frontend: Svelte 5 + TypeScript + Vitest + Playwright. Builds on v0.4.18 RunRow substrate, v0.4.21 long-handler restructures, brand-canon `SKILL.md` + `component-patterns.md`.

**Decomposition:** v0.4.22 = 16 cycles (14 protocol + 2 gate). Each protocol cycle = 7-dispatch protocol (5 implementer + 2 validator). Gate cycles = 2 validators only. Total ≈ 102 baseline dispatches + 35-70 iteration dispatches = 137-172 commits.

---

## File structure

| File | Type | Notes |
|---|---|---|
| `backend/app/models.py` | EXTEND | NEW `ValidationSuite` ORM (11 cols + 3 indexes); `RunRow.suite_id` declaration gains `ForeignKey("validation_suite.id", ondelete="SET NULL")`; `RunRow.__table_args__` gains `Index("ix_run_row_suite_id", "suite_id", started_at.desc())`. |
| `backend/alembic/versions/<rev>_validation_suite_topic_probe_t2.py` | NEW migration | Single forward-only; `inspector.has_table()` idempotency guard; `batch_alter_table("run_row", recreate="always")` for FK addition; `text("created_at DESC")` for partial index direction; reversible downgrade nulls `suite_id` before drop. |
| `backend/app/schemas/runs.py` | EXTEND | `RunRequest.mode` + `RunSummary.mode` + `RunResult.mode` Literal each extends `["topic_probe", "seed_agent"]` → `["topic_probe", "seed_agent", "replay_run"]`. |
| `backend/app/schemas/probes.py` | EXTEND | `ProbeContext.repo_full_name: str` → `str | None`; new `commit_sha: str | None` + `topic_only: bool = False`. |
| `backend/app/schemas/validation_suite.py` | NEW | 11 Pydantic models: `SaveSuiteRequest`, `RetireSuiteRequest`, `ValidationSuiteOut` (with `ConfigDict(from_attributes=True)`), `ValidationSuiteListItem`, `ValidationSuiteListResponse`, `ReplayRunOut`, `RegressionAlarmEntry`, `RegressionAlarmBlock`, nested `PromptSnapshotItem`/`PerPromptScore`/`BaselineScoresPayload`. |
| `backend/app/schemas/mcp_models.py` | EXTEND | NEW `SaveSuiteOutput` + `ReplayInitiatedOutput` (`*Output` suffix per dominant convention; 12 of 13 existing classes use it). |
| `backend/app/services/validation_suite_service.py` | NEW | Stateless class; `create_from_run`/`retire`/`get`/`list`/`list_replays`/`compute_regression_alarm`. 30s `TTLCache` on alarm. In-memory `_prior_alarm_states` for transition detection. Detached-ORM-safe per Foundation P4. |
| `backend/app/services/generators/_aggregate.py` | NEW | Shared `compute_run_aggregate(prompt_results: list[dict]) -> dict` extracted from `TopicProbeGenerator._build_aggregate`. Output keys: `mean_overall`, `p5_overall`, `p50_overall`, `p95_overall`, `completed_count`, `failed_count`, `f5_flag_fires`, `scoring_formula_version`, `task_type_distribution` (NEW, defensive: `r.get("task_type", "unknown")`). |
| `backend/app/services/generators/replay_run_generator.py` | NEW | Conforms to `RunGenerator` Protocol; consumes `RunRequest.payload={"suite_id", "project_id", "repo_full_name"}`; sequential per-prompt loop with try/except; reuses `batch_pipeline.run_single_prompt` with full collaborator graph; emits `probe_prompt_completed` + `probe_completed` (NOT a new event family). |
| `backend/app/services/generators/topic_probe_generator.py` | EXTEND | `run()` branches on `request.payload.get("grounding_mode", "codebase")`; topic_only skips Phase 1 with safe `intent_hint` Literal coercion + `scope=str(payload.get("scope") or "**/*")`. `_build_aggregate` refactored to thin delegate calling `compute_run_aggregate`. |
| `backend/app/services/probe_generation.py` | EXTEND | `generate_probe_prompts()` gains `mode: Literal['codebase', 'topic_only'] = 'codebase'` + `template_name: str = 'probe-agent.md'`. Topic-only inverts per-prompt predicate to `_lacks_backtick` (drop prompts WITH backticks); batch `_DROP_THRESHOLD=0.5` preserved. |
| `backend/app/services/run_orchestrator.py` | EXTEND | `_persist_final()` gains `mode: str` parameter (mode-keyed `operation_label`: `replay_run_persist` for replay, existing for others); `_extract_probe_meta()` returns dict with new `grounding_mode` key (continues to gate on `mode == "topic_probe"`); `_create_row()` body adds `suite_id=(request.payload.get("suite_id") if mode == "replay_run" else None)` to the `RunRow(...)` ctor; NEW `_run_generator_and_persist(mode, request, run_id)` helper; NEW `dispatch_async(*, mode, request, run_id, done_event=None)` for 202 callers + `_run_to_completion(*, mode, request, run_id, done_event=None)` wrapper. `run()` refactored to thin wrapper calling `dispatch_async(done_event=asyncio.Event())` then awaiting the event + reload. |
| `backend/app/main.py` | EXTEND | Lifespan registration: extend `RunOrchestrator(generators={...})` dict at `:1191-1196` with `"replay_run": replay_run_gen`. |
| `backend/app/routers/suites.py` | NEW | 6 endpoints: save-as-suite (201, 20/min), GET /api/suites (paginated), GET /api/suites/{id}, GET /api/suites/{id}/replays, POST /replay (202, 5/min), POST /retire (200, 30/min). |
| `backend/app/routers/probes.py` | EXTEND | `Prefer: respond-async` header → 202; `grounding_mode` body field; default SSE path unchanged. |
| `backend/app/routers/runs.py` | EXTEND | `list_runs::mode` Query Literal extended with `replay_run`. |
| `backend/app/routers/health.py` | EXTEND | `regression_alarm` JSON block from `ValidationSuiteService.compute_regression_alarm()`. |
| `backend/app/tools/save_suite.py`, `tools/replay_suite.py` | NEW | MCP handlers calling `ValidationSuiteService` (do NOT re-implement persistence). |
| `backend/app/mcp_server.py` | EXTEND | Register 2 new `@mcp.tool(structured_output=True)` decorators (15 → 17 tools). |
| `backend/app/config.py` | EXTEND (FLIP) | `WRITE_QUEUE_AUDIT_HOOK_RAISE: bool = Field(default=True, ...)` (was `default=False`). Only Cycle 15 touches this. |
| `prompts/probe-agent-topic-only.md` | NEW | Template body for topic-only mode (no code references). Hot-reloaded via `PromptLoader`. |
| `prompts/manifest.json` | EXTEND | Add manifest entry for new template. |
| `frontend/src/lib/components/probes/TopicProbeForm.svelte` | NEW | Form: topic textarea + grounding-mode segmented control + scope picker + N slider + intent dropdown + Run button. |
| `frontend/src/lib/components/probes/TopicProbeProgressView.svelte` | NEW | Active-run view; source-agnostic SSE/poll. |
| `frontend/src/lib/components/probes/TopicProbeReportCard.svelte` | NEW | Final report + Save-Suite + Replay + Copy-md. |
| `frontend/src/lib/components/probes/TaxonomyMiniView.svelte` | NEW | ~200px emergent-taxonomy tree with forge-spark on new nodes. |
| `frontend/src/lib/components/suites/SuitesPanel.svelte` | NEW | Left-nav panel + filter + status dots. |
| `frontend/src/lib/components/suites/SuiteRow.svelte` | NEW | h-5 data row with Recipe A hover (border + bg-tint, no lift). |
| `frontend/src/lib/components/suites/SuiteDetailView.svelte` | NEW | Per-suite detail + replay history + per-prompt baseline-vs-latest. |
| `frontend/src/lib/components/suites/RegressionBadge.svelte` | NEW | StatusBar badge mount (lower-case `12 ok` / `2 alarm`). |
| `frontend/src/lib/components/taxonomy/SeedModal.svelte` | EXTEND | Third tab "TOPIC PROBE" (matches shipped `.seed-tab` font-mono + letter-spacing 0.05em — canonical font-display migration deferred to T4). |
| `frontend/src/lib/components/layout/StatusBar.svelte` | EXTEND | Mount RegressionBadge edge-to-edge inside shipped 20px bar. |
| `frontend/src/lib/components/layout/Navigator.svelte` (or equivalent left-nav module — verify path during Cycle 12 GREEN) | EXTEND | Add SUITES entry that routes to SuitesPanel (per spec §6 "EXTENDED components" Left-nav row). |
| `frontend/src/app.css` | EXTEND | NEW `@keyframes forge-spark` (0→25→100%: scale 1→1.2→1 + rotate 0→3deg→0 + yellow flash via color/background; 250ms ease-out single-shot). |
| `frontend/src/lib/api/suites.ts` | NEW | Per-domain module: `saveSuite`, `replaySuite`, `getSuites`, `getSuite`, `getSuiteReplays`, `retireSuite`. Imports `apiFetch` from `./client`. |
| `frontend/src/lib/stores/suitesStore.ts` | NEW | Suite list + selected + regression alarm cache. |
| `frontend/src/lib/stores/probesStore.ts` | EXTEND | 202+polling abstraction; auto-attach `Prefer: respond-async` when `n_prompts > 10`. |
| `.claude/skills/brand-guidelines/SKILL.md` | DOC-SYNC | Lines 79/214/375 extend 5-dir → 7-dir scope; line 140 22px → 20px; line 395 prose-only (no substitution). |
| `.claude/skills/brand-guidelines/references/component-patterns.md` | DOC-SYNC | Line 219 `copy-flash` 600ms → 1500ms; line 223 `forge-spark` "varies" → "250ms". |
| `backend/CLAUDE.md` | DOC-SYNC | T2/T3/T4 minor numbers harmonized; line 143 `probe_taxonomy_change` reference dropped (never emitted). |
| `docs/ROADMAP.md` | DOC-SYNC | Line 333 path `POST /api/probes/{id}/replay` → `POST /api/suites/{suite_id}/replay`. |

**Test files (NEW):**
- `backend/tests/test_validation_suite_migration.py` (Cycle 1, ~7 tests)
- `backend/tests/test_validation_suite_service.py` (Cycles 2-4, ~27 tests)
- `backend/tests/test_routers_suites.py` (Cycle 5, ~15 tests)
- `backend/tests/test_replay_run_generator.py` (Cycle 6, ~11 tests)
- `backend/tests/test_topic_only_mode.py` (Cycle 7, ~9 tests)
- `backend/tests/test_probes_202_polling.py` (Cycle 8, ~7 tests)
- `backend/tests/test_health_regression_alarm.py` (Cycle 9, ~5 tests)
- `backend/tests/test_mcp_tools_save_replay.py` (Cycle 10, ~9 tests)
- `backend/tests/integration/test_audit_hook_full_t2_pipeline.py` (Cycle 15, ~3 tests + extends existing P4 test)
- `frontend/src/lib/components/probes/*.test.ts` (Cycle 11, ~18 tests)
- `frontend/src/lib/components/suites/*.test.ts` (Cycle 12, ~14 tests)
- `frontend/src/lib/stores/probesStore.test.ts` (Cycle 13, ~7 tests)

**Modified:** ~28 files. **New:** ~22 files (incl. tests + migration + frontend components).

---

## TDD discipline (applies to every protocol cycle, per spec §10)

```
RED → GREEN → REFACTOR → INTEGRATE → OPERATE → Validator 1 (spec) → Validator 2 (quality)
 (1)   (2)      (3)         (4)        (5)         (subagent)            (subagent)
                                                       │
                                  BOTH must APPROVED-ZERO-INCONSISTENCIES.
                                  APPROVED-WITH-MINOR or REJECTED →
                                  route back to relevant phase + fresh dispatches.
                                  Behavior-affecting fixes cascade through
                                  INTEGRATE + OPERATE + BOTH validators.
```

**Per-phase dispatch rules:**
- Phases 1, 2, 3, 4, 5 = fresh implementer subagent dispatch (clean context per phase)
- Validators 1, 2 = fresh `general-purpose` subagent dispatches (independent contexts)

**Iteration mandate** (spec §10 + `feedback_tdd_protocol.md`): if either validator returns APPROVED-WITH-MINOR or REJECTED, route back to the relevant phase, re-dispatch + cascade through downstream phases if behavior-affecting. Total dispatches per cycle can grow if iteration is needed.

**Gate-only cycles (14, 16):** NO RED→GREEN code change; only the 2 validator dispatches run.

---

## Generic dispatch prompt templates (Cycles 2-13 + 15 reuse these)

Cycle 1's Tasks 1.3-1.7 demonstrate the fully-expanded per-phase dispatch pattern. Cycles 2-13 + 15 use a **collapsed bullet form** for Tasks N.3-N.7 because the per-phase boilerplate is identical across cycles — only the cycle-specific scope/anti-pattern focus varies. Each checkbox in the collapsed form maps to one fresh subagent dispatch using these templates, parameterized by the cycle-specific scope text in that bullet:

**REFACTOR dispatch template (`Task N.3`):**
> REFACTOR phase for v0.4.22 T2 Cycle N — local code quality only. Touched files: <list from cycle's Files section>. Per `feedback_tdd_protocol.md` REFACTOR section: (1) run `ruff check` + `mypy` on touched files; fix all warnings (no `type: ignore`); (2) walk error paths — every `except` has the right exception class + intentional action; (3) cover edge cases (empty/None/zero/negative/max-int/unicode) — add tests for contract-permitted cases; (4) precise type annotations (no untyped `dict[str, Any]` where a TypedDict/dataclass would be clearer); (5) DRY — extract logic appearing 3+ times within the cycle's diff; (6) local commit hygiene (independent revertable, specific file staging); (7) lint hygiene on test files too; (8) docstrings on non-trivial helpers + inline comments where the *why* isn't obvious. **Cycle-specific scope:** <see the cycle's Task N.3 bullet>. Commit: `refactor(t2-cN): <cycle-specific summary>`.

**INTEGRATE dispatch template (`Task N.4`):**
> INTEGRATE phase for v0.4.22 T2 Cycle N — canonical-pattern parity (static review). Per `feedback_tdd_protocol.md` INTEGRATE section: (1) open the schema, not the test mock; (2) diff against the canonical caller (column-by-column for persistence, kwarg-by-kwarg for service calls, event-by-event for emission); (3) read every service the cycle calls; (4) trace the user-visible surface code; (5) verify test fixtures construct real production types (no `MagicMock` for typed dataclasses). **Cycle-specific anti-pattern focus:** <see the cycle's Task N.4 bullet — A1-A6 codes named, with cite to canonical reference (spec § / models.py:line / etc.)>. Report APPROVED-ZERO-INCONSISTENCIES or list findings; findings → fix inline + re-run checklist from step 1.

**OPERATE dispatch template (`Task N.5`):**
> OPERATE phase for v0.4.22 T2 Cycle N — dynamic verification under realistic load. Per `feedback_tdd_protocol.md` OPERATE section: (1) inventory every writer in the process; (2) verify writer-coordination layers (process mutex + busy_timeout + retry-with-backoff); (3) inventory every cancellation surface (every `await` boundary; `asyncio.shield()` around cleanup writes); (4) inventory every timeout in the call path (longest must ≥ worst-case operation duration plus buffer); (5) run the live concurrency test (full stack + concurrent peers); (6) inspect live result against spec (query user-visible surfaces by ID); (7) run it twice back-to-back; (8) run under stress when feasible; (9) test cancellation explicitly (`curl --max-time`). **Cycle-specific anti-pattern focus:** <see the cycle's Task N.5 bullet — O1-O8 codes named with verification command>. Report OPERATE status.

**Validator 1 dispatch template (`Task N.6` — spec compliance):**
> Spec-compliance review for v0.4.22 T2 Cycle N. Read `docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md` <cycle-specific spec sections>. Read all touched files (per cycle's Files section). Verify implementation matches spec verbatim — column names, types, FK ondelete, error envelope codes, event payload shapes, operation labels, anti-pattern coverage. Report APPROVED-ZERO-INCONSISTENCIES or list findings. Findings → re-dispatch the failing phase (typically REFACTOR or GREEN); if behavior-affecting, cascade through INTEGRATE + OPERATE + BOTH validators fresh.

**Validator 2 dispatch template (`Task N.7` — code quality):**
> Code-quality review for v0.4.22 T2 Cycle N. Read all touched files. Run `ruff check`, `mypy`, `alembic check` (backend cycles), `svelte-check` + `npm run test` (frontend cycles); inspect output. Verify: test naming clarity, docstrings present on public surfaces, no `type: ignore`, error paths sane, no `MagicMock` for typed dataclasses, migration body matches project conventions, frontend follows brand-canon (h-5 buttons, p-1.5 sidebar, font-mono numerics, lower-case status badges, no banned-vocab in 2D-UI dirs). Report APPROVED-ZERO-INCONSISTENCIES or list findings.

When you see a `Tasks N.3-N.7` umbrella section in Cycles 2-13 + 15, each bullet inside is a subagent dispatch using the matching template above. Each checkbox = one dispatch + one verify step (PASS gate).

---

## Cycle 1 — Substrate (Migration + `ValidationSuite` ORM + `RunRow` FK/index hardening)

**Spec coverage:** §2 components-touched (models.py + migration rows), §3 data model + migration + RunRow declaration update, §10 Cycle 1.

**Files:**
- Create: `backend/app/models.py` (EXTEND — `ValidationSuite` class + `RunRow.suite_id` FK declaration + `RunRow.__table_args__` index)
- Create: `backend/alembic/versions/<rev>_validation_suite_topic_probe_t2.py`
- Create: `backend/tests/test_validation_suite_migration.py`

### Task 1.1 — RED dispatch

- [ ] **Dispatch fresh RED implementer subagent**

> You are the RED-phase implementer for v0.4.22 T2 Cycle 1. Strict TDD — write failing tests, NO production code.
>
> **Spec:** `docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md` §3 data model + migration + RunRow declaration update.
>
> Create `backend/tests/test_validation_suite_migration.py` with these 7 tests (full bodies — no stubs):
>
> 1. **`test_validation_suite_table_exists_post_upgrade`** — `alembic upgrade head` on fresh DB; assert `inspector.has_table("validation_suite") == True`.
> 2. **`test_validation_suite_has_11_columns`** — assert column names = exact set `{id, source_run_id, prompts_snapshot, baseline_scores, tolerance_abs, label, project_id, repo_full_name, created_at, retired_at, retired_reason}`.
> 3. **`test_validation_suite_source_run_id_fk_set_null`** — assert FK exists with `ondelete='SET NULL'` and column is nullable.
> 4. **`test_validation_suite_3_indexes_exist`** — assert all 3 indexes: `ix_validation_suite_project_id`, `ix_validation_suite_source_run_id`, `ix_validation_suite_active`.
> 5. **`test_validation_suite_active_index_partial_predicate`** — query `sqlite_master` (or pg equivalent) and assert `ix_validation_suite_active` has the `WHERE retired_at IS NULL` predicate.
> 6. **`test_run_row_suite_id_fk_exists`** — assert `fk_run_row_suite_id` FK constraint on `run_row.suite_id` exists with `ondelete='SET NULL'`.
> 7. **`test_ix_run_row_suite_id_exists_with_descending_started_at`** — assert `ix_run_row_suite_id` exists on `(suite_id, started_at DESC)`.
>
> Use shared conftest fixtures. Tests should fail because the migration + ORM updates don't exist yet. Do NOT add tests beyond 7. Commit with message: `test(t2-c1): RED — ValidationSuite migration + RunRow FK/index 7 tests`.

- [ ] **Verify RED phase complete**: 7 tests added, all fail with appropriate "table/index/FK does not exist" errors. Commit landed.

### Task 1.2 — GREEN dispatch

- [ ] **Dispatch fresh GREEN implementer subagent**

> You are the GREEN-phase implementer for v0.4.22 T2 Cycle 1. Minimal implementation to make the 7 RED tests pass.
>
> **Spec:** §3 data model + migration body + ORM declaration. Follow spec verbatim — `inspector.has_table()` idempotency guard, `batch_alter_table("run_row", recreate="always")` for FK addition (per `a2f6d8e31b09:76,97` precedent), `text("created_at DESC")` for partial index direction, downgrade nulls `run_row.suite_id` before drop.
>
> Steps:
> 1. Create `backend/alembic/versions/<rev>_validation_suite_topic_probe_t2.py` via `alembic revision -m "validation_suite_topic_probe_t2"` then edit the body per spec §3 migration template.
> 2. Add `ValidationSuite` class to `backend/app/models.py` per spec §3 ORM declaration (with `ConfigDict(from_attributes=True)` mirroring `schemas/templates.py:16` if needed at later schema layer).
> 3. Extend `RunRow.suite_id` declaration to `Mapped[str | None] = mapped_column(String, ForeignKey("validation_suite.id", ondelete="SET NULL"), nullable=True)` (currently bare `String` at `models.py:643`).
> 4. Extend `RunRow.__table_args__` tuple with `Index("ix_run_row_suite_id", "suite_id", started_at.desc())` per `models.py:125, 250, 350` precedent.
> 5. Run `alembic upgrade head` on fresh DB; run `pytest backend/tests/test_validation_suite_migration.py -v`; all 7 tests PASS.
> 6. Run `alembic check`; exit 0 (no drift).
> 7. Run `alembic downgrade -1`; assert clean reversal.
> 8. Run `alembic upgrade head` again; assert idempotent.
>
> Commit with message: `feat(t2-c1): GREEN — ValidationSuite migration + RunRow FK/index`.

- [ ] **Verify GREEN phase complete**: all 7 tests pass, `alembic check` clean, round-trip clean.

### Task 1.3 — REFACTOR dispatch

- [ ] **Dispatch fresh REFACTOR implementer subagent**

> REFACTOR phase for v0.4.22 T2 Cycle 1. Local code quality only. Touched files: `models.py`, new migration, test file.
>
> Checklist per `feedback_tdd_protocol.md` REFACTOR section:
> 1. Run `ruff check` + `mypy` on touched files; fix all warnings.
> 2. Walk error paths — does migration's `inspector.has_table()` guard fire correctly on re-upgrade?
> 3. Docstrings — add `"""..."""` to `ValidationSuite` class describing T2's frozen-fixture invariants.
> 4. Type annotations precise — `Mapped[str | None]` matches FK SET NULL semantics.
> 5. Test file lint clean.
>
> Commit: `refactor(t2-c1): lint + docstrings`.

- [ ] **Verify REFACTOR complete**: ruff + mypy clean on touched files.

### Task 1.4 — INTEGRATE dispatch

- [ ] **Dispatch fresh INTEGRATE implementer subagent**

> INTEGRATE phase for v0.4.22 T2 Cycle 1. Canonical-pattern parity check.
>
> Anti-patterns to verify (per spec §10 Cycle 1 INTEGRATE focus):
> - **A2:** migration follows `bdd8e96cf489_consolidate_lifespan_ddl_into_alembic.py` precedent (single forward-only; `inspector.has_table()` guard). Compare migration body against `bdd8e96cf489` line-by-line.
> - **A2 cascade-FK precedent:** the `RunRow.suite_id` ORM declaration MUST match the migration's FK constraint per `a2f6d8e31b09_cascade_optimization_fks.py` precedent (verified at `models.py:136, 290, 493, 507`).
> - **A5:** test fixtures construct real `ValidationSuite(...)` instances — NO `MagicMock` for the ORM class.
>
> Verification steps:
> 1. `alembic check` — exit 0 (no drift). If drift, fix the ORM-vs-migration mismatch and re-run.
> 2. Read `bdd8e96cf489` and confirm migration shape matches.
> 3. Read `a2f6d8e31b09:136, 290, 493, 507` and confirm `RunRow.suite_id`'s `ForeignKey()` declaration matches the cascade-FK pattern.
> 4. Grep test file for `MagicMock` references on `ValidationSuite` — should be zero.
>
> Report findings or "ZERO INCONSISTENCIES". No commit unless fixes needed.

- [ ] **Verify INTEGRATE complete**: report returned; any fixes applied.

### Task 1.5 — OPERATE dispatch

- [ ] **Dispatch fresh OPERATE implementer subagent**

> OPERATE phase for v0.4.22 T2 Cycle 1. Dynamic verification under realistic load.
>
> Anti-pattern: **O2** — migration completes cleanly under `WriteQueue` active (no contention dropouts).
>
> Verification steps:
> 1. Start full stack: `./init.sh restart`.
> 2. Run `alembic upgrade head` against `data/synthesis.db` (with backend running, WriteQueue active).
> 3. Verify no errors in `data/backend.log`; assert `alembic upgrade head` exits 0.
> 4. Run `alembic downgrade -1` + `alembic upgrade head` round-trip; assert clean.
> 5. Run `pytest backend/tests/test_main_lifespan_no_ddl.py -v`; assert continues to pass (P3 prework invariant).
> 6. Stop the stack.
>
> Report OPERATE status. No commit unless fixes needed.

- [ ] **Verify OPERATE complete**: no errors, round-trip clean, `test_main_lifespan_no_ddl.py` passes.

### Task 1.6 — Validator 1 (spec-compliance)

- [ ] **Dispatch fresh `general-purpose` subagent**

> Spec-compliance review for v0.4.22 T2 Cycle 1. Read `docs/superpowers/specs/2026-05-11-topic-probe-tier-2-design.md` §3 + §10 Cycle 1. Read all touched files (migration, models.py changes, test file).
>
> Verify the implementation matches the spec verbatim — column names, types, FK ondelete, index ordering, idempotency guard, downgrade null-out. Report APPROVED-ZERO-INCONSISTENCIES or list findings. Findings → re-dispatch GREEN.

- [ ] **Verify Validator 1 ZERO**: APPROVED-ZERO-INCONSISTENCIES. If MINOR/FINDINGS, route back to GREEN + cascade.

### Task 1.7 — Validator 2 (code-quality)

- [ ] **Dispatch fresh `general-purpose` subagent**

> Code-quality review for v0.4.22 T2 Cycle 1. Read all touched files. Run `ruff check`, `mypy`, `alembic check` and inspect output.
>
> Verify: test naming clarity, docstrings present on `ValidationSuite`, no `type: ignore`, error paths sane, no `MagicMock` for typed dataclasses, migration body matches project conventions. Report APPROVED-ZERO-INCONSISTENCIES or list findings.

- [ ] **Verify Validator 2 ZERO**: APPROVED-ZERO-INCONSISTENCIES. Cycle 1 complete when both validators clean.

---

## Cycle 2 — `ValidationSuiteService.create_from_run` + `SuiteSnapshotInputs`

**Spec coverage:** §4 Pydantic schemas (`SaveSuiteRequest`, `ValidationSuiteOut`, nested types), §4 op label `validation_suite_create`, §5 `validation_suite_service.py::create_from_run` + `SuiteSnapshotInputs`, §10 Cycle 2.

**Files:**
- Create: `backend/app/schemas/validation_suite.py` (NEW — Pydantic models)
- Create: `backend/app/services/validation_suite_service.py` (NEW — class)
- Create: `backend/tests/test_validation_suite_service.py`

### Task 2.1 — RED dispatch

- [ ] **Dispatch fresh RED implementer**

> RED phase for v0.4.22 T2 Cycle 2 — `create_from_run` + snapshot inputs. NO production code.
>
> **Spec:** §4 Pydantic schemas block + §5 `ValidationSuiteService.create_from_run` + `SuiteSnapshotInputs`. Per spec key invariants: suite is immutable, position correspondence per_prompt[i] ↔ prompts_snapshot[i], only `topic_probe` runs forkable.
>
> Create `backend/tests/test_validation_suite_service.py` with these 11 tests:
>
> 1. **`test_create_from_run_happy_path`** — seed `RunRow(mode='topic_probe', status='completed', aggregate={'mean_overall': 7.85, 'p5_overall': 6.2, 'p95_overall': 9.1, 'per_prompt': [...], 'task_type_distribution': {...}})`. Call `ValidationSuiteService.create_from_run(run_id, label='test', tolerance_abs=0.5)`. Assert returned `ValidationSuiteOut` has matching fields. Assert DB row exists via `SELECT * FROM validation_suite WHERE id=:s`.
> 2. **`test_create_from_run_raises_run_not_found_on_missing_id`** — assert `pytest.raises(ValueError, match='run_not_found')`.
> 3. **`test_create_from_run_raises_run_not_completed_on_running_status`** — seed running run; assert `ValueError('run_not_completed')`.
> 4. **`test_create_from_run_raises_not_a_probe_run_on_seed_agent_mode`** — seed `mode='seed_agent'`; assert `ValueError('not_a_probe_run')`.
> 5. **`test_create_from_run_raises_not_a_probe_run_on_replay_run_mode`** — seed `mode='replay_run'`; assert `ValueError('not_a_probe_run')` (no recursive forking).
> 6. **`test_create_from_run_raises_run_missing_aggregate`** — seed completed topic_probe with `aggregate=None` or missing `mean_overall`; assert `ValueError('run_missing_aggregate')`.
> 7. **`test_create_from_run_position_correspondence_preserved`** — seed run with 10 distinct prompts. Save as suite. Assert `suite.prompts_snapshot[i].raw_prompt == run.prompt_results[i]['raw_prompt']` for all i.
> 8. **`test_create_from_run_uses_validation_suite_create_operation_label`** — patch `WriteQueue.submit` to record `operation_label`. Save. Assert `operation_label="validation_suite_create"` was called.
> 9. **`test_create_from_run_detached_orm_safe`** — assert no DB session is held when `WriteQueue.submit` is called (snapshot is frozen dataclass). Patch session.close to count; should fire BEFORE submit.
> 10. **`test_create_from_run_emits_validation_suite_created_event`** — subscribe to event_bus; save; assert one `validation_suite_created` event with `suite_id`, `source_run_id`, `label`, `tolerance_abs`, `prompts_count`, `baseline_mean`, `project_id`. ALSO assert JSONL trace `phase="validation_suite"` per spec §9 trace tagging.
> 11. **`test_create_from_run_baseline_scores_uses_p5_overall_not_p5`** — seed run.aggregate with `p5_overall=6.2` (NOT `p5=6.2`). Save. Assert `suite.baseline_scores['p5_overall'] == 6.2` (canonical key naming).
>
> Tests should fail because `ValidationSuiteService` doesn't exist. Commit: `test(t2-c2): RED — create_from_run 11 tests`.

- [ ] Verify RED: 11 tests fail with appropriate "module not found" / attribute errors.

### Task 2.2 — GREEN dispatch

- [ ] **Dispatch fresh GREEN implementer**

> GREEN phase for v0.4.22 T2 Cycle 2. Write minimal implementation to pass the 11 RED tests.
>
> **Spec:** §4 Pydantic schemas + §5 `ValidationSuiteService.create_from_run` + `SuiteSnapshotInputs`.
>
> Steps:
> 1. Create `backend/app/schemas/validation_suite.py` with the Pydantic block from §4: `PromptSnapshotItem`, `PerPromptScore`, `BaselineScoresPayload`, `SaveSuiteRequest`, `ValidationSuiteOut` (WITH `model_config = ConfigDict(from_attributes=True)`), `ValidationSuiteListItem` (for Cycle 3), `ReplayRunOut` (for Cycle 5 — define here, used later), `RegressionAlarmEntry`/`RegressionAlarmBlock` (for Cycle 4/9).
> 2. Create `backend/app/services/validation_suite_service.py` with `SuiteSnapshotInputs` frozen dataclass + `ValidationSuiteService` class with `create_from_run()` method per spec §5 body.
> 3. Implement `_build_snapshot_inputs(run, label, tolerance_abs)` pure-function helper that extracts canonical 8 + `task_type_distribution` keys from `run.aggregate` and `per_prompt` from `run.prompt_results`.
> 4. Implement `_persist_suite_create(snapshot)` write-queue callback inside the service module.
> 5. Emit `validation_suite_created` event AFTER `write_queue.submit()` returns successfully.
> 6. Build `ValidationSuiteOut` via `model_validate({...})` from snapshot (NOT a custom `from_snapshot()` classmethod).
> 7. Run `pytest backend/tests/test_validation_suite_service.py -v -k create_from_run`; all 11 PASS.
>
> Commit: `feat(t2-c2): GREEN — ValidationSuiteService.create_from_run`.

- [ ] Verify GREEN: 11 tests pass.

### Tasks 2.3-2.7 — REFACTOR / INTEGRATE / OPERATE / Validator 1 / Validator 2

- [ ] **REFACTOR**: ruff + mypy on touched files; docstrings on service class + methods; error-path review (each `raise ValueError` has explicit code matching error envelope); DRY (extract `_build_snapshot_inputs` to module-level pure function for testability).
- [ ] **INTEGRATE** dispatch:
  > Verify A4 (`SuiteSnapshotInputs` populates ALL ValidationSuite columns; column-by-column diff against ORM); A5 (fixtures use real `ValidationSuite(...)` constructor, no MagicMock). Read `app/services/run_orchestrator.py:142-148` `_extract_probe_meta` for canonical mode-gating pattern + compare service's `if run.mode != "topic_probe"` guard. Read `models.py` `ValidationSuite` declaration + diff against `SuiteSnapshotInputs` fields.
- [ ] **OPERATE** dispatch:
  > O1 (SELECT from `validation_suite` by id post-create — confirm row exists with ALL columns populated; never trust function return value as evidence of persistence per `feedback_tdd_protocol.md` O1). Start stack, invoke create_from_run via Python REPL, query DB directly.
- [ ] **Validator 1 (spec)**: review §4 schemas + §5 service body matches implementation.
- [ ] **Validator 2 (quality)**: lint/mypy clean, no `type: ignore`, error envelope codes match `raise` strings.

---

## Cycle 3 — `.retire`, `.get`, `.list`, `.list_replays` + `SuiteRetireInputs` + `validation_suite_retire` op label

**Spec coverage:** §4 `RetireSuiteRequest`, `ValidationSuiteListItem`, `ValidationSuiteListResponse`, error code `suite_not_found`; §5 `retire()` body + `SuiteRetireInputs`; §10 Cycle 3.

**Files:**
- Modify: `backend/app/services/validation_suite_service.py` (add 4 methods)
- Modify: `backend/app/schemas/validation_suite.py` (add `RetireSuiteRequest`)
- Modify: `backend/tests/test_validation_suite_service.py` (add 9 tests)

### Task 3.1 — RED dispatch

- [ ] **Dispatch RED**

> Add 9 tests to `backend/tests/test_validation_suite_service.py`:
>
> 1. `test_retire_sets_retired_at_and_reason_writeonly` — first retire writes `retired_at`/`retired_reason`. Verify via DB.
> 2. `test_retire_does_not_mutate_other_columns` — assert all columns OTHER than `retired_at`/`retired_reason` unchanged.
> 3. `test_retire_is_idempotent_on_already_retired` — second retire returns success without writing; verify `retired_at` unchanged from first retire timestamp.
> 4. `test_retire_idempotent_no_event_emitted_on_re_retire` — subscribe to event_bus; first retire emits `validation_suite_retired` AND JSONL trace `phase="validation_suite"`; second retire emits ZERO events AND ZERO JSONL trace entries.
> 5. `test_retire_uses_validation_suite_retire_op_label` — patch `WriteQueue.submit`; assert `operation_label="validation_suite_retire"`.
> 6. `test_retire_raises_suite_not_found_on_missing` — `pytest.raises(ValueError, match="suite_not_found")`.
> 7. `test_get_returns_full_validation_suite_out_for_existing_id` — seed suite via create_from_run; call `.get(id)`; assert all fields match.
> 8. `test_list_filters_retired_by_default` — seed 5 suites, retire 2; `.list()` returns 3; `.list(include_retired=True)` returns 5.
> 9. `test_list_replays_returns_only_replay_run_mode_rows_for_suite` — seed suite + 3 `replay_run` rows + 2 `topic_probe` rows; `.list_replays(suite_id)` returns the 3 replay_run rows ordered by `started_at DESC`.
>
> Commit: `test(t2-c3): RED — retire/get/list/list_replays 9 tests`.

- [ ] Verify RED.

### Task 3.2 — GREEN

- [ ] **Dispatch GREEN**

> Implement `retire`, `get`, `list`, `list_replays` per spec §5. `SuiteRetireInputs` frozen dataclass. Add `_persist_suite_retire(inputs)` write-queue callback. `RetireSuiteRequest` Pydantic model in `schemas/validation_suite.py`. Run tests; 9 PASS. Commit: `feat(t2-c3): GREEN — retire/get/list/list_replays`.

- [ ] Verify GREEN.

### Tasks 3.3-3.7 — REFACTOR / INTEGRATE / OPERATE / Validators

- [ ] **REFACTOR**: lint, docstrings, error-path review on `suite_not_found` raise.
- [ ] **INTEGRATE**: A4 (retire updates ONLY `retired_at`/`retired_reason`); A2 (uses `WriteQueue.submit()`, not direct DB write).
- [ ] **OPERATE**: O1 (live verify idempotent re-retire emits zero events via event-bus subscription).
- [ ] **Validator 1 + 2** dispatches.

---

## Cycle 4 — `.compute_regression_alarm` + 30s TTL cache + `regression_alarm_transition` event

**Spec coverage:** §4 `RegressionAlarmEntry`, `RegressionAlarmBlock`; §5 alarm SQL + Python filter; §9 `regression_alarm_transition` event; §10 Cycle 4.

**Files:**
- Modify: `backend/app/services/validation_suite_service.py` (add `compute_regression_alarm` method + `_prior_alarm_states` instance state)
- Modify: `backend/tests/test_validation_suite_service.py` (add 7 tests)

### Task 4.1 — RED

- [ ] **Dispatch RED**

> Add 7 tests:
>
> 1. `test_compute_regression_alarm_returns_empty_block_with_no_replays` — seed 3 suites with no replays; assert `block.suites_total=3, suites_in_alarm=0, latest_alarms=[]`.
> 2. `test_compute_regression_alarm_excludes_retired_suites` — seed 5 suites, retire 2, all have replays; assert only 3 in result.
> 3. `test_compute_regression_alarm_uses_latest_replay_per_suite` — seed suite + 3 replays at distinct timestamps; assert latest_alarms entry uses MAX(started_at) replay's `aggregate.mean_overall`.
> 4. `test_compute_regression_alarm_fires_when_delta_exceeds_tolerance` — seed suite (`baseline_mean=7.85, tolerance_abs=0.5`) + replay (`mean_overall=7.21`); assert in `latest_alarms` with `delta_abs=-0.64`.
> 5. `test_compute_regression_alarm_does_not_fire_within_tolerance` — replay `mean_overall=7.50` (delta=-0.35 < 0.5 tolerance); assert NOT in `latest_alarms`.
> 6. `test_compute_regression_alarm_30s_ttl_cache_hits_on_second_call` — call twice within 30s; assert SQL query fires only once (patch session.execute count).
> 7. `test_regression_alarm_transition_event_fires_on_state_change_only` — first call seeds state. Second call with replay-firing emits one `regression_alarm_transition` event with `previous_state='nominal', new_state='firing'`. Third call with same firing state emits ZERO events. Fourth call with new replay (back to nominal) emits one event with `previous_state='firing', new_state='nominal'`.
>
> Commit: `test(t2-c4): RED — compute_regression_alarm 7 tests`.

- [ ] Verify RED.

### Task 4.2 — GREEN

- [ ] **Dispatch GREEN**

> Implement `compute_regression_alarm()` per spec §5 alarm SQL + Python filter. Use `cached_property`-style 30s `TTLCache` (matches `taxonomy/sub_domain_readiness.py` pattern). `_prior_alarm_states: dict[str, Literal['nominal','firing','none']]` instance state for transition detection. Emit `regression_alarm_transition` only on state-change suites. Run 7 tests; PASS. Commit.

- [ ] Verify GREEN.

### Tasks 4.3-4.7

- [ ] **REFACTOR**: lift query to module-level pure function for reuse.
- [ ] **INTEGRATE**: A3 (Python filter matches canonical alarm eval — diff against spec's SQL); A1 (all query columns consumed — none silently dropped).
- [ ] **OPERATE**: O4 (READ path under concurrent `replay_run` writes — race-free snapshot via WAL; concurrent writer harness with 30 `/api/health` polls during active replay). O6 (cross-process MCP replay writes do not corrupt alarm state).
- [ ] **Validators**.

---

## Cycle 5 — `routers/suites.py` (6 endpoints, rate limits, error envelopes)

**Spec coverage:** §4 REST surface table (6 endpoints), §4 error envelope (10 codes), §6 path placement (`/api/probes/{id}/replay` → `/api/suites/{id}/replay` ROADMAP-divergence note), §10 Cycle 5 (OPERATE LIGHT — placeholder dispatch).

**Files:**
- Create: `backend/app/routers/suites.py`
- Modify: `backend/app/main.py` (register router)
- Modify: `backend/app/schemas/runs.py` (extend 3 Literal sites with `replay_run`)
- Modify: `backend/app/routers/runs.py` (extend `list_runs::mode` Query Literal with `replay_run`)
- Create: `backend/tests/test_routers_suites.py`

### Task 5.1 — RED

- [ ] **Dispatch RED**

> Spec §4 routers/suites.py table — 6 endpoints, rate limits, error codes. Write 15 tests covering:
>
> **Save-as-suite (6):**
> 1. `test_save_as_suite_happy_path_returns_201_with_validation_suite_out`
> 2. `test_save_as_suite_invalid_label_returns_400` (label too long / empty)
> 3. `test_save_as_suite_invalid_tolerance_returns_400` (outside [0.1, 5.0])
> 4. `test_save_as_suite_run_not_found_returns_404`
> 5. `test_save_as_suite_combined_run_not_completed_or_not_a_probe_run_or_run_missing_aggregate_409` (one parameterized test covering all 3 service-raised 409s)
> 6. `test_save_as_suite_rate_limit_20_per_minute_returns_429_after_threshold`
>
> **Replay (4 — placeholder behavior per Cycle 5 GREEN-step shape):**
> 7. `test_replay_returns_202_with_replay_run_out_body_and_location_retry_after_headers`
> 8. `test_replay_inserts_initial_run_row_mode_replay_run_status_running_suite_id_set`
> 9. `test_replay_suite_not_found_returns_404` (combined with `suite_retired_returns_409` as parameterized)
> 10. `test_replay_rate_limit_5_per_minute_returns_429_after_threshold`
>
> **List / get / replays-list / retire (5):**
> 11. `test_get_suites_paginated_envelope_shape` (validation_suite_list_response fields)
> 12. `test_get_suite_by_id_returns_404_on_missing`
> 13. `test_get_suite_replays_returns_run_summary_paginated`
> 14. `test_retire_returns_200_idempotent`
> 15. `test_retire_invalid_reason_returns_400` (empty / >500 chars)
>
> **Immutability negative assertion (Cycle 5 RED test 15 doubles as scope-hardness gate)**: also assert `PATCH /api/suites/{id}` returns 405 (no PATCH route registered — locks the §14 NEVER list invariant that suites are immutable).
>
> **Topic-only-unavailable envelope reservation note**: the `topic_only_unavailable` 400 error code is provisioned in the error-envelope union for future kill-switch (per spec §4 + §14). T2 ships zero RED test for this code (kill-switch implementation deferred to T3+); the envelope reservation is the only T2 deliverable.
>
> Commit: `test(t2-c5): RED — routers/suites.py 15 tests`.

- [ ] Verify RED.

### Task 5.2 — GREEN

- [ ] **Dispatch GREEN**

> Create `backend/app/routers/suites.py` with 6 endpoints per spec §4. Use `slowapi` rate-limit decorators. Error envelope translates service `raise ValueError(code)` → HTTPException with `{code, message}` body. REPLAY ENDPOINT BEHAVIOR (per spec §10 Cycle 5 GREEN-step note): INSERT initial `RunRow(mode='replay_run', status='running', suite_id=...)` via WriteQueue.submit and return 202 — DO NOT spawn the generator (the `dispatch_async` spawn primitive lands in Cycle 8). The orphan `status='running'` row is reconciled by existing `_gc_orphan_runs` sweep within 1h until Cycle 6+8 fill in the real spawn. Extend `schemas/runs.py` 3 Literal sites + `routers/runs.py` mode Query Literal with `replay_run`. Register router in `main.py`. Run tests; 15 PASS. Commit: `feat(t2-c5): GREEN — routers/suites.py + Literal extensions`.

- [ ] Verify GREEN.

### Tasks 5.3-5.7

- [ ] **REFACTOR**: route handler docstrings + factor common Pydantic validation paths.
- [ ] **INTEGRATE**: A2 (use `WriteQueue.submit()` via `ValidationSuiteService` — no re-implementation); A4 (response payloads populate all fields).
- [ ] **OPERATE** (scoped — replay-endpoint end-to-end checks deferred to Cycle 8): O5 (client disconnect mid-request for save-as-suite/retire — no orphan rows on rate-limit-rejected writes); O8 partial (replay endpoint placeholder leaves orphan `running` row; verify `_gc_orphan_runs` reconciles); O4 (concurrent listings during active write_queue traffic).
- [ ] **Validators**.

---

## Cycle 6 — `ReplayRunGenerator` + lifespan registration + `_aggregate.py` shared helper

**Spec coverage:** §2 components `services/generators/replay_run_generator.py` + `_aggregate.py`; §5 ReplayRunGenerator body (sequential per-prompt, full collaborator graph, `PendingOptimization.trace_id` correlation, non-None final_report stub); §10 Cycle 6 (replay's 30-min worst case).

**Files:**
- Create: `backend/app/services/generators/replay_run_generator.py`
- Create: `backend/app/services/generators/_aggregate.py`
- Modify: `backend/app/services/generators/topic_probe_generator.py` (refactor `_build_aggregate` to thin delegate)
- Modify: `backend/app/main.py` (lifespan: extend `RunOrchestrator(generators={...})` dict with `"replay_run"`)
- Modify: `backend/app/services/run_orchestrator.py` (extend `_persist_final` signature with `mode: str`; extend `_create_row` to mode-gate `suite_id`; extend `_extract_probe_meta` for `grounding_mode`)
- Create: `backend/tests/test_replay_run_generator.py`

### Task 6.1 — RED

- [ ] **Dispatch RED**

> 11 tests per spec §5 ReplayRunGenerator + §10 Cycle 6:
>
> 1. `test_replay_run_generator_conforms_to_run_generator_protocol` — `isinstance(gen, RunGenerator)` (runtime_checkable).
> 2. `test_replay_run_generator_reads_suite_id_from_payload` — `request.payload["suite_id"]` extraction.
> 3. `test_replay_run_generator_loads_suite_snapshot_in_short_session` — patch session_factory; assert session closed before per-prompt loop.
> 4. `test_replay_run_generator_raises_suite_retired_on_retired_suite` — seed retired suite; assert `ValueError("suite_retired")`.
> 5. `test_replay_run_generator_emits_probe_warning_on_repo_drift` — suite.repo_full_name='owner/A', request.repo_full_name='owner/B'; assert one `probe_warning(code='repo_drift', ...)` event.
> 6. `test_replay_run_generator_sequential_per_prompt_with_try_except` — seed 3-prompt suite; force prompt 2 to raise; assert prompt_results has 3 entries with statuses ['completed', 'failed', 'completed']; aggregate.mean_overall computed from 2 completed.
> 7. `test_replay_run_generator_position_correspondence` — seed 10-prompt suite; assert `prompt_results[i].raw_prompt_idx == i`.
> 8. `test_replay_run_generator_uses_trace_id_not_id` — assert `prompt_results[i]["trace_id"] == pending_optimization.trace_id` (NOT `pending.id`).
> 9. `test_replay_run_generator_uses_overall_score_key` — assert per-prompt dict has `"overall_score"` key (matches canonical `_build_aggregate` input contract at `topic_probe_generator.py:414-418`).
> 10. `test_replay_run_generator_final_report_is_non_none_stub` — assert `GeneratorResult.final_report` is a non-None string (markdown stub).
> 11. `test_replay_run_generator_persists_via_replay_run_persist_op_label_and_is_registered_in_lifespan` — combined assertion: (a) start FastAPI app via TestClient; verify `app.state.run_orchestrator._generators["replay_run"]` is an instance of `ReplayRunGenerator` (locks Cycle 6 GREEN step 7); (b) trigger a replay; patch WriteQueue.submit; assert orchestrator's terminal-persist uses `operation_label="replay_run_persist"` (mode-keyed); (c) assert JSONL trace `phase="replay_run"` per spec §9 trace tagging. Combining lifespan + op-label + JSONL into one test preserves the 11-test count for cycle 6 (matches spec §10 sum invariant 7+11+9+7+15+11+9+7+5+9+18+14+7+3=132).
>
> Commit: `test(t2-c6): RED — ReplayRunGenerator 11 tests`.

- [ ] Verify RED.

### Task 6.2 — GREEN

- [ ] **Dispatch GREEN**

> Implement:
>
> 1. `backend/app/services/generators/_aggregate.py` — extract `compute_run_aggregate(prompt_results)` from `TopicProbeGenerator._build_aggregate` body (`topic_probe_generator.py:406-444`). Output keys: canonical 8 + `task_type_distribution: dict[str, int]` (defensive: `r.get("task_type", "unknown")`).
> 2. Refactor `TopicProbeGenerator._build_aggregate` to thin delegate calling `compute_run_aggregate`. Verify existing topic-probe tests still pass.
> 3. `backend/app/services/generators/replay_run_generator.py` — full class per spec §5 ReplayRunGenerator. `__init__(provider, prompt_loader, embedding_service, session_factory, taxonomy_engine, domain_resolver, context_service, write_queue)`. `run(request, *, run_id) -> GeneratorResult`. Sequential per-prompt loop with try/except. `tier='internal'` for deterministic regression detection. `historical_stats` pre-fetched once via `OptimizationService(read_db).get_score_distribution(exclude_scoring_modes=["heuristic"])`. Per-prompt dict uses `"overall_score"` key + `"trace_id": pending.trace_id`. Aggregate generator-added keys `replay_warnings`/`replay_suite_id`/`replay_n_completed`/`replay_n_failed` AFTER calling `compute_run_aggregate`. Final report: non-None stub `f"# Replay — suite_id={suite_id}\nSee SuiteDetailView for the baseline-vs-latest diff."`.
> 4. Extend `RunOrchestrator._persist_final` signature with `mode: str` parameter; update single caller at `:86`. Mode-keyed `operation_label` per spec §4: `replay_run_persist` for `mode='replay_run'`, existing label otherwise.
> 5. Extend `RunOrchestrator._create_row` body to add `suite_id=(request.payload.get("suite_id") if mode == "replay_run" else None)` to the `RunRow(...)` constructor.
> 6. Extend `RunOrchestrator._extract_probe_meta` to include `grounding_mode: request.payload.get("grounding_mode", "codebase")` in returned dict; preserve `mode == "topic_probe"` gating.
> 7. Extend `app/main.py:1191-1196` lifespan `RunOrchestrator(generators={...})` dict with `"replay_run": replay_run_gen`.
> 8. Run 11 RED tests + all existing run_orchestrator tests; PASS. Commit.

- [ ] Verify GREEN.

### Tasks 6.3-6.7

- [ ] **REFACTOR**: lint; service docstrings; sequential-loop comment justifying rejection of `asyncio.TaskGroup`.
- [ ] **INTEGRATE**:
  > A1 (read every field of `PendingOptimization` — 5 score dimensions + `trace_id` (not `.id`) + `task_type` per spec §10 Cycle 6).
  > A2 (use `batch_pipeline.run_single_prompt` — NOT hand-rolled inner pipeline; diff signature against `batch_pipeline.py:158-178`).
  > A3 (diff `GeneratorResult` return shape against `TopicProbeGenerator.run()` and `SeedAgentGenerator.run()`; `taxonomy_delta={}` and final_report stub justified inline).
  > A5 (replay tests construct real `ValidationSuite(...)` + `SuiteSnapshotInputs` — no MagicMock).
  > A6 (every prompt receives full enrichment context — same as canonical `batch_orchestrator.run_batch:113-126`).
- [ ] **OPERATE**:
  > O3 (per-prompt persistence routes through queue in short batches — replay's 30-min worst case must NOT hold any single write tx >5s; verify warm-engine debounce + hot-path commits don't starve under sequential-replay load).
  > O4 (warm-engine fires mid-replay — no contention).
  > O7 (replay 30-min worst case — every timeout in path: LLM provider, HTTP client, asyncio.wait_for ≥ 30min + buffer).
  > O8 (cancel mid-replay — `RunRow.status` reaches terminal `failed` via `asyncio.shield()` write; test by killing spawned task at every per-prompt boundary).
- [ ] **Validators**.

---

## Cycle 7 — Topic-only mode (`TopicProbeGenerator` branch + 2nd template + `probe_generation` signature + `ProbeContext` schema extension)

**Spec coverage:** §2 `schemas/probes.py` + `services/probe_generation.py` + `topic_probe_generator.py` rows; §5 topic-only generator branch + ProbeContext schema extensions + probe_generation signature extensions; §10 Cycle 7.

**Files:**
- Modify: `backend/app/schemas/probes.py` (relax `repo_full_name` to `str | None`; add `commit_sha: str | None = None`, `topic_only: bool = False`)
- Modify: `backend/app/services/probe_generation.py` (add `mode` + `template_name` kwargs; topic-only inverts F1 backtick filter direction)
- Modify: `backend/app/services/generators/topic_probe_generator.py` (run() branches on grounding_mode; safe intent_hint coercion; scope str default)
- Create: `prompts/probe-agent-topic-only.md`
- Modify: `prompts/manifest.json` (add new template entry)
- Create: `backend/tests/test_topic_only_mode.py`

### Task 7.1 — RED

- [ ] **Dispatch RED**

> 9 tests:
>
> 1. `test_probe_request_with_grounding_mode_topic_only_skips_phase_1` — assert no `probe_grounding` event emitted; ProbeContext has `topic_only=True`, empty `relevant_files`, `repo_full_name=None`.
> 2. `test_probe_request_without_grounding_mode_defaults_to_codebase` — backward-compat for existing callers.
> 3. `test_probe_request_grounding_mode_topic_only_bypasses_link_repo_first` — no linked repo + grounding_mode='topic_only' returns 202/SSE-start, NOT 400 link_repo_first.
> 4. `test_topic_only_selects_probe_agent_topic_only_md_template` — patch generate_probe_prompts; assert `template_name="probe-agent-topic-only.md"`.
> 5. `test_topic_only_inverts_f1_backtick_filter` — generate batch with backtick-heavy prompts under topic_only mode; assert prompts with backticks are dropped (inverse of codebase mode).
> 6. `test_topic_only_preserves_batch_drop_threshold_50pct` — assert `_DROP_THRESHOLD=0.5` still applies (>50% drop → ProbeGenerationError).
> 7. `test_safe_intent_hint_coercion_replaces_none_with_explore` — request with `intent_hint=None`; ProbeContext.intent_hint='explore' (default).
> 8. `test_probe_context_extensions_accept_topic_only_and_commit_sha` — instantiate ProbeContext with `topic_only=True, commit_sha=None, repo_full_name=None`; no Pydantic ValidationError.
> 9. `test_grounding_mode_persisted_in_run_row_topic_probe_meta` — run topic_only probe; query `RunRow.topic_probe_meta`; assert `{"grounding_mode": "topic_only", ...}`.
>
> Commit: `test(t2-c7): RED — topic-only mode 9 tests`.

- [ ] Verify RED.

### Task 7.2 — GREEN

- [ ] **Dispatch GREEN**

> 1. Extend `schemas/probes.py` ProbeContext: `repo_full_name: str | None = None`, add `commit_sha: str | None = None`, `topic_only: bool = False`. Preserve `model_config = {"extra": "forbid"}`.
> 2. Extend `probe_generation.py` `generate_probe_prompts()` signature with `mode: Literal['codebase', 'topic_only'] = 'codebase'` + `template_name: str = 'probe-agent.md'`. Topic-only branch: predicate becomes `_lacks_backtick` (drop prompts WITH backticks); threshold `_DROP_THRESHOLD=0.5` unchanged.
> 3. Create `prompts/probe-agent-topic-only.md` template (no code references; instructions to generate prompts without backtick code citations).
> 4. Add manifest entry in `prompts/manifest.json`.
> 5. Extend `TopicProbeGenerator.run()` to branch on `request.payload.get("grounding_mode", "codebase")` per spec §5. Safe intent_hint coercion + scope str default per spec §5 body.
> 6. (Already in Cycle 6) `_extract_probe_meta` extended with `grounding_mode` — verify it lands in RunRow.topic_probe_meta.
> 7. Run 9 tests; PASS. Commit.

- [ ] Verify GREEN.

### Tasks 7.3-7.7

- [ ] **REFACTOR**: docstrings on new ProbeContext fields, template manifest validation.
- [ ] **INTEGRATE**: A2 (don't re-implement Phase 2 generation — call existing `generate_probe_prompts` with new kwargs); A3 (consume same return shape both modes).
- [ ] **OPERATE**: O1 (RunRow.topic_probe_meta.grounding_mode='topic_only' persisted and queryable via `GET /api/runs/{id}`).
- [ ] **Validators**.

---

## Cycle 8 — 202+polling on `POST /api/probes` + `dispatch_async()` + `_run_to_completion` + `run()` refactor

**Spec coverage:** §5 `RunOrchestrator` extensions (`dispatch_async`, `_run_to_completion`, `_run_generator_and_persist`, `run()` thin wrapper with `done_event`); §8 202+polling architecture; §10 Cycle 8.

**Files:**
- Modify: `backend/app/services/run_orchestrator.py` (new `dispatch_async`, `_run_to_completion`, `_run_generator_and_persist`; refactor `run()`)
- Modify: `backend/app/routers/probes.py` (Prefer: respond-async header → 202)
- Create: `backend/tests/test_probes_202_polling.py`

### Task 8.1 — RED

- [ ] **Dispatch RED**

> 7 tests:
>
> 1. `test_post_probes_without_prefer_header_returns_sse_unchanged` — default SSE behavior preserved.
> 2. `test_post_probes_with_prefer_respond_async_returns_202_with_location_retry_after` — assert 202 + `Location: /api/probes/{run_id}` + `Retry-After: 5` + body `{run_id, status, poll_url}`.
> 3. `test_post_probes_with_prefer_respond_async_initial_insert_awaited_before_response` — race-stress: dispatch_async + immediate `GET /api/probes/{run_id}` 1000 iterations; 100% find the row.
> 4. `test_spawned_task_survives_caller_connection_close` — `curl --max-time 0.5`; assert RunRow eventually reaches terminal status (no orphan).
> 5. `test_run_method_blocks_until_terminal_via_done_event` — synchronous test caller invokes `run()`; assert it blocks until terminal status; reload returns final RunRow.
> 6. `test_current_run_id_contextvar_set_inside_spawned_task` — patch event_logger.log_decision to capture context; assert all events from spawned task have correct `run_id`.
> 7. `test_dispatch_async_cancellation_marks_failed_via_shield` — cancel the spawned task at every per-prompt yield boundary; assert RunRow ends at `status='failed' or 'partial'`, never orphan `running`.
>
> Commit: `test(t2-c8): RED — 202+polling + dispatch_async 7 tests`.

- [ ] Verify RED.

### Task 8.2 — GREEN

- [ ] **Dispatch GREEN**

> 1. Add `dispatch_async(self, *, mode, request, run_id, done_event=None)` to `RunOrchestrator`. Awaits `_create_row()` only, then `asyncio.create_task(self._run_to_completion(...))`.
> 2. Add `_run_to_completion(self, *, mode, request, run_id, done_event=None)` per spec §5 body. `token = current_run_id.set(run_id)` inside spawned task. `contextlib.suppress(Exception)` + `asyncio.shield(_mark_failed)` on CancelledError/Exception (mirror existing pattern). `done_event.set()` in finally block if not None.
> 3. Add `_run_generator_and_persist(self, mode, request, run_id)` helper extracted from `run()` body.
> 4. Refactor `run()` to thin wrapper per spec §5 example: create `asyncio.Event()`, call `dispatch_async(done_event=done)`, await `done.wait()`, return `self._reload(run_id)`.
> 5. Extend `routers/probes.py` to accept `Prefer: respond-async` Header param. If present: call `dispatch_async()` and return 202 + Location + Retry-After. If absent: existing SSE entry path unchanged.
> 6. Run 7 tests; PASS. Existing run_orchestrator tests + SSE tests also PASS. Commit.

- [ ] Verify GREEN.

### Tasks 8.3-8.7

- [ ] **REFACTOR**: docstrings on new methods.
- [ ] **INTEGRATE**: A2 (dispatch_async reuses existing `_create_row` + `_persist_final` lifecycle; SSE entry path refactors to internally call dispatch_async — single-codepath body).
- [ ] **OPERATE**: O1 (poll endpoint reflects committed row); O5 (curl --max-time shorter than initial INSERT — server state consistent; **plus deferred Cycle-5 check: curl --max-time 0.1 against POST /api/suites/{id}/replay — no orphan rows**); O7 (inventory every timeout in dispatch_async path); O8 (spawned task survives connection close; **plus deferred Cycle-5 check: replay endpoint INSERT-then-spawn cancellation reconciled by `_gc_orphan_runs`**).
- [ ] **Validators**.

---

## Cycle 9 — `/api/health` `regression_alarm` block

**Spec coverage:** §3 `regression_alarm` JSON block shape; §10 Cycle 9.

**Files:**
- Modify: `backend/app/routers/health.py`
- Create: `backend/tests/test_health_regression_alarm.py`

### Task 9.1 — RED

- [ ] **Dispatch RED**

> 5 tests:
>
> 1. `test_health_regression_alarm_block_empty_when_no_suites` — assert `block.suites_total=0, suites_in_alarm=0, latest_alarms=[]`.
> 2. `test_health_regression_alarm_block_nominal_with_replays` — seed 3 suites + 3 nominal replays; `suites_in_alarm=0`.
> 3. `test_health_regression_alarm_block_firing_includes_in_latest_alarms` — seed firing replay; assert in `latest_alarms` with correct delta_abs.
> 4. `test_health_regression_alarm_30s_ttl_cache_respected` — call /api/health twice within 30s; assert backend SQL fires only once.
> 5. `test_health_regression_alarm_uses_existing_response_shape` — assert returned block matches `RegressionAlarmBlock` Pydantic shape.
>
> Commit: `test(t2-c9): RED — health regression_alarm 5 tests`.

- [ ] Verify RED.

### Task 9.2 — GREEN

- [ ] **Dispatch GREEN**

> Extend `/api/health` response to include `regression_alarm` block from `ValidationSuiteService.compute_regression_alarm()`. Run 5 tests; PASS. Commit.

- [ ] Verify GREEN.

### Tasks 9.3-9.7

- [ ] **REFACTOR**: ensure block is optional / safe-default if service unavailable.
- [ ] **INTEGRATE**: A4 (RegressionAlarmEntry populates all required fields from joined query — no NULL leakage).
- [ ] **OPERATE**: O4 (alarm-query READ path coexists with active replays).
- [ ] **Validators**.

---

## Cycle 10 — MCP tools (`synthesis_save_suite` + `synthesis_replay_suite`)

**Spec coverage:** §4 MCP tools (15→17), §10 Cycle 10.

**Files:**
- Create: `backend/app/tools/save_suite.py`
- Create: `backend/app/tools/replay_suite.py`
- Modify: `backend/app/mcp_server.py` (register 2 new `@mcp.tool` decorators)
- Modify: `backend/app/schemas/mcp_models.py` (add `SaveSuiteOutput`, `ReplayInitiatedOutput`)
- Create: `backend/tests/test_mcp_tools_save_replay.py`

### Task 10.1 — RED

- [ ] **Dispatch RED**

> 9 tests:
>
> 1. `test_synthesis_save_suite_structured_output_shape` — assert `SaveSuiteOutput` returned (NOT *Result).
> 2. `test_synthesis_save_suite_returns_run_not_completed_error_envelope` — invoke against running run; assert structured ToolError with code='run_not_completed'.
> 3. `test_synthesis_save_suite_returns_run_missing_aggregate_error_envelope` — completed run without aggregate; assert ToolError.
> 4. `test_synthesis_save_suite_persists_via_validation_suite_service` — patch service.create_from_run; assert called with correct args.
> 5. `test_synthesis_replay_suite_returns_replay_initiated_output_with_202_semantics` — assert `ReplayInitiatedOutput{run_id, suite_id, mode: 'replay_run', poll_url, started_at}` returned.
> 6. `test_synthesis_replay_suite_returns_suite_retired_error_envelope_on_retired` — invoke against retired suite; assert ToolError code='suite_retired'.
> 7. `test_synthesis_replay_suite_uses_replay_run_persist_op_label` — patch WriteQueue; assert label.
> 8. `test_synthesis_replay_suite_dispatches_through_run_orchestrator` — patch `app.state.run_orchestrator.dispatch_async`; assert called with mode='replay_run'.
> 9. `test_mcp_tool_count_is_now_17` — invoke MCP tool list; assert exactly 17 tool names (15 existing + 2 new).
>
> Commit: `test(t2-c10): RED — MCP save_suite + replay_suite 9 tests`.

- [ ] Verify RED.

### Task 10.2 — GREEN

- [ ] **Dispatch GREEN**

> 1. Add `SaveSuiteOutput` + `ReplayInitiatedOutput` to `schemas/mcp_models.py` (Pydantic, `*Output` suffix matching dominant convention).
> 2. Create `app/tools/save_suite.py` with `handle_save_suite(...)` async function calling `ValidationSuiteService.create_from_run()`.
> 3. Create `app/tools/replay_suite.py` with `handle_replay_suite(...)` async function calling `RunOrchestrator.dispatch_async(mode='replay_run', ...)`.
> 4. Register both in `mcp_server.py` with `@mcp.tool(structured_output=True)` decorators. Use `Annotated[..., Field(...)]` boundary validation per existing pattern.
> 5. Run 9 tests; PASS. Commit.

- [ ] Verify GREEN.

### Tasks 10.3-10.7

- [ ] **REFACTOR**: error envelope normalization across both tools.
- [ ] **INTEGRATE**: A2 (handlers call ValidationSuiteService methods — don't re-implement); A4 (output payloads populate all fields); A5 (fixtures use real RunRow + ValidationSuite rows).
- [ ] **OPERATE**: O6 (cross-process MCP→backend bridge for `replay_run` writes works under load — concurrent MCP `synthesis_replay_suite` from 3 clients); O5 (MCP client disconnects after `synthesis_save_suite` returns — write committed; verify by inspecting DB).
- [ ] **Validators**.

---

## Cycle 11 — Frontend Topic Probe tab + form + report card

**Spec coverage:** §6 NEW probes components, §6 voice + density + 5-state + a11y, §6 forge-spark keyframe def.

**Files:**
- Create: `frontend/src/lib/components/probes/TopicProbeForm.svelte`
- Create: `frontend/src/lib/components/probes/TopicProbeProgressView.svelte`
- Create: `frontend/src/lib/components/probes/TopicProbeReportCard.svelte`
- Create: `frontend/src/lib/components/probes/TaxonomyMiniView.svelte`
- Modify: `frontend/src/lib/components/taxonomy/SeedModal.svelte` (3rd tab)
- Modify: `frontend/src/app.css` (NEW @keyframes forge-spark)
- Create: `frontend/src/lib/api/suites.ts` (per-domain module)
- Test: `frontend/src/lib/components/probes/*.test.ts`

### Task 11.1 — RED

- [ ] **Dispatch RED**

> 18 Vitest + Playwright tests covering:
>
> Form (5): topic textarea validation 3-500 chars; N slider 5-25; intent dropdown; grounding-mode segmented control; submit-button enabled/disabled per linked-repo state.
>
> ProgressView (3): per-prompt strip cells fill on SSE events; taxonomy mini-view nodes flash-in with forge-spark; progress source-agnostic (SSE vs poll).
>
> ReportCard (4): renders top-3 prompts + score distribution + taxonomy delta + follow-ups; Save-as-Suite click triggers save + toast; Copy-md icon triggers copy-flash (green 1500ms); Replay button triggers replay endpoint.
>
> TaxonomyMiniView (2): emergent node entry animation **250ms forge-spark** (matches canonical @keyframes forge-spark duration per spec §2 + file-structure table + Cycle 11 GREEN); NEW chip on <30s-old nodes.
>
> SeedModal (2): third tab renders; tab typography matches shipped .seed-tab (font-mono + letter-spacing 0.05em — NOT font-display).
>
> Forge-spark keyframe (2): @keyframes forge-spark defined in app.css; consumed by Save-Suite button via animation: forge-spark 250ms ease-out.
>
> Commit: `test(t2-c11): RED — Topic Probe UI 18 tests`.

- [ ] Verify RED.

### Task 11.2 — GREEN

- [ ] **Dispatch GREEN**

> 1. Create the 4 NEW Svelte components per spec §6 NEW components table. All h-5 buttons, p-1.5 sidebar containers, font-mono numerics, font-display section headings.
> 2. Extend SeedModal: add 3rd tab `topic_probe` with `mode = $state<'generate' | 'provide' | 'topic_probe'>('generate')`. Tab typography uses shipped `.seed-tab` font-mono pattern (canonical font-display migration deferred to T4).
> 3. Add `@keyframes forge-spark` to `frontend/src/app.css` per spec §2: `0%: scale(1) rotate(0); 25%: scale(1.2) rotate(3deg) + yellow flash via color/background-color; 100%: scale(1) rotate(0). 250ms ease-out single-shot.`
> 4. Create `frontend/src/lib/api/suites.ts` per-domain module per spec §6 with `apiFetch` import (not `client`).
> 5. Run 18 tests; PASS. Commit.

- [ ] Verify GREEN.

### Tasks 11.3-11.7

- [ ] **REFACTOR**: Svelte component lint (`svelte-check`); shared sub-components extracted.
- [ ] **INTEGRATE**: A2 (use per-domain `lib/api/` modules — `runs.ts`/`seed.ts`/`suites.ts` — NOT a monolithic api.ts); A3 (consume canonical `RunListResponse`/`RunSummary`/`RunResult` shapes — no fictional `RunOut`).
- [ ] **OPERATE**: O1 (browser smoke: SeedModal renders 3rd tab, form submits, report card displays all fields populated from RunResult).
- [ ] **Validators**.

---

## Cycle 12 — Frontend SuitesPanel + SuiteRow + SuiteDetailView + RegressionBadge + SuitesStore + doc-sync

**Spec coverage:** §6 NEW suites components, §11 pre-release item 11 doc-sync items.

**Files:**
- Create: `frontend/src/lib/components/suites/{SuitesPanel,SuiteRow,SuiteDetailView,RegressionBadge}.svelte`
- Modify: `frontend/src/lib/components/layout/StatusBar.svelte` (mount RegressionBadge)
- Create: `frontend/src/lib/stores/suitesStore.ts`
- DOC-SYNC: `.claude/skills/brand-guidelines/SKILL.md` (lines 79/214/375 5-dir → 7-dir; line 140 22px → 20px)
- DOC-SYNC: `.claude/skills/brand-guidelines/references/component-patterns.md` (line 219 600ms → 1500ms; line 223 forge-spark "varies" → "250ms")
- DOC-SYNC: `backend/CLAUDE.md` (T2/T3/T4 minor numbers; line 143 probe_taxonomy_change reference removed)
- DOC-SYNC: `docs/ROADMAP.md` (line 333 path placement)
- Test: `frontend/src/lib/components/suites/*.test.ts` + store test

### Task 12.1 — RED

- [ ] **Dispatch RED**

> 14 Vitest + Playwright tests:
>
> **SuitesPanel (3):**
> 1. `test_suites_panel_renders_with_no_suites_empty_state` — assert "No suites. Save a probe to create one." copy.
> 2. `test_suites_panel_filters_by_project_id_when_set` — multi-project ADR-005 filter applies.
> 3. `test_suites_panel_navigator_entry_routes_correctly` — clicking SUITES nav entry renders SuitesPanel (verifies the Navigator EXTEND in File Structure).
>
> **SuiteRow (3):**
> 4. `test_suite_row_h_5_data_row_with_status_dot_and_delta` — h-5 height + status dot (6px green/red/grey) + delta text.
> 5. `test_suite_row_recipe_a_hover_border_plus_bg_tint_no_translate_y` — border-accent + bg-bg-hover/40; assert no `translateY` set.
> 6. `test_suite_row_click_opens_suite_detail_view` — click navigates to detail.
>
> **SuiteDetailView (2):**
> 7. `test_suite_detail_view_replay_history_table_renders_run_summary_rows` — paginated runs.
> 8. `test_suite_detail_view_per_prompt_baseline_vs_latest_diff_renders` — pairs `baseline_scores.per_prompt[i].overall` with latest replay's `aggregate.replay_per_prompt[i].overall` + delta column.
>
> **RegressionBadge (4):**
> 9. `test_regression_badge_nominal_lower_case_12_ok` — matches shipped `statusLabel()` lower-case pattern.
> 10. `test_regression_badge_firing_lower_case_2_alarm` — same.
> 11. `test_regression_badge_edge_to_edge_inside_status_bar` — px-1.5 py-0 inside shipped 20px bar.
> 12. `test_regression_badge_nominal_to_firing_transition_fires_forge_spark` — animate `@keyframes forge-spark` 250ms once on state change.
>
> **SuitesStore (2):**
> 13. `test_suites_store_polls_health_for_regression_alarm_state` — auto-polls /api/health every 30s.
> 14. `test_suites_store_refreshes_on_taxonomy_changed_sse` — SSE event triggers immediate refresh.
>
> Commit: `test(t2-c12): RED — SuitesPanel + RegressionBadge 14 tests`.

- [ ] Verify RED.

### Task 12.2 — GREEN

- [ ] **Dispatch GREEN**

> Build the 4 NEW Svelte components per spec §6 + §6 voice + §6 5-state machine. Mount RegressionBadge in `layout/StatusBar.svelte`. Create `suitesStore.ts`. Apply doc-sync updates per §11 item 11.
>
> Commit: `feat(t2-c12): GREEN — SuitesPanel + RegressionBadge + doc-sync`.

- [ ] Verify GREEN.

### Tasks 12.3-12.7

- [ ] **REFACTOR**: svelte-check on all new components; brand audit lint locally.
- [ ] **INTEGRATE**: A3 (suite list rows render every field of ValidationSuiteListItem; SuiteDetailView consumes full ValidationSuiteOut including baseline_scores.per_prompt).
- [ ] **OPERATE**: O1 (browser smoke: SuitesPanel renders for empty/nominal/firing states; RegressionBadge transitions correctly on SSE-emitted `regression_alarm_transition` events).
- [ ] **Validators**.

---

## Cycle 13 — 202+polling abstraction in `probesStore`

**Spec coverage:** §6 NEW + EXTENDED stores, §6 polling integration, §10 Cycle 13.

**Files:**
- Modify: `frontend/src/lib/stores/probesStore.ts`
- Test: `frontend/src/lib/stores/probesStore.test.ts`

### Task 13.1 — RED

- [ ] **Dispatch RED**

> 7 Vitest tests:
>
> 1. `test_run_probe_returns_async_iterable` — `for await (const e of probesStore.runProbe(req))` yields events.
> 2. `test_run_probe_n_prompts_gt_10_auto_attaches_prefer_respond_async_header` — verify fetch call options include `headers: { 'Prefer': 'respond-async' }`.
> 3. `test_run_probe_n_prompts_le_10_falls_through_to_sse_path` — no Prefer header sent.
> 4. `test_run_probe_202_path_polls_every_5s_via_retry_after` — mock 202 response; assert subsequent `GET /api/probes/{id}` fires every 5s (Retry-After).
> 5. `test_run_probe_browser_tab_close_cancels_poll_interval` — abort signal cancels in-flight poll; no leaked timers.
> 6. `test_run_probe_sse_and_poll_paths_emit_identical_event_shapes` — consumer reading async-iterable cannot distinguish source.
> 7. `test_run_probe_never_times_out_client_side_during_long_probes` — 10-min mock probe; no client-side timeout.
>
> Commit: `test(t2-c13): RED — probesStore 202+polling 7 tests`.

- [ ] Verify RED.

### Task 13.2 — GREEN

- [ ] **Dispatch GREEN**

> Implement `probesStore.runProbe()` async-iterable abstraction per spec §6. SSE + poll paths emit identical event shapes; consumers don't branch on source. Commit.

- [ ] Verify GREEN.

### Tasks 13.3-13.7

- [ ] **REFACTOR**: store types + JSDoc.
- [ ] **INTEGRATE**: A2 (single async-iterable contract — no two-codepath divergence in consumers).
- [ ] **OPERATE**: O5 (browser tab closes mid-probe — store cleans up poll interval); O7 (long probe runs >10min — poll cadence persists).
- [ ] **Validators**.

---

## Cycle 14 — Brand + a11y audit (GATE CYCLE — Validator 1 + Validator 2 only)

**Spec coverage:** §6 banned-list + 5-state machine + a11y; §11 pre-release item 4; §13 success criterion 8.

**NO RED → GREEN code change.**

### Task 14.1 — Validator 1 (brand audit)

- [ ] **Dispatch fresh general-purpose subagent**

> Brand-canon audit for v0.4.22 T2 Cycle 14.
>
> Read `.claude/skills/brand-guidelines/SKILL.md` + `references/component-patterns.md`.
>
> Run grep against `frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/` (taxonomy/ EXCLUDED per 3D-scope canon):
>
> ```bash
> grep -rE "box-shadow:.*[1-9]px [1-9]" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/
> grep -rE "text-shadow|filter:[[:space:]]*drop-shadow" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/
> grep -rE "(?<![-\w])(glow|halo|bloom|radiance|breathing|dust|pulse|flash)(?![-\w])" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/
> ```
>
> Expected: zero hits across all 3 sweeps. Hyphenated canon (`copy-flash`, `forge-spark`, `useCopyFlash`) is permitted via word-boundary regex.
>
> Also verify: density pins (h-5 buttons, h-7 tabs, p-1.5 sidebar), motion canon (uniform-duration multi-property transitions, canonical animation names), 5-state machine focus/disabled inheritance, casing conventions (UPPERCASE buttons, lower-case status badges), `frontend/src/lib/api/suites.ts` per-domain module path (no monolithic api.ts).
>
> Report APPROVED-ZERO-INCONSISTENCIES or list findings.

- [ ] Verify Validator 1 ZERO. If FINDINGS, route fixes back to the cycle owning the offending code (typically 11-13).

### Task 14.2 — Validator 2 (a11y audit)

- [ ] **Dispatch fresh general-purpose subagent**

> A11y audit for v0.4.22 T2 Cycle 14.
>
> Run axe-core CI on all new components in `frontend/src/lib/components/{probes,suites}/`.
>
> Verify: `:focus-visible` outline on every interactive element; `prefers-reduced-motion → 0.01ms` on all keyframes including `@keyframes forge-spark`; `aria-live="polite"` `role="status"` on RegressionBadge; aria-label parity on icon-only buttons; color paired with text (`● 2 alarm` not just `●`).
>
> Report APPROVED-ZERO-INCONSISTENCIES or list findings.

- [ ] Verify Validator 2 ZERO. Cycle 14 complete when both validators clean.

---

## Cycle 15 — Audit-hook WARN→RAISE flip + 3 new tests + extended P4 integration test

**Spec coverage:** §7 audit-hook flip mechanics + soak gate + kill-switch; §10 Cycle 15.

**Prerequisite:** ≥2026-05-18 (7-day post-v0.4.21 soak window closed). Soak grep: `grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100` returns zero unexpected sources.

**Files:**
- Modify: `backend/app/config.py` (flip default)
- Create: `backend/tests/integration/test_audit_hook_full_t2_pipeline.py` (3 new tests + existing P4 test extension)

### Task 15.1 — RED

- [ ] **Dispatch RED**

> 3 NEW tests in `backend/tests/integration/test_audit_hook_full_t2_pipeline.py`:
>
> 1. `test_audit_hook_default_is_raise` — `Settings()` (no env overrides); assert `settings.WRITE_QUEUE_AUDIT_HOOK_RAISE is True` (locks the flipped default).
> 2. `test_audit_hook_emits_zero_warn_under_full_t2_pipeline` (decorated `@pytest.mark.integration`) — exercise EVERY T2 write path under `WRITE_QUEUE_AUDIT_HOOK_RAISE=True`: save-as-suite, replay, topic-only probe, retire, regression-alarm computation. Assert zero `read-engine audit:` WARN lines in `data/backend.log` for the run.
> 3. `test_audit_hook_kill_switch_env_var_reverts_to_warn` — set env `WRITE_QUEUE_AUDIT_HOOK_RAISE=false`; instantiate Settings; assert audit hook reverts to WARN-only behavior (matching pre-v0.4.22 v0.4.21 semantics).
>
> Existing P4 test `test_audit_hook_emits_zero_warn_under_full_pipeline` STAYS UNCHANGED.
>
> Commit: `test(t2-c15): RED — audit-hook RAISE flip 3 tests`.

- [ ] Verify RED.

### Task 15.2 — GREEN

- [ ] **Pre-flip soak verification** (manual gate — required for Cycle 15 GREEN to proceed):

```bash
# Run on or after 2026-05-18 (≥7 days post-v0.4.21 ship date 2026-05-11)
grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100
# Expect: zero new WARN sources beyond known/explained ones
```

If any unexpected WARN source appears, BLOCK Cycle 15 and restructure the offending write path BEFORE flipping.

- [ ] **Dispatch GREEN**

> One-line change in `backend/app/config.py`:
>
> ```python
> WRITE_QUEUE_AUDIT_HOOK_RAISE: bool = Field(
>     default=True,   # was False — flip per v0.4.22 post-soak gate
>     description="If True (CI/prod), audit hook RAISES WriteOnReadEngineError on read-engine writes outside the allow-list; otherwise WARN. Kill-switch: set WRITE_QUEUE_AUDIT_HOOK_RAISE=false to revert.",
> )
> ```
>
> Run all 3 new RED tests; PASS. Also re-run existing P4 test `test_audit_hook_emits_zero_warn_under_full_pipeline`; PASS.
>
> Commit: `feat(t2-c15): GREEN — audit-hook WARN→RAISE flip (default=True)`.

- [ ] Verify GREEN: all 4 audit-hook tests pass (3 new + 1 existing P4).

### Tasks 15.3-15.7

- [ ] **REFACTOR**: docstring on `Field(description=...)` covers kill-switch path.
- [ ] **INTEGRATE**: A2 (no behavioral logic change — only the `default=False` → `default=True` flip; existing logic at `database.py:395-397` unchanged).
- [ ] **OPERATE**: **All O1-O8** — the extended integration test exercises every T2 write path under `WRITE_QUEUE_AUDIT_HOOK_RAISE=True` (save-as-suite, replay, topic-only probe, retire, regression-alarm computation) and asserts zero `read-engine audit:` lines. Existing P4 test must continue PASSing.
- [ ] **Validators**.

---

## Cycle 16 — E2E validation workflow + soak grep verification (GATE CYCLE — Validator 1 + Validator 2 only)

**Spec coverage:** §11 pre-release checklist items 1-11, §13 success criteria.

**NO RED → GREEN code change.**

### Task 16.1 — Validator 1 (E2E round-trip)

- [ ] **Dispatch fresh general-purpose subagent**

> E2E validation for v0.4.22 T2 Cycle 16.
>
> Manual E2E smoke per spec §13 success criterion 4:
>
> 1. Start full stack: `./init.sh restart`
> 2. Run T1 topic probe via `synthesis_probe` MCP tool or `POST /api/probes` (any non-trivial topic).
> 3. Wait for completion; verify `RunRow(mode='topic_probe', status='completed')` row exists.
> 4. POST /api/probes/{id}/save-as-suite with label='test-suite' tolerance_abs=0.5. Verify `ValidationSuite` row created.
> 5. Manually degrade scoring weights (edit `backend/app/services/score_blender.py` to artificially lower all scores by 1.0 point; restart backend).
> 6. POST /api/suites/{id}/replay. Wait for completion.
> 7. GET /api/health; verify `regression_alarm.suites_in_alarm >= 1` and alarm entry includes this suite with `delta_abs <= -1.0`.
> 8. Revert scoring weights; restart backend.
> 9. POST /api/suites/{id}/replay again. GET /api/health; verify `regression_alarm.suites_in_alarm == 0` (alarm clears).
>
> Report APPROVED-ZERO-INCONSISTENCIES or list findings.

- [ ] Verify Validator 1 ZERO: round-trip works end-to-end.

### Task 16.2 — Validator 2 (soak grep + pre-release checklist)

- [ ] **Dispatch fresh general-purpose subagent**

> Pre-release checklist verification for v0.4.22 T2 Cycle 16.
>
> Run the 11 pre-release checklist items from spec §11:
>
> 1. `grep -E "audit_drift|read-engine audit:" data/backend.log | tail -100` — zero unexpected sources in last 7 days.
> 2. All 16 cycles GREEN: `cd backend && pytest --cov=app -v` ≥90% backend coverage.
> 3. `cd frontend && npm run test:coverage` ≥80% frontend coverage.
> 4. Brand audit: `grep -rE "box-shadow.*[1-9]px [1-9]|text-shadow|filter:[[:space:]]*drop-shadow|(?<![-\w])(glow|halo|bloom|radiance|breathing|dust|pulse|flash)(?![-\w])" frontend/src/lib/components/{layout,editor,refinement,shared,landing,probes,suites}/` returns zero hits.
> 5. A11y audit pass — axe-core CI clean.
> 6. `cd backend && alembic check` exit 0.
> 7. `./init.sh restart` smoke test — all services green.
> 8. E2E validation per Cycle 16 Task 16.1 (already done).
> 9. Backward-compat smoke: `POST /api/probes` without `Prefer:` header — SSE response unchanged from v0.4.21.
> 10. CHANGELOG entry written under `## v0.4.22 — YYYY-MM-DD` per canon voice (no first-person, no "we observed").
> 11. Stale-doc fixes: backend/CLAUDE.md T2/T3/T4 numbers harmonized + line 143 probe_taxonomy_change removed; docs/ROADMAP.md line 333 path updated; SKILL.md lines 79/140/214/375 + component-patterns.md lines 219/223 updated.
> 12. CHANGELOG voice grep — `grep -niE "\\b(we|i|our|us|I'd|I'll|let's|you should|let me|here is what)\\b" docs/CHANGELOG.md | head` returns ZERO hits inside the `## v0.4.22` block (canon-voice gate per `feedback_changelog_voice.md` memory; first-person + conversational framing banned).
> 13. `docs/SHIPPED.md` gains a `## v0.4.22 — YYYY-MM-DD` section with implementation summary (cycle count, RED test count, dispatch count) before release tag. Verifies spec §13 success criterion 12.
> 14. Spec §13 success criteria 1-12 verified end-to-end: schema migration round-trip (§13.1), 14 protocol cycles ZERO-INCONSISTENCIES on both validators (§13.2), coverage gates (§13.3), E2E round-trip green (§13.4), audit-hook flip + soak clean (§13.5), backward-compat smoke (§13.6), MCP structured_output (§13.7), brand audit clean (§13.8), a11y audit clean (§13.9), 4 events emit via JSONL+ring buffer+SSE visible in `ActivityPanel` filtered to `path=validation_suite` or `path=regression_alarm` (§13.10), `regression_alarm` block valid under all conditions with 30s cache respected (§13.11), docs hygiene per items 10+11+13 above (§13.12).
>
> Report APPROVED-ZERO-INCONSISTENCIES or list findings.

- [ ] Verify Validator 2 ZERO: all 11 pre-release items pass.

---

## Post-implementation: Release v0.4.22

After Cycle 16 ZERO across both validators:

- [ ] **Move CHANGELOG**: edit `docs/CHANGELOG.md` — move all v0.4.22 unreleased items to `## v0.4.22 — YYYY-MM-DD` heading (use today's date).

- [ ] **Run release script**:

```bash
./scripts/release.sh
```

This handles version sync → commit → tag → push → GitHub Release (with changelog body) → dev bump to v0.4.23-dev. Requires `gh` CLI authenticated.

- [ ] **Post-release CLAUDE.md updates**: per the v0.4.21 SHIPPED post-release pattern (commit `bd5463ca`), update project root CLAUDE.md + backend/CLAUDE.md to reflect v0.4.22 SHIPPED state.

- [ ] **Update docs/SHIPPED.md**: add `## v0.4.22 — YYYY-MM-DD` section with the implementation summary (cycle count, RED test count, dispatch count).

- [ ] **Update docs/ROADMAP.md**: mark Topic Probe Tier 2 SHIPPED; T3=v0.4.23 → next planned tier.

---

## Iteration rules (per spec §10 + `feedback_tdd_protocol.md`)

If either Validator 1 or Validator 2 returns FINDINGS or APPROVED-WITH-MINOR:

1. Identify the failing phase (typically REFACTOR or GREEN — but could be RED if a test was wrong).
2. Re-dispatch a fresh implementer subagent for that phase with the specific findings.
3. If the fix is **behavior-affecting**, cascade through INTEGRATE + OPERATE + BOTH validators (fresh dispatches). Static-only fixes (e.g., docstring polish) can re-run only the failing phase + both validators.
4. NEVER proceed-with-notes. ZERO INCONSISTENCIES on both validators is the gate.

---

## Total dispatch count

- **14 protocol cycles** (1-13, 15) × 7 dispatches = **98**
- **2 gate cycles** (14, 16) × 2 validators = **4**
- **Baseline** = 102 dispatches
- **+ Iteration** (35-70 expected based on Foundation P3/P4 bracketing): 30-60 commits
- **+ Process commits** (spec already shipped, plan, release-script bump, CHANGELOG, post-release docs): ~5-10
- **Total estimated commits** = 137-172

Foundation P3 (95 commits, 5 spec review rounds) + P4 (~50 commits, 1 plan review round) bracket the expected range.
