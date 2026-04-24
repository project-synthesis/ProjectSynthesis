# Unified Taxonomy Lifecycle: Domain + Sub-Domain Discovery and Dissolution

**Status:** Shipped (v0.3.35). Shared `_dissolve_node()` primitive handles both domain and sub-domain dissolution. `_reevaluate_domains()` (5 guards: general permanent, sub-domain anchor, ≥48h age, ≤5 member ceiling, Source-1 consistency <15% with 45-pt hysteresis) + `_reevaluate_sub_domains()` (consistency < 0.25 floor). Seed domains have no special protection per ADR-006. `dissolved_this_cycle` flip-flop guard. Historical record.

**Goal:** Unify domain and sub-domain lifecycle into a shared organic system with re-evaluation and graceful dissolution at both hierarchy levels. Remove seed domain protection so the taxonomy is fully organic from day one. Aligns with ADR-006 (Universal Prompt Engine).

**Problem:** Domain discovery and sub-domain discovery are currently separate mechanisms with different signal sources, different lifecycle rules, and asymmetric behavior. Domains are created from "general" using only `domain_raw` primary labels, never re-evaluated, never dissolved, and seed domains are permanently protected. This means:
- A comic book artist's taxonomy permanently shows "backend", "devops", "security" even with zero matching prompts
- A domain that was auto-discovered but never gained traction lives forever
- Domain classification errors are permanent — misclassified prompts stay in the wrong domain with no correction mechanism
- The domain lifecycle is fundamentally different from the sub-domain lifecycle, despite solving the same problem at different hierarchy levels

**Solution:** Extract a shared dissolution core (`_dissolve_node()`) that handles both domain and sub-domain dissolution with parameterized targets. Add domain re-evaluation and dissolution with bottom-up sub-domain anchoring. Remove seed domain protection. The only permanent node is "general" (structural root).

---

## Architecture

### Signal Sources by Hierarchy Level

Domain and sub-domain lifecycle use **different signal sources** appropriate to their level in the hierarchy:

**Domain-level consistency** (is this prompt correctly classified as "backend"?):
- **Source 1 only:** Parse `domain_raw` for primary domain label. This is the authoritative signal — the LLM/heuristic analyzer already classified the prompt into a domain. The organic vocabulary (qualifier keywords like "auth", "api") is for sub-qualifier detection, not domain membership validation.

**Sub-domain-level consistency** (is this "backend" prompt specifically about "auth"?):
- **Three-source cascade:** domain_raw qualifier parse + intent_label keyword match + TF-IDF signal_keywords. Sub-qualifiers require richer signals because they're finer-grained classifications within an already-confirmed domain.

### Shared Dissolution Core

`_dissolve_node()` — shared method for both domain and sub-domain dissolution:

```
Input:  node, dissolution_target, existing_labels, clear_signal_loader: bool
Output: {clusters_reparented, meta_patterns_merged}
```

Handles: reparent clusters, reparent any direct optimizations, merge meta-patterns (UPDATE not DELETE), archive node, clear all 4 indices, clear DomainResolver, optionally clear DomainSignalLoader (domain-level only), discard from existing_labels.

### Parameterized Thresholds

| Parameter | Domain | Sub-Domain |
|-----------|--------|-----------|
| Creation consistency | 60% fixed (`DOMAIN_DISCOVERY_CONSISTENCY`) | `max(40%, 60% - 0.4% * members)` adaptive |
| Dissolution floor | `DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` = 0.15 | `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` = 0.25 |
| Consistency signal | Source 1 only (domain_raw primary label) | Three-source cascade (domain_raw qualifier + intent_label + TF-IDF) |
| Age gate | `DOMAIN_DISSOLUTION_MIN_AGE_HOURS` = 48 | `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS` = 6 |
| Member ceiling | `DOMAIN_DISSOLUTION_MEMBER_CEILING` = 5 (must be ≤ this AND below consistency floor) | None |
| Sub-node anchor | Cannot dissolve while sub-domains exist | N/A |
| "general" protection | Permanent — never dissolves | N/A |
| Dissolution target | "general" domain node | Parent domain node |
| Signal loader cleanup | Yes — remove domain from `DomainSignalLoader._signals` | No |

### Hysteresis Gaps (prevent flip-flop)

| Level | Creation | Dissolution | Gap |
|-------|----------|-------------|-----|
| Domain | 60% | 15% | 45 points |
| Sub-domain | 40-60% | 25% | 15-35 points |

