# ADR-004: Unified Domain Taxonomy — Domains as PromptCluster Nodes

**Status:** Accepted
**Date:** 2026-03-28

## Context

The system classifies every optimized prompt into a "domain" — a navigational category that drives UI color coding, cluster filtering, topology grouping, and strategy affinity. As of v0.3.7-dev, domains are governed by a hardcoded 7-item constant:

```python
VALID_DOMAINS = {"backend", "frontend", "database", "devops", "security", "fullstack", "general"}
```

This constant propagates through four layers that must agree: backend validation (`pipeline_constants.py`), the LLM analyzer prompt (`analyze.md`), the heuristic classifier (`heuristic_analyzer.py`), the frontend color map (`colors.ts`), and the frontend domain picker (`Inspector.svelte`). Any prompt not matching a known domain is silently collapsed to `"general"`.

### Problems with the current design

1. **Coding-centric bias.** Six of seven domains are software development categories. The system's core capability — prompt optimization — is domain-agnostic, but the taxonomy infrastructure forces non-coding prompts (marketing, legal, education, data science, creative writing, business strategy) into a single undifferentiated `"general"` bucket.

2. **Wasted signal.** The analyzer produces a free-form `domain_raw` string (e.g., `"marketing: email campaigns"`), but validation strips it to the primary and discards anything not in `VALID_DOMAINS`. The `domain_raw` column in `Optimization` preserves the original for audit, but no system reads it for domain discovery.

3. **Taxonomy engine mismatch.** The taxonomy engine clusters prompts by embedding similarity and evolves organically through HDBSCAN discovery and lifecycle mutations (emerge/merge/split/retire). Domains do not participate in this evolution — they are static labels applied before clustering. The engine already has the intelligence to discover new categories; the domain system blocks it.

4. **"General" is a dumping ground.** Cross-domain merge prevention (`family_ops.py`) uses primary domain equality to keep "backend" and "frontend" clusters separate. All non-coding prompts share `domain="general"` and freely merge with each other regardless of semantic distance — a marketing prompt can merge with a legal prompt.

5. **Lifecycle gap.** When the warm path creates new nodes via `emerge()` or splits them via `split()`, children default to `domain="general"` rather than inheriting their parent's domain. Domain context is lost during the most important evolutionary operations.

6. **Hardcoded frontend.** `DOMAIN_COLORS` (7-entry hex map) and `KNOWN_DOMAINS` (7-entry array) are compile-time constants. Adding a domain requires code changes, redeployment, and synchronization across backend and frontend.

### Decision drivers

- The system should discover and create domains organically from user behavior, not require code changes.
- Non-coding use cases must be first-class citizens with proper domain identity, coloring, and filtering.
- The existing taxonomy engine's evolution machinery (HDBSCAN, quality gates, lifecycle operations) should be reused, not duplicated.
- Domain stability (colors, names, persistence) must be guaranteed despite the engine's evolutionary nature.

## Decision

**Domains become `PromptCluster` nodes with `state="domain"`.** The existing taxonomy engine evolves domains through the same infrastructure that handles clusters: embedding-based assignment, HDBSCAN discovery, quality-gated lifecycle mutations. Seven seed domains are created via migration as the initial domain-level nodes. New domains emerge organically when the warm path detects coherent sub-populations within the "general" domain.

All hardcoded domain constants, color maps, and keyword dictionaries are removed. The domain node table is the single source of truth. Frontend fetches the domain palette from a new `/api/domains` endpoint. The analyzer prompt template receives the domain list via template variable injection.

### Stability guardrails

Five guardrails prevent the taxonomy engine's evolutionary behavior from destabilizing navigational categories:

| # | Guardrail | Mechanism | Rationale |
|---|-----------|-----------|-----------|
| 1 | **Color pinning** | Domain nodes get `color_hex` at creation time via OKLab max-perceptual-distance calculation. Cold path color assignment skips `state="domain"` nodes. | Users associate domains with stable colors. UMAP-derived colors drift across cold path runs. |
| 2 | **Retire exemption** | `lifecycle.py` retire operation skips `state="domain"`. Domains can only be archived via explicit manual PATCH. | Seasonal usage dips must not destroy stable categories. |
| 3 | **Separate coherence floor** | Domain quality uses `DOMAIN_COHERENCE_FLOOR=0.3` instead of cluster threshold (0.6). | A domain spanning many sub-topics has inherently lower coherence — this is correct behavior, not a quality problem. |
| 4 | **Merge requires approval** | Warm path never auto-merges two domain nodes. Emits `domain_merge_proposed` event instead. | Merging "devops" into "backend" is a navigational decision, not a statistical one. |
| 5 | **Split creates clusters, not domains** | Children from domain splits are `state="candidate"` clusters. Domain promotion requires a separate path (warm path proposal + threshold, or manual override). | Prevents domain proliferation from noisy splits. |

### Domain discovery algorithm

