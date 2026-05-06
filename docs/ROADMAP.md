# Project Synthesis — Roadmap

Living document tracking planned improvements. Items are prioritized but not scheduled. Each entry links to the relevant spec or ADR when available.

**Snapshot:** v0.4.18-dev (in development, post-v0.4.17). Last release: v0.4.17 (2026-05-06 — Foundation P2 Path A: probe internals split into 3 new modules + 1 trimmed orchestrator).

**Active foundation phase (zero-tech-debt prep for Probe T2-T4):**
- ~~P1 SQLite-debt closure~~ — **SHIPPED v0.4.16 (2026-05-05)**: cold-path commit chunking (P1a) + `_bg_index`/`build_index` per-batch chunking (P1b)
- ~~P2 Path A probe internals cleanup~~ — **SHIPPED v0.4.17 (2026-05-06)**: 9 module-level helpers + `current_probe_id` ContextVar relocated to `probe_common.py` / `probe_phases.py` / `probe_phase_5.py`. ProbeService class methods + `_run_impl()` body untouched. ~12% LOC shrink. **P2 Path B (Phase 3 body extraction) deferred** — see "Probe Phase 3 body extraction — deferred" entry under Exploring for the architectural questions to resolve.
- **P3 Substrate unification** (`ProbeRun` + seed surface → unified `RunRow`; seed gains row-state for the first time — there is no `SeedRun` model today) — **next, target v0.4.18**
- **P4 Long-handler restructures** — re-allocated to v0.4.19 (originally bundled with v0.4.17 P2 but P2 alone shipped at the right size; P4 deserves its own cycle)

After foundation, Probe Tier 2 / Tier 3 / Tier 4 ship on the unified substrate with no retroactive migration debt. See "Foundation phase" section below for ordering rationale + scope detail per phase.

> **Shipped work archived in [`SHIPPED.md`](SHIPPED.md).** This file tracks only forward-looking items (Immediate, Planned, Exploring, Deferred) plus partial-tier work where a follow-up tier is still active.

## Conventions

- **Planned** — designed, waiting for implementation
- **Exploring** — under investigation, no decision yet
- **Deferred** — considered and postponed with rationale
- **Partially shipped** — portions shipped with version tags; remaining work called out
- **Superseded** — entry is an earlier scope subsumed by a later phase/cycle (kept for cross-reference)
- **Shipped** — historical entry retained for context; full detail in [`SHIPPED.md`](SHIPPED.md)

---

## Immediate

### Taxonomy observatory — live domain & sub-domain lifecycle dashboard
**Status:** Tier 1 Shipped (v0.4.4, merged to main — three-panel shell + pinned `OBSERVATORY` tab; period-aware Timeline + Heatmap, current-state Readiness Aggregate). Tier 2+ (steering suggestions, vocabulary transparency, cross-domain pattern flow) remains Exploring.
**Spec:** [docs/superpowers/specs/2026-04-24-taxonomy-observatory-design.md](superpowers/specs/2026-04-24-taxonomy-observatory-design.md)
**Plan:** [docs/superpowers/plans/2026-04-24-taxonomy-observatory-plan.md](superpowers/plans/2026-04-24-taxonomy-observatory-plan.md)
**Context:** The taxonomy engine now discovers domains and sub-domains organically from user activity through a three-source signal pipeline (domain_raw qualifiers, Haiku-generated vocabulary, dynamic TF-IDF keywords). The warm path runs every 5 minutes, making discovery decisions with full observability events logged to JSONL. Readiness endpoints + sparklines + topology overlay shipped in v0.3.37–v0.3.38 cover the per-domain lifecycle surface; the Observatory extends that into a first-class panel with cross-domain trajectories.

**Vision:** A "Taxonomy Observatory" panel that gives users creative and functional insights into their prompt catalogue's structure, growth trajectory, and optimization opportunities. Inspired by the Tamagotchi/buddy concept — the taxonomy is a living system that the user cultivates through their prompting activity.

**Core capabilities:**

1. **Domain lifecycle timeline** — visual history of when domains and sub-domains were discovered, how they grew, and which signals triggered their creation. Shows the three-source pipeline contribution per domain (how much came from domain_raw vs vocabulary matching vs dynamic keywords). Users see their taxonomy growing organically as they use the system.

2. **Sub-domain readiness indicators** — for each domain, show which potential sub-domains are approaching the creation threshold. "SaaS pricing is at 17% — needs 40% to form a sub-domain. 23 more pricing-focused prompts would get you there." This steers users toward concentrating their activity in areas where the taxonomy can provide richer organization. (Per-domain surface already shipped as `DomainStabilityMeter` + `SubDomainEmergenceList` in v0.3.37.)

3. **Pattern density heatmap** — which domains/sub-domains have the richest pattern libraries (most MetaPatterns, highest injection rates, best score lift). Highlights where the taxonomy is adding the most value and where it's thin.

4. **Dynamic steering suggestions** — based on taxonomy state, suggest actions that would improve coverage. Observational, never prescriptive.

5. **Vocabulary transparency** — show which qualifier vocabularies are active per domain (static, LLM-generated, or dynamic TF-IDF), what keywords they contain, and how recently they were refreshed. Users can see exactly why the system classified their prompt as "backend: auth" and provide feedback if the classification is wrong.

6. **Cross-domain pattern flow** — visualize how patterns propagate across domains via GlobalPatterns and cross-cluster injection.

**Steering model (exploratory):** Key principles:
- Steering is observational, not prescriptive
- Suggestions are contextual — shown when the user is in a relevant domain, not as global notifications
- Transparent about reasoning — every suggestion links to the underlying data
- Gamification is minimal — progress indicators yes, achievements/badges no
- The user's prompting freedom is never constrained — steering is purely advisory

**Data sources (already available):**
- Taxonomy events JSONL (`data/taxonomy_events/`) — full decision history
- Ring buffer (500 events) — real-time stream via SSE `taxonomy_activity` events
- Readiness history JSONL (`data/readiness_history/`) — 30-day rolling snapshots with hourly bucketing beyond 7d
- `GET /api/clusters/tree`, `/api/clusters/stats`, `/api/clusters/activity`
- `GET /api/domains/readiness`, `/api/domains/{id}/readiness`, `/api/domains/{id}/readiness/history`

**Prerequisites:** Signal-driven sub-domain discovery (shipped v0.3.25), LLM-generated qualifier vocabulary (shipped v0.3.32), taxonomy event observability (shipped v0.3.25), readiness telemetry + history (shipped v0.3.37–v0.3.38). Data infrastructure is complete — this is a frontend visualization and UX design challenge.

**Files:** New `frontend/src/lib/components/taxonomy/TaxonomyObservatory.svelte`. Possibly new `backend/app/routers/taxonomy_insights.py` for aggregated steering suggestions. Possibly new `frontend/src/lib/stores/observatory.svelte.ts`.

---

## Planned

### Foundation phase — zero-tech-debt prep for Probe Tier 2-4 (v0.4.16-v0.4.20)
**Status:** P1 + P2 Path A shipped (v0.4.16, v0.4.17). P3 + P4 + P2 Path B remaining. Updated 2026-05-06.
**Rationale:** Topic Probe Tier 1 (v0.4.12) shipped against the v0.4.x SQLite + probe-internal substrate as it existed then. Tiers 2, 3, and 4 each layer significant new surfaces (save-as-suite, replay, regression alarm, CI hooks, promotion flow, substrate unification). Shipping them on the current substrate would either (a) accumulate retroactive migration debt as the substrate evolves, or (b) force breaking schema changes mid-tier. The foundation phase eliminates that future cost by closing all known SQLite-migration tail items + probe-internal tech debt + substrate unification BEFORE T2 features start.

**Phase ordering (dependency chain):**

| # | Sub-project | Status | Depends on | Notes |
|---|---|---|---|---|
| **P1** | **SQLite-debt closure** (P1a cold-path commit chunking + P1b `_bg_index`/`build_index` per-batch chunking) | **SHIPPED v0.4.16** (2026-05-05) | nothing | Closes v0.4.13/v0.4.14 migration story. Audit-hook RAISE-in-prod flip unblocked. PR #67 (P1a) + PR #68 (P1b) both rebase-merged. |
| **P2 (Path A)** | **Probe internals cleanup — module-level helpers** (9 module-level free functions + `current_probe_id` ContextVar relocated to `probe_common.py` / `probe_phases.py` / `probe_phase_5.py`. ProbeService class + `_run_impl()` body untouched.) | **SHIPPED v0.4.17** (2026-05-06) | nothing | Pure code-move. probe_service.py 2493 → 2204 LOC (~12% shrink). 7 commits, both V1+V2 validators APPROVED-ZERO. PR #69 rebase-merged. |
| **P2 (Path B — DEFERRED)** | **Phase 3 body extraction from `_run_impl()`** | **DEFERRED indefinitely** (no version target) | P2 Path A complete | Plan-validation round 1 caught structural defects spec missed: (a) `_run_impl` is `AsyncIterator`-returning + Phase 3 contains 3 `yield` statements — extraction signature must be redesigned as async-generator or callback-yield pattern; (b) 7 actual `self.X` captures (spec invented `target_score`/`read_failures`/`embed_failures` — none exist in source); (c) 8+ inline imports inside body need re-homing decisions; (d) 2 test sites use `patch.object(probe_service_mod, ...)` / `monkeypatch.setattr(ps_mod, ...)` patch-target drift. **T2-T4 do NOT depend on Path B** — they depend on P3 substrate unification only. Full architectural questions documented in "Probe Phase 3 body extraction — deferred" Exploring entry below. |
| **P3** | **Substrate unification** (introduce a unified `RunRow` model + `RunOrchestrator` service with pluggable `SeedAgentGenerator` + `TopicProbeGenerator`; `ProbeRun` migrates into `RunRow`; the seed surface — which has **no run-state model today** — gains persistence for the first time; backward-compat REST/MCP shims preserve `/api/probes`, `/api/seed`, `synthesis_probe`, `synthesis_seed` response shapes) — **next foundation phase** | **Planned, target v0.4.18** | P2 Path A (clean module boundaries simplify the migration surface) | Biggest single architectural commitment of foundation. **The asymmetry: `ProbeRun` exists with 17 columns + full lifecycle (status tracking, GC sweep, REST list/get); the seed surface is fire-and-forget — no row-state, no list endpoint, no `GET /api/seed/{id}`, no `SeedRun` model has ever existed**. P3 is therefore not a "collapse two models" exercise; it is "introduce row-state to seeding while reshaping `ProbeRun` into a generic `RunRow`". T4 ships by construction; T2/T3 features build natively on unified substrate with zero retroactive migration. See dedicated "Foundation P3 — Substrate unification" section below for architectural sketch. |
| **P4** | **Long-handler restructures** (separate read/process/write phases in `tools/refine.py:50,:156`, `tools/save_result.py:85`, `tools/optimize.py:198` so the LLM call lives outside any session and persistence boundaries route through the queue) | **Planned, target v0.4.19** | nothing (probe-independent but bundled in foundation envelope) | Closes the final SQLite migration tail. Was originally bundled with v0.4.17 P2 ("if compatible scope"); P2 alone shipped at the right size, so P4 gets its own cycle at v0.4.19. Not probe-blocking but a cleanup-track release inside the foundation envelope. See dedicated "Foundation P4 — Long-handler restructures" section below. |

