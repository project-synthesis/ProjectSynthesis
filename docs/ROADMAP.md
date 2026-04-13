# Project Synthesis — Roadmap

Living document tracking planned improvements. Items are prioritized but not scheduled. Each entry links to the relevant spec or ADR when available.

## Conventions

- **Planned** — designed, waiting for implementation
- **Exploring** — under investigation, no decision yet
- **Deferred** — considered and postponed with rationale

---

## Immediate

### Sub-domain discovery cluster-count trigger
**Status:** Immediate
**Context:** The sub-domain discovery system (`_propose_sub_domains()` in engine.py) only triggers when a domain's mean child cluster coherence drops below 0.50. With well-organized taxonomy (the warm path keeps clusters coherent), this threshold is never reached — the SaaS domain has 23 active clusters, 59 members, but mean coherence 0.828.

**Diagnosis:** The 0.50 coherence ceiling was designed for domains that grow organically incoherent (many diverse prompts forced into a single mega-cluster). But the taxonomy engine's split/merge/reconcile cycle prevents this — new prompts either join matching clusters (maintaining coherence) or spawn focused new clusters (coherence=1.0). The domain IS diverse (pricing vs metrics vs onboarding vs customer success vs operations) but diversity is captured at the cluster level, not reflected in per-cluster coherence.

**Proposed fix:** Add OR-logic trigger: sub-domain discovery fires if EITHER `(members >= 20 AND mean_coherence < 0.50)` OR `(active_clusters >= 15 AND members >= 30)`. The cluster-count path recognizes that a domain with 15+ clusters is structurally complex enough to benefit from sub-grouping regardless of individual cluster coherence.

**Files:** `backend/app/services/taxonomy/_constants.py` (new `SUB_DOMAIN_MIN_CLUSTERS` constant), `backend/app/services/taxonomy/engine.py` (`_propose_sub_domains()` — add second trigger path)

---

## Planned

### Integration store — pluggable context providers beyond GitHub
**Status:** Planned
**Context:** GitHub is the sole external integration — it provides codebase context for the explore phase and serves as the project creation trigger (ADR-005). This creates two problems: (1) non-developers have zero external context enrichment, and (2) the project system is tightly coupled to GitHub repos.

**Vision:** A VS Code-style integration "store" where GitHub is one installable provider among many. Each integration is a self-contained plugin that provides: a context source (documents for the explore phase), a project trigger (linking creates a project node), and optionally domain keyword seeds and heuristic weakness signals for its vertical.

**Architecture:**
- **ContextProvider protocol** — each integration implements `list_documents(project_id) -> list[Document]` and `fetch_document(id) -> str`. The existing `ContextEnrichmentService` dispatches to whichever provider is linked for the active project. GitHub's current implementation becomes the first provider, not a special case
- **Project decoupling** — project nodes (`state="project"`) are created by any provider, not just GitHub. A Notion integration creates a project node when the user links a Notion workspace. A local-files integration creates one when the user points to a directory. The `LinkedRepo` model generalizes to `LinkedSource` (or the provider stores its own link model)
- **Provider lifecycle** — install (enable provider), configure (auth + link a source), unlink (preserve data, clear link), uninstall (disable provider). Each provider brings its own auth flow (GitHub OAuth, Google OAuth, Notion API key, no auth for local files)
- **Frontend: Integrations panel** — new Navigator section (alongside Strategies, History, Clusters, GitHub, Settings) showing installed providers with install/configure/unlink controls. Replaces the current GitHub-specific Navigator section

**Candidate providers:**

| Provider | Vertical | Context source | Auth |
|----------|----------|---------------|------|
| GitHub | Developers | Repo files, README, architecture docs | OAuth |
| Google Drive | Business/marketing | Documents, spreadsheets, brand guidelines | OAuth |
| Notion | Product/content | Pages, databases, knowledge bases | API key |
| Local filesystem | Anyone | Any directory on disk | None |
| Confluence | Enterprise | Wiki pages, project specs | API token |
| Figma | Design | Design system docs, component specs | API key |

