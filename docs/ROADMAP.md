# Project Synthesis — Roadmap

Living document tracking planned improvements. Items are prioritized but not scheduled. Each entry links to the relevant spec or ADR when available.

**Snapshot:** v0.4.8-dev (sub-domain dissolution hardening shipped 2026-04-27; audit `docs/audits/sub-domain-regression-2026-04-27.md` R1-R8 all closed). Last release: v0.4.7 (2026-04-26).

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
**Status:** Planned
**Context:** v0.4.9 shipped F1-F5 audit-prompt scoring hardening. Cycle-23 (5 fresh prompts) was inconclusive — 2 hit Opus 4.7 infrastructure timeouts (>600s urlopen budget), 3 averaged 7.35 (vs v0.4.8 baseline 7.36). Follow-up: replay all 20 cycle-19→22 audit prompts on the new v4 scoring formula. Expected: mean rises from 7.36 → ~7.85 per the audit doc's projection. Either confirms F1-F5 effectiveness on a representative corpus or surfaces a new bottleneck (LLM scorer, conciseness gate, etc.). Also: extend the validate_taxonomy_emergence.py `_post` timeout from 600s → 900s OR add an Opus-4.7 → Sonnet-4.6 downshift for prompts that exceed the budget, so future cycles don't lose data points to infrastructure.

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

## Completed (recent)

### v0.4.8-dev — 2026-04-27

Sub-domain dissolution hardening — closes all 8 audit recommendations from
`docs/audits/sub-domain-regression-2026-04-27.md`. Multi-cycle TDD via
RED→GREEN→REFACTOR→VALIDATION subagent dispatches per recommendation,
gated by independent spec verification, integration validation cycles
(`cycle-12`/`cycle-13`/`cycle-14`), and a comprehensive PR-wide
systematic check. 46 new tests + 8 adapted, full backend suite at 3153
passed / 1 skipped.

- **R1 — Bayesian shrinkage on consistency** — replace point-estimate
  consistency in `_reevaluate_sub_domains()` with a Beta-Binomial
  posterior using prior strength `SUB_DOMAIN_DISSOLUTION_PRIOR_STRENGTH=10`
  centered at `SUB_DOMAIN_DISSOLUTION_PRIOR_CENTER=0.40`. Prevents
  small-N noise (one off-topic member at N=5) from triggering
  dissolution. Both `sub_domain_reevaluated` and `sub_domain_dissolved`
  events now carry `shrunk_consistency_pct` + `prior_strength` keys
  alongside the legacy `consistency_pct`. `TestSubDomainBayesianShrinkage`
  (4 tests). Spec: `docs/specs/sub-domain-dissolution-hardening-2026-04-27.md` §R1.
- **R2 — 24h dissolution grace period** — `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS`
  bumped from 6 to 24. Both observed dissolutions in the audit incident
  fired at 6h 0m and 6h 8m post-creation — exactly at the gate.
  24 hours gives one full daily cycle of bootstrap volatility. Two
  tests in `TestSubDomainGracePeriod`.
- **R3 — Empty-snapshot guardrail** — when a sub-domain's
  `cluster_metadata.generated_qualifiers` is empty, the matcher would
  fall through to v0.4.6 exact-equality behavior (the bug class v0.4.7
  fixed). Now `_reevaluate_sub_domains()` skips dissolution and emits
  `sub_domain_reevaluation_skipped` with `reason=empty_vocab_snapshot`.
  Two tests in `TestSubDomainEmptySnapshotSkip`.
- **R4 — Per-opt matcher extracted to shared primitive** — the inline
  three-source matching cascade in `_reevaluate_sub_domains()` is now a
  pure function `match_opt_to_sub_domain_vocab() -> SubDomainMatchResult`
  in `services/taxonomy/sub_domain_readiness.py`. Engine consumes the
  primitive. Same predicate available to future tools and the rebuild
  endpoint. 7 unit tests + 1 byte-equivalence integration test.
- **R5 — Forensic dissolution telemetry** — both reevaluated and
  dissolved events now carry `matching_members` (int) and up to 3
  `sample_match_failures` entries with `cluster_id` (preserved
  unsanitized) + `domain_raw`/`intent_label` (truncated to 80 chars) +
  `reason` (from `SubDomainMatchResult`). Closes the audit's "30
  minutes of forensic work that should have been one log line" gap.
  Two new constants `SUB_DOMAIN_FAILURE_SAMPLES=3`,
  `SUB_DOMAIN_FAILURE_FIELD_TRUNCATE=80`. `TestSubDomainForensicTelemetry`
  (5 tests).
- **R6 — Operator rebuild endpoint** —
  `POST /api/domains/{domain_id}/rebuild-sub-domains` (10/min) lets
  operators force discovery on a single domain with optional
  `min_consistency` override (Pydantic `ge=0.25` floor + runtime
  defense-in-depth) and `dry_run` semantics. Idempotent. Single
  `db.begin_nested()` SAVEPOINT for partial-failure rollback. Always
  emits `sub_domain_rebuild_invoked` telemetry; publishes
  `taxonomy_changed` only when sub-domains actually create AND
  non-dry-run. 11 service tests + 6 router tests. Spec:
  `docs/specs/sub-domain-dissolution-hardening-r4-r6.md` §R6.
- **R7 — Vocab regeneration overlap telemetry** —
  `vocab_generated_enriched` event gains `previous_groups`,
  `new_groups`, `overlap_pct` (Jaccard %). WARNING log fires when
  `overlap_pct < 50%` on a non-bootstrap regen — the audit's incident
  saw vocab swap from `{metrics, tracing, pattern-instrumentation}` to
  `{concurrency, observability, embeddings, security}` (zero overlap)
  one minute after the second sub-domain dissolved; this telemetry
  surfaces that correlation immediately. 5 tests in
  `TestVocabRegenOverlap`.
- **R8 — Threshold-collision invariant** — `_validate_threshold_invariants()`
  callable in `_constants.py` invoked at module-import time; fails fast
  if `SUB_DOMAIN_QUALIFIER_CONSISTENCY_LOW <= SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR`.
  Function-call form (rather than literal assert) lets tests exercise
  the invariant logic without `importlib.reload` quirks.
  `TestThresholdCollisionInvariant` (3 tests).

