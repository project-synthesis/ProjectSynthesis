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

### Validation tier — first-class regression detection alongside seed tier
**Status:** Planned
**Motivating use case:** the cycle-19→22 replay above. Today the validation harness is a CLI script with hardcoded `PROMPT_SETS`; results live in `data/validation/<cycle>.json` and are invisible to the running system. Productizing it closes the regression-detection gap that v0.4.9 surfaced.

**Vision:** A `ValidationSuite` peer to seed agents — hot-reloaded markdown files in `prompts/validation-suites/*.md` with curated prompts + assertions + optional baseline run reference. Each suite captures pre/post snapshots (cluster tree, domain readiness, scoring distribution from `/api/health`), submits prompts sequentially through the live pipeline, computes per-prompt + aggregate metrics, and compares against an optional baseline run for delta analysis. Different intent than seed (which generates diverse content via LLM agents); same execution primitive (`batch_pipeline.run_single_prompt`).

**Core capabilities:**

1. **Hot-reloaded suites** — `prompts/validation-suites/*.md` with YAML frontmatter (name, description, tags, baseline_run_id) + prompts in markdown body + assertions block. Mirror seed agent loader pattern.

2. **`ValidationRun` model + service** — captures pre/post snapshots, runs prompts sequentially (preserves the existing 35s warm-path debounce semantic), aggregates per-prompt scores, persists raw + summary, computes delta vs baseline. New `routers/validation.py`: `POST /api/validation/runs` (SSE), `GET /api/validation/runs`, `GET /api/validation/runs/{id}`, `GET /api/validation/runs/{id}/diff/{baseline_id}`.

3. **`synthesis_validate` MCP tool** — args: `suite_name`, `baseline_run_id` (optional). Returns structured `ValidationRunResult` with per-prompt scores, aggregate mean, delta vs baseline, flagged divergences (e.g., F5 fires on a prompt that didn't fire in baseline).

4. **Assertion DSL** — YAML frontmatter or per-prompt YAML blocks define assertions: `min_score`, `must_have_flags`, `must_not_have_flags`, run-level (`mean >= 7.7`, `p5 >= 6.5`). Pass/fail status persists on each run.

5. **Killer feature — automatic regression detection.** `/api/health` gains `validation` block reporting last-run-per-suite mean + delta vs registered baseline. StatusBar surfaces a red badge if any suite drops ≥0.5 from baseline. Optional `release.sh` hook runs registered validation suites against the dev branch before tagging — fail = block release. Catches what cycle-23 nearly missed.

6. **UI integration** — new Navigator section `VALIDATION SUITES` (peer to STRATEGIES, HISTORY, CLUSTERS). Click suite → run dialog with baseline picker → live SSE progress → run-detail view with per-prompt score-delta heatmap vs baseline. Run history with score-trajectory sparkline per suite.

**Architecture (concrete):**

```
prompts/validation-suites/*.md     → ValidationSuite (hot-reloaded markdown)
backend/app/services/
  validation_service.py            → ValidationRun lifecycle (snapshot → batch → assert → diff)
backend/app/routers/validation.py  → REST surface
backend/app/models.py
  ValidationRun                    → id, suite_name, commit_sha, started_at, completed_at,
                                      pre_snapshot, post_snapshot, prompt_results JSON,
                                      aggregate JSON, assertions_status JSON,
                                      baseline_run_id FK, delta_vs_baseline JSON,
                                      status (running|passed|failed|partial)
synthesis_validate                 → MCP tool (extends existing 14 → 15 tools)
```

**Cross-pollination opportunities:**
- Successful validation prompts (≥8.0 across 3 consecutive runs) graduate to seed material as "proven prompt" labels feeding seed-agent few-shot context.
- Failed validation prompts surface as candidates for the next audit deep-dive.
- Validation runs feed strategy intelligence — `resolve_strategy_intelligence()` can downweight strategies that consistently underperform.

**Minimum viable bite (ship-first):**
1. `prompts/validation-suites/audit-class-regression.md` (move cycle 19-22 prompts here)
2. `validation_service.py` + `ValidationRun` model + Alembic migration
3. `routers/validation.py` (POST /run, GET /runs, GET /runs/{id})
4. `synthesis_validate` MCP tool
5. CLI shim — `scripts/validate_taxonomy_emergence.py` becomes a thin wrapper that calls `POST /api/validation/runs`

**Defer to follow-up tier:** UI navigator panel, baseline-diff view, regression alarm in health endpoint, CI hook in `release.sh`, prompt-graduation cross-pollination.

**Prerequisite:** none (uses existing `batch_pipeline.run_single_prompt` primitive). **Estimated scope:** ~600 LOC backend + new router + new model + 1 MCP tool. **Spec target:** `docs/specs/validation-tier-2026-04-28.md` (TBD).

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

### PostgreSQL migration
**Status:** Exploring
**Context:** SQLite's single-writer limitation causes `database is locked` errors when the MCP server pipeline (optimization write) and backend warm path (taxonomy mutations) write concurrently. WAL mode + busy_timeout=30s mitigates but doesn't eliminate the issue. At scale (concurrent users, parallel optimizations), SQLite becomes a bottleneck.

**Scope:** Replace `aiosqlite` with `asyncpg` + PostgreSQL. Requires: Alembic migration infrastructure (already present), connection pooling config, Docker Compose for local dev, production deployment update, test fixture changes.

**Trigger:** When `database is locked` errors become user-facing despite busy_timeout, or when concurrent multi-user access is needed.

**Files:** `database.py` (engine), `config.py` (DATABASE_URL), `main.py`/`mcp_server.py` (PRAGMA removal), `docker-compose.yml` (new), all test fixtures.

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