---

## Domain Dissolution Rules

**ALL prerequisites must be true for dissolution:**

1. **Not "general"** — permanent structural root (all per-project "general" nodes are protected, including empty ones)
2. **No surviving sub-domains** — bottom-up dependency. Sub-domains dissolve first through their own re-evaluation. If any sub-domain passes its consistency check, the parent domain is structurally anchored and cannot dissolve. The anchor check queries the current DB session AFTER sub-domain dissolution has run (both execute in the same `phase_discover` session, so uncommitted sub-domain archival is visible via SQLAlchemy's identity map).
3. **Age ≥ 48 hours** — domains earn permanence through time
4. **Consistency below 15%** — using Source 1 only (domain_raw primary label count / total optimizations). Well below the 60% creation threshold.
5. **Member count ≤ 5** (`DOMAIN_DISSOLUTION_MEMBER_CEILING`) — `member_count` on the domain node represents direct child clusters (reconciled by Phase 0). Large domains don't dissolve on consistency alone. The member ceiling check fires AFTER the sub-domain anchor check — a domain with 3 direct clusters but a healthy sub-domain with 20 clusters is anchor-protected regardless.

**On dissolution (prompts never lost):**
- All child clusters reparented to "general" (keep optimizations + meta-patterns)
- Any optimizations with `cluster_id` pointing to the domain node reparented to "general" (defensive)
- Meta-patterns merged into "general" domain node (UPDATE, not DELETE)
- `DomainResolver.remove_label()` clears domain from resolution cache
- `DomainSignalLoader.remove_domain()` clears domain from classification signals and qualifier cache (new API)
- Domain node archived (`state="archived"`, zeroed metrics)
- Indices cleared (embedding, transformation, optimized, qualifier)
- Label freed for future re-discovery
- `dissolved_this_cycle` prevents same-cycle re-creation

**Classification-resolution mismatch after dissolution (intentional):**
When a domain like "backend" dissolves, the `DomainSignalLoader` no longer has its signals. BUT: if another project still has an active "backend" domain, `DomainSignalLoader.load()` picks up its signals (cross-project singleton). If NO project has "backend", new prompts' heuristic classification may still emit `domain_raw="backend"` (from the LLM's training data, not from DomainSignalLoader). The DomainResolver maps this to "general" (unknown domain label). These prompts accumulate under "general" with `domain_raw="backend"`, which triggers `_propose_domains()` to re-discover "backend" when consistency threshold is met. **This is intentional** — it's the organic re-discovery mechanism.

**Domain Groundhog Day prevention:**
Dissolved domains whose clusters retain strong `domain_raw` signals may be re-created on the next cycle by `_propose_domains()`. The `dissolved_this_cycle` set prevents same-cycle re-creation. Cross-cycle re-creation is acceptable and expected — the 48-hour age gate on dissolution ensures the re-created domain survives long enough to prove itself or accumulate real membership. The 45-point hysteresis gap (60% creation vs 15% dissolution) means a domain that re-creates at 60%+ consistency will not dissolve unless it drops below 15%, providing strong damping.

---

## Bootstrap Seed Removal

The Alembic migration `a1b2c3d4e5f6` seeded 7 developer-focused domain nodes (backend, frontend, database, devops, security, fullstack, data) with `source="seed"` in metadata. Per ADR-006, these are bootstrapping data, not architectural constraints.

**Changes:**
- Remove `source="seed"` protection from `_reevaluate_sub_domains()` — seed sub-domains subject to same lifecycle as organic ones
- Remove `source="seed"` protection from `phase_archive_empty_sub_domains()` — empty seed sub-domains can be archived
- New `_reevaluate_domains()` has no seed protection — seed domains dissolve when they fail consistency
- The Alembic migration stays (historical) — its seed data becomes expendable

**Cold-start behavior for new deployments:**
1. Migration seeds 7 developer domain nodes → first prompts classify immediately (fast cold-start)
2. If user's prompts match the seeds → domains survive and grow organically
3. If user's prompts DON'T match (comic book artist) → unused seed domains dissolve after 48 hours with ≤5 members
4. User's organic domains emerge from "general" → clean taxonomy with no developer bias

---

## Phase 5 Execution Order