The warm path gains a domain proposal step after HDBSCAN clustering:

1. Select all clusters under `domain="general"` with `member_count >= 5` and `coherence >= 0.6`.
2. For each, aggregate `domain_raw` values from linked `Optimization` rows. Extract primaries via `parse_domain()`.
3. If a single primary appears in >= 60% of members AND that primary is not already a domain node label, propose a new domain.
4. Create `PromptCluster(state="domain", label=<primary>, color_hex=<OKLab max-distance>, persistence=1.0)`.
5. Re-parent qualifying members under the new domain node.
6. Backfill `Optimization.domain` for re-parented rows.
7. Extract TF-IDF keywords from member `raw_prompt` texts, store as domain node metadata for heuristic classifier signals.
8. Emit `domain_created` SSE event.

### Heuristic signal learning

`_DOMAIN_SIGNALS` in `heuristic_analyzer.py` is replaced with a dynamic signal loader:

- At startup, loads keyword signals from all `state="domain"` nodes' metadata.
- Seed domains carry their keyword signals in migration-populated metadata (identical to current hardcoded values).
- Discovered domains get auto-generated signals from TF-IDF extraction.
- Hot-reloaded on `domain_created` / `taxonomy_changed` events.

### Analyzer prompt adaptation

`analyze.md` replaces the hardcoded domain list with a `{{known_domains}}` template variable. `PromptLoader.render()` injects the current list from domain node labels. New domains appear in future analyzer prompts automatically.

### Frontend architecture

All hardcoded domain constants are removed:

- `DOMAIN_COLORS` map in `colors.ts` — removed. `taxonomyColor()` resolves from API-fetched domain data.
- `KNOWN_DOMAINS` array in `Inspector.svelte` — removed. Domain picker populated from `/api/domains`.
- Domain palette cached in a new `domains` reactive store, refreshed on `domain_created` / `taxonomy_changed` SSE events.
- `ClusterNavigator` already groups dynamically from cluster data — no change needed.

### Migration strategy

1. Add `"domain"` to the `state` column's valid values (no schema change — `String(20)` already accommodates it).
2. Create 7 `PromptCluster` rows: `state="domain"`, `label` from current `VALID_DOMAINS`, `color_hex` from current `DOMAIN_COLORS` palette, `persistence=1.0`, `centroid_embedding` computed from seed keyword vectors.
3. Store seed keyword signals as JSON metadata on each domain node.
4. Re-parent existing clusters: match `PromptCluster.domain` string to a domain node label, set `parent_id` to the domain node's ID.
5. Backfill `Optimization.domain` for any `domain_raw` values that resolve to a known domain but were previously collapsed to `"general"`.
6. Remove `VALID_DOMAINS` constant, `DOMAIN_COLORS` map, `KNOWN_DOMAINS` array, and `_DOMAIN_SIGNALS` dictionary.
7. Remove `apply_domain_gate()` — domain gating is now handled by the taxonomy engine's embedding-based assignment with fallback to the "general" domain node.

### `Optimization.domain` column

Remains `String` (not FK). Stores the domain node's `label` (e.g., `"backend"`, `"marketing"`). This avoids a breaking migration on the highest-traffic table while enabling domain discovery. The taxonomy engine resolves domains by label lookup against `PromptCluster` rows where `state="domain"`. A future phase can add an optional `domain_cluster_id` FK column for direct join queries if needed.

## Alternatives Considered

### Option A: Dynamic DomainRegistry table (non-breaking)

Separate `domain_definition` table alongside `PromptCluster`. Discovery logic duplicated outside the taxonomy engine.

**Rejected because:** Duplicates the HDBSCAN discovery, quality gating, and lifecycle machinery that already exists in the taxonomy engine. Two parallel systems doing the same thing with different codepaths. Higher maintenance burden and divergence risk.

### Option C: Hybrid (DomainRegistry + phased intelligence)

Start with Option A's registry, incrementally add Option B's intelligence features.

**Rejected because:** The phased approach defers the core architectural decision (are domains clusters or not?) while accumulating adapter code. Phase 3 of Option C approaches Option B's capabilities but with more indirection, more tables, and more sync logic. Going directly to B avoids the throwaway intermediate layers.

### FK on `Optimization.domain`

Replace `String` column with FK to `PromptCluster.id`.

**Deferred because:** This is the highest-traffic table (every optimization writes to it). FK migration requires careful coordination with the backfill and re-parenting operations. The string-label approach works correctly and can be upgraded to FK in a future phase without data loss.

## Consequences

### Positive