**Release allocation (revised 2026-05-06 — P4 split out of v0.4.17, T2-T4 shifted by 1):**
- **v0.4.15** (2026-05-04 retroactive) — HistoryPanel pagination correctness P0 fix — **SHIPPED**
- **v0.4.16** (2026-05-05) — Foundation P1 (SQLite-debt closure: P1a cold-path chunking + P1b bg_index batching) — **SHIPPED**
- **v0.4.17** (2026-05-06) — Foundation P2 Path A (probe internals — module-level helpers extraction) — **SHIPPED**
- **v0.4.18** (next) — Foundation P3 (substrate unification — biggest architectural commitment) — **PLANNED**
- **v0.4.19** — Foundation P4 (long-handler restructures: refine/save_result/optimize handlers) — **PLANNED**
- **v0.4.20** — Probe Tier 2 (save-as-suite + replay + UI + regression alarm) — **PLANNED**
- **v0.4.21** — Probe Tier 3 (release.sh CI + probe→seed promotion + drill-into-cluster) — **PLANNED**
- **v0.4.22** — Probe Tier 4 (final UI consolidation — substrate already done in P3) — **PLANNED**
- **TBD** — Foundation P2 Path B (Phase 3 body extraction — deferred indefinitely; needs fresh design cycle; not blocking T2-T4 because T2 ships on the unified substrate from P3, not on Phase 3 isolation)

**Per-phase specs:** each phase gets its own spec → plan → strict 7-dispatch TDD cycle (RED → GREEN → REFACTOR → INTEGRATE → OPERATE → spec-compliance reviewer → code-quality reviewer) per `feedback_tdd_protocol.md`. P1 brainstorm starts immediately after this ROADMAP update lands.

**Cross-references:**
- P1 supersedes the standalone "Cold-path commit chunking" + "_bg_index/RepoIndexService.build_index() per-file batching" entries below.
- P3 supersedes Topic Probe "Tier 4 (substrate unification)" — that delivery shifts left into foundation.
- P4 supersedes the standalone "tools/refine.py", "tools/save_result.py", "tools/optimize.py:198" entries below.

**P2 scope reduction (2026-05-06):** Path B (Phase 3 body extraction) deferred after spec round 5 + plan round 1 surfaced unresolved architectural questions. v0.4.17 P2 shipped Path A only (helpers extraction) — pure code-move, ~12% LOC shrink, zero risk. Path B re-design queued as an exploring item below ("Probe Phase 3 body extraction — deferred"). T2/T3/T4 do NOT depend on Path B; they depend on P3 substrate unification only.

**P4 re-allocation (2026-05-06):** P4 was originally bundled with v0.4.17 P2 with the qualifier "if compatible scope". v0.4.17 P2 (Path A only) shipped at the right size as a focused cycle, so P4 moves to its own v0.4.19 release. Probe Tier 2-4 shift by one minor (T2=v0.4.20, T3=v0.4.21, T4=v0.4.22).

---

### Foundation P3 — Substrate unification (target v0.4.18)
**Status:** Brainstorm complete (2026-05-06); spec authoring queued. Target v0.4.18.

**Scope:** Introduce a unified `RunRow` model + a `RunOrchestrator` service that dispatches to pluggable generators (`SeedAgentGenerator` for template-driven seed runs, `TopicProbeGenerator` for the agentic-from-topic-and-codebase path). `ProbeRun` migrates into `RunRow`; the seed surface gains run-state persistence for the first time. All current REST + MCP surfaces (`/api/probes`, `/api/seed`, `synthesis_probe`, `synthesis_seed`) keep their endpoints and response shapes via backward-compat shims that translate to/from the unified substrate. A new `GET /api/runs` endpoint (paginated, mode-filterable) exposes the unified view for T4's UI consolidation.

**Reality check on the asymmetry (verified 2026-05-06 against `main` @ v0.4.18-dev):**

| Layer | Probe (today) | Seed (today) |
|---|---|---|
| Persistence model | `ProbeRun` — 17 columns: `id, topic, scope, intent_hint, repo_full_name, project_id, commit_sha, started_at, completed_at, prompts_generated, prompt_results, aggregate, taxonomy_delta, final_report, status, suite_id, error` (`models.py:570`, migration `ec86c86ba298`) | **none** — no `SeedRun` model has ever existed; zero matches in `models.py`, `alembic/versions/`, or any service. Only persisted artifact of a seed run is the resulting `Optimization` rows tagged `source="batch_seed"` |
| REST surface | `POST /api/probes` (SSE), `GET /api/probes` (paginated list), `GET /api/probes/{id}` | `POST /api/seed` (synchronous fire-and-forget — returns `SeedOutput` once); **no list, no GET-by-id** |
| MCP tool | `synthesis_probe` (returns `probe_id`, supports SSE under sampling) | `synthesis_seed` (synchronous; returns `SeedOutput`; no run id retained beyond the in-memory `batch_id` UUID) |
| Lifecycle | full — status tracking, error capture, `_gc_orphan_probe_runs` startup sweep, cancellation handler under `asyncio.shield()`, `_set_probe_status` queued helper | none — `seed_batch_progress` events fly through `event_bus`, lost on disconnect, never accumulated |
| Frontend | **zero code** — no `probe.ts` API client, no `Probe*` components; the REST + MCP surfaces have no SvelteKit consumer | `SeedModal.svelte` renders live progress + final summary in-modal; no history component |

So P3 is not "collapse two models into one" — it is "introduce run-state to the seed surface AND reshape probe run-state into a generic `RunRow` substrate at the same time, without regressing any existing surface contract."

**Why this matters:**

1. **T2 save-as-suite + replay** keys off `RunRow.id`. Without P3, the seed surface has no row-state to attach a save-as-suite operation to — save-as-suite ships probe-only or seed grows ad-hoc persistence.
2. **T3 probe→seed-agent promotion** becomes a `RunRow.mode` flip plus a metadata write. Without P3, promotion has nothing on the seed side to read from.
3. **T4 final UI consolidation** (SeedModal becomes one tab with two modes) ships natively with one history surface. Today there is no history surface for either mode on the frontend, so T4 builds it once on top of `RunRow` rather than twice.
4. **Lifecycle parity, end of helper drift** — today only `_set_probe_status` exists; there is no seed-side equivalent. `RunOrchestrator` owns the persistence helpers once and both modes inherit identical status/error/GC behavior. Eliminates a class of bugs we haven't hit yet only because the seed side has no row to drift on.

**Architectural sketch (post-brainstorm, 2026-05-06):**

```
backend/app/models.py
  RunRow                      → id, mode ∈ {seed_agent, topic_probe}, ...shared lifecycle
                                (status, started_at, completed_at, error, project_id FK,
                                 repo_full_name, prompts_generated, prompt_results JSON,
                                 aggregate JSON, taxonomy_delta JSON, final_report TEXT,
                                 suite_id) + promoted-from-probe query-hot columns
                                (topic, intent_hint — both nullable; NULL for seed mode)
                                + mode-specific JSON metadata columns:
                                  - topic_probe_meta: {scope, commit_sha}
                                  - seed_agent_meta: {project_description, workspace_path,
                                    agents, prompt_count, prompts_provided, batch_id, tier,
                                    estimated_cost_usd}
  ProbeRun                    → DROPPED in the same Alembic migration after backfill.
                                No SQL VIEW (no future purpose) and no SeedRun deprecation
                                (no SeedRun model has ever existed).

backend/app/services/
  run_orchestrator.py         → RunOrchestrator: creates row → dispatches to generator by
                                mode → awaits result → persists final state. Owns
                                _set_run_status (replaces _set_probe_status), GC sweep,
                                cancellation handler under asyncio.shield(). All writes
                                route through WriteQueue.
  generators/
    base.py                   → RunGenerator protocol: `async def run(request, *, run_id)
                                -> RunResult`. Awaitable, NOT an async iterator —
                                progress events publish to event_bus directly with run_id
                                in payload (no re-publication layer in RunOrchestrator).
    seed_agent_generator.py   → refactored from seed_orchestrator + tools/seed dispatch
    topic_probe_generator.py  → refactored from probe_service.py 5-phase flow

backend/app/routers/
  runs.py                     → NEW — unified GET /api/runs (paginated list, filter by mode,
                                ordered started_at desc), GET /api/runs/{id}
  probes.py                   → backward-compat shim — POST returns SSE constructed by
                                event_bus subscription filtered by run_id (NOT by service
                                iteration); event names + payload shapes byte-identical
                                to today's probe contract. GET endpoints serialize from
                                RunRow through the existing ProbeRunSummary/ProbeRunResult
                                shapes.
  seed.py                     → backward-compat shim — POST /api/seed stays SYNCHRONOUS
                                (Path 1 from brainstorm: live UI updates flow through
                                /api/events bus filtered by run_id, NOT by SSE on POST).
                                SeedOutput response gains additive run_id field — only
                                allowed shape change. New GET /api/seed and
                                GET /api/seed/{id} surfaces (additive — no existing caller
                                breaks).

MCP tools/
  synthesis_probe             → backward-compat (response shape unchanged; backend dispatches via RunRow)
  synthesis_seed              → backward-compat (SeedOutput unchanged + additive run_id field)
```

**Migration path** (one Alembic migration; no dual-write window needed since seed has no rows to dual-write):
1. Alembic up: create `run_row` table (shared lifecycle columns + promoted `topic`/`intent_hint` + `mode` discriminator + per-mode JSON metadata columns), create 4 indexes (`mode+started`, `status+started`, `project_id`, `topic`).
2. Backfill: `INSERT INTO run_row SELECT ... FROM probe_run` — `mode='topic_probe'` for every row, shared columns copied direct, `scope`/`commit_sha` rolled into `topic_probe_meta` JSON.
3. Drop `probe_run` indexes + table in the same upgrade (decision Q4=a — no VIEW, no follow-up migration).
4. Read-flip is automatic at deploy time: PR1 ships `RunRow` model + `RunOrchestrator` dark (no router wiring); PR2 wires shims atomically.
5. Seed gains row-state from cycle one: every `POST /api/seed` writes a `RunRow(mode='seed_agent', status='running')` via WriteQueue **before** kicking off `batch_pipeline.run_batch()` (small extra latency on synchronous return is acceptable; resolves the "persist before vs after" risk by accepting the latency cost in exchange for crash-recovery semantics).

