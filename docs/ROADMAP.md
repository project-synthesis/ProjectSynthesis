# Project Synthesis — Roadmap

Living document tracking planned improvements. Items are prioritized but not scheduled. Each entry links to the relevant spec or ADR when available.

**Snapshot:** v0.4.10-dev (in development, post-v0.4.9). Last release: v0.4.9 (2026-04-28 — audit-prompt scoring hardening F1–F5; suite 3177 passing + 1 skipped).

> **Shipped work archived in [`SHIPPED.md`](SHIPPED.md).** This file tracks only forward-looking items (Immediate, Planned, Exploring, Deferred) plus partial-tier work where a follow-up tier is still active.

## Conventions

- **Planned** — designed, waiting for implementation
- **Exploring** — under investigation, no decision yet
- **Deferred** — considered and postponed with rationale
- **Partially shipped** — portions shipped with version tags; remaining work called out

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

### Cycle-19→22 replay validation post-v0.4.9 (immediate follow-up)
**Status:** In flight (running 2026-04-28, 4 cycles × 20 prompts total via `scripts/validate_taxonomy_emergence.py`)
**Context:** v0.4.9 shipped F1-F5 audit-prompt scoring hardening. Cycle-23 (5 fresh prompts) was inconclusive — 2 hit Opus 4.7 infrastructure timeouts (>600s urlopen budget), 3 averaged 7.35 (vs v0.4.8 baseline 7.36). Replay re-runs the 20-prompt corpus through the v4 scoring formula. Expected: mean rises from 7.36 → ~7.85 per the audit doc's projection. Either confirms F1-F5 effectiveness on a representative corpus or surfaces a new bottleneck (LLM scorer, conciseness gate, etc.).

**Mean delta validation (pending):** post-replay, compare per-cycle mean against v0.4.8 baseline + measure F5 false-premise flag firings + verify F4 strategy fidelity (`strategy_used == effective_strategy` on every persisted row). If mean ≥ 7.7 → close the loop. If <7.7 → forensic deep-dive on which fix(es) under-delivered.

**Infrastructure follow-up:** extend `validate_taxonomy_emergence.py` `_post` timeout from 600s → 900s OR add an Opus-4.7 → Sonnet-4.6 downshift for prompts that exceed the budget, so future cycles don't lose data points to infrastructure.

---

### v0.4.11 domain proposal hardening (post-replay forensic finding, 2026-04-28)
**Status:** Planned
**Trigger:** the cycle-19→22 replay surfaced a "ghost domain" pathology — `fullstack` was promoted to a top-level domain at 04:59:35 UTC on insufficient evidence, ended up with 0 members, and is now frozen by the 48h dissolution grace gate (`DOMAIN_DISSOLUTION_MIN_AGE_HOURS=48`) until ~02:59 UTC the next day. The empty domain node clutters `/api/domains` and `taxonomy/tree` for ~46 hours with no functional path to dissolve.

**Root-cause analysis (live forensic):**

`engine._propose_domains()` accepted `fullstack` as a new domain on:
- **1 seed cluster** (single point of evidence — cluster `7461846c`, since merged out)
- **66.67% consistency** — just 2-of-3 prompts agreeing on `domain_raw="fullstack"`
- **Coherence = 0.0**, *skipped* the enrichment threshold (0.4) instead of failing the gate
- **`signal_enrichment: skipped_coherence` event** logged the issue but did not block creation

This violates the design intent. Domain emergence should be **signal concentration** — multiple clusters consistently labeling themselves with a new domain. One cluster of 3 prompts is not a domain; it's a cluster.

**Two structural bugs:**

1. **Single-cluster acceptance** — `_propose_domains()` does not enforce a minimum cluster-count floor. Sub-domain proposal already has equivalent `single_cluster` skip logic (visible in events: `sub_domain_skipped: reason=single_cluster, consistency_pct=83.3`). Domains lack the parallel gate.

2. **Coherence skip semantics** — when coherence is 0.0 (uncomputable due to single cluster), `signal_enrichment` logs `skipped_coherence` but does NOT block creation. Should either fail the gate or require an alternate evidence threshold.

**Compounding pathology:** subsequent prompts classifying `prompt_domain="fullstack"` get `tree_integrity_repair: domain_reset_to_general` (resolver cache race, or qualifier vocabulary mismatch) — meaning a ghost domain cannot organically accumulate followers. Worst of both worlds: created on weak evidence AND can't recover.

**Fixes:**

**P0 — `DOMAIN_PROPOSAL_MIN_SEED_CLUSTERS`** — add constant to `taxonomy/_constants.py` with default `3`. `_propose_domains()` rejects candidates below this floor. Module-level invariant assert at import time. R8-style fail-fast on configuration drift. Mirrors the existing sub-domain `single_cluster` gate. Estimated: ~50 LOC + 4 tests.

**P0 — `DOMAIN_PROPOSAL_REQUIRE_COHERENCE`** — add boolean constant to `_constants.py` with default `True`. When set, `_propose_domains()` rejects candidates with `coherence < DOMAIN_COHERENCE_FLOOR (0.4)`. Replaces the soft `skipped_coherence` event with a hard `proposal_rejected_coherence` event. Estimated: ~30 LOC + 3 tests.