```
Phase 5 (discover) execution order:

  1. Vocabulary generation pass
     - ALL domains including "general"
     - Generates/refreshes organic Haiku vocabulary
     - Extracted as standalone _generate_domain_vocabularies()

  2. Sub-domain re-evaluation (bottom-up)
     - For each non-general domain with existing sub-domains
     - Dissolve sub-domains below 25% consistency
     - Three-source cascade for consistency check

  3. Domain re-evaluation
     - For each non-general domain (including seeds)
     - Skip if domain has surviving sub-domains (anchor rule — queries
       current session state AFTER step 2 dissolution, within same DB session)
     - Skip if "general" (permanent)
     - Dissolve domains below 15% consistency AND ≤5 members AND ≥48h old
     - Uses Source 1 only (domain_raw primary label)

  4. Domain discovery
     - Scan "general" children for new domain candidates
     - Create domain when ≥3 members, ≥0.3 coherence, ≥60% consistency
     - Uses Source 1 only (domain_raw primary label — existing behavior)
     - Post-discovery re-parenting sweep (existing behavior)

  5. Sub-domain discovery
     - Scan each non-general domain for new sub-domain candidates
     - Three-source cascade with adaptive threshold
     - Label dedup + flip-flop prevention

  6. Existing post-discovery operations (PRESERVED, not new)
     - _detect_domain_candidates() — risk monitoring
     - _monitor_general_health() — general domain health check
     - _check_signal_staleness() — signal freshness
     - _suggest_domain_archival() — archival suggestions
     - verify_domain_tree_integrity() + _repair_tree_violations()
```

Steps 2→3: bottom-up dependency — sub-domains dissolve first, then parent is eligible.
Steps 4→5: creation order — domains form from "general" before sub-domains form within them.
Step 6: existing operations retained exactly as-is — the refactor adds steps 2-3, not removes existing steps.

---

## Structural Refactor

### Current code structure (separate, asymmetric):
- `_propose_domains()` — domain creation only, `domain_raw` primary label only, no re-evaluation
- `_propose_sub_domains()` — vocab gen + sub-domain creation + re-evaluation + dissolution, three-source cascade
- `_reevaluate_sub_domains()` — sub-domain consistency check + dissolution (inline logic)

### New code structure (unified, symmetric):

| Method | Responsibility |
|--------|---------------|
| `_generate_domain_vocabularies()` | **Extracted** from `_propose_sub_domains()` — generates/refreshes organic vocabulary for ALL domains. Called first in Phase 5. |
| `_dissolve_node()` | **Shared core** — reparent clusters + direct optimizations, merge meta-patterns, archive node, clear all 4 indices, clear resolver. Parameterized: `dissolution_target` (parent domain or "general"), `clear_signal_loader` (True for domains, False for sub-domains). Returns `{clusters_reparented, meta_patterns_merged}`. |
| `_reevaluate_domains()` | Domain-specific wrapper — iterates non-general domains, checks "general" protection + sub-domain anchor + member ceiling + age gate + Source 1 consistency, calls `_dissolve_node()`. |
| `_reevaluate_sub_domains()` | **Existing** — refactored to call `_dissolve_node()` instead of inline dissolution logic. Still uses three-source cascade for consistency. |
| `_propose_domains()` | **Existing** — domain creation from "general". No signal source changes (Source 1 is correct for domain-level discovery). |
| `_propose_sub_domains()` | **Existing** — sub-domain creation. Vocab generation extracted out. Still uses three-source cascade. |

### DomainSignalLoader API addition