**Impact on ADR-005 project system:** The current `LinkedRepo.project_node_id` pattern generalizes. Each provider creates its own link record with a `project_node_id` FK. Project resolution in `process_optimization()` checks the active provider's link, not just `LinkedRepo`. The two-tier cluster assignment, per-project Q metrics, and dirty-set tracking work identically regardless of which provider created the project.

**Prerequisite:** ADR-006 (universal engine principle). The integration store is the concrete mechanism that makes the universal engine accessible to non-developer verticals.

**Files:** New `backend/app/services/integrations/` package (provider protocol, registry, lifecycle). Refactor `github_repos.py` → provider implementation. New `backend/app/routers/integrations.py`. Frontend `Integrations` panel component. Migration for `LinkedSource` generalization or per-provider link tables.

### Non-developer onboarding pathway
**Status:** Exploring
**Context:** The current UI assumes developer context at multiple touchpoints: GitHub OAuth in the sidebar, "Clusters" and "Taxonomy" jargon, developer-focused seed agents, codebase scanning references in Settings. A non-developer (marketer, writer, business analyst) arriving at the app would encounter an experience that feels foreign and exclusionary, even though the engine works perfectly for their prompts.

**Problem:** It's not that features need to change — it's that the UI presentation needs to adapt based on who the user is. A marketer doesn't need to see GitHub integration, and the taxonomy visualization should use language like "Your prompt patterns" instead of "Clusters."

**Proposed approaches:**

1. **Vertical-aware onboarding** — first-run flow asks "What do you primarily use AI for?" with options (coding, writing, marketing, analysis, etc.). Selection configures: which integrations are highlighted, what seed agents appear in the SeedModal, what language the UI uses for taxonomy concepts, and which Navigator sections are visible by default. Developer scaffold remains default; non-developer pathways de-emphasize GitHub/IDE features without removing them.

2. **Adaptive UI labels** — taxonomy concepts get user-facing aliases based on the active vertical. "Clusters" → "Pattern groups" for non-developers. "Domains" → "Categories." "Meta-patterns" → "Proven techniques." The underlying data model is unchanged — only display labels adapt. This could be driven by a simple `vertical: "developer" | "general"` preference.

3. **Content-driven differentiation** — rather than changing the UI, add non-developer seed agents (per ADR-006 playbook) and let the taxonomy organically discover non-developer domains. The UI stays the same, but the content it surfaces (patterns, suggestions, domain names) naturally adapts to the user's prompt type. Lowest effort, relies on organic discovery.

**Recommended:** Start with (3) — add marketing/writing/business seed agents per ADR-006 playbook. Then (2) — adaptive labels based on a vertical preference. Then (1) — full vertical-aware onboarding. Each step is independently valuable and shippable.

**Spec:** [ADR-006](../adr/ADR-006-universal-prompt-engine.md) (Universal Prompt Engine — vertical rollout playbook)

### Hierarchical topology navigation — project → domain → cluster → prompt
**Status:** Planned (edge system shipped in v0.3.30 — curved bundling, depth attenuation, domain coloring, focus-reveal)
**Context:** The current 3D topology view (`SemanticTopology`) renders ALL nodes in a single scene: project nodes, domain nodes, active clusters, candidates, mature clusters, templates — 76+ nodes at current scale. At 200+ clusters across 3 projects, this becomes visually overwhelming. Domain nodes (structural grouping) and active clusters (semantic content) serve different purposes but are rendered identically in the same space. The user has no way to "zoom into" a project or domain to see only its contents.

**Vision:** A hierarchical drill-down topology inspired by filesystem navigation. Each level of the taxonomy hierarchy gets its own view with appropriate aesthetics and interaction patterns:

**Level 0: Project Space** — the outermost view showing project nodes as large entities with gravitational relationships. Distance between projects reflects semantic similarity of their content. Size reflects optimization count. Color reflects the project's dominant domain. Projects with cross-project GlobalPatterns have visible connection lines. Double-click a project to drill into it.