**Audit:** `docs/audits/sub-domain-regression-2026-04-27.md` (Resolution
status table — all 8 SHIPPED). **Specs:** three docs under
`docs/specs/sub-domain-dissolution-hardening-{2026-04-27,r4-r6,r7-r8}.md`,
each with companion `…-plan.md`. **Validation:** comprehensive PR-wide
subagent check confirmed code, tests, CHANGELOG, spec status, and live
behavior across all R1-R8.

### v0.4.7 — 2026-04-26

Shipped: 5 root-cause MCP routing fixes (debounce + initialize suppression + recovery trust-fresh-file), TF-IDF cascade source-3 unblocked + organic feedback loop into Haiku vocab regeneration, B5/B5+ writing-about-code task-type lock + codebase trim, C1-C5 score calibration sweep, T1.x learning loops (Bayesian shrinkage, MMR few-shot, A4 confidence gate, T1.3-lite pattern usefulness counters), `heuristic_baseline_scores` deterministic baseline (C4) + idempotent migration `d3f5a8c91024`, frontend halo + observatory SSE refresh fixes.

- **MCP routing thrash root-cause sweep** — Claude Code per-tool-call SSE cycling produced visible status-flicker every 30–110s + ~994 cross-process publishes per cycle. Five fixes ship together: (1) `routing.disconnect_averted` log demote INFO → DEBUG, (2) disconnect broadcast debounced via `DISCONNECT_DEBOUNCE_SECONDS=3.0` and `loop.create_task(_deferred_disconnect_broadcast())` — re-initialize within window cancels the pending task, (3) initialize broadcast suppression via `_pre_disconnect_sampling` snapshot — halves per-tool-call publish volume when capability unchanged, (4) recovery trust-fresh-file in `_recover_state()` when both `is_capability_fresh AND not detect_disconnect` — eliminates ~60s blackhole window when FastAPI backend reloads while MCP server is alive, (5) operational uvicorn `--reload-dir app` already correctly scoped. 178 routing+sampling+migration tests pass; 3 new `TestDisconnectDebounce` regression tests + `test_capability_fresh_but_disconnected` recovery negative case. (`backend/app/services/routing.py`)
- **TF-IDF cascade source-3 unblocked + organic feedback loop** — qualifier cascade's third source reported `0` hits across every domain in live readiness telemetry for ~5 days. Two stacked bugs: (1) `_extract_domain_keywords` queried `Optimization.cluster_id == cluster.id` but domain nodes never own opts directly (opts live in child clusters), so refresh persisted `signal_keywords=[]`; (2) raw TF-IDF mean scores topped at 0.167 but the cascade admit gate requires `weight >= 0.5`. Fix shape: dual-mode query (domain nodes aggregate across descendants; regular clusters keep direct-id query) + min-max normalization. Vocab regeneration now receives `domain_signal_keywords` + `existing_vocab_groups` so Haiku absorbs latent themes the cascade is recording exclusively via source 3 — closes the organic feedback loop with no manual intervention. 3 regression tests in `test_domain_discovery.py`. (`backend/app/services/taxonomy/engine.py` + `taxonomy/labeling.py`)
- **B5/B5+ writing-about-code path** — three regressions stacked when a writing/creative prompt has technical anchors in its first sentence: B2 first-sentence rescue catches them via inline backticks → `code_aware`, LLM analyzer flips `task_type` writing→coding, codebase-context layer delivers full 80K curated retrieval, optimizer hallucinates against related-but-wrong code. Fix shape: (1) **B5+ task-type lock** in `pipeline_phases.resolve_post_analyze_state` — prefer the writing lead verb when heuristic also says writing and LLM says coding; `write` requires a prose-output cue. (2) **B5 full-prompt rescue** in `context_enrichment.enrich()` — when B2 missed but task is writing/creative AND body has tech content, scan full prompt with `has_technical_nouns` and upgrade to `code_aware` (sets `full_prompt_technical_rescue=True`). (3) **B5+ codebase trim** — `_writing_about_code` caps codebase context at `WRITING_CODE_CONTEXT_CAP_CHARS=15000`. End-to-end live: B2 path scored 7.39 (was 6.61), B5 path 7.33 with `concision_preserved=True`. (`context_enrichment.py` + `pipeline_phases.py` + `prompts/optimize.md`)
- **C1-C5 score calibration sweep** — (C1/C5) z-score asymmetric cap with floor only at `-2.0`, ceiling uncapped — preserves legitimate above-average upside that the prior symmetric clip compressed; (C2) length budget guidance in analysis_summary based on `_orig_conc` and `_orig_len`; (C3) technical-prompt conciseness blend bumped to 0.35 so repeated domain vocabulary doesn't get TTR-penalized as verbose; (C4) `Optimization.heuristic_baseline_scores` JSON column stores deterministic `HeuristicScorer.score_prompt(raw_prompt)` snapshot distinct from LLM-blended `original_scores`; `improvement_score` derivation uses `heuristic_lift`; (C6) selective inline-backtick handling — `_looks_like_code_reference` unwraps when contents contain `/`, `_`, or source extension; pruned ambiguous brand names from `_TECHNICAL_NOUNS` (github/lambda/react/vue/kafka). (`score_blender.py` + `pipeline_phases.py` + `models.py` + `task_type_classifier.py`)
- **T1.x learning loops** — (T1.1) Bayesian shrinkage on phase-weight learning with `SCORE_ADAPTATION_PRIOR_KAPPA=8.0` + `SCORE_ADAPTATION_MIN_SAMPLES=2` replaces prior min-10 hard gate; (T1.6) MMR diversity on few-shot retrieval with `FEW_SHOT_MMR_LAMBDA=0.6`; (T1.3-lite) `OptimizationPattern.useful_count`/`unused_count` counters; (T1.7) A4 confidence gate tuned to `0.40` + `0.10` margin so Haiku fallback fires more often (~15-20%) for genuinely ambiguous prompts. (`fusion.py` + `pattern_injection.py` + `task_type_classifier.py`)
- **Migration `d3f5a8c91024` idempotency + frontend fixes** — wrapped each `add_column` in `inspector.get_columns()` guards (matching `c2d4e6f8a0b2` pattern). Topology halo + readiness ring per-frame sync inside formation animation callback. Observatory `refreshPatternDensity()` + `loadTimelineEvents()` fire from `taxonomy_changed`/`domain_created`/SSE-reconnect handlers. (`alembic/d3f5a8c91024.py` + `SemanticTopology.svelte` + `+page.svelte`)