**Risks (post-brainstorm — design risks resolved; spec-implementation risks remain):**
- Schema migration complexity — largest of any foundation phase but materially simpler than the original framing because there is no `SeedRun` to merge or backfill from. Alembic downgrade reverses the backfill via `INSERT INTO probe_run SELECT ... FROM run_row WHERE mode='topic_probe'` with JSON-extract for scope/commit_sha — gives rollback safety without needing a VIEW.
- Cancellation shielding under `RunOrchestrator` — probe's existing `asyncio.shield()` cancellation handler currently lives inside `_run_impl`. Moving it up one layer to `RunOrchestrator` requires the spec to verify the SSE response (now bus-subscription-based) does not terminate before the row is marked failed. **Spec-level risk.**
- `current_probe_id` ContextVar re-export coverage — every taxonomy event firing inside a run uses this ContextVar today. P3 renames it to `current_run_id` with a re-export shim; coverage must be verified at every firing site via grep at spec time. **Spec-level risk.**
- Frontend filter race — `SeedModal.svelte` subscribes to `seed_batch_progress` filtered by `run_id` after `POST /api/seed` returns. Race window between subscription and first event is small but must be tested. **Spec-level risk.**

**Files (estimated):** 1 Alembic migration, `models.py` (`RunRow` add, `ProbeRun` retire-or-shim), `services/run_orchestrator.py` (NEW), `services/generators/` (NEW package — `base.py`, `seed_agent_generator.py`, `topic_probe_generator.py`), `routers/runs.py` (NEW), `routers/probes.py` (shim), `routers/seed.py` (shim — and gain row-write), `tools/seed.py` + `tools/probe.py` (dispatch updates), `services/probe_service.py` (refactored into `topic_probe_generator.py`), `services/seed_orchestrator.py` (refactored into `seed_agent_generator.py`), `services/gc.py` (`_gc_orphan_probe_runs` → `_gc_orphan_runs`).

**Estimated scope:** ~1500-2000 LOC backend + 1 schema migration + ~80 new tests + comprehensive backward-compat regression suite for both `/api/probes` and `/api/seed` shapes + `synthesis_probe`/`synthesis_seed` MCP tool snapshot tests.

**Brainstorm decisions (2026-05-06 — all six questions resolved; spec doc captures full rationale):**

| # | Question | Decision |
|---|---|---|
| Q1 | Asymmetry handling | **Asymmetric collapse, one-step.** RunRow + RunOrchestrator + generators ship in v0.4.18; seed gains row-state for the first time; ProbeRun → RunRow in same migration. No transient SeedRun model. |
| Q2 | RunRow column shape | **Hybrid columns.** Shared lifecycle fields + promoted `topic`/`intent_hint` first-class; mode-specific fields in `topic_probe_meta` / `seed_agent_meta` JSON. |
| Q3 | POST /api/seed semantics | **Path 1: sync POST + global SSE bus.** Sync POST gains additive `run_id`; live UI updates flow through existing `/api/events` filtered by `run_id` (additive event field). No SSE on POST. |
| Q4 | probe_run table fate | **Drop immediately** in same Alembic migration. No SQL VIEW. Alembic downgrade gives rollback safety. |
| Q5 | Generator protocol | **Awaitable generators + bus events.** `async def run(request, *, run_id) -> RunResult`. Probe's vestigial AsyncIterator retired; events publish directly to bus from generators (no re-publication layer in RunOrchestrator). |
| Q6 | Rollout | **Two PRs.** PR1 = dark substrate (RunRow + RunOrchestrator + generators + tests, no router wiring). PR2 = wire shims atomically + frontend `run_id` filter additive. Backend-only; T4 (v0.4.22) does the unified UI. |

**Spec doc:** `docs/superpowers/specs/2026-05-06-foundation-p3-substrate-unification-design.md` (committed at brainstorm close).

**Earlier-draft correction:** the original "ProbeRun has 13 columns, SeedRun has 11 — most overlap" framing in pre-2026-05-06 ROADMAP drafts was incorrect — `ProbeRun` has 17 columns and `SeedRun` does not exist. The corrected reality is captured in the asymmetry table above.

---

### Foundation P4 — Long-handler restructures (target v0.4.19)
**Status:** Planned. Detailed brainstorm pending.

**Scope:** Split read/process/write phases in 3 long-running MCP tool handlers so each LLM call lives outside any DB session lifetime, and persistence boundaries route through the WriteQueue. Closes the final v0.4.13 SQLite migration tail (the 3 sites explicitly deferred at v0.4.14).

**Affected sites (verified at v0.4.17):**

| Site | Issue | Restructure pattern |
|---|---|---|
| `tools/refine.py:50, :156` | `RefinementService.refine_prompt()` LLM call wrapped inside `async with` session lifetime | Extract LLM call out → return tuple → caller persists via `submit()` |
| `tools/save_result.py:85` | Heuristic scoring + analyzer A4 LLM fallback wrapped inside session | Same pattern: split scoring (read), LLM analysis (no session), persistence (queued submit) |
| `tools/optimize.py:198` | Full `PipelineOrchestrator` 4-LLM SSE loop wrapped inside one session | Each LLM phase yields out of session; persistence boundaries become discrete `submit()` calls between yields |

**Why this matters:**

1. **Audit-hook RAISE-in-prod flip** is currently gated on these 3 sites being clean. Today they emit WARN. Post-P4, audit hook can flip to RAISE for the entire stack — drift writes become hard failures at source instead of forensic-reconstruction-after-symptom.
2. **Foundation invariant.** v0.4.13 architectural fix promised "100% of writers route through queue, except cold path (closed in P1)". P4 is the last remaining exception.
3. **Long-LLM-call SQLite contention.** Today, an LLM call inside a session holds the writer slot for the multi-second LLM round-trip. Concurrent writers either contend or fail. Post-P4, the writer slot is held only during the persistence flush (~10-50ms).

**Restructure pattern (canonical):**

```python
# Before (refine.py)
async with async_session_factory() as db:
    refinement = await refinement_service.refine_prompt(db, ...)  # LLM call inside session
    db.add(refinement)
    await db.commit()

# After
# Step 1: read context (short session)
async with async_session_factory() as db:
    context = await refinement_service.build_refinement_context(db, ...)

# Step 2: LLM call (no session)
result = await refinement_service.invoke_refinement_llm(context)

# Step 3: persist via WriteQueue
async def _persist(db: AsyncSession) -> None:
    db.add(Refinement(...))
    await db.commit()

await write_queue.submit(_persist, operation_label="refine_persist")
```

**Risks:**
- `RefinementService.refine_prompt()` and friends may have internal session-bound caching that breaks under split. Audit each method for `db.refresh()` / `db.flush()` calls inside the LLM-call portion.
- SSE streaming during `tools/optimize.py:198` 4-phase loop: the SSE generator must yield through the orchestrator, not through the session. Current code passes `db` into the orchestrator — restructure requires passing a callback or context object instead.

**Files:** `backend/app/tools/refine.py`, `backend/app/tools/save_result.py`, `backend/app/tools/optimize.py`, possibly `backend/app/services/refinement_service.py` (signature changes), `backend/app/services/pipeline.py` (orchestrator session boundary extraction).

**Estimated scope:** ~600-800 LOC + ~30 new tests + 3 dedicated TDD cycles (one per site). Each site can ship independently if cycles compress.

---

### Post-foundation horizon (v0.5.x — exploring)

After Foundation P3 + P4 + Probe T2-T4 ship (target window: v0.4.18-v0.4.22), the architecture is ready for a v0.5.0 major. Candidate themes (none yet committed):

| Theme | Surface | Status |
|---|---|---|
| **Integration store** | Pluggable context providers beyond GitHub (Google Drive, Notion, local FS, Confluence, Figma) per ADR-006 | Planned (no version target — see "Integration store" entry below) |
| **Non-developer onboarding** | Adaptive UI labels + vertical-aware first-run flow per ADR-006 | Partially shipped (engine parity complete; UI adaptation remains — see "Non-developer onboarding pathway" entry) |
| **Hierarchical topology navigation** | 4-level drill-down topology (project → domain → cluster → prompt) | Planned (was target v0.4.0 — re-targeting to v0.5.x given foundation phase pre-empted; see "Hierarchical topology navigation" entry) |
| **Unified scoring service** | Eliminate scoring-orchestration duplication across 4 call sites | Planned (no version — see "Unified scoring service" entry) |
| **Pipeline progress visualization** | Streaming token previews + per-phase timing for optimize/refine | Planned (no version — see "Pipeline progress visualization" entry) |
| **Time-gated cleanup follow-ups** | Audit-hook RAISE-in-prod flip + `WriterLockedAsyncSession` removal | Time-gated (7-day post-P4 trigger — see entries under Planned) |

The v0.5.0 major would either (a) ship one or two of these as the headline feature, or (b) be a clean architecture milestone marker if the foundation + probe lifecycle leaves the codebase in a release-worthy state worth bumping the minor for.

---