**Level 1: Domain Map** (per project) — shows the domains within a selected project. Each domain is a region or cluster with its own color (from the existing domain palette). Size reflects member count. Distance reflects domain overlap (how often prompts cross domain boundaries). Sub-domains are nested. No active clusters visible at this level — just the categorical structure. Double-click a domain to drill into it.

**Level 2: Cluster View** (per domain) — the current topology experience, but scoped to a single domain's clusters only. No domain nodes in this view — they're the parent you drilled from. Clusters shown with the existing lifecycle state coloring (active, mature, template, candidate). Cluster size reflects member count. Distance reflects centroid similarity. Force layout + UMAP positioning as today. Double-click a cluster to drill into it.

**Level 3: Prompt Detail** (per cluster) — individual optimizations within a cluster. Each node is a prompt/optimization. Size reflects score. Color reflects improvement delta. Position reflects embedding proximity within the cluster. Hovering shows the prompt text. Clicking loads it in the editor. This level doesn't exist today — it's a new visualization that replaces the current cluster detail panel's optimization list with a spatial view.

**Navigation:**
- Breadcrumb bar at top: `All Projects › user/backend-api › backend › API Endpoint Patterns`
- Back button / Escape returns to parent level
- Animation: smooth zoom-in transition when drilling down, zoom-out when going back (like macOS folder zoom)
- Each level preserves its camera position when returning (no reset)
- Ctrl+F search works across all levels (highlights matching node at whatever level it lives)

**Per-level aesthetics:**
- Level 0 (projects): large glowing orbs, minimal, wide spacing, slow drift. Ambient starfield background
- Level 1 (domains): colored regions with soft boundaries, domain labels prominent, keyword clouds visible on hover
- Level 2 (clusters): current wireframe contour style, lifecycle state encoding, force layout. This is the most data-dense level
- Level 3 (prompts): small nodes, text-preview on hover, score-gradient coloring, tight clustering

**Technical approach:**
- Each level is a separate Three.js scene (or scene state) with its own camera, lighting, and node renderer
- Transition between levels is animated (camera fly-through + node scale/fade)
- Data loading is lazy — Level 2 and 3 data fetched on drill-down, not at initial load
- The existing `TopologyData`, `TopologyRenderer`, `TopologyInteraction` components refactor into level-aware variants
- `GET /api/clusters/tree?project_id=...` (ADR-005) provides the per-project data. New endpoints needed for per-domain and per-cluster detail views
- `TopologyWorker` force simulation runs per-level (different force parameters for each level)

**Impact on existing features:**
- State filter tabs (active/mature/template/candidate) move to Level 2 only
- Activity panel stays global (shows events from all levels)
- Inspector panel adapts to the current level (project stats at L0, domain stats at L1, cluster detail at L2, prompt detail at L3)
- Pattern suggestion on paste searches across the current project's clusters (Level 2 scope)
- SeedModal targets the current project (Level 0 scope)

**Single-project behavior:** When only one project exists (Legacy or a single repo), skip Level 0 and open directly at Level 1 (domains). Level 0 only renders when 2+ projects exist. The breadcrumb still shows the project name for context.

**Legacy project:** Always visible at Level 0 as a permanent node. Contains all pre-repo and non-repo optimizations. Never merged or renamed. Users who never link a repo see only Legacy and skip straight to Level 1.

**Existing backend support:**
- `GET /api/projects` already returns project nodes (built in Phase 2A)
- `GET /api/clusters/tree?project_id=...` already filters by project (Phase 2A)
- `GET /api/clusters/{id}` already returns `member_counts_by_project` and `project_ids` (Phase 2A)
- New endpoints needed: per-domain cluster lists and per-cluster optimization lists with embedding positions for Level 3