### v0.4.6 — 2026-04-25

Self-update hardening — pre-flight endpoint + drain lock + auto-stash + per-step progress against three P0 risks (strategy edits silently lost, in-flight optimization race, local-commits-ahead-of-origin orphan). New `customization_tracker.py` records strategy edits; `GET /api/update/preflight` returns `PreflightStatus` with dirty-source classification (`user_api`/`manual_edit`/`untracked`), commits-ahead, in-flight count, detached-HEAD, target-tag presence; `UpdateInflightTracker` coordinates pipeline `begin/end(trace_id)` with `apply_update`'s drain wait (60s budget); auto-stash + pop on `prompts/`; per-step SSE events (`update_step` at preflight/drain/fetch_tags/stash/checkout/deps/migrate/pop_stash/restart/validate). Frontend rebuild: `UpdateBadge` dialog with preflight panel, dirty-file paths, in-flight counter, `Update & Restart (force)` warning variant, completion view rendering `validationChecks` + `stashPopConflicts`, retry button after timeout. 17 customization-tracker + 17 update_service tests.

### v0.4.5 — 2026-04-25

Pattern-injection provenance now writes post-commit (provenance was silently rolling back inside `begin_nested()` SAVEPOINT due to FK-failure on uncommitted parent — `auto_inject_patterns()` gains `record_provenance` flag; internal/sampling pass `False` and `record_injection_provenance()` is invoked after `db.commit()`). Enrichment profile no longer demotes async/concurrency code prompts (async/concurrency vocab + interior `.`/`-`/`/` split + snake_case/PascalCase identifier syntax detection). GitHub OAuth no longer surfaces upstream JSON failures as misleading CORS errors. Optimizer-thinking interrogative-voice no longer leaks into deliverables. Post-LLM domain reconciliation (qualifier syntax flows into `domain_raw` via `_normalize_llm_domain()`; runs BEFORE resolver). `find_best_qualifier()` tiebreaker prefers in-text qualifier name on hit-count ties. Sub-domain label canonicalization single-source-of-truth via `normalize_sub_domain_label(raw, max_len=30)`. Task-type structural-evidence rescue (creative/writing → coding when first sentence has snake_case/PascalCase/technical-noun signals; scope-guarded against analysis/data). `enrichment` summary on `/api/history` rows. Source-breakdown chip strip on `SubDomainEmergenceList`. Multi-sibling sub-domain test coverage. Observability separation between ActivityPanel (topology terminal) and DomainLifecycleTimeline (Observatory) — shared `activity-filters.ts` + `activity-summary.ts` modules.

### v0.4.4 — 2026-04-25