**P1 — `POST /api/domains/{id}/dissolve-empty` operator endpoint** — bypasses the 48h age gate when `member_count == 0 AND age >= DOMAIN_GHOST_DISSOLUTION_MIN_AGE_MINUTES (default 30)`. Mirrors v0.4.8 R6 (`rebuild-sub-domains`) — operator escape hatch for the current ghost. Pydantic `ge=0` runtime validation, idempotent on already-dissolved targets, emits `domain_ghost_dissolved` telemetry. Rate-limited 10/min. Estimated: ~80 LOC + 3 router tests + 4 service tests.

**P2 — `ghost` readiness tier** — extend `compute_domain_readiness()` to return `ghost` tier when `member_count == 0 AND age < DOMAIN_DISSOLUTION_MIN_AGE_HOURS`. Surfaces in UI as "awaiting dissolution" instead of looking healthy in the topology. ToastDispatcher gates ghost-tier on a separate preference (default off — operator-only signal). Estimated: ~40 LOC + 2 tests.

**Acceptance criteria for v0.4.11 ship:**

1. Live verification: replay a fullstack-classifying prompt, confirm `_propose_domains()` rejects with `proposal_rejected_min_seed_clusters` event (P0.1)
2. Live verification: same prompt path with single cluster + 0 coherence rejected with `proposal_rejected_coherence` event (P0.2)
3. `POST /api/domains/{current-fullstack-id}/dissolve-empty` returns 200 + emits `domain_ghost_dissolved` + `taxonomy_changed`, with the ghost gone from `/api/domains` immediately (P1)
4. Frontend topology shows `ghost` tier on empty domains awaiting dissolution (P2)
5. Full backend suite passes ≥ 3187 (3180 + ~7 new)

**Spec target:** `docs/specs/domain-proposal-hardening-2026-04-28.md` (TBD).

**Cross-link:** the cycle-19→22 replay v2 (currently in flight) is the validation corpus for whether these proposal-gate changes affect organic domain emergence in the audit-class workload. Pre-fix `fullstack` was created from a cross-cutting prompt; post-fix the same evidence pattern would not promote — which may be desirable (no ghost) or undesirable (slower emergence). The replay's domain-creation event count tells us which.

---

### Topic Probe — agentic targeted exploration of a user-specified concern against the linked codebase
**Status:** **Tier 1 SHIPPED** (v0.4.12, 2026-04-29). Tier 2 / Tier 3 / Tier 4 remain **Planned** within the 0.4.x line: T2=v0.4.13, T3=v0.4.14, T4=v0.4.15.

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

