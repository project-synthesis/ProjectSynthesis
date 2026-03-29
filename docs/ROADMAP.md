# Project Synthesis — Roadmap

Living document tracking planned improvements. Items are prioritized but not scheduled. Each entry links to the relevant spec or ADR when available.

## Conventions

- **Planned** — designed, waiting for implementation
- **Exploring** — under investigation, no decision yet
- **Deferred** — considered and postponed with rationale

---

## Planned

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

### Pipeline progress visualization
**Status:** Planned
**Context:** During optimization (2+ minutes for Opus), the web UI shows only a 3-step phase indicator (Analyzing → Optimizing → Scoring) with step counters. The internal tier streams SSE phase events correctly, but there's no rich progress experience — no estimated time remaining, no streaming preview, no per-phase timing. The sampling and passthrough tiers have different progress patterns that should also be visualized distinctly. A unified pipeline progress component would adapt to the active tier and show meaningful real-time feedback.
**Quick fixes shipped (v0.3.8-dev):** Replaced spinning cube with 3-step phase indicator in Inspector, added step counter `[1/3]` in StatusBar, tier-aware accent colors, model ID display during processing.

### Passthrough refinement UX
**Status:** Deferred
**Context:** Passthrough results cannot be refined (returns 503). Refinement requires an LLM provider to rewrite the prompt. The user already has their external LLM — refinement would need a different interaction model (e.g., show the assembled refinement prompt for copy-paste like the initial passthrough flow).
**Rationale:** Low demand — users who use passthrough can iterate manually

---

## Completed (recent)

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