**ADR-006 label adaptation:** Topology level labels should respect the active vertical. For non-developers: Level 0 "Workspaces", Level 1 "Categories", Level 2 "Pattern groups", Level 3 "Prompts". For developers: Level 0 "Projects", Level 1 "Domains", Level 2 "Clusters", Level 3 "Optimizations". Driven by a preference setting per ADR-006 non-developer onboarding pathway.

**Prerequisites:** ADR-005 Phase 2A (project nodes and tree endpoint with project filter). The integration store (above) for project creation beyond GitHub. ADR-006 (universal engine — vertical-aware labels).

**Files:** Major frontend refactor. New `TopologyLevel0`, `TopologyLevel1`, `TopologyLevel2`, `TopologyLevel3` components. Refactored `TopologyNavigation` with breadcrumb + back. New `topology-state.svelte.ts` store for current level + drill path. New backend endpoints for per-domain cluster lists and per-cluster optimization lists with spatial data. Updated `TopologyWorker` with per-level force configs.

### MCP routing fallback — per-client capability awareness
**Status:** Planned
**Context:** MCP tool calls from non-sampling clients (e.g., Claude Code) are routed to the sampling tier when a sampling-capable client (VS Code bridge) is also connected. The call fails with "Method not found" because the calling client doesn't support `sampling/createMessage`. The internal provider (CLI/API) is available but bypassed because `caller="mcp"` + `sampling_capable=true` (global flag) routes to sampling.

**Root cause:** The routing resolver checks global `sampling_capable` state, not per-client capabilities. It doesn't know whether the *specific client making the call* supports sampling.

**Proposed approaches:**
1. **Per-client capability tagging** — track each MCP session's declared capabilities from `initialize`. Route based on the calling session's sampling support, not the global flag.
2. **Internal fallback for MCP** — if sampling fails for an MCP caller, retry on internal pipeline when a provider exists. Simpler but reactive (fails first).
3. **Prefer internal for non-interactive MCP** — default MCP tool calls to internal pipeline when a provider exists. Only use sampling when explicitly requested. Preserves IDE LLM quota for interactive work.

**Files:** `services/routing.py` (resolve_route), `mcp_server.py` (capability middleware), `tools/optimize.py` (context construction)

### REST-to-sampling proxy via IDE session registry
**Status:** Planned
**Context:** The web UI cannot perform MCP sampling because `POST /api/optimize` uses `caller="rest"`, which the routing resolver correctly blocks from sampling (only `caller="mcp"` can reach sampling tiers). A previous sampling proxy implementation (`mcp_proxy.py` called from `routers/optimize.py`) was removed in v0.3.16-dev because it was architecturally broken: the proxy opened a **new** MCP session with `capabilities: {}` (no sampling support), so `create_message()` targeted the proxy client — not the IDE's session — and hung for 120s per phase before timing out.

**Root cause:** MCP's `create_message()` is a server-to-client request that goes to the session that made the tool call. The proxy's session has no sampling handler. There is no mechanism to route a sampling request through a *different* client's session (the IDE's).

**Proposed solution:** The MCP server maintains a **session registry** mapping session IDs to their declared capabilities. When a non-sampling MCP client (or REST proxy) calls `synthesis_optimize` and routing selects sampling tier:
1. The tool handler queries the registry for any active sampling-capable session
2. If found, it borrows that session's `create_message()` channel for the LLM call
3. The original caller receives the result when the IDE completes the sampling request
4. If no sampling session exists, falls back to internal/passthrough with clear error

**Complexity:** Medium-high. Requires changes to session lifecycle tracking, cross-session request routing in FastMCP, and proper cleanup when IDE sessions disconnect mid-request. Must handle race conditions (IDE disconnects while a proxied request is in-flight).

**Files:** `mcp_server.py` (session registry + lifecycle hooks), `services/mcp_proxy.py` (optional: thin REST proxy using registry), `tools/optimize.py` (session lookup before `run_sampling_pipeline`), `services/routing.py` (per-session capability in `RoutingContext`)