Add `remove_domain(label: str)` method:
- Removes from `_signals` (keyword weights)
- Removes from `_patterns` (compiled regexes for that domain's keywords)
- Removes from `_qualifier_cache` (organic vocabulary)
- Invalidates `_qualifier_embedding_cache` (embeddings may reference removed keywords)

This is called by `_dissolve_node()` when `clear_signal_loader=True` (domain dissolution only).

### Implementation note: qualifier_index behavior change

The current sub-domain dissolution code clears 3 indices (embedding, transformation, optimized) but NOT `qualifier_index`. The new `_dissolve_node()` clears all 4 indices. This is a behavior fix — sub-domain dissolution will now also clean up qualifier index entries, preventing stale qualifier vectors from persisting for archived sub-domains.

---

## Observability

Full upstream (decision events) and downstream (outcome events) observability so every lifecycle decision can be traced and validated.

### Upstream Events (decision inputs — what the system evaluated)

| Path | Op | Decision | Context | When |
|------|----|----------|---------|------|
| `warm` | `discover` | `domain_reevaluated` | `{domain, consistency_pct, floor_pct, member_count, member_ceiling, has_sub_domains, source: "domain_raw", total_opts, matching_opts, passed: bool}` | Every domain re-evaluation |
| `warm` | `discover` | `domain_dissolution_blocked` | `{domain, reason: "has_sub_domains"\|"too_young"\|"above_member_ceiling"\|"is_general", sub_domain_count?, age_hours?, member_count?}` | When dissolution prerequisites fail |
| `warm` | `discover` | `domain_dissolution_eligible` | `{domain, consistency_pct, floor_pct, member_count, age_hours}` | When all prerequisites pass, immediately before dissolution |

### Downstream Events (outcomes — what the system did)

| Path | Op | Decision | Context | When |
|------|----|----------|---------|------|
| `warm` | `discover` | `domain_dissolved` | `{domain, consistency_pct, floor_pct, clusters_reparented, meta_patterns_merged, optimizations_reparented, indices_cleared: int, resolver_cleared: bool, loader_cleared: bool, reason}` | After successful dissolution |
| `warm` | `discover` | `domain_dissolution_failed` | `{domain, error, stage: "reparent"\|"merge_patterns"\|"archive"\|"clear_indices"}` | On transient failure during dissolution |
| `warm` | `discover` | `dissolve_node_reparented` | `{node_label, node_type: "domain"\|"sub_domain", clusters_moved: int, target_label, meta_patterns_merged: int}` | Per-node dissolution outcome from shared `_dissolve_node()` |

### Existing events retained (unchanged)

- `sub_domain_reevaluated`, `sub_domain_dissolved` — sub-domain lifecycle
- `domain_created`, `sub_domain_created` — discovery
- `vocab_generated`, `vocab_refreshed`, `vocab_fallback_to_cache` — vocabulary lifecycle
- `sub_domain_domain_reevaluated` — sub-domain anchor check
- `sub_domain_qualifier_eval` — per-qualifier evaluation during discovery

### Health Endpoint

Extend health response with domain lifecycle stats (cumulative per process lifetime):

```python
"domain_lifecycle": {
    "domains_reevaluated": int,      # total domain re-evaluations run
    "domains_dissolved": int,        # total domains dissolved
    "domains_dissolution_blocked": int,  # blocked by anchor/age/member/general
    "seeds_remaining": int,          # seed domains still alive (tracks ADR-006 progress)
    "last_domain_reeval": str | None,  # ISO 8601 timestamp of last re-evaluation cycle
}
```

### Logging (structured, per-operation)

Each lifecycle operation logs at the appropriate level:

| Operation | Level | Message Pattern |
|-----------|-------|----------------|
| Domain re-evaluation starts | DEBUG | `"Re-evaluating domain '%s': %d members, %d sub-domains"` |
| Domain passes re-evaluation | DEBUG | `"Domain '%s' healthy: consistency=%.1f%% >= floor=%.1f%%"` |
| Domain dissolution blocked | INFO | `"Domain '%s' dissolution blocked: %s"` |
| Domain dissolution starts | INFO | `"Dissolving domain '%s': consistency=%.1f%% < floor=%.1f%%, %d clusters"` |
| Domain dissolution complete | INFO | `"Dissolved domain '%s': %d clusters reparented, %d patterns merged"` |
| Domain dissolution failed | WARNING | `"Domain dissolution failed for '%s' at stage '%s': %s"` |
| `_dissolve_node()` per-cluster reparent | DEBUG | `"Reparented cluster '%s' from '%s' to '%s'"` |
| `_dissolve_node()` meta-pattern merge | DEBUG | `"Merged %d meta-patterns from '%s' to '%s'"` |
| Signal loader domain removed | INFO | `"DomainSignalLoader: removed domain '%s' (signals=%d, qualifiers=%d)"` |
| Phase 5 cycle summary | INFO | `"Phase 5: domains_reevaluated=%d dissolved=%d blocked=%d created=%d"` |

### Error Handling

| Component | Failure Mode | Handling | Retry |
|-----------|-------------|----------|-------|
| `_dissolve_node()` reparenting | Individual cluster reparent fails | WARNING log + `domain_dissolution_failed` event, continue with remaining clusters | Same cycle (continues) |
| `_dissolve_node()` meta-pattern merge | UPDATE fails | WARNING log, continue (patterns remain on archived node — not lost) | Next cycle via `_maintenance_pending` |
| `_dissolve_node()` index clearing | remove() fails | WARNING log, continue (stale entry harmless, cleared on next rebuild) | Cold path |
| `_dissolve_node()` signal loader removal | `remove_domain()` fails | WARNING log, continue (stale signals cleared on next `load()`) | Next startup |
| Domain re-evaluation loop | Single domain fails | Log + continue to next domain (no cascade failure) | Next cycle |
| Sub-domain anchor check | DB error counting sub-domains | Assume sub-domains exist (safe side — skip dissolution), WARNING log | Next cycle |
| Phase 5 overall | `phase_discover()` raises | Caught by `execute_maintenance_phases()` try/except, `_maintenance_pending = True` | Next cycle |

**Principle:** Same as all taxonomy lifecycle — best-effort, never crashes the warm path, never loses data. Every failure is logged with enough context to diagnose: the operation that failed, the node involved, the error message, and which stage of dissolution was reached before failure.

---

## Invariants

- "general" never dissolves — permanent structural root per project (including empty "general" nodes)
- Prompts are never lost — dissolution reparents clusters (with all optimizations) and merges meta-patterns
- Bottom-up only — sub-domains dissolve before their parent domain can dissolve
- Flip-flop prevention — `dissolved_this_cycle` blocks same-cycle re-creation at both levels
- Hysteresis — creation thresholds are significantly higher than dissolution floors at both levels
- Domain dissolution requires BOTH low consistency AND low member count — prevents catastrophic churn for large domains
- `DomainResolver` and `DomainSignalLoader` caches cleared on domain dissolution — new prompts don't resolve to archived domains
- Classification-resolution mismatch after domain dissolution is intentional — enables organic re-discovery

---

## Changes

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/engine.py` | Extract `_generate_domain_vocabularies()`. Add `_dissolve_node()` shared method. Add `_reevaluate_domains()`. Refactor `_reevaluate_sub_domains()` to use `_dissolve_node()`. Remove `source="seed"` protection. |
| `backend/app/services/taxonomy/_constants.py` | Add `DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR = 0.15`, `DOMAIN_DISSOLUTION_MIN_AGE_HOURS = 48`, `DOMAIN_DISSOLUTION_MEMBER_CEILING = 5` |
| `backend/app/services/taxonomy/warm_phases.py` | Update `phase_discover()` execution order: vocab gen → sub-domain reeval → domain reeval → domain discovery → sub-domain discovery. Remove `source="seed"` protection from `phase_archive_empty_sub_domains()`. |
| `backend/app/services/domain_signal_loader.py` | Add `remove_domain(label)` method (clears signals, patterns, qualifier cache, embedding cache for that domain) |
| `backend/app/routers/health.py` | Add `domain_lifecycle` stats field |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Add domain dissolution tests, seed dissolution tests, update seed protection tests |

---

## Testing Strategy

### Unit Tests
- `_dissolve_node()` — reparenting to "general", reparenting to parent domain, meta-pattern merge, index cleanup, resolver clearing, signal loader clearing
- `_reevaluate_domains()` — sub-domain anchor blocks dissolution, member ceiling blocks dissolution, age gate blocks dissolution, "general" never dissolves, Source 1 consistency check
- `DomainSignalLoader.remove_domain()` — signals removed, patterns cleared, qualifier cache cleared

### Integration Tests
- Full Phase 5 cycle: sub-domain dissolves → parent domain now eligible → domain dissolves → clusters in "general"
- Seed domain dissolution: empty seed domain archived after 48h with ≤5 members
- Seed domain survival: seed domain with active prompts survives indefinitely
- Large domain protection: domain with 30 clusters and low consistency does NOT dissolve (member ceiling)
- Bottom-up cascade: domain with healthy sub-domain is anchored even with low parent consistency
- Flip-flop prevention: dissolved domain not re-created in same cycle
- **Domain Groundhog Day:** dissolved domain's clusters in "general" → re-discovered next cycle → verify 48h age gate prevents immediate re-dissolution
- **Mass dissolution:** ALL seed domains dissolve (fresh install, no matching prompts after 48h) → all clusters in "general" → system operates correctly with general-only taxonomy

### Regression Tests
- Existing sub-domain lifecycle tests pass (refactored to use shared `_dissolve_node()`)
- Existing domain discovery tests pass (`_propose_domains()` unchanged)
- "general" domain never dissolved regardless of content