### v0.4.11 domain proposal hardening — SHIPPED
**Status:** SHIPPED v0.4.11 (2026-04-28). Closed the `fullstack` ghost-domain pathology surfaced by the cycle-19→22 v2 replay: domain proposal now requires ≥2 distinct contributing clusters (`DOMAIN_PROPOSAL_MIN_SOURCE_CLUSTERS`); operator escape hatch `POST /api/domains/{id}/dissolve-empty` lets ops force-dissolve ghost domains without waiting for the 48h dissolution gate. Full technical detail (P0a + P1 + acceptance criteria + event taxonomy) in [`SHIPPED.md`](SHIPPED.md#v0411--2026-04-28).

---

### Topic Probe — agentic targeted exploration of a user-specified concern against the linked codebase
**Status:** **Tier 1 SHIPPED** (v0.4.12, 2026-04-29). Tier 2 / Tier 3 / Tier 4 remain **Planned**, sequenced AFTER the foundation phase: T2=v0.4.20, T3=v0.4.21, T4=v0.4.22 (revised 2026-05-06 — shifted by 1 minor when P4 split out of v0.4.17). Tier 2 was bumped repeatedly through public releases — v0.4.13 → v0.4.14 → v0.4.15 → v0.4.20 — as architectural fixes (SQLite contention, SQLite migration finalization, HistoryPanel pagination, foundation phase) shipped first; see SHIPPED.md + the "Foundation phase" entry above for the complete rationale.

**Tier 1 deliverables (SHIPPED):**
- `POST /api/probes` (SSE), `GET /api/probes`, `GET /api/probes/{id}`
- `synthesis_probe` MCP tool (15th tool)
- `prompts/probe-agent.md` hot-reloaded system prompt (8 template variables)
- `ProbeRun` model + idempotent Alembic migration (`ec86c86ba298`)
- 5-phase orchestrator (`grounding → generating → running → observability → reporting`) in `services/probe_service.py`
- 7 `probe_*` taxonomy events + `current_probe_id` ContextVar correlation on existing taxonomy events
- `scripts/probe.py` CLI shim translating `validate_taxonomy_emergence.py::PROMPT_SETS` presets to `POST /api/probes`
- Robustness hardening: `asyncio.CancelledError` handler + top-level `Exception` handler + `_gc_orphan_probe_runs()` startup sweep (rows running >1h marked failed)
- Optimizer timeout calibration: `_CLI_TIMEOUT_SECONDS=600`, `_post=1800s`, `probe.py httpx=3600s`
- 8 TDD cycles + 2 hotfix cycles all RED→GREEN→REFACTOR→code-review approved; suite at 3237 passed + 1 skipped
- Spec: `docs/specs/topic-probe-2026-04-29.md` (gitignored)
- Plan: `docs/plans/topic-probe-tier-1-2026-04-29.md` (gitignored)

**Tier 2 (v0.4.20) — Planned. PREREQUISITES (all SHIPPED or PLANNED in foundation): SQLite writer-slot contention fix (v0.4.13) ✓ + finalization (v0.4.14) ✓ + Foundation P1 (cold-path chunking + bg_index batching, v0.4.16) ✓ + Foundation P2 Path A (probe internals split, v0.4.17) ✓ + Foundation P3 (unified substrate, v0.4.18) — pending. Tier 2's save-as-suite + replay regression detection depend on the unified substrate so saved suites travel cleanly into T3/T4.**
- `POST /api/probes/{id}/save-as-suite` — fork a probe run into a `ValidationSuite` (frozen prompt fixture + assertions captured from the run's actual scores)
- `POST /api/probes/{id}/replay` — re-run a saved suite against current code state (regression detection)
- UI Navigator panel: "Topic Probe" tab in SeedModal + live taxonomy mini-view + final report card with copy-as-markdown
- `/api/health` regression alarm + StatusBar badge for suite-level mean drops ≥0.5 from baseline
- Topic-only mode (no codebase grounding) for non-developer verticals — drops Phase 1, generates from topic alone (ADR-006 follow-up)
- Replace blocking SSE with 202 Accepted + `GET /api/probes/{id}` polling for client-timeout decoupling on long probes (>10 prompts)

**Tier 3 (v0.4.21) — Planned:**
- `release.sh` CI hook: register critical probe topics as pre-release gates (fail = block release)
- Probe → seed-agent promotion flow: a saved probe with consistently high scores can be promoted to `prompts/seed-agents/<topic-slug>.md` (trivially clean on the unified substrate from Foundation P3 — promotion is a `mode` flip plus a metadata write, not a cross-model migration)
- "Drill into cluster" action on seed runs auto-launches a probe scoped to the cluster's intent_label
- Cross-tier composition: probe-discovered prompts feed seed-agent few-shot context

**Tier 4 (v0.4.22) — Planned (mostly cleanup):**
- The substrate unification originally slated for T4 has been promoted into Foundation P3 (v0.4.18). T4's remaining surface is the user-visible consolidation: SeedModal becomes one tab with two modes (template-driven / topic-driven) sharing the unified form scaffold. Single history surface (already enabled by P3's `RunRow`). Final UI polish.

**Vision:** A user-driven, codebase-aware seed mode where the user specifies a topic, concern, or feature and the agentic seed system organically populates the taxonomy with focused prompts anchored in the user's actual code. The system reads the linked GitHub repo, generates 10–20 prompts targeted at the topic with real code references (`engine.py:_compute_centroid`, `services/taxonomy/matching.py`, etc.), runs them through the optimization pipeline, watches the taxonomy emerge new domains/sub-domains organically as signal concentrates, and delivers a final report of what shipped — taxonomy changes, top-scoring outputs, extracted patterns, recommended follow-ups.

This is the productization of the manual workflow that emerged the `embeddings` sub-domain and `data` / `frontend` top-level domains during cycles 15–22. Today that flow requires human-curated prompts in `scripts/validate_taxonomy_emergence.py::PROMPT_SETS` and a CLI invocation; the user wants to specify a concern (e.g., "NumPy memory layout optimization", "auth middleware security review", "embedding cache invalidation correctness") and let the agentic system handle the rest with zero friction.

**User flow:**
1. User opens "Topic Probe" in the SeedModal (new tab) or invokes `synthesis_probe` MCP tool from their IDE.
2. Specifies topic + optional scope (e.g., specific files/dirs in linked repo, or "whole repo"). Optional intent hint (audit / refactor / explore / regression-test).
3. System:
   - **Phase 1 — Codebase grounding.** Reads linked codebase context (cached explore synthesis + curated retrieval against the topic embedding). Builds a `ProbeContext` object with relevant file references, dominant tech stack, project name.
   - **Phase 2 — Agentic prompt generation.** LLM (Sonnet, configurable) generates `N` prompts (default 12, configurable 5–25) anchored in real code identifiers from `ProbeContext`. Prompts are diverse along an explore/audit/refactor axis to populate multiple intent labels — not all `Audit X` or all `Refactor Y`.
   - **Phase 3 — Pipeline run.** Each prompt flows through the standard analyze → optimize → score pipeline using the existing `batch_pipeline.run_single_prompt` primitive. Configurable concurrency (3–10 default; sequential when sampling tier requires it).
   - **Phase 4 — Live observability.** Per-prompt SSE events stream `probe_progress` (current/total, optimization_id, intent_label, current overall_score). Taxonomy events (`domain_created`, `sub_domain_created`, `cluster_split`) are correlated with the probe via `probe_id` so the user sees their topic literally growing the taxonomy in real time.
   - **Phase 5 — Final report.** Structured summary: prompts generated (with the top 3 reproduced), score distribution (mean/p5/p95), patterns extracted, taxonomy changes (new domains/sub-domains/clusters with their cluster_ids), recommended follow-ups (e.g., "you have 2 prompts in the new `data:numpy-memory-layout` sub-domain; 3 more would cross the maturity threshold").

**Examples (concrete usage):**

| Topic | Generates | Likely taxonomy outcome |
|---|---|---|
| "NumPy advanced indexing performance" | 12 prompts citing `np.take`, `np.choose`, `np.where`, `arr[mask]` patterns from the user's code | New `data:numpy-indexing` sub-domain |
| "Embedding cache invalidation in EmbeddingIndex" | 10 prompts citing `_id_to_label`, `tombstones`, `_recompute_centroid` | New cluster under `backend:embeddings` |
| "GitHub OAuth token rotation" | 8 prompts citing `github_service.encrypt_token`, `_get_session_token`, `refresh_token` flow | Cluster lift in `backend:auth` |
| "Frontend brand-grammar drift" | 15 prompts citing `.rsd-stat-val`, `taxonomyColor`, `cubic-bezier` rules | Cluster growth in `frontend:brand-compliance` |

**Cross-tier composition (this is the unifying view):**

The Topic Probe is the meta-prompting front door. **Validation/regression detection becomes a downstream save-as-suite capability**, not the primary feature. Two follow-ups crystallize from the same primitive:

- **Save Probe → Validation Suite.** A successful probe run can be saved as a `ValidationSuite` — the generated prompts become a frozen fixture, optionally with assertions captured from the run's actual scores (e.g., "mean was 7.85, regression threshold = 7.35"). Re-running the suite later detects scoring drift against the same code. This is what closes the cycle-23/cycle-19→22 regression-detection gap, but as a *use case* of probes, not a separate feature.

- **Probe → Routine.** Save a probe topic to be re-run on a schedule (`/schedule` integration) — e.g., "every Monday, probe the auth middleware and report any score regressions on the auth-class clusters". Pairs with the existing release.sh CI integration: register critical probe topics as pre-release gates.

**Core capabilities (architecture):**

1. **`ProbeService`** — orchestrates the 5 phases. Owns the codebase-grounding step (cached, reuses `RepoIndexQuery`), the agentic generation step (delegates to a new `prompts/probe-agent.md` system prompt with `topic`/`scope`/`codebase_context` template variables), and the pipeline run step (delegates to `batch_pipeline`). Emits SSE events, persists `ProbeRun` row, produces final structured report.

2. **`ProbeRun` model** — `id`, `topic`, `scope`, `intent_hint`, `repo_full_name`, `commit_sha`, `started_at`, `completed_at`, `prompts_generated` (count), `prompt_results` (JSON: per-prompt optimization_id + score + intent_label + emerged_cluster_id), `taxonomy_delta` (JSON: domains_created, sub_domains_created, clusters_split), `final_report` (markdown), `status` (`grounding|generating|running|reporting|completed|failed`).

3. **REST surface** — `POST /api/probes` (SSE), `GET /api/probes`, `GET /api/probes/{id}`, `POST /api/probes/{id}/save-as-suite` (forks to ValidationSuite), `POST /api/probes/{id}/replay`.

4. **MCP tool `synthesis_probe`** — args: `topic`, `scope?`, `intent_hint?`, `n_prompts?`. Returns structured `ProbeRunResult` with the final report. Streams progress via MCP server-sampling SSE bridge.

5. **UI integration** — new SeedModal tab "Topic Probe" alongside existing seed agents. Topic input + scope selector (file picker on linked repo) + N slider + intent hint dropdown + Run button. During run: per-prompt progress strip + live taxonomy mini-view showing domain/sub-domain emergence. After run: final report card with copy-as-markdown + "Save as Validation Suite" + "Replay later" actions.

6. **Observability** — every probe emits these taxonomy events with `probe_id` correlation: `probe_started`, `probe_grounding`, `probe_generating` (with the agentic LLM prompt + N), `probe_prompt_completed` (per prompt), `probe_taxonomy_change`, `probe_completed`, `probe_failed`. All flow through the existing `taxonomy_activity` SSE so the Observatory's Activity panel becomes the audit trail.

**Architecture (concrete):**

```
prompts/
  probe-agent.md                     → System prompt for agentic generation
                                       (template vars: topic, scope, codebase_context,
                                        repo_full_name, intent_hint, n_prompts)

backend/app/services/
  probe_service.py                   → ProbeRun lifecycle (5 phases, SSE emission)
  probe_generation.py                → Agentic prompt-from-topic primitive
                                       (consumes RepoIndexQuery + Sonnet generation)

backend/app/routers/probes.py        → REST surface

backend/app/models.py
  ProbeRun                           → id, topic, scope, intent_hint, repo_full_name,
                                        commit_sha, started_at, completed_at,
                                        prompts_generated, prompt_results JSON,
                                        taxonomy_delta JSON, final_report TEXT,
                                        status, suite_id (FK to ValidationSuite, NULL until
                                        saved-as-suite)

synthesis_probe                      → MCP tool (extends existing 14 → 15 tools)
```

**Cross-pollination with seed tier:** A topic probe IS a runtime-defined seed agent. The execution layer (`batch_pipeline.run_single_prompt`) is shared. The difference is the prompt source: pre-defined (seed agents) vs runtime-generated-from-topic-and-codebase (probes). Long-term, the pre-defined seed agents become discoverable "saved probes" — a marketing seed agent is a saved probe with topic="marketing copy techniques" pinned to the marketing domain.

**Minimum viable bite (ship-first):**

1. `prompts/probe-agent.md` — system prompt for the agentic generator
2. `probe_service.py` + `ProbeRun` model + Alembic migration
3. `routers/probes.py` (POST /probes SSE, GET /probes, GET /probes/{id})
4. `synthesis_probe` MCP tool
5. SSE event taxonomy with `probe_*` event types
6. CLI shim — `scripts/validate_taxonomy_emergence.py::PROMPT_SETS` becomes "saved probe presets" callable via `python -m scripts.probe <topic>` for backward compat

**Defer to follow-up tier (Tier 2+):**
- UI SeedModal "Topic Probe" tab + live taxonomy mini-view + final report card
- Save-as-Validation-Suite (regression-detection fork)
- /api/health validation block + StatusBar regression badge
- release.sh CI hook
- /schedule integration for routine probes
- Cross-tier promotion: probe → seed agent

**Prerequisites:** GitHub integration linked (probes need a repo to ground against; non-linked sessions get a "link a repo first" prompt). **Estimated scope (Tier 1):** ~800 LOC backend + new router + new model + 1 MCP tool + 1 system prompt template. **Spec target:** `docs/specs/topic-probe-2026-04-28.md` (TBD).

---

### v0.4.10+ audit-driven hardening (sourced from cycles 19–22 meta-prompts, 2026-04-27)

Top 5 architectural audit findings surfaced by self-prompting the running v0.4.8 system about its own inconsistencies. Each is a candidate v0.4.10+ follow-up; scores are the system's own optimization-quality grade for the audit prompt that surfaced the gap. (Originally targeted at v0.4.9; that release was reallocated to ship F1-F5 audit-prompt scoring hardening from `docs/specs/audit-prompt-hardening-2026-04-28.md`.)

**1. R3/R5 telemetry asymmetry — `sub_domain_health_check` periodic event (score 8.10)**
Today an operator investigating a quiet sub-domain (no events in JSONL) cannot tell whether R3 silently skipped it, R2's grace-gate blocked it, or re-eval simply hasn't fired. Propose: a per-cycle `sub_domain_health_check` event for every existing sub-domain with `reason ∈ {grace_period, empty_snapshot, evaluated}`. Bounded volume (one per sub-domain per cycle), fully observable, closes the silent-skip blind spot. Trace: `d74283a8`.

**2. Cascade-vs-parse_domain unified primitive (score 8.04)**
The R6 spec already documents this divergence (see `docs/specs/sub-domain-dissolution-hardening-r4-r6.md` §R6 implementation note) but it remains a structural risk. The cascade normalizes literal qualifiers (`embedding`, `embedding-correctness`) → vocab groups (`embeddings`); rebuild bypasses that and operates on raw `parse_domain`. Today's cycle-15→17 emergence push exposed this concretely — R6 dry-runs at 0.30/0.35/0.38 thresholds returned `proposed=[]` even when the cascade view showed the qualifier consolidating well past the threshold. Propose: shared `compute_unified_qualifier_view()` primitive that runs the cascade normalization with a vocab-empty fallback to literal `parse_domain`, used by both readiness and rebuild. Trace: `eca121be`.

**3. Phase 4.95 vocab regen cadence — auto-trigger on `sub_domain_created` (score 7.74)**
Phase 4.95 (vocab regeneration) runs on `MAINTENANCE_CYCLE_INTERVAL=6` cadence — but today's `embeddings` sub-domain emergence at 20:15 didn't trigger an immediate vocab regen on `backend`; the next regen waited for the cadence tick (6 minutes later at 20:21). For ~6 min the parent domain's vocab was stale, still listing `embeddings` as one of its own groups. Propose: decouple Phase 4.95 from the cadence specifically when `sub_domain_created` fires — the parent's vocab needs to drop the graduated qualifier immediately. Trace: `c4da176c`.

**4. Cross-process telemetry sync — MCP ↔ backend bridge flush (score 7.68)**
Both processes write to `data/taxonomy_events/decisions-YYYY-MM-DD.jsonl` but events from the MCP process route through an HTTP POST bridge to `/api/events/_publish` with up to 30s of buffering. Today's `sub_domain_rebuild_invoked` events show this asymmetry: events from REST calls land instantly; events from MCP-tool-triggered actions delay. Propose: `flush_on_decision_emit` policy — every `log_decision()` call from a process that's NOT the JSONL owner immediately POSTs to the bridge with a 1s timeout, falls back to the existing buffer on timeout. Trace: `de801d3b`.

**5. R7 label-truncation discrepancy + per-process event_logger lifespan tied (score 7.68)**
Two related findings tied at score 7.68 — both around event-logger correctness:
- **5a** `previous_groups` in the WARNING-firing R7 event today contains `pipeline-observabili` (truncated to 20 chars) while `new_groups` has the full `pipeline-observability` (22 chars). Stored vocab labels were truncated somewhere in storage; new regens produce full labels. Propose: audit `normalize_sub_domain_label(raw, max_len=30)` callers to find the silent 20-char truncation path. Confirmed live in: `2026-04-27T20:53:00 general` regen.
- **5b** Per-process event_logger lifespan singleton — when MCP or backend restarts mid-session, the new process's events flow to a fresh JSONL file but old-process pending writes go to the old file. Propose: emit a `process_started` decision so operators can correlate event gaps with restarts. Trace: `c93a188f`.

**Audit-cycle methodology:** 4 cycles × 4–7 prompts each = 20 prompts asking the running system to introspect specific surfaces. Average score 7.36 (slightly below v0.4.8 baseline 7.96 — consistent with the audit-prompt score-drift hypothesis itself surfaced by cycle-21). The 20 prompts also organically emerged a `frontend` top-level domain (3rd new node today, after `embeddings` sub and `data` domain).

---

### Live pattern intelligence — real-time context awareness during prompt authoring
**Status:** Tier 1 Shipped (v0.4.4) — `ContextPanel.svelte` sidebar + `match_level` / `cross_cluster_patterns` additive keys on `POST /api/clusters/match`. Two-path detection (typing 800 ms + paste 300 ms) with multi-pattern selection committing to `forgeStore.appliedPatternIds`. Single-banner `PatternSuggestion.svelte` retired. Tier 2 (enrichment preview via `POST /api/clusters/preview-enrichment`) and Tier 3 (proactive inline hints) remain Planned.
**Spec:** [ADR-007](adr/ADR-007-live-pattern-intelligence.md), [Tier 1 design spec](superpowers/specs/2026-04-24-live-pattern-intelligence-tier-1-design.md)
**Context:** Tier 1 closes the authoring-phase visibility gap — users see matched cluster identity, top meta-patterns, and cross-cluster patterns continuously as they type rather than only on paste. Backend primitives were already in place (embedding search ~200 ms, heuristic classification ~30 ms, strategy intelligence ~100 ms); the work was UI orchestration plus two additive response keys.

**Tier 2 — Enrichment preview**: lightweight `POST /api/clusters/preview-enrichment` returns analyze + strategy intelligence preview without running the full optimization. Surface in the ContextPanel as a second section below the patterns list. No LLM calls; reuses `HeuristicAnalyzer` + `resolve_strategy_intelligence`.

**Tier 3 — Proactive inline hints**: tech-stack divergence alerts, strategy mismatches, refinement opportunities surfaced inline in the ContextPanel as the user types. Ranked by relevance to the current prompt + project.

---

### Integration store — pluggable context providers beyond GitHub
**Status:** Planned
**Context:** GitHub is the sole external integration — codebase context for the explore phase plus the project-creation trigger (ADR-005). Two problems: (1) non-developers have zero external context enrichment, (2) the project system is coupled to GitHub repos as the primary link source.

**Vision:** A VS Code-style integration "store" where GitHub is one installable provider among many. Each integration is a self-contained plugin that provides a context source (documents for the explore phase), a project trigger (linking creates a project node), and optionally domain keyword seeds and heuristic weakness signals for its vertical.

**Architecture:**
- **ContextProvider protocol** — each integration implements `list_documents(project_id) -> list[Document]` and `fetch_document(id) -> str`. The existing `ContextEnrichmentService` dispatches to whichever provider is linked for the active project. GitHub's current implementation becomes the first provider, not a special case.
- **Hybrid-taxonomy fit** — ADR-005's hybrid taxonomy (projects as sibling roots at `parent_id=NULL`) already normalizes `Optimization.project_id` as the attribution axis. The Integration Store generalizes provider-side: each provider creates a `PromptCluster` with `state="project"` via `project_service.ensure_project_for_repo()` (or its sibling for non-repo providers) and maintains its own link record. `LinkedRepo.project_node_id` stays as the GitHub-specific link record; the generalized contract is a `LinkedSource` protocol where each provider owns its link table (or a shared polymorphic link table).
- **Provider lifecycle** — install (enable provider), configure (auth + link a source), unlink (preserve data, clear link), uninstall (disable provider). Each provider brings its own auth flow (GitHub OAuth, Google OAuth, Notion API key, no auth for local files).
- **Frontend: Integrations panel** — new Navigator section showing installed providers with install/configure/unlink controls. Replaces the current GitHub-specific Navigator section.

**Candidate providers:**

| Provider | Vertical | Context source | Auth |
|----------|----------|---------------|------|
| GitHub | Developers | Repo files, README, architecture docs | OAuth (Device Flow) |
| Google Drive | Business/marketing | Documents, spreadsheets, brand guidelines | OAuth |
| Notion | Product/content | Pages, databases, knowledge bases | API key |
| Local filesystem | Anyone | Any directory on disk | None |
| Confluence | Enterprise | Wiki pages, project specs | API token |
| Figma | Design | Design system docs, component specs | API key |

**Supersedes:** The former "Project Workspaces — explicit project_id override" item. ADR-005 F3 already shipped explicit `project_id` on `/api/optimize`, `/api/refine`, and `synthesis_optimize`; the remaining work is the provider abstraction itself.

**Prerequisite:** ADR-006 (universal engine principle). The integration store is the concrete mechanism that makes the universal engine accessible to non-developer verticals.

**Files:** New `backend/app/services/integrations/` package (provider protocol, registry, lifecycle). Refactor `github_repos.py` → provider implementation. New `backend/app/routers/integrations.py`. Frontend `Integrations` panel. Migration for `LinkedSource` generalization or per-provider link tables.

---

### Non-developer onboarding pathway
**Status:** Partially shipped — engine parity complete, UI adaptation remains
**Context:** ADR-006 established that the engine is already universal. Work shipped in v0.3.x verifies this: seed-agent hot-reload, organic domain discovery, signal loader, removal of `VALID_DOMAINS`/`DOMAIN_COLORS`/`KNOWN_DOMAINS`/`_DOMAIN_SIGNALS`, domain lifecycle with no seed protection. A non-developer using Project Synthesis today gets correct clustering, pattern discovery, and scoring — but the UI still assumes developer context: GitHub OAuth in the sidebar, "Clusters" and "Taxonomy" jargon, 5 developer-only seed agents, codebase scanning references in Settings.

**Remaining work:**

1. **Content-first vertical additions** (ADR-006 playbook) — add marketing/writing/business seed agents to `prompts/seed-agents/`. Add domain keyword seeds via Alembic migration for non-dev domains. Add heuristic weakness signals for non-dev verticals. Lowest effort, relies on organic discovery once seeded.

2. **Adaptive UI labels** — taxonomy concepts get user-facing aliases based on the active vertical. "Clusters" → "Pattern groups" for non-developers. "Domains" → "Categories." "Meta-patterns" → "Proven techniques." The underlying data model is unchanged — only display labels adapt. Driven by a `vertical: "developer" | "general"` preference.

3. **Vertical-aware onboarding** — first-run flow asks "What do you primarily use AI for?" Selection configures: which integrations are highlighted, what seed agents appear in the SeedModal, what language the UI uses, which Navigator sections are visible by default. Depends on the Integration Store item above for GitHub to become one of many.

**Recommended order:** (1) → (2) → (3). Each step is independently valuable and shippable.

**Spec:** [ADR-006](adr/ADR-006-universal-prompt-engine.md)

---

### Hierarchical topology navigation — project → domain → cluster → prompt
**Status:** Planned (no firm version target — moved to v0.5.x post-foundation per "Post-foundation horizon" above; original v0.4.0 target predated the foundation phase reordering. Edge system shipped in v0.3.30; drill-down is a major render-pipeline rewrite that deserves a clean major or its own focused minor.)
**Context:** The current 3D topology view (`SemanticTopology`) renders ALL nodes in a single scene: project nodes, domain nodes, active clusters, candidates, mature clusters — 76+ nodes at current scale. At 200+ clusters across 3 projects, this becomes visually overwhelming. Domain nodes (structural grouping) and active clusters (semantic content) serve different purposes but are rendered identically in the same space. There is no way to "zoom into" a project or domain.

**Vision:** A hierarchical drill-down topology inspired by filesystem navigation. Each level of the taxonomy hierarchy gets its own view with appropriate aesthetics and interaction patterns:

**Level 0: Project Space** — outermost view showing project nodes as large entities with gravitational relationships. Distance reflects semantic similarity; size reflects optimization count; color reflects dominant domain. Projects with cross-project GlobalPatterns have visible connection lines. Double-click to drill in.

**Level 1: Domain Map** (per project) — shows the domains within a selected project. Each domain is a region or cluster with its own color. Size reflects member count; distance reflects domain overlap. Sub-domains nested. Double-click to drill in.

**Level 2: Cluster View** (per domain) — the current topology experience scoped to a single domain's clusters. No domain nodes at this level — they're the parent you drilled from. Lifecycle state coloring (active, mature, candidate). Double-click to drill in.

**Level 3: Prompt Detail** (per cluster) — individual optimizations within a cluster. Each node is a prompt. Size reflects score; color reflects improvement delta; position reflects embedding proximity. Hover shows prompt text; click loads it in the editor. New visualization replacing the current cluster detail panel's optimization list.

**Navigation:**
- Breadcrumb bar: `All Projects › user/backend-api › backend › API Endpoint Patterns`
- Back / Escape returns to parent level
- Smooth zoom transitions (like macOS folder zoom)
- Each level preserves camera position when returning
- Ctrl+F search works across all levels

**Per-level aesthetics:**
- L0 (projects): large glowing orbs, minimal, wide spacing, slow drift. Ambient starfield
- L1 (domains): colored regions with soft boundaries, domain labels prominent, keyword clouds on hover
- L2 (clusters): current wireframe contour style, lifecycle state encoding, force layout — most data-dense level
- L3 (prompts): small nodes, text-preview on hover, score-gradient coloring, tight clustering

**Technical approach:**
- Each level is a separate Three.js scene (or scene state) with its own camera, lighting, and node renderer
- Level transitions animated (camera fly-through + node scale/fade)
- Data loading is lazy — L2 and L3 data fetched on drill-down
- Existing `TopologyData`, `TopologyRenderer`, `TopologyInteraction` refactor into level-aware variants
- `GET /api/clusters/tree?project_id=...` (ADR-005 B6, shipped) provides per-project data. New endpoints for per-domain and per-cluster detail views
- `TopologyWorker` force simulation runs per-level (different force parameters per level)

**Single-project behavior:** When only one project exists (Legacy or a single repo), skip Level 0 and open directly at Level 1.

**Legacy project:** Always visible at Level 0 as a permanent sibling root (ADR-005 hybrid). Contains all pre-repo and non-repo optimizations.

**ADR-006 label adaptation:** Level labels respect the active vertical. Developers: Projects → Domains → Clusters → Optimizations. Non-developers: Workspaces → Categories → Pattern groups → Prompts. Driven by the preference from the non-developer onboarding item.

**Prerequisites:** ADR-005 hybrid (shipped). The Integration Store for project creation beyond GitHub. Non-developer onboarding for vertical-aware labels.

**Files:** Major frontend refactor. New `TopologyLevel0…3` components. Refactored `TopologyNavigation` with breadcrumb + back. New `topology-state.svelte.ts` store for current level + drill path. New backend endpoints for per-domain cluster lists and per-cluster optimization lists with spatial data. Updated `TopologyWorker` with per-level force configs.

---

### MCP routing fallback — per-client capability awareness
**Status:** Deferred — partially mitigated by v0.4.2 Hybrid Phase Routing + priority reshuffle
**Context:** Historically, MCP tool calls from non-sampling clients (e.g., Claude Code) were routed to the sampling tier when a sampling-capable client (VS Code bridge) was also connected, failing with "Method not found" because the calling client didn't support `sampling/createMessage`. v0.4.2 landed two related fixes that substantially reduce blast radius: (1) `resolve_route()` now tries tier 3 `internal` before tier 4 `auto_sampling`, so whenever a provider is detected the auto path prefers internal even if `sampling_capable=True`; (2) Hybrid Phase Routing means fast phases (analyze/score/suggest) always run on the internal provider — sampling is only invoked for the optimize phase when the caller is sampling-capable. `_write_optimistic_session` also no longer forces `sampling_capable=True` on session-less reconnects.

**Remaining work:** The `RoutingManager` still tracks `sampling_capable` as a single process-global flag, so a true per-client capability registry is not yet in place. The remaining sharp edge is `force_sampling=True` from a non-sampling MCP caller while another sampling-capable session exists — that path still routes to sampling and fails. Revisit when the issue re-emerges.

**Proposed approaches (when revisiting):**
1. **Per-client capability tagging** — track each MCP session's declared capabilities from `initialize`. Route based on the calling session's sampling support, not the global flag.
2. **Internal fallback for MCP** — if sampling fails for an MCP caller, retry on internal pipeline when a provider exists. Simpler but reactive.

**Files:** `services/routing.py` (resolve_route), `mcp_server.py` (capability middleware), `tools/optimize.py` (context construction)

---

### REST-to-sampling proxy via IDE session registry
**Status:** Deferred — scope narrowed by v0.4.2 routing changes
**Context:** The web UI cannot perform MCP sampling because `POST /api/optimize` uses `caller="rest"`, which routing correctly blocks from sampling. A previous sampling proxy was removed in v0.3.16-dev because it was architecturally broken: the proxy opened a new MCP session with no sampling handler. In v0.4.2, `_can_sample()` was narrowed from `ctx.caller in ("mcp", "rest")` to `ctx.caller == "mcp"`, formalizing the REST exclusion.

**Root cause:** MCP's `create_message()` is a server-to-client request that goes to the session that made the tool call. No mechanism exists to route a sampling request through a *different* client's session.

**Proposed solution (if revisited):** The MCP server maintains a session registry mapping session IDs to declared capabilities. When a REST caller invokes the optimize endpoint and the user wants to use their IDE LLM:
1. Handler queries the registry for any active sampling-capable session
2. If found, borrows that session's `create_message()` channel
3. Original caller receives the result when the IDE completes sampling
4. If no sampling session exists, falls back to internal/passthrough with clear error

**Complexity:** Medium-high. Requires cross-session request routing in FastMCP, proper cleanup when IDE sessions disconnect mid-request, race-condition handling.

**Files:** `mcp_server.py` (session registry + lifecycle hooks), `services/mcp_proxy.py` (optional), `tools/optimize.py`, `services/routing.py`

---

### Unified scoring service
**Status:** Planned
**Context:** The scoring orchestration (heuristic compute → historical stats fetch → hybrid blend → delta compute) is repeated across `pipeline.py`, `sampling_pipeline.py`, `save_result.py`, and `optimize.py` with divergent error handling. A shared `ScoringService` would eliminate duplication and ensure consistent behavior across all tiers.
**Spec:** Code quality audit (2026-03-27) identified this as the #3 finding

---

### Unified onboarding journey
**Status:** Planned
**Context:** The current system has 3 separate tier-specific modals (InternalGuide, SamplingGuide, PassthroughGuide) firing independently on routing tier detection. This creates a fragmented first-run experience — users only see one tier's guide and miss the others.

**Two changes required:**

1. **Consolidated onboarding modal:** Replace the 3 separate modals with a single multi-step journey walking through all 3 tiers sequentially. Each tier section is actionable — user must acknowledge each before proceeding. Modal blocks the UI until all steps are actioned. Fires at every startup unless "Don't show again" is checked and persisted.

2. **Dynamic routing change toasts:** Replace per-tier-change modal triggers with concise inline toasts that explain *what caused* the routing change ("Routing changed to passthrough — no provider detected"). Fire only on *automatic* tier transitions, not manual toggles.

**Prerequisite:** Refactor `tier-onboarding.svelte.ts`, merge 3 guide components, new `onboarding-dismissed` preference field.

---

### Pipeline progress visualization — optimize/refine streaming previews
**Status:** Planned (GitHub indexing shipped v0.3.40 with full phase SSE)
**Context:** GitHub indexing now publishes live `index_phase_changed` SSE events (`pending → fetching_tree → embedding → synthesizing → ready|error`) with files_seen/files_total counters and synthesized error messages. Optimize and refine flows still lack rich progress — they show a phase indicator and step counter (v0.3.8-dev) but no streaming preview, estimated time, or per-phase timing surfaced in the UI.

**Scope:** Stream partial tokens from the optimize/refine phases into the Inspector so users see the optimizer working. Per-phase timing breakdown in the Inspector footer (analyze X ms, optimize Y ms, score Z ms). Estimated remaining time based on rolling per-phase histograms. Tier-adaptive visualization — sampling tier shows IDE-side progress, passthrough shows "waiting on user to paste".

**Files:** `routers/optimize.py` (SSE payload extensions), `frontend/src/lib/components/layout/Inspector.svelte` (streaming preview slot), possibly a new `PhaseTimingStrip.svelte` component.

---

## Exploring

### Domain FK on Optimization table
**Status:** Exploring
**Context:** `Optimization.domain` is currently a `String` column storing the domain node's label (e.g., `"backend"`). Resolution uses label lookup against `PromptCluster` rows where `state='domain'`. This works correctly via `DomainResolver` but requires subqueries for domain-level aggregations. Adding an optional `domain_cluster_id` FK to `PromptCluster.id` would enable direct JOINs.

**Triggers (implement when any becomes a priority):**

1. **Domain-level analytics dashboard** — average score improvement per domain over time, member count trends, strategy effectiveness.
2. **Domain-scoped strategy affinity** — the adaptation tracker currently tracks `(task_type, strategy)` pairs. Domain-scoped tracking — `(domain, strategy)` — would enable insights like "chain-of-thought works best for security prompts". Most likely trigger.
3. **Cross-domain relationship graph** — weighted edges between domain nodes in the topology. FK enables `GROUP BY domain_cluster_id` aggregations.

**Migration:** Add nullable `domain_cluster_id` FK alongside existing `domain` String. Backfill from label lookup. Both columns coexist. Non-breaking.
**Decision:** ADR-004 deferred this as YAGNI. Revisit when a concrete feature requires domain-level JOINs.

---

### Conciseness heuristic calibration for technical prompts
**Status:** Exploring
**Context:** The heuristic conciseness scorer uses Type-Token Ratio which penalizes repeated domain terminology ("scoring", "heuristic", "pipeline" across sections). Technical specification prompts score artificially low on conciseness despite being well-structured. Needs a domain-aware TTR adjustment or alternative metric.

---

### Probe Phase 3 body extraction — deferred from Foundation P2 (2026-05-06)
**Status:** Exploring (was Foundation P2 Path B; deferred 2026-05-06 after spec validation surfaced unresolved architectural questions).
**Context:** The original Foundation P2 design proposed extracting Phase 3's ~600-LOC body from `ProbeService._run_impl()` into a free function `running(...)` in a new `probe_phase_3.py` module. After 5 spec validation rounds + 1 plan validation round, the plan reviewer caught structural defects:

1. **`_run_impl()` is `async def -> AsyncIterator[Any]`**, and Phase 3 contains 3 `yield` statements (`yield ProbeRateLimitedEvent`, `yield ProbeProgressEvent` ×2). A function with `yield` becomes an `AsyncGenerator`, not an awaitable returning a tuple. The spec's `running(...) -> tuple[list, int, int]` signature is impossible. Two valid redesigns: (a) `running(...) -> AsyncIterator[Event]` with caller doing `async for ev in running(...): yield ev`, or (b) `running(...)` accepts a `yield_callback` parameter that the caller wires to its own `yield`. Both change the orchestrator's call shape.

2. **Spec's 10-param `running(...)` signature is wrong.** Live `grep -oE "self\.\w+"` over Phase 3 (lines 924-1530) shows 7 actual captures: `context_service`, `embedding_service`, `_pending_to_prompt_result`, `provider`, `_resolve_write_queue`, `session_factory`, `_tag_probe_rows`. Spec invented `target_score`, `read_failures`, `embed_failures` — none exist in source. Spec also got attribute names wrong (`self._db` vs actual `self.db`, etc.).

3. **Phase 3 has 8+ inline imports** at lines ~938-952 (`from app.config import PROMPTS_DIR`, `from app.services.batch_orchestrator import run_batch`, etc.). Lifting them to top-of-`probe_phase_3.py` could introduce circular-import risk via `taxonomy/__init__`; keeping them inline works but is unconventional.

4. **Test patch-target drift.** `tests/test_probe_service.py:1042` uses `patch.object(probe_service_mod, "_render_final_report", ...)`; `tests/test_probe_service.py:1100` uses `monkeypatch.setattr(ps_mod, "bulk_persist", ...)` — both rebind module attributes that move post-extraction. Whether they continue to work depends on call-site choice (bare-name lookup vs fully-qualified import). Spec didn't address.

**v0.4.17 P2 (Path A only) shipped without these issues** — extracted only the 9 module-level free functions + `current_probe_id` ContextVar to 3 new modules (probe_common, probe_phases, probe_phase_5). `_run_impl()` body untouched. Pure code-move.

**Triggers (revisit when):**
- A Topic Probe T2/T3 feature requires a stable Phase 3 entry point (not currently true — T2 ships on the unified `RunRow` substrate from P3, not on Phase 3 isolation)
- The orchestrator-extracted-from-yield-generator pattern is needed elsewhere in the codebase (e.g., refine pipeline) — would amortize the design cost
- A different cycle revisits `probe_service.py` for unrelated reasons and the Phase 3 body has grown further

**When ready, the design must answer:**
- Async-generator return vs yield-callback parameter (pick one)
- Exhaustive `self.<X>` capture audit (live grep, not spec-invented)
- Inline-import handling (lift vs keep, with circular-import audit)
- Test patch-target audit (bare-name lookup vs fully-qualified — preserve current semantics)

**Files:** `backend/app/services/probe_service.py` (Phase 3 body removal — currently lines 924-1530), `backend/app/services/probe_phase_3.py` (NEW — full body + inner-closure `_abort_watcher`).

---

### `Accept`-header content negotiation for SSE on `POST /api/seed` — deferred from Foundation P3 (2026-05-06)
**Status:** Exploring (deferred from Foundation P3 brainstorm). No version target.
**Context:** P3 (v0.4.18) keeps `POST /api/seed` synchronous to preserve byte-for-byte backward compat with all existing callers (REST, MCP `synthesis_seed`, frontend `SeedModal`, CLI). Live UI updates flow through the existing `/api/events` global SSE bus filtered by the additive `run_id` field, which suffices for the T4 history surface. Future clients that prefer SSE on the POST itself (parity with `POST /api/probes`) can be served via HTTP content negotiation: `Accept: application/json` → sync as today; `Accept: text/event-stream` → SSE stream of `seed_*` events terminating on `seed_completed`/`seed_failed`. Strictly additive — old callers unchanged.

**Trigger:** any of the following.
1. T4's frontend history-detail UI prefers tailing the POST response over subscribing to `/api/events` (e.g., to handle race conditions when the modal opens before subscription registers).
2. A new external integration (CI script, automation tool) requests SSE-on-POST without bus-subscription overhead.
3. `synthesis_seed_stream` MCP tool variant needed for parity with future streaming MCP patterns.

**Files (estimated):** `backend/app/routers/seed.py` (Accept-header switch + SSE response branch), `backend/app/tools/seed.py` (optional streaming variant), 6-8 new tests covering both paths + Accept-header fallback semantics.

**Decision:** YAGNI at P3 ship time. Revisit post-T4 if any trigger fires.

---

### `current_probe_id` → `current_run_id` ContextVar rename completion — deferred from Foundation P3 (2026-05-06)
**Status:** Exploring (cleanup follow-up). No version target.
**Context:** Foundation P3 (v0.4.18) renames the `current_probe_id` ContextVar (declared in `services/probe_service.py`, re-exported by `services/probe_event_correlation.py`) to `current_run_id` as part of the substrate unification — every taxonomy event fired during a run now correlates via the unified ContextVar regardless of mode. To preserve byte-for-byte backward compat for any out-of-tree consumer or test patching `current_probe_id` directly, P3 keeps the old name as a re-export alias of the new one. The alias is dead weight after a few release cycles confirm no in-tree callers use the old name.

**Trigger:** 2+ release cycles post-v0.4.18 with zero in-tree references to `current_probe_id` (verified via grep) and no external bug reports about ContextVar correlation. Then drop the re-export shim and let any remaining stragglers update to the canonical name.

**Files (estimated):** `backend/app/services/probe_event_correlation.py` (delete `current_probe_id` re-export), 1-2 test updates if any tests still import the old name.

**Decision:** Bundle into a future cleanup-track release (likely v0.4.20+). Sub-1-hour change once trigger is met.

---

### Cold-path commit chunking with per-phase Q-gates → **Foundation P1 (v0.4.16)** — SHIPPED
**Status:** SHIPPED v0.4.16 (P1a). Full historical detail in [`SHIPPED.md`](SHIPPED.md#v0416--foundation-p1-sqlite-debt-closure-2026-05-05). Retained here for technical context + cross-reference.
**Context:** v0.4.13 shipped the single-writer queue and v0.4.14 finalized short-lived foreground writes, but the taxonomy cold-path full-refit remains on `WriterLockedAsyncSession` + `cold_path_mode` audit-hook bypass. The refit's transaction span (multi-second commits across thousands of cluster rows) does not fit the queue's per-task timeout model.
**Plan:** chunk each refit phase (re-embedding pass, cluster reassignment, label reconciliation, member-count repair) into per-batch `submit()` calls with `await asyncio.sleep(0)` between, gated by per-phase Q-gates so a downstream phase only runs after its upstream chunk batch confirms commit. Each chunk fits the default `timeout=300s` envelope. Removes the last `cold_path_mode` audit-hook bypass; cold path becomes structurally identical to hot/warm in routing terms.
**Files:** `backend/app/services/taxonomy/cold_path.py`, `taxonomy/warm_phases.py` (refit invocation), audit-hook flag retirement in `database.py`.

---

### `_bg_index` / `RepoIndexService.build_index()` per-file batching → **Foundation P1 (v0.4.16)** — SHIPPED
**Status:** SHIPPED v0.4.16 (P1b — combined with cold-path chunking). Full historical detail in [`SHIPPED.md`](SHIPPED.md#v0416--foundation-p1-sqlite-debt-closure-2026-05-05). Retained here for technical context + cross-reference.
**Context:** v0.4.14 left `_bg_index` and `RepoIndexService.build_index()` on the legacy session because per-file index inserts span minutes for large repos. Plan: per-file write batching where each batch is one `submit()` call that fits the default `timeout=300s` envelope, with progress events between batches so users see live indexing.
**Files:** `backend/app/services/repo_index_service.py`, `routers/github_repos.py` (background-task integration).

---

### `tools/refine.py` handler restructure → **Foundation P4 (v0.4.19)**
**Status:** SUPERSEDED by Foundation P4 entry above. Retained for cross-reference.
**Context:** v0.4.14 deferred `tools/refine.py:50, :156` because the handler wraps `RefinementService` LLM call inside its own session — write-queue migration requires extracting the LLM call out of the session lifetime so the queued submit owns only the persistence boundary, not the multi-second LLM round-trip.
**Files:** `backend/app/tools/refine.py`, `services/refinement_service.py` (session boundary refactor).

---

### `tools/save_result.py` handler restructure → **Foundation P4 (v0.4.19)**
**Status:** SUPERSEDED by Foundation P4 entry above. Retained for cross-reference.
**Context:** v0.4.14 deferred `tools/save_result.py:85` because the handler wraps heuristic scoring + analyzer A4 LLM fallback inside its own session. Same restructure pattern as refine: extract LLM call from session lifetime, keep only persistence inside the queued submit.
**Files:** `backend/app/tools/save_result.py`, scoring/analyzer call-site refactor.

---

### `tools/optimize.py:198` orchestrator restructure → **Foundation P4 (v0.4.19)**
**Status:** SUPERSEDED by Foundation P4 entry above. Retained for cross-reference.
**Context:** v0.4.14 deferred the `tools/optimize.py:198` site because the handler wraps the full `PipelineOrchestrator` 4-LLM SSE loop. The orchestrator must be split so each persistence boundary is a discrete `submit()` while the long LLM phases run outside any session.
**Files:** `backend/app/tools/optimize.py`, `services/pipeline.py` (SSE persistence boundary extraction).

---

### Switch read-engine audit hook to RAISE in production (v0.4.14.x time-gated)
**Status:** Planned for v0.4.14.x patch
**Context:** v0.4.13 shipped the audit hook in WARN mode for dev/prod and RAISE-only for CI (`WRITE_QUEUE_AUDIT_HOOK_RAISE` env flag); v0.4.14 finalized the short-lived foreground writer migration with 0 audit warns under the OPERATE regression bar. Once 7+ days of zero locks/warns confirmed in real usage post-v0.4.14, switch the production default to RAISE. Drift writes that escape the migration become hard failures at source instead of WARN-and-continue.

**Trigger:** 7 consecutive days post-v0.4.14 ship with zero `database is locked` errors and zero audit-hook WARN events on the active branch.

---

### Remove `WriterLockedAsyncSession` defense-in-depth class (v0.4.14.x time-gated)
**Status:** Planned for v0.4.14.x patch
**Context:** v0.4.13 retains the legacy `WriterLockedAsyncSession` (process-wide flush serializer) as defense-in-depth during the post-release watch window. Once the audit hook is RAISE in production AND no flush events fire for 7+ days, the class is dead code and gets deleted along with its tests.

**Trigger:** post-RAISE-switch + zero flush events for 7+ days.

---

### LLM domain classification — remaining optimizations
**Status:** Exploring (core heuristic pipeline shipped v0.3.30)
**Context:** v0.3.30 shipped the heuristic accuracy pipeline: compound keywords (A1), technical verb+noun disambiguation (A2), TF-IDF domain signal auto-enrichment (A3), confidence-gated Haiku LLM fallback (A4). Classification agreement tracking (E1) provides ongoing measurement. Prompt-context divergence detection (B1+B2) ships tech stack conflict alerts with 4-category intent classification.

**Remaining future optimizations (exploring, not yet designed):**
- **Constrained decoding** — `Literal` enum on `AnalysisResult.domain` to restrict LLM output at schema level
- **Dynamic text fallback keywords** — `_build_analysis_from_text()` uses hardcoded keywords instead of `DomainSignalLoader`
- **DomainResolver confidence-aware caching** — unknown domain cached as "general" at low confidence persists; self-corrects on `load()`
- **C2: Heuristic-to-LLM reconciliation** — use accumulated E1 disagreement data to adjust keyword weights over time. Requires `signal_adjuster.py`
- **E1b: Cross-process agreement bridge** — MCP process agreement data invisible to health endpoint. Needs HTTP POST forwarding

**Specs:** [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md), [`docs/enrichment-consolidation-action-items.md`](enrichment-consolidation-action-items.md), [`docs/specs/phase-a-heuristic-accuracy-a3-a4.md`](specs/phase-a-heuristic-accuracy-a3-a4.md)

---

### Hybrid taxonomy empty-state polish
**Status:** Exploring
**Context:** ADR-005 F5 shipped the empty-state panel for scoped project views (when "show project X" has zero clusters). Copy is intentionally generic today ("This project has no clusters yet"). Once the non-developer onboarding pathway lands, per-vertical copy would sharpen the message — e.g., "Start optimizing marketing copy" vs. "Start optimizing code prompts" — driven by the same `vertical` preference.

**Prerequisite:** Non-developer onboarding pathway (adaptive UI labels step).

---

## Deferred

### Passthrough refinement UX
**Status:** Deferred (low demand)
**Context:** Passthrough results cannot be refined (returns 503). Refinement requires an LLM provider to rewrite the prompt. Users who passthrough have their own external LLM — refinement would need a different interaction model (show the assembled refinement prompt for copy-paste like the initial passthrough flow).
**Rationale:** Users who use passthrough can iterate manually.

---

### ADR-005 Phase 3 — HNSW + round-robin at scale
**Status:** Deferred (trigger-gated)
**Context:** ADR-005's Phase 3 work is partially shipped (`_HnswBackend` exists in `backend/app/services/taxonomy/embedding_index.py`, activated at `HNSW_CLUSTER_THRESHOLD=1000`; `AdaptiveScheduler` shipped as part of B-layer). The deferred piece is large-corpus stress validation — trigger condition (≥1000 clusters sustained across warm cycles) has not been reached at current v0.4.4-dev scale.

**Trigger:** When a real corpus crosses the 1000-cluster threshold for multiple consecutive warm cycles, amend ADR-005 with validation results and any scheduler tuning that proves necessary at scale.

**Files:** Amendment to `docs/adr/ADR-005-taxonomy-scaling-architecture.md`. Potentially `backend/app/services/taxonomy/_constants.py` for tuned thresholds.

---

## Shipped

For the historical record of completed work — every release tag from v0.3.6-dev to the current latest, with per-fix detail, file/line references, and audit cross-links — see [`SHIPPED.md`](SHIPPED.md).

**Recent releases:**
- **v0.4.17** (2026-05-06) — Foundation P2 Path A: probe internals split into 3 new modules (`probe_common.py`, `probe_phases.py`, `probe_phase_5.py`) + 1 trimmed orchestrator (`probe_service.py` 2493 → 2204 LOC, ~12% shrink). `_run_impl()` body byte-for-byte preserved. Public API (`ProbeService` + `current_probe_id`) preserved via 2-symbol re-export shim. Path B Phase 3 body extraction deferred indefinitely.
- **v0.4.16** (2026-05-05) — Foundation P1: cold-path commit chunking with cumulative Q-gates (P1a) + `_bg_index`/`build_index`/`incremental_update`/`invalidate_index` migrated to WriteQueue with per-batch chunking + per-(repo, branch) `asyncio.Lock` + lifespan orphan recovery + 8 decision-event types + `repo_index` health block (P1b). 6 new constants in `taxonomy/_constants.py`.
- **v0.4.15** (2026-05-04 retroactive) — HistoryPanel pagination correctness P0: server-pushdown of `project_id` + `status` filters; capture-and-bail race guard at fetch sites. Retroactively tagged 2026-05-05.
- **v0.4.14** (2026-05-04) — SQLite migration finalization: short-lived foreground writes (passthrough pending, sampling persist, audit logs, OAuth flows, status updates) routed through WriteQueue; `submit_batch()` helper added
- **v0.4.13** (2026-05-04) — SQLite writer-slot contention architectural fix (single-writer queue), suite 3457 passing
- **v0.4.12** (2026-05-02) — Topic Probe Tier 1 + post-Tier-1 architectural hardening
- **v0.4.11** (2026-04-28) — domain proposal hardening (`fullstack` ghost-domain finding)
- **v0.4.10** (2026-04-28) — F3.1 persistence wiring for analysis-weighted overall score
- **v0.4.9** (2026-04-28) — audit-prompt scoring hardening F1–F5, suite 3177 passing
- **v0.4.8** (2026-04-27) — sub-domain dissolution hardening R1–R8, audit `sub-domain-regression-2026-04-27.md`
- **v0.4.7** (2026-04-26) — MCP routing + TF-IDF cascade source-3 + B5/B5+ writing-about-code + C1-C5 score calibration + T1.x learning loops
- **v0.4.6** (2026-04-25) — self-update hardening (preflight + drain + auto-stash)
- **v0.4.5** (2026-04-25) — pattern-injection provenance + post-LLM domain reconciliation
- **v0.4.4** (2026-04-25) — ADR-007 Tier 1 + Taxonomy Observatory Tier 1
- **v0.4.3** (2026-04-24) — bulk delete + History UX + brand audit
- **v0.4.2** (2026-04-23) — MCP sampling unification + Hybrid Phase Routing
- **v0.4.1** (2026-04-20) — sidebar refactor + backend Phase 3 module split
- **v0.4.0** (2026-04-19) — ADR-005 Hybrid Taxonomy + Opus 4.7 features

For per-change detail with commit SHAs, see [`CHANGELOG.md`](CHANGELOG.md). For architectural decisions, see [`adr/`](adr/).