### Unified scoring service
**Status:** Planned
**Context:** The scoring orchestration (heuristic compute → historical stats fetch → hybrid blend → delta compute) is repeated across `pipeline.py`, `sampling_pipeline.py`, `save_result.py`, and `optimize.py` with divergent error handling. A shared `ScoringService` would eliminate duplication and ensure consistent behavior across all tiers.
**Spec:** Code quality audit (2026-03-27) identified this as the #3 finding

### Domain FK on Optimization table
**Status:** Exploring
**Context:** `Optimization.domain` is currently a `String` column storing the domain node's label (e.g., `"backend"`). Resolution uses a label lookup against `PromptCluster` rows where `state='domain'`. This works correctly via `DomainResolver` but requires subqueries for domain-level aggregations. Adding an optional `domain_cluster_id` FK to `PromptCluster.id` would enable direct JOINs without changing the existing `domain` string column (additive, non-breaking).

**Trigger:** Implement when any of these three scenarios becomes a priority:

1. **Domain-level analytics dashboard** — average score improvement per domain over time, member count trends, strategy effectiveness. Today requires `WHERE domain IN (SELECT label ... WHERE state='domain')` subqueries. A FK enables a single JOIN with the domain node's metrics, color, and `preferred_strategy` in one query.

2. **Domain-scoped strategy affinity** — the adaptation tracker currently tracks `(task_type, strategy)` pairs. Domain-scoped tracking — `(domain, strategy)` — would enable insights like "chain-of-thought works best for security prompts." A FK lets us aggregate feedback by domain node efficiently and drive `preferred_strategy` on the domain node itself. This is the most likely trigger — it's the natural evolution of the adaptation system.

3. **Cross-domain relationship graph** — weighted edges between domain nodes in the topology (not just between clusters). A FK enables `GROUP BY domain_cluster_id` aggregations to compute inter-domain traffic patterns, showing which domains users frequently switch between or combine.

**Migration:** Add nullable `domain_cluster_id` FK alongside existing `domain` String. Backfill from label lookup. Both columns coexist — string for display/filtering, FK for joins. No breaking changes.
**Decision:** ADR-004 deferred this as YAGNI. Revisit when a concrete feature requires domain-level JOINs.

### Conciseness heuristic calibration for technical prompts
**Status:** Exploring
**Context:** The heuristic conciseness scorer uses Type-Token Ratio which penalizes repeated domain terminology (e.g., "scoring", "heuristic", "pipeline" across sections). Technical specification prompts score artificially low on conciseness despite being well-structured. Needs a domain-aware TTR adjustment or alternative metric.

### Unified onboarding journey
**Status:** Planned
**Context:** The current system has 3 separate tier-specific modals (InternalGuide, SamplingGuide, PassthroughGuide) that fire independently on routing tier detection. This creates a fragmented first-run experience — users only see one tier's guide and miss the others. Two changes required:

**1. Consolidated onboarding modal:** Replace the 3 separate modals with a single multi-step onboarding journey that walks the user through all 3 tiers sequentially (Internal → Sampling → Passthrough). Each tier section is actionable — the user must acknowledge each before proceeding. The modal blocks the UI until all steps are actioned. Fires at every startup unless a "Don't show again" checkbox is checked and persisted to preferences.

**2. Dynamic routing change toasts:** Replace the per-tier-change modal triggers with concise inline toasts that explain *what caused* the routing change (e.g., "Routing changed to passthrough — no provider detected", "Sampling available — VS Code bridge connected"). These fire only on *automatic* tier transitions, not when the user manually toggles force_passthrough or force_sampling.

**Prerequisite:** Refactor `tier-onboarding.svelte.ts`, merge 3 guide components into 1, new `onboarding-dismissed` preference field, update `triggerTierGuide()` to emit toast instead of modal after initial onboarding, update `+page.svelte` startup gate.

### PostgreSQL migration
**Status:** Exploring
**Context:** SQLite's single-writer limitation causes `database is locked` errors when the MCP server pipeline (optimization write) and backend warm path (taxonomy mutations) write concurrently. WAL mode + busy_timeout=30s mitigates but doesn't eliminate the issue. At scale (concurrent users, parallel optimizations), SQLite becomes a bottleneck.