Shipped: ADR-007 Live Pattern Intelligence Tier 1, Taxonomy Observatory Tier 1, `since`/`until` activity-history range variant, `/api/taxonomy/pattern-density` aggregator, plus the nine audit-follow-up + roadmap-debt items below. Two PRs (#50 ContextPanel + #51 Observatory) merged to `main` as feature work; the audit follow-up landed as standalone RED→GREEN-tested commits.

- **ADR-007 Tier 1 — Live Pattern Intelligence (`ContextPanel.svelte`)** — replaces the legacy `PatternSuggestion.svelte` banner with a persistent sidebar mounted by `EditorGroups`. Cluster identity row + meta-patterns checkboxes + neon-purple-bordered GLOBAL section for cross-cluster patterns. APPLY commits multi-pattern selection to `forgeStore.appliedPatternIds`. Mount-gated to the prompt tab, hidden during synthesis, full a11y. Backend `POST /api/clusters/match` response gains additive `match_level` + `cross_cluster_patterns` keys (no schema migration). Spec: [docs/superpowers/specs/2026-04-24-live-pattern-intelligence-tier-1-design.md](superpowers/specs/2026-04-24-live-pattern-intelligence-tier-1-design.md). PR #50.
- **Taxonomy Observatory Tier 1 — three-panel observability tab** — pinned `OBSERVATORY` workbench tab mounting `TaxonomyObservatory.svelte`. Three panels: `DomainLifecycleTimeline` (reverse-chrono SSE-live + JSONL backfill), `DomainReadinessAggregate` (composes existing meter+emergence per domain), `PatternDensityHeatmap` (read-only data grid with hover tooltip). Period selector (`24h | 7d | 30d`) drives Timeline + Heatmap via `observatoryStore`. Backend additions: `since`/`until` range variant on `GET /api/clusters/activity/history`, new `GET /api/taxonomy/pattern-density` aggregator, `taxonomy_insights.py` service + router + Pydantic schemas. Spec: [docs/superpowers/specs/2026-04-24-taxonomy-observatory-design.md](superpowers/specs/2026-04-24-taxonomy-observatory-design.md). PR #51.
- **Post-merge spec compliance audit (PRs #50 + #51)** — five spec gaps caught during a full re-read: (1) `getClusterActivityHistory` API client missing `since`/`until` range params, (2) Timeline period chips were no-op for the Timeline panel, (3) `DomainReadinessAggregate` cards missing 6 px chromatic dot, (4) `PatternDensityHeatmap` rows missing hover affordance + tooltip, (5) Activity-history within-day events emitted oldest-first in both single-day and range modes. Each fix lands with a regression test source-locked against the contract.

The remaining v0.4.4 work shipped to `main` after the v0.4.3 cut as standalone RED→GREEN-tested commits:

- **Full doc audit against v0.4.4-dev state** — every document under `docs/` (excluding `CHANGELOG.md`) cross-referenced against the current codebase. ROADMAP, routing-architecture, embedding-architecture, sub-domain-discovery, context-injection-use-case-matrix, sampling-tier-data-processing, hybrid-taxonomy-plan, SUPPORT, the three heuristic-analyzer docs, enrichment-consolidation-action-items, the three context-depth-audit iterations, ADR-001, ADR-007 — all updated with current-state facts + version markers + (for historical records) status banners. 30 files touched, 320 insertions / 152 deletions.
- **#8 `prepare.py` preferences snapshot parity with `optimize.py`** — hoist `prefs_snapshot = prefs.load()` once and thread into every `prefs.get(key, snapshot)` call. Eliminates the redundant disk I/O + legacy-key-migration pass per `synthesis_prepare_optimization` invocation. Regression-guard test in `test_mcp_tools.py` asserts `load()` is called exactly once per call and every `prefs.get` passes the snapshot as second arg. (Commit `48c82a9d`.)
- **#11 DomainResolver confidence-aware cache TTL** — replaced the unbounded `dict[str, str]` cache with a confidence-tagged TTL cache (`_CacheEntry(resolved, confidence, expires_at)`). Low-confidence "general" collapses get a 60 s TTL so they self-heal when the warm path promotes the organic label; known-label / high-confidence preserved resolutions get 3600 s. A subsequent call with confidence ≥ cached + 0.1 (`CACHE_CONFIDENCE_UPGRADE_DELTA`) evicts the stale entry — prevents A4 Haiku-retry starvation. All 12 pre-existing tests pass unchanged; 4 new cases cover the TTL + confidence-upgrade paths. (Commit `9f886d1e`.)
- **#10 Conciseness heuristic calibration for technical prompts** — TTR penalized technical specs that repeat domain vocabulary ("pipeline", "schema", "service") despite those repetitions reflecting density, not verbosity. Observed on matched-TTR pairs: tech prompt 6.51 vs prose prompt 6.51 (zero differentiation). Fix: when `_count_technical_nouns(prompt) >= 3` (word-boundary match against `_TECHNICAL_NOUNS`), multiply raw TTR by `TECHNICAL_TTR_MULTIPLIER=1.15` before band mapping, clamped at 1.0. Three new tests lock behavior; all 30 pre-existing tests pass. (Commit `6b5a615b`.)
- **#9 E2 enrichment profile effectiveness on `/api/health`** — `phase-e-observability.md` has listed E2 as "still to be designed" since v0.3.30. The persistence layer already populated `context_sources["enrichment_meta"]["enrichment_profile"]` on every completed row; nothing consumed it. New `OptimizationService.get_enrichment_profile_effectiveness(limit=200)` aggregates Python-side (no SQL `GROUP BY` — nested JSON key, dialect-portable) and surfaces via new `HealthResponse.enrichment_effectiveness` field. Per-profile `{count, avg_overall_score, avg_improvement_score}`. NULL-improvement rows contribute to count + avg_overall_score but are skipped in the improvement average. 3 service tests + 2 endpoint tests. (Commit `e7f9e468`.)
- **#7 Code-block / markdown-table strip before first-sentence extraction** — code fences + pipe-delimited table rows at the top of a prompt polluted the `first_sentence` boundary used by the 2x positional-boost keyword scoring. New `extract_first_sentence()` helper in `task_type_classifier.py` pre-strips triple-backtick fences, inline-backtick spans, and markdown table rows before splitting on `.?!`. Applied at both call sites (heuristic_analyzer + context_enrichment). 4 new tests assert unit-granular boundary behavior — code-fence / table / inline-backtick stripping + the no-code baseline. (Commit `fff63665`.)
- **#12 A1+A2 follow-up: "design a system prompt" classifies as system** — audit of commit `14511cd3` surfaced one drift case: "Design a system prompt for evaluating design systems" classified as `coding` because `design a system` (coding 1.3) left-substring-matched without a longer compound to break the tie. Fix: three compounds at weight 1.5 on the `system` task_type (`design a system prompt`, `build a system prompt`, `create a system prompt`). 7 new tests pin mixed compound + single-word signal interactions (`system prompt` → system, plain `design a system` → coding retained, meta-prompt + debug intent → system, diagnose + recommend → analysis, design-a-pipeline → coding). (Commit `124d5cf7`.)
- **#1 Sampling fallback classifier alignment with DomainSignalLoader** — `build_analysis_from_text()` in `sampling/primitives.py` (the analyze-phase last-resort when IDE sampling response can't be parsed) maintained its own hardcoded `type_keywords` + `domain_keywords` dicts since v0.3.32, drifting silently from the organic warm-path pipeline (`_TASK_TYPE_SIGNALS` + `DomainSignalLoader`). Replaced with `classify_task_type(combined, extract_first_sentence(combined), get_task_type_signals())` + `DomainSignalLoader.score(words)` + `.classify(scored)`. Graceful `None`-loader fallback to `"general"` for startup / test contexts. 6 new tests pin the delegation + end-to-end dynamic-signal flow-through. (Commit `7f41f870`.)
- **#3 `signal_adjuster.py` — TaskTypeTelemetry consumer (C2)** — since v0.4.2 the A4 Haiku fallback has persisted every ambiguous-prompt classification to `TaskTypeTelemetry`; nothing consumed those rows. New active-learning oracle reads the last 7 days (`SIGNAL_ADJUSTER_LOOKBACK_DAYS`), tokenizes prompts, tallies `(token, task_type)` pairs, and merges tokens that cross `SIGNAL_ADJUSTER_MIN_FREQUENCY=3` hits into `_TASK_TYPE_SIGNALS[task_type]` at `SIGNAL_ADJUSTER_WEIGHT=0.5`. Only ADDS novel tokens — never overwrites existing weights. Emits one `signal_adjusted` taxonomy event per merged token. Wired as Phase 4.76 in warm path (runs after Phase 4.75 TF-IDF extraction so active-learning additions layer on top). 8 new tests + non-fatal-on-missing-table + non-fatal-on-missing-logger degradation. (Commit `b8159a70`.)
- **#6 Non-developer vertical — seed agents + domain keyword migration (ADR-006 content-first playbook step 1)** — two new seed agents (`marketing-copy.md`, `business-writing.md`) + Alembic migration `c2d4e6f8a0b2` seeding three new domain nodes with brand-aligned OKLab colors + keyword signals: `marketing` (#ff7a45, 16 keywords), `business` (#3fb0a9, 18 keywords), `content` (#f5a623, 17 keywords). Each seed carries `cluster_metadata.vertical="non-developer"` for ADR-006 traceability + safe downgrade. Idempotent via `existing_labels` guard. 6 new tests cover agent loading, loader integration, migration idempotency, and the vertical marker. Zero engine code changes — pure content additions proving ADR-006's universal-engine claim. (Commit `a217c6cf`.)

### v0.4.3 — 2026-04-24
- **Bulk delete REST + History UX** — `POST /api/optimizations/delete` (1-100 ids, 10/min rate-limited, `DeleteOptimizationsResponse` envelope) + single-row `DELETE /api/optimizations/{id}` both route through `OptimizationService.delete_optimizations()`. Frontend: hover × with 5 s `UndoToast` grace window (commit deferred until timer expires), opt-in multi-select mode with `Select`/`Cancel`/`Delete N` toolbar, `DestructiveConfirmModal` with type-to-confirm (`DELETE` literal), keyboard shortcuts (Ctrl/Cmd+Click auto-seed, Shift+Click range, Ctrl+A, Esc, Delete/Backspace, arrow nav), bulk-to-single graceful fallback.
- **Reusable destructive-action primitives** — `toastsStore` singleton with pre-commit grace hook (`commit?: () => Promise<void>`), `UndoToast` (`scaleX` transform for compositor-only RAF repaints, pause-on-hover, `aria-live="polite"`), `DestructiveConfirmModal` (glass panel, `role="dialog"`, case-sensitive literal gate, focus return on cancel). All respect the brand zero-effects directive + `prefers-reduced-motion`.
- **Frontend brand-guidelines strict audit — zero violations** — removed stray `text-shadow` rules, consolidated remaining `border-radius: 4px` surfaces to `0`, replaced `box-shadow` with 1px inset contour, purged `--glow-*` refs from legacy comments.
- **Frontend consumes `optimization_deleted` SSE** — event has shipped since v0.4.2 but had no UI handler; `+page.svelte` bridges to `CustomEvent('optimization-deleted')`, `HistoryPanel` removes matching row surgically (no full re-fetch), 2 s fallback timeout covers SSE reconnect gaps.
- **Delete endpoints use `BASE_URL`** — `frontend/src/lib/api/optimizations.ts` routes through shared `apiFetch` so dev→prod port drift doesn't silently 404 the delete surface.
- **`task_type_signal_extractor` graceful degradation** — INSERT into `task_type_telemetry` wrapped in `try/except OperationalError`; unmigrated DBs get warn-log once/cycle instead of crashing the warm path (was leaving `member_count` stale on domain nodes after deletes).
- **Test isolation helpers** — public `reset_rate_limit_storage()` + shared `drain_events_nonblocking()` promoted from per-file spelunking to `conftest.py` / `dependencies/rate_limit.py`.

### v0.4.2 — 2026-04-23
- **MCP sampling architecture unification + Hybrid Phase Routing** — `MCPSamplingProvider` now a first-class `LLMProvider`; 1,700-line redundant sampling pipeline collapsed to re-export layer over the primary orchestrator. Hybrid Execution Routing: fast phases (analyze, score, suggest) stay on internal provider; optimize routes through the IDE LLM. MCP transport errors map to `ProviderError` so Tenacity retries apply. `StreamableHTTPServerTransport` patched to extract TS SDK `sessionId` from query params.
- **`TaskTypeTelemetry` model + migration `2f3b0645e24d`** — records heuristic vs LLM classification events (`raw_prompt`, `task_type`, `domain`, `source`) for drift analysis + A4 tuning.
- **Inspector analyzer telemetry rendering (UI2)** — ENRICHMENT panel surfaces signal-source tag (bootstrap/dynamic), TASK-TYPE SCORES distribution, CONTEXT INJECTION counts. `build_pipeline_result()` propagates `inputs.repo_full_name` into `PipelineResult.repo_full_name`.
- **`enrichment_meta.injection_stats` uniform emission (UI1) + AA1 auto-bind** — every tier emits `{patterns_injected, injection_clusters, has_explicit_patterns}`. `project_service.resolve_effective_repo()` resolves repo via explicit → session-cookie → most-recently-linked cascade so curl / session-less API callers bind to the live `LinkedRepo`'s project instead of falling through to Legacy.
- **Explicit DOMAIN SIGNALS + RETRIEVAL headings + CLI-family classifier coverage (A8)** — `cli`/`daemon`/`binary` added to `_TECHNICAL_NOUNS` (A2) and coding-signal keywords at moderate weights.
- **`DELETE /api/optimizations/{id}` REST + `synthesis_delete` MCP tool (audit #3 + #4)** — thin wrapper over long-existing `delete_optimizations()` primitive; unknown id now 404s.
- **`POST /api/taxonomy/reset` admin recovery (I-0)** — force-prunes archived zero-member clusters + delegates to `run_warm_path` synchronously; idempotent.
- **`taxonomy_changed` SSE publish on bulk delete (I-0)** — cross-process dirty-set bridge fires warm Phase 0 immediately instead of waiting for 30 s debounce.
- **Inspector per-layer enrichment skip reason (I-9)** — renders right-aligned reason tags ("skipped — cold start profile", "deferred to pipeline").
- **Tree integrity repair SSE events (I-8)** — `tree_integrity_repair` emits per repair with `{violation_type, action, label}`.
- **`OptimizationService.delete_optimizations(ids, *, reason)` bulk primitive** — relies on DB `ondelete="CASCADE"` (migration `a2f6d8e31b09`), emits per-row `optimization_deleted` events + aggregated `taxonomy_changed`. Migration `b3a7e9f4c2d1` dedupes orphan unnamed FKs.
- **Warm Phase 0 clears stale `learned_phase_weights` on empty clusters** — prevents "phantom learning" if cluster id is reused within 24h archival window.
- **Provider threaded through enrichment + tool handlers** — `HeuristicAnalyzer.analyze()` + `ContextEnrichmentService.enrich()` accept `provider` kwarg at every call site; A4 Haiku fallback resolves without global lookup.
- **Negation-aware weakness detection + signal-source accuracy** — `_is_negated()` + `_compute_structural_density()`; `_TASK_TYPE_EXTRACTED` set distinguishes `bootstrap` from `dynamic` honestly. Legacy `static` value accepted as read-compat synonym for one release cycle.
- **Analyze phase effort clamp to `high` ceiling (A3)** — `ANALYZE_EFFORT_CEILING='high'` applied at all three analyze call sites; `max`/`xhigh` downshift to `high`. Does NOT apply to optimize/score. Expected drop: 200+s → 30–60s.
- **`auto → strategy` routed by `intent_label` (A2)** — new step 5b in `resolve_effective_strategy` inspects `intent_label` for chain-of-thought (audit/debug/diagnose), structured-output (extract/classify/list), role-playing (story/poem/narrative) keywords. Fires when current strategy equals the task-type default too (not only literal `"auto"`).
- **`enrichment_meta.domain_signals` shape — `{resolved, score, runner_up}`** — winner named explicitly; `reconcile_domain_signals()` rebuilds after pipeline finalizes domain; runner_up emits only when `best_runner <= top_score AND > 0`.
- **`/api/health` surfaces `taxonomy_index_size` + `avg_vocab_quality`** — Plan I-1 wired boot logging but health fields were stuck at `None`; now pulled off `app.state.taxonomy_engine`.
- **`Q_system` / `Q_health` return `None` with fewer than 2 active clusters (A5)** — single-node taxonomies no longer report perfect scores; Q-gates treat transitions as growth/destruction/no-progress.
- **Routing: REST callers excluded from sampling, internal beats auto-sampling** — `_can_sample()` narrowed to `caller == "mcp"`; auto path tries tier 3 internal before tier 4 auto-sampling.
- **`_write_optimistic_session` preserves `sampling_capable`** — no longer forces `True` on session-less reconnects from plain Claude Code.
- **Cross-process `taxonomy_changed` bridged into engine dirty_set** — `_apply_cross_process_dirty_marks(engine, event_data)` runs before `_warm_path_pending.set()` so MCP/CLI deletes reconcile immediately.
- **Classifier B1/B2/B6 fixes** — SQLAlchemy/FastAPI/Django nouns added to coding signals + `_TECHNICAL_NOUNS`; `has_technical_nouns(first_sentence)` rescues any task_type to `code_aware` when repo linked; `_STATIC_SINGLE_SIGNALS` preserves single-word defaults through dynamic merges.
- **Pattern injection — unscoped clusters visible inside project filter (A10)** — `embedding_index.search()` treats `project_ids[label]=None` as "unreconciled, visible within any scope" (brand-new pre-Phase-0 clusters).
- **Pattern injection provenance — `begin_nested()` SAVEPOINT** — replaces manual expunge; prevents `PendingRollbackError` from cascading into subsequent pipeline phases on FK IntegrityError.

### v0.4.1 — 2026-04-20
- **Sidebar brand audit finale — Navigator 2,692 → 182 lines** — 8-panel extraction (`StrategiesPanel`, `HistoryPanel`, `GitHubPanel`, `SettingsPanel`, `ClusterRow`, `DomainGroup`, `StateFilterTabs`, `TemplatesSection`). `CollapsibleSectionHeader` gains Snippet-based whole-bar/split modes. `ActivityBar` sliding indicator, Inspector phase-dot.
- **Inspector.svelte split — 3 sections extracted** — `ClusterPatternsSection` (103 l), `ClusterTemplatesSection` (70 l, disambiguated from Navigator's proven-templates section), `TaxonomyHealthPanel` (123 l). Inspector 1,404 → 1,165 l.
- **Backend Phase 3 refactor split (A-F)** — six module-boundary extractions: Phase 3A (`context_enrichment.py` 1,394 → 3 modules: `repo_relevance.py`, `divergence_detector.py`, `strategy_intelligence.py`), Phase 3B (`sampling_pipeline.py` 1,705 → 3 sub-modules: `sampling/primitives.py`, `sampling/persistence.py`, `sampling/analyze.py`), Phase 3C (`repo_index_service.py` 1,676 → `repo_index_outlines.py`, `repo_index_file_reader.py`, `repo_index_query.py`), Phase 3D (`pipeline.py` 1,146 → 610, 12 pure helpers in `pipeline_phases.py`), Phase 3E (`batch_pipeline.py` 1,077 → `batch_orchestrator.py` + `batch_persistence.py`), Phase 3F (`heuristic_analyzer.py` 929 → thin orchestrator + `task_type_classifier.py` + `domain_detector.py` + `weakness_detector.py`). All preserve public API via re-exports.
- **UI persistence through stores (code-quality sweep Phase 2)** — `githubStore.uiTab`, `stores/hints.svelte.ts`, `stores/topology-cache.svelte.ts` replace ad-hoc `$effect` + direct localStorage in `GitHubPanel`, `TopologyControls`, `SemanticTopology`. One-shot migration shims preserve user state.
- **`utils/keyboard.ts` + `utils/transitions.ts`** — pure `nextTablistValue()` + `handleTablistArrowKeys()` for tablist arrow-key nav; `navSlide`/`navFade` presets driven by inline 8-iteration Newton-Raphson bezier solver matching `--ease-spring` exactly (Svelte's built-in `cubicOut` drifted visibly).
- **PRAGMA event hook on every pool checkout** — `@event.listens_for(engine.sync_engine, "connect")` applies WAL + busy_timeout + synchronous + cache_size + foreign_keys to every SQLite pool connection. Replaces throwaway single-connection aiosqlite block. `pool_pre_ping=True` + `pool_recycle=3600` restored.
- **Recurring GC sweep — hourly expired-token + orphan-repo cleanup** — `run_recurring_gc()` + `_recurring_gc_task` scheduled in lifespan. Sweeps expired `GitHubToken` (24 h grace) + orphan `LinkedRepo` rows. Previously accumulated indefinitely between restarts.
- **Hotpath indices migration `cc9c44e78f78`** — seven single-column indices on `optimizations` + composite `ix_optimizations_project_created(project_id, created_at DESC)` + `ix_feedbacks_optimization_id`.
- **Soft-delete retirement documented** — v2 rebuild excised the `deleted_at` column set; archive-as-soft-delete via `cluster.state='archived'` covers legitimate undelete cases; hard-delete simpler for GDPR.

### v0.4.0 — 2026-04-19
- **ADR-005 Hybrid Taxonomy — projects as sibling roots (8-commit shipment)** — supersedes original "project as tree parent" data model. Projects at `parent_id IS NULL` alongside domain nodes; clusters parent to domains and carry `dominant_project_id` FK. S1 migration + B1 pipeline freezes `project_id` at request time via `resolve_project_id()` + B2-B5 `POST /api/projects/migrate` rate-limited + link/unlink `mode=keep|rehome` + B6 tree/stats SQL-scoped by `dominant_project_id` + B7-B8 pattern filtering + dual-gate global promotion (`GLOBAL_PATTERN_PROMOTION_MIN_CLUSTERS=5` + `MIN_PROJECTS=2`) + C1 warm/cold maintenance + F1-F5 frontend (projectStore, project selector, explicit `project_id` threading, transition toasts, per-project Inspector breakdown). Locked-decision record: `docs/hybrid-taxonomy-plan.md`. ADR-005 Amendment 2026-04-19 links out. Phase 3 HNSW stays trigger-gated.
- **Opus 4.7 provider feature surface** — `xhigh` effort level (Opus 4.7 only, `xhigh → high` downgrade with warning on other models), `display: "summarized"` adaptive thinking (was silent on Opus 4.7 which defaults to `omitted`), Task Budgets beta (`task_budget: int | None`, 20k min clamp, `task-budgets-2026-03-13` header), Compaction beta (`compaction: bool`, Opus 4.7/4.6 + Sonnet 4.6 only, `compact-2026-01-12` header). Combined betas comma-joined into single `extra_headers["anthropic-beta"]`. `ClaudeCLIProvider` accepts both as documented no-ops for ABC uniformity. `LLMProvider.supports_xhigh_effort(model)` static helper centralizes the gate. 11 new provider tests + 3 preferences tests.
- **`synthesis_health` `linked_repo` block** — returns `full_name`, `branch`, `language`, `index_status`, `index_phase`, `files_indexed`, `synthesis_ready` for the active linked repo. Single MCP health call now confirms codebase-context availability end-to-end.
- **Per-agent seed model override + per-dispatch JSONL trace** — seed agents default to Haiku; can opt into Sonnet/Opus via YAML frontmatter `model:`. `SeedAgent.model` added; `_resolve_agent_model()` maps frontmatter → `settings.MODEL_*`. Each dispatch emits `phase="seed_agent"` trace with `trace_id="seed:{batch_id}:{agent}"`, duration, tokens, resolved model.
- **Explore synthesis routed to Sonnet + JSONL trace** — `CodebaseExplorer._explore_inner` synthesizes 30-80K-token file payloads; long-context reading favors Sonnet. Cached per repo/branch/SHA in `RepoIndexMeta.explore_synthesis` so cost delta is negligible. Per-run `phase="explore_synthesis"` trace.
- **Phase 0 orphan-structural-node sweep** — warm-path reconciles empty domain / sub-domain nodes with 0 active-cluster children AND 0 sub-domain children AND 0 optimization references, gated on `ORPHAN_STRUCTURAL_GRACE_HOURS=24`. Fixes "ghost Legacy 1m 0 --" visibility bug where ADR-005 migration left empty `general` domain inflating `member_count=1` forever. 5 RED→GREEN tests.
- **Heuristic classifier — audit verbs + sentence boundary + compound keywords** — added `audit`/`diagnose`/`inspect` to analysis signals; first-sentence boundary uses `re.split(r"[.?!]", ...)` (was `.split(".")`); compound-phrase signals (`"write a prompt"` → system, `"audits the"` → analysis) outweigh single-word collisions.
- **B0 repo-relevance gate — project-anchored synthesis + path-enriched anchor** — anchor embedding prepends `Project: {repo_full_name}\n` + appends up to 500 indexed file paths as `Components:` block (stride-sampled at 100 for MiniLM 512-tok window). `REPO_RELEVANCE_FLOOR` 0.20 → 0.15; `REPO_DOMAIN_MIN_OVERLAP` removed. Reason codes collapse to `{above_floor, below_floor}`. Same-stack-different-project separation rides on project-name signal, not vocabulary overlap. `extract_domain_vocab()` retained for UI attribution only.
- **Background task GC race fix — strong-ref holder** — `_background_tasks: set[asyncio.Task]` + `_spawn_bg_task()` helper prevents weak-ref GC mid-await on `link_repo` / `reindex` (was silently killing `synthesis_status='running'` jobs).
- **Inspector "no codebase context" warning chip** — yellow chip renders when `activeResult.repo_full_name` is set AND `context_sources.codebase_context === false`. Recommends reindex + rerun.
- **Cluster navigator MBR column semantic suffix** — `Nd` (domains, project rows), `Nc` (clusters, domain rows), `Nm` (members, cluster rows). `member_count` is semantically overloaded across node types.
- **`DomainResolver` preserve high-confidence unknown labels + gate lowered 0.6 → 0.5** — resolver no longer collapses unknowns to `general` pre-emptively; warm-path domain discovery depends on organic labels reaching the engine.
- **Seed palette preservation on domain re-promotion** — `SEED_PALETTE` mirrors alembic `SEED_DOMAINS`; `engine._create_domain_node()` restores canonical color on re-promotion if not already in use. Empty seed domains still dissolve per ADR-006, but "Backend is purple" survives dissolution cycles.
- **A4 LLM classification wrapped in retry** — wrapped with `call_provider_with_retry` so transient rate-limit/overload errors retry before degrading to heuristic result.

### Templates entity + fork-on-promotion (v0.3.39)
Immutable `PromptTemplate` rows forked from mature clusters crossing fork thresholds. Source cluster stays `mature` and keeps learning. Warm Phase 0 reconciles `template_count` + auto-retires templates whose source degrades (avg_score < 6.0) or is archived. Templates router (`GET /api/templates`, `POST /api/clusters/{id}/fork-template`, `POST /api/templates/{id}/retire`, `POST /api/templates/{id}/use`). Partial-unique constraint on `(source_cluster_id, source_optimization_id) WHERE retired_at IS NULL`. Halo rendering on 3D topology (`template_count > 0` → 1px contour ring). Navigator PROVEN TEMPLATES group + Inspector collapsible section. `.claude/hooks/pre-pr-template-guard.sh` blocks residual `state='template'` literals. Deprecates the old `state='template'` enum path (410/400 on legacy routes).

### GitHub indexing pipeline with caching (v0.3.40)
Four coordinated caches cut GitHub API pressure and embedder cost: tree ETag conditional fetches, content-hash embedding dedup across branches/repos, file-content TTL+FIFO cache (keyed by blob SHA, branch-independent), curated retrieval invalidation on rebuild. Per-phase `index_phase` column (`pending → fetching_tree → embedding → synthesizing → ready|error`) with `index_phase_changed` SSE events. Frontend `connectionState` expanded to 7 states; phase-aware Navigator badge + pulse animation + error row. Preference key rename `enable_adaptation` → `enable_strategy_intelligence` (+ lazy migration). Shared file-exclusion filters (`file_filters.py`) between repo-index and codebase-explorer.

### Domain readiness telemetry + sparklines + topology overlay (v0.3.37 → v0.3.38 → v0.3.39)
`GET /api/domains/readiness` + `/api/domains/{id}/readiness` with 30s TTL cache; three-source cascade primitive shared with `_propose_sub_domains()` for zero-drift. `DomainStabilityMeter`, `SubDomainEmergenceList`, `DomainReadinessPanel` UI with per-row mute bells + master mute. Readiness snapshot writer (`readiness_history.py`) with JSONL daily rotation + 30-day retention. `GET /api/domains/{id}/readiness/history?window=24h|7d|30d` with hourly bucketing beyond 7d. `DomainReadinessSparkline` peer component with window selector (persisted to localStorage). Tier-crossing detector with 2-cycle hysteresis + per-domain cooldown publishes `domain_readiness_changed` SSE gated by `domain_readiness_notifications` preference (default on). Topology overlay: per-domain readiness ring with composite tier coloring (`composeReadinessTier()`), billboard orientation, LOD attenuation, cubic-bezier tier transitions, `prefersReducedMotion()` awareness. 1142+ frontend tests passing (brand-guard + behavioral).

### Opus 4.7 default + mypy strict cleanup (v0.3.37)
`MODEL_OPUS` default flipped to `claude-opus-4-7` (1M-token native context). Full mypy strict cleanup: 103 → 0 errors across 133 source files; `backend/app/models.py` refactored to SQLAlchemy 2.0 `Mapped[]` typed declarative columns.

### Enrichment engine consolidation + heuristic accuracy (v0.3.30)
Unified context enrichment with auto-selected profiles (`code_aware` / `knowledge_work` / `cold_start`), task-gated curated retrieval, strategy intelligence merge, workspace guidance collapse. Heuristic accuracy pipeline: compound keywords (A1), verb+noun disambiguation (A2), TF-IDF domain signal auto-enrichment (A3), confidence-gated Haiku fallback (A4). Prompt-context divergence detection (B1+B2), domain-relaxed fallback queries (C1), classification agreement tracking (E1). 2107 backend tests. Full spec: [`docs/enrichment-consolidation-action-items.md`](enrichment-consolidation-action-items.md).

### Hierarchical edge system (v0.3.30)
Curved edge bundling in 3D topology with depth-based attenuation shader, density-adaptive opacity, proximity suppression, focus-reveal on hover, domain-colored edges. 5-phase hierarchical edge declutter.

### Injection effectiveness + orphan recovery + project-node UX (v0.3.29)
Warm-path Phase 4 measures mean score lift for pattern-injected vs non-injected optimizations. Orphan recovery with exponential backoff. Project node dodecahedron geometry + rich Inspector mode.

### SSE health + incremental refresh + per-project scheduling (v0.3.28)
Real-time SSE latency tracking (p50/p95/p99), degradation detection, exponential backoff reconnection. Repo index incremental refresh via SHA comparison. Per-project scheduler budgets with proportional quotas.

### Full source context + import graph + curated retrieval (v0.3.27)
Curated retrieval delivers actual file source code (not outlines). Import-graph expansion, test file exclusion, cross-domain noise filter, performance signals, context diagnostic panel. Skip-and-continue budget packing, source-type soft caps.

### Alembic migration for domain nodes (v0.3.8-dev)
Idempotent migration `a1b2c3d4e5f6`: adds `cluster_metadata` column, `ix_prompt_cluster_state_label` index, `uq_prompt_cluster_domain_label` partial unique index, seeds 7 domain nodes with keyword metadata, re-parents existing clusters, backfills `Optimization.domain`. Async env.py commit for DML persistence.

### Unified domain taxonomy — ADR-004 (v0.3.8-dev)
Domains are `PromptCluster` nodes with `state="domain"`. Replaces all hardcoded domain constants (`VALID_DOMAINS`, `DOMAIN_COLORS`, `KNOWN_DOMAINS`, `_DOMAIN_SIGNALS`). `DomainResolver` and `DomainSignalLoader` provide cached DB-driven resolution. Warm path discovers new domains organically from coherent "general" sub-populations. Five stability guardrails, tree integrity with auto-repair, stats cache with trend tracking. Supersedes the planned "Multi-label domain classification" item. See [`docs/adr/ADR-004-unified-domain-taxonomy.md`](adr/ADR-004-unified-domain-taxonomy.md).

### Multi-dimensional domain classification (v0.3.7-dev)
LLM analyze prompt and heuristic analyzer output "primary: qualifier" format (e.g., "backend: security"). Taxonomy clustering, Pattern Graph edges, and color resolution parse the primary for comparison while preserving qualifier for display. Zero schema changes.

### Zero-LLM heuristic suggestions (v0.3.6-dev)
Deterministic suggestions from weakness analysis, score dimensions, and strategy context for the passthrough tier. 18 unit tests.

### Structural pattern extraction (v0.3.6-dev)
Zero-LLM meta-pattern extraction via score delta detection and structural regex. Passthrough results now contribute patterns to the taxonomy knowledge graph.

### Process-level singleton RoutingManager (v0.3.6-dev)
Fixed 6 routing tier bugs caused by per-session RoutingManager replacement in FastMCP's Streamable HTTP transport.

### Inspector metadata parity (v0.3.6-dev)
All tiers now show provider, scoring mode, model, suggestions, changes, domain, and duration in the Inspector panel.

### Electric neon domain palette (v0.3.6-dev)
Domain colors overhauled to vibrant neon tones with zero overlap to tier accent colors. Sharp wireframe contour nodes matching the brand's zero-effects directive.