**Tier 2 (v0.4.13) — Planned. PREREQUISITE: SQLite writer-slot contention fix (see "SQLite writer-slot contention — architectural fix" entry below). Tier 2 features depend on reliable persistence for save-as-suite + replay regression detection to produce trustworthy results. Same v0.4.13 release; contention fix lands first.**
- `POST /api/probes/{id}/save-as-suite` — fork a probe run into a `ValidationSuite` (frozen prompt fixture + assertions captured from the run's actual scores)
- `POST /api/probes/{id}/replay` — re-run a saved suite against current code state (regression detection)
- UI Navigator panel: "Topic Probe" tab in SeedModal + live taxonomy mini-view + final report card with copy-as-markdown
- `/api/health` regression alarm + StatusBar badge for suite-level mean drops ≥0.5 from baseline
- Topic-only mode (no codebase grounding) for non-developer verticals — drops Phase 1, generates from topic alone (ADR-006 follow-up)
- Replace blocking SSE with 202 Accepted + `GET /api/probes/{id}` polling for client-timeout decoupling on long probes (>10 prompts)

**Tier 3 (v0.4.14) — Planned:**
- `release.sh` CI hook: register critical probe topics as pre-release gates (fail = block release)
- Probe → seed-agent promotion flow: a saved probe with consistently high scores can be promoted to `prompts/seed-agents/<topic-slug>.md`
- "Drill into cluster" action on seed runs auto-launches a probe scoped to the cluster's intent_label
- Cross-tier composition: probe-discovered prompts feed seed-agent few-shot context

**Tier 4 (v0.4.15) — Planned:**
- Substrate unification: migrate `Optimization.source="batch_seed"` semantics into a `SeedRun` model that's effectively a `ProbeRun` with `template=True`. Single history surface. SeedModal becomes one tab with two modes (template-driven / topic-driven) sharing the same form scaffold.

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

### Hierarchical topology navigation — project → domain → cluster → prompt (target: v0.4.0)
**Status:** Planned (targeted for v0.4.0 — edge system shipped in v0.3.30; drill-down is a major render-pipeline rewrite)
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

### SQLite writer-slot contention — architectural fix (v0.4.13 P0)
**Status:** **Planned for v0.4.13** (trigger met by v0.4.12 probe integration validation)

**Context:** Probes v22 → v29 (live integration validation against the v0.4.12 probe Tier 1 surface, 2026-04-29) catalogued a sustained `database is locked` failure mode that defeats every layer of the existing writer-coordination stack (`busy_timeout=30s`, app-level `bulk_persist` retries 5×, `WriterLockedAsyncSession` asyncio.Lock, per-prompt streaming, early-abort, warm-path Groundhog Day fix). The orphan audit on `probe_run` found **11 of 26 historical runs** (every probe since v9's canonical-batch refactor) silently lost all 5 optimization rows.

**Diagnostic chain (v22 → v29):**
- **v22**: pytest racing + warm-path concurrent → 0 of 5 persisted, silent-success defect surfaced (probe reported `status='completed'` from in-memory aggregate). Verify-after-persist gate added in `ae379bf6` to make failures loud.
- **v23/v24**: clean services, no pytest. Still catastrophic. Confirmed contention is not test-fixture-driven.
- **v25**: rate-limit fallback path. All 5 persists collapsed into a tight window. Catastrophic. Per-prompt streaming added in `e32515eb` to reduce per-attempt window size.
- **v26**: full architectural fix stack (verify-gate + streaming + warm-path-age fix + early-abort). Still catastrophic. Confirmed the contention is at a layer below all the orchestration fixes.
- **v27**: **MCP server stopped, only backend running**. Still catastrophic. **This proves the contention is purely within-backend, not cross-process** — refining the framing in the original ROADMAP entry which suspected MCP-vs-backend was the dominant case.
- **v28**: pool_size=1 + early-abort. Different failure mode (`QueuePool limit of size 1 overflow 0 reached, connection timed out, timeout 30.00`) — pool deadlocks when LLM calls hold connections inside `ContextEnrichmentService.enrich()` and peer Phase 3 tasks need a connection. Confirmed contention IS at the connection/pool layer, but pool_size=1 is too restrictive. Reverted in `7693efc8`.
- **v29**: clean revert. Catastrophic again — same failure as v22-v27.

**Root-cause analysis:** Within a single backend process, `WriterLockedAsyncSession` correctly serializes FLUSH calls via the process-wide asyncio.Lock. But SQLAlchemy's connection pool checks out separate underlying SQLite connections per session — the asyncio.Lock guards the flush moment, NOT the underlying connection's WAL writer-slot acquisition. When a connection releases the asyncio.Lock after commit, it may still hold lingering WAL state during transition cleanup; the next writer's connection sees `database is locked` despite holding the asyncio.Lock.

**Two implementation options for v0.4.13:**

#### Option A — Single-writer queue worker (in-process, ~300 lines)
Route ALL writes through one dedicated async worker that owns a single SQLite connection. Other code wanting to write enqueues a task and awaits its `Future`. Eliminates connection-pool races without changing the database engine.

- **Pros:** Lower scope (no migration), preserves SQLite simplicity, no infrastructure changes, no test-fixture rewrites.
- **Cons:** Doesn't help cross-process contention (MCP write paths) — but v27 proved cross-process is not the dominant case anyway. Throughput-bound by the single worker.
- **Files:** new `app/services/write_queue.py` (worker + queue + `submit()` API), `bulk_persist`, `optimization_service`, `feedback_service`, `taxonomy/family_ops`, `taxonomy/warm_path` callsites refactored to enqueue. ~30 callsites total.
- **Risk:** Higher latency on individual writes (queue serialization), debuggability harder during failure cascades.

#### Option B — PostgreSQL migration
Replace `aiosqlite` with `asyncpg` + PostgreSQL. MVCC handles reader/writer contention natively at the DB layer.

- **Pros:** Permanent architectural fix, supports concurrent multi-user access, scales horizontally.
- **Cons:** Significant infrastructure change — Alembic migration, connection pooling re-config, Docker Compose for local dev, production deployment update, ~all test fixtures rewritten (in-memory PostgreSQL via `pgserver` or testcontainer).
- **Files:** `database.py` (engine), `config.py` (DATABASE_URL), `main.py`/`mcp_server.py` (PRAGMA removal), `docker-compose.yml` (new), every test fixture.
- **Risk:** Migration timing — if the user has existing local SQLite DBs with active probe data, need a migration tool.

**Recommendation:** Ship **Option A first** as the immediate v0.4.13 P0. Cheaper, faster, addresses the actually-observed failure mode (within-backend contention). Move PostgreSQL to a parallel track (v0.5.x) when concurrent multi-user access becomes a real requirement.

**Prerequisite for Topic Probe Tier 2** (v0.4.13 save-as-suite + replay): persistence MUST be reliable for replay-mode regression detection to produce trustworthy results. Topic Probe Tier 2 ships AFTER the contention fix — same v0.4.13 release, Option A first.

**v0.4.12 partial mitigations already shipped** (see `docs/CHANGELOG.md`):
- Verify-after-persist gate — no silent success
- Per-prompt streaming — smaller transactions
- Early-abort on catastrophic — saves 12-20 min of LLM tokens per failed run
- Warm-path Groundhog Day fix — no compounding

These make failures loud and structured but don't fix the root cause. Probes under realistic concurrent-writer load still fail catastrophic.

**Files:** see Option A scope above. Tracking commit references: probes v22-v29 diagnostic chain in `docs/CHANGELOG.md`.

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