**Scope:** Replace `aiosqlite` with `asyncpg` + PostgreSQL. Requires: Alembic migration infrastructure, connection pooling config, Docker Compose for local dev, production deployment update, test fixture changes (async session factory).

**Trigger:** When `database is locked` errors become user-facing despite busy_timeout, or when concurrent multi-user access is needed.

**Files:** `database.py` (engine), `config.py` (DATABASE_URL), `main.py`/`mcp_server.py` (PRAGMA removal), `docker-compose.yml` (new), all test fixtures.

### Project workspaces — explicit project_id override
**Status:** Exploring (subsumed by integration store)
**Context:** ADR-005 Phase 2A implements session-based project resolution: optimizations inherit their project from the session's linked GitHub repo. This covers the primary use case (one repo per session) but doesn't support MCP callers without GitHub auth, REST API targeting, or automation workflows.

**Proposed enhancement:** Add optional `project_id` parameter to `POST /api/optimize` and `synthesis_optimize` MCP tool. Requires project CRUD endpoints (`POST /api/projects`, `GET /api/projects`).

**Note:** This becomes part of the integration store design — when projects are decoupled from GitHub, explicit project_id selection is a natural consequence. The CRUD endpoints would serve both the integration store UI and the API override.
**Spec:** `docs/adr/ADR-005-taxonomy-scaling-architecture.md` (Section 1: Data Model)

### LLM domain classification accuracy
**Status:** Partially shipped (v0.3.30), remaining items exploring
**Context:** Three systemic domain classification failures fixed in v0.3.8-dev (schema contradiction, confidence gate, manual gate). v0.3.30 shipped the heuristic accuracy pipeline: compound keywords (A1), technical verb disambiguation (A2), TF-IDF domain signal auto-enrichment (A3), and confidence-gated Haiku LLM fallback (A4). Classification agreement tracking (E1) provides ongoing measurement. Prompt-context divergence detection (B1+B2) ships tech stack conflict alerts.

**Remaining future optimizations:**
- **Constrained decoding** — `Literal` enum on `AnalysisResult.domain` to restrict LLM output at schema level
- **Dynamic text fallback keywords** — `_build_analysis_from_text()` uses hardcoded keywords instead of `DomainSignalLoader`
- **DomainResolver confidence-aware caching** — unknown domain cached as "general" at low confidence persists. Self-corrects on `load()` but could improve
- **C2: Heuristic-to-LLM reconciliation** — use accumulated E1 disagreement data to adjust keyword weights over time. Requires signal_adjuster.py (Sprint 3)
- **E1b: Cross-process agreement bridge** — MCP process agreement data invisible to health endpoint. Needs HTTP POST forwarding (Sprint 3)

**Specs:** [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md), [`docs/enrichment-consolidation-action-items.md`](enrichment-consolidation-action-items.md), [`docs/specs/phase-a-heuristic-accuracy-a3-a4.md`](specs/phase-a-heuristic-accuracy-a3-a4.md)

### Pipeline progress visualization
**Status:** Partially shipped
**Context:** v0.3.8-dev shipped phase indicator + step counter in Inspector/StatusBar. Rich progress (estimated time, streaming preview, per-phase timing, tier-adaptive visualization) remains planned.

### Passthrough refinement UX
**Status:** Deferred
**Context:** Passthrough results cannot be refined (returns 503). Refinement requires an LLM provider to rewrite the prompt. The user already has their external LLM — refinement would need a different interaction model (e.g., show the assembled refinement prompt for copy-paste like the initial passthrough flow).
**Rationale:** Low demand — users who use passthrough can iterate manually

---

## Completed (recent)