- **Self-evolving taxonomy.** Domains emerge organically from user behavior. A user who optimizes 50 marketing prompts gets a "marketing" domain with its own color, filter tab, and strategy affinity — no code change required.
- **Single evolution engine.** Domain and cluster discovery share the same HDBSCAN + quality gate + lifecycle infrastructure. One codepath to maintain, test, and reason about.
- **Domain-level quality metrics.** Domain nodes get coherence, separation, stability, persistence, Q_system participation for free — they're PromptCluster rows.
- **Native topology visualization.** Domain nodes appear in the 3D graph as high-persistence parent nodes. No synthetic overlay needed.
- **Cross-domain relationships.** Embedding similarity edges between domains emerge naturally from the existing cross-cluster edge computation.
- **Non-coding first-class support.** Writing, analysis, creative, data, system, and any future task type can develop rich domain taxonomies.

### Negative

- **Conditional behavior in lifecycle operations.** `emerge()`, `split()`, `retire()`, merge, and cold path color assignment must check `state="domain"` and apply different rules. This adds ~5 conditional branches across the taxonomy engine.
- **Domain quality metrics differ from cluster metrics.** Coherence thresholds, merge rules, and retire rules are state-dependent. This must be clearly documented and tested to prevent regressions when modifying lifecycle logic.
- **Migration complexity.** The migration creates 7 domain nodes, re-parents existing clusters, and backfills optimizations. This is a multi-step data migration that must be tested against production-scale data.
- **Frontend palette loading.** Domain colors are now API-fetched instead of compile-time constants. First paint may show fallback colors until the API response arrives. Mitigated by caching in `localStorage` and loading in the app's initialization sequence.

### Risks to monitor

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Domain proliferation (too many domains created) | Medium | Medium — cluttered navigation | Discovery thresholds (5+ members, coherence ≥0.6, ≥60% consistent primary). Manual approval for merge. Monitor domain count in health endpoint. |
| Stale learned signals (TF-IDF keywords become outdated) | Low | Low — heuristic falls back to embedding match | Periodic signal refresh in warm path. Signals are supplementary — embedding-based assignment is the primary path. |
| Guardrail bypass (future code changes accidentally skip domain checks) | Medium | High — domain stability broken | Guardrails tested with dedicated test cases. `state="domain"` checks are centralized in well-documented functions, not scattered. |
| "General" never shrinks (threshold too conservative) | Low | Low — same as current behavior | Threshold values are configurable constants. Warm path logs proposals for monitoring. Adjust thresholds based on observed proposal rate. |
| Migration data corruption | Low | High — cluster tree integrity | Migration is idempotent and reversible. Backup before running. Integration test validates tree structure post-migration. |

## Related decisions

- **ADR-001** (MCP Authentication) — domain endpoints inherit the same auth model.
- **ADR-003** (Dependency Pinning) — no new dependencies; uses existing numpy, scikit-learn, sentence-transformers.
- Taxonomy engine design spec: `docs/superpowers/specs/2026-03-20-evolutionary-taxonomy-engine-design.md`
- Unified prompt lifecycle spec: `docs/specs/2026-03-21-unified-prompt-lifecycle-design.md`

## Implementation status

**Shipped.** This ADR is the prerequisite that made ADR-005's sibling-root projects possible.

- **Domain nodes as PromptCluster rows** — `state="domain"` live alongside clusters. `_propose_domains()` in `backend/app/services/taxonomy/engine.py` fires the discovery pipeline; migration seeds 7 starter domains.
- **Resolver replaces `VALID_DOMAINS`** — `backend/app/services/domain_resolver.py` (cached DB lookup, `add_label()`/`remove_label()` for sub-domain registration and dissolution).
- **Signal loader replaces `_DOMAIN_SIGNALS`** — `backend/app/services/domain_signal_loader.py` reads keyword signals from domain node metadata, hot-reloaded on `domain_created` / `taxonomy_changed` events. Organic vocabulary (`generated_qualifiers`) generated by Haiku from cluster labels.
- **Frontend constants removed** — `frontend/src/lib/api/domains.ts` and `frontend/src/lib/stores/domains.svelte.ts` fetch the palette from `/api/domains`. `DOMAIN_COLORS` / `KNOWN_DOMAINS` no longer exist in source.
- **Analyzer prompt** — `prompts/analyze.md` uses `{{known_domains}}` template variable.
- **Endpoint** — `GET /api/domains` exposed via `backend/app/routers/domains.py`, plus readiness telemetry (`/api/domains/readiness`, `/api/domains/{id}/readiness`, `/api/domains/{id}/readiness/history`) and promotion (`POST /api/domains/{id}/promote`).
- **Lifecycle extensions since original ADR** — domain dissolution (`_reevaluate_domains()` with 5 guards: general permanent, sub-domain anchor, ≥48h age, ≥5 member ceiling, Source-1 consistency <15% with 45-pt hysteresis) and sub-domain dissolution via shared `_dissolve_node()` primitive. Seed domains have no special protection (ADR-006 principle — the engine treats seeds as bootstrapping data, not architectural constants).

All five stability guardrails from the original Decision section remain enforced. No `VALID_DOMAINS` references survive outside ADR-004 historical context and comment strings noting what was replaced.