### Enrichment engine consolidation (v0.3.30)
Unified context enrichment with auto-selected profiles (code_aware/knowledge_work/cold_start), task-gated curated retrieval, strategy intelligence merge, workspace guidance collapse. Heuristic accuracy pipeline: compound keywords (A1), verb disambiguation (A2), TF-IDF domain signal auto-enrichment (A3), confidence-gated Haiku fallback (A4). Prompt-context divergence detection (B1+B2), domain-relaxed fallback queries (C1), classification agreement tracking (E1). 2107 backend tests. Full spec: [`docs/enrichment-consolidation-action-items.md`](enrichment-consolidation-action-items.md).

### Hierarchical edge system (v0.3.30)
Curved edge bundling in 3D topology with depth-based attenuation shader, density-adaptive opacity, proximity suppression, focus-reveal on hover, domain-colored edges. 5-phase hierarchical edge declutter.

### Injection effectiveness + orphan recovery (v0.3.29)
Warm-path Phase 4 measures mean score lift for pattern-injected vs non-injected optimizations. Orphan recovery detects failed hot-path extractions and retries with exponential backoff. Project node UX with dodecahedron geometry and rich inspector mode.

### SSE health + incremental refresh + per-project scheduling (v0.3.28)
Real-time SSE latency tracking (p50/p95/p99), degradation detection, exponential backoff reconnection. Repo index incremental refresh via SHA comparison. Per-project scheduler budgets with proportional quotas.

### Full source context + import graph + curated retrieval (v0.3.27)
Curated retrieval delivers actual file source code (not outlines). Import-graph expansion, test file exclusion, cross-domain noise filter, performance signals, context diagnostic panel. Skip-and-continue budget packing, source-type soft caps.

### Alembic migration for domain nodes (v0.3.8-dev)
Idempotent migration `a1b2c3d4e5f6`: adds `cluster_metadata` column, `ix_prompt_cluster_state_label` index, `uq_prompt_cluster_domain_label` partial unique index, seeds 7 domain nodes with keyword metadata, re-parents existing clusters, backfills `Optimization.domain`. Also fixed async env.py commit for DML persistence.

### Unified domain taxonomy (v0.3.8-dev)
Domains are `PromptCluster` nodes with `state="domain"`. Replaces all hardcoded domain constants (`VALID_DOMAINS`, `DOMAIN_COLORS`, `KNOWN_DOMAINS`, `_DOMAIN_SIGNALS`). `DomainResolver` and `DomainSignalLoader` provide cached DB-driven resolution. Warm path discovers new domains organically from coherent "general" sub-populations. Five stability guardrails, tree integrity with auto-repair, stats cache with trend tracking. Supersedes the planned "Multi-label domain classification" item — ADR-004 chose a different architectural approach. See `docs/adr/ADR-004-unified-domain-taxonomy.md`.

### Multi-dimensional domain classification (v0.3.7-dev)
LLM analyze prompt and heuristic analyzer now output "primary: qualifier" format (e.g., "backend: security"). Taxonomy clustering, Pattern Graph edges, and color resolution all parse the primary domain for comparison while preserving the full qualifier for display. Zero schema changes required.

### Zero-LLM heuristic suggestions (v0.3.6-dev)
Deterministic suggestions from weakness analysis, score dimensions, and strategy context for the passthrough tier. 18 unit tests.

### Structural pattern extraction (v0.3.6-dev)
Zero-LLM meta-pattern extraction via score delta detection and structural regex. Passthrough results now contribute patterns to the taxonomy knowledge graph.

### Process-level singleton RoutingManager (v0.3.6-dev)
Fixed 6 routing tier bugs caused by per-session RoutingManager replacement in FastMCP's Streamable HTTP transport.

### Inspector metadata parity (v0.3.6-dev)
All tiers now show provider, scoring mode, model, suggestions, changes, domain, and duration in the Inspector panel.

### Electric neon domain palette (v0.3.6-dev)
Domain colors overhauled to vibrant neon tones with zero overlap to tier accent colors. Sharp wireframe contour nodes in Pattern Graph matching the brand's zero-effects directive.
