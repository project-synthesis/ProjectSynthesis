# Unified Taxonomy Lifecycle: Domain + Sub-Domain Discovery and Dissolution

**Goal:** Unify domain and sub-domain lifecycle into a shared organic system where both levels use the same three-source signal cascade, organic vocabulary, re-evaluation, and graceful dissolution. Remove seed domain protection so the taxonomy is fully organic from day one. Aligns with ADR-006 (Universal Prompt Engine).

**Problem:** Domain discovery and sub-domain discovery are currently separate mechanisms with different signal sources, different lifecycle rules, and asymmetric behavior. Domains are created from "general" using only `domain_raw` primary labels, never re-evaluated, never dissolved, and seed domains are permanently protected. This means:
- A comic book artist's taxonomy permanently shows "backend", "devops", "security" even with zero matching prompts
- A domain that was auto-discovered but never gained traction lives forever
- Domain classification errors are permanent — misclassified prompts stay in the wrong domain with no correction mechanism
- The domain lifecycle is fundamentally different from the sub-domain lifecycle, despite solving the same problem at different hierarchy levels

**Solution:** Extract a shared lifecycle core that handles both domain and sub-domain re-evaluation with parameterized thresholds. Add domain dissolution with bottom-up sub-domain anchoring. Remove seed domain protection. The only permanent node is "general" (structural root).

---

## Architecture

### Shared Lifecycle Core

Domain and sub-domain lifecycle are the **same algorithm at different hierarchy levels**: scan a parent's children, check qualifier consistency against a threshold, create or dissolve nodes based on signal strength.

**`_reevaluate_node()`** — shared method on `TaxonomyEngine` for both domain and sub-domain re-evaluation:

```
Input:  node, parent_node, vocabulary, thresholds, dissolution_target
Output: dissolved: bool
```

Uses the same three-source cascade for consistency checking:
- Source 1: `domain_raw` qualifier parse
- Source 2: `intent_label` keyword match against organic vocabulary
- Source 3: (sub-domains only) TF-IDF signal_keywords

### Parameterized Thresholds

| Parameter | Domain | Sub-Domain |
|-----------|--------|-----------|
| Creation consistency | 60% fixed (`DOMAIN_DISCOVERY_CONSISTENCY`) | `max(40%, 60% - 0.4% * members)` adaptive |
| Dissolution floor | `DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` = 0.15 | `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR` = 0.25 |
| Age gate | `DOMAIN_DISSOLUTION_MIN_AGE_HOURS` = 48 | `SUB_DOMAIN_DISSOLUTION_MIN_AGE_HOURS` = 6 |
| Member floor | `DOMAIN_DISSOLUTION_MAX_MEMBERS` = 5 (must be ≤ this AND below consistency floor) | None |
| Sub-node anchor | Cannot dissolve while sub-domains exist | N/A |
| "general" protection | Permanent — never dissolves | N/A |
| Dissolution target | "general" domain node | Parent domain node |

### Hysteresis Gaps (prevent flip-flop)

| Level | Creation | Dissolution | Gap |
|-------|----------|-------------|-----|
| Domain | 60% | 15% | 45 points |
| Sub-domain | 40-60% | 25% | 15-35 points |

---

## Domain Dissolution Rules

**ALL prerequisites must be true for dissolution:**

1. **Not "general"** — permanent structural root
2. **No surviving sub-domains** — bottom-up dependency. Sub-domains dissolve first through their own re-evaluation. If any sub-domain passes its consistency check, the parent domain is structurally anchored and cannot dissolve.
3. **Age ≥ 48 hours** — domains earn permanence through time
4. **Consistency below 15%** — using the three-source cascade (domain_raw + intent_label keyword match against organic vocabulary). Well below the 60% creation threshold.
5. **Member count ≤ 5** — large domains don't dissolve on consistency alone. A 30-cluster domain with a temporary consistency dip is experiencing vocabulary drift, not invalidity. The member floor prevents catastrophic churn.

**On dissolution (prompts never lost):**
- All child clusters reparented to "general" (keep optimizations + meta-patterns)
- Meta-patterns merged into "general" domain node (UPDATE, not DELETE)
- `DomainResolver.remove_label()` clears domain from resolution cache
- `DomainSignalLoader` qualifier cache cleared for the domain
- Domain node archived (`state="archived"`, zeroed metrics)
- Indices cleared (embedding, transformation, optimized, qualifier)
- Label freed for future re-discovery
- `dissolved_this_cycle` prevents same-cycle re-creation

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
     - Already implemented (separate pass before discovery)

  2. Sub-domain re-evaluation (bottom-up)
     - For each non-general domain with existing sub-domains
     - Dissolve sub-domains below 25% consistency
     - Three-source cascade for consistency check

  3. Domain re-evaluation
     - For each non-general domain (including seeds)
     - Skip if domain has surviving sub-domains (anchor rule)
     - Skip if "general" (permanent)
     - Dissolve domains below 15% consistency AND ≤5 members AND ≥48h old

  4. Domain discovery
     - Scan "general" children for new domain candidates
     - Create domain when ≥3 members, ≥0.3 coherence, ≥60% consistency
     - Post-discovery re-parenting sweep (existing behavior)

  5. Sub-domain discovery
     - Scan each non-general domain for new sub-domain candidates
     - Three-source cascade with adaptive threshold
     - Label dedup + flip-flop prevention
```

Steps 2→3: bottom-up dependency — sub-domains dissolve first, then parent is eligible.
Steps 4→5: creation order — domains form from "general" before sub-domains form within them.

---

## Structural Refactor

### Current code structure (separate, asymmetric):
- `_propose_domains()` — domain creation only, `domain_raw` primary label only, no re-evaluation
- `_propose_sub_domains()` — sub-domain creation + re-evaluation + dissolution, three-source cascade
- `_reevaluate_sub_domains()` — sub-domain consistency check + dissolution

### New code structure (unified, symmetric):

| Method | Responsibility |
|--------|---------------|
| `_reevaluate_node()` | **Shared core** — consistency check via three-source cascade, parameterized thresholds. Returns whether the node should dissolve. Used by both domain and sub-domain re-evaluation. |
| `_dissolve_node()` | **Shared core** — reparent clusters, merge meta-patterns, archive node, clear indices/caches. Parameterized by dissolution target (parent domain or "general"). Used by both. |
| `_reevaluate_domains()` | Domain-specific wrapper — iterates non-general domains, checks sub-domain anchor + member floor + age gate, calls `_reevaluate_node()` + `_dissolve_node()`. |
| `_reevaluate_sub_domains()` | **Existing** — refactored to call `_reevaluate_node()` + `_dissolve_node()` instead of inline logic. |
| `_propose_domains()` | **Existing** — domain creation from "general". Gains three-source cascade (currently domain_raw only). |
| `_propose_sub_domains()` | **Existing** — sub-domain creation. Already uses three-source cascade. |

### Code deduplication

The dissolution logic currently lives inline in `_reevaluate_sub_domains()` (~40 lines: reparent clusters, merge meta-patterns, archive node, clear indices, clear resolver, log event). The new `_dissolve_node()` extracts this into a shared method called by both domain and sub-domain dissolution.

The consistency check logic (three-source cascade: parse domain_raw → keyword match → TF-IDF) is currently in `_reevaluate_sub_domains()` and `_propose_sub_domains()`. The new `_reevaluate_node()` extracts the consistency calculation into a shared method.

---

## Domain Discovery Enhancement

`_propose_domains()` currently uses only `domain_raw` primary labels to discover new domains from "general". This should be enhanced to also use the three-source cascade for consistency, matching the sub-domain discovery approach:

- **Source 1 (primary — existing):** Parse `domain_raw` for primary domain label (e.g., "backend" from "backend: auth")
- **Source 2 (new):** Match `intent_label` against the "general" domain's organic vocabulary keywords
- **Source 3 (new):** Match `raw_prompt` against "general" domain's `signal_keywords` TF-IDF

This makes domain discovery symmetric with sub-domain discovery — both use the same signal quality.

---

## Observability

### Events

| Path | Op | Decision | Context |
|------|----|----------|---------|
| `warm` | `discover` | `domain_reevaluated` | `{domain, consistency_pct, floor_pct, member_count, member_floor, has_sub_domains, passed: bool}` |
| `warm` | `discover` | `domain_dissolved` | `{domain, consistency_pct, floor_pct, clusters_reparented, meta_patterns_merged, reason}` |
| `warm` | `discover` | `domain_dissolution_blocked` | `{domain, reason: "has_sub_domains"\|"too_young"\|"above_member_floor"\|"is_general", detail}` |

Existing events retained:
- `sub_domain_reevaluated`, `sub_domain_dissolved` — sub-domain lifecycle
- `domain_created`, `sub_domain_created` — discovery
- `vocab_generated`, `vocab_refreshed` — vocabulary lifecycle

### Health Endpoint

Extend health response with domain lifecycle stats:

```python
"domain_lifecycle": {
    "domains_reevaluated": int,
    "domains_dissolved": int,
    "seeds_remaining": int,
    "dissolution_blocked": int,
}
```

### Error Handling

| Component | Failure Mode | Handling |
|-----------|-------------|----------|
| `_reevaluate_node()` consistency check | DB error loading optimizations | WARNING log, skip node, continue |
| `_dissolve_node()` reparenting | Individual cluster reparent fails | Log, continue with remaining clusters |
| `_dissolve_node()` meta-pattern merge | UPDATE fails | WARNING log, continue (patterns remain on archived node — not lost) |
| Domain re-evaluation loop | Single domain fails | Log, continue to next domain (no cascade failure) |
| Sub-domain anchor check | DB error counting sub-domains | Assume sub-domains exist (safe side — skip dissolution) |

**Principle:** Same as all taxonomy lifecycle — best-effort, never crashes the warm path, never loses data. Failed dissolution is retried on next cycle via `_maintenance_pending`.

---

## Invariants

- "general" never dissolves — permanent structural root per project
- Prompts are never lost — dissolution reparents clusters (with all optimizations) and merges meta-patterns
- Bottom-up only — sub-domains dissolve before their parent domain can dissolve
- Flip-flop prevention — `dissolved_this_cycle` blocks same-cycle re-creation at both levels
- Hysteresis — creation thresholds are significantly higher than dissolution floors at both levels
- Domain dissolution requires BOTH low consistency AND low member count — prevents catastrophic churn for large domains
- `DomainResolver` and `DomainSignalLoader` caches cleared on dissolution — new prompts don't resolve to archived domains

---

## Changes

| File | Change |
|------|--------|
| `backend/app/services/taxonomy/engine.py` | Add `_reevaluate_node()`, `_dissolve_node()` shared methods. Add `_reevaluate_domains()`. Refactor `_reevaluate_sub_domains()` to use shared core. Enhance `_propose_domains()` with three-source cascade. Remove `source="seed"` protection. |
| `backend/app/services/taxonomy/_constants.py` | Add `DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR = 0.15`, `DOMAIN_DISSOLUTION_MIN_AGE_HOURS = 48`, `DOMAIN_DISSOLUTION_MAX_MEMBERS = 5` |
| `backend/app/services/taxonomy/warm_phases.py` | Update `phase_discover()` execution order: sub-domain reeval → domain reeval → domain discovery → vocab gen → sub-domain discovery. Remove `source="seed"` protection from `phase_archive_empty_sub_domains()`. |
| `backend/app/routers/health.py` | Add `domain_lifecycle` stats field |
| `backend/tests/taxonomy/test_sub_domain_lifecycle.py` | Add domain dissolution tests (anchor rule, member floor, age gate, seed dissolution, "general" protection). Update seed protection tests. |

---

## Testing Strategy

### Unit Tests
- `_reevaluate_node()` — consistent domain passes, inconsistent domain fails, three-source cascade matching
- `_dissolve_node()` — reparenting to target, meta-pattern merge, index cleanup, resolver/loader cache clearing
- `_reevaluate_domains()` — sub-domain anchor blocks dissolution, member floor blocks dissolution, age gate blocks dissolution, "general" never dissolves

### Integration Tests
- Full Phase 5 cycle: sub-domain dissolves → parent domain now eligible → domain dissolves → clusters in "general"
- Seed domain dissolution: empty seed domain archived after 48h
- Seed domain survival: seed domain with active prompts survives indefinitely
- Large domain protection: domain with 30 clusters and low consistency does NOT dissolve (member floor)
- Bottom-up cascade: domain with healthy sub-domain is anchored even with low parent consistency
- Flip-flop prevention: dissolved domain not re-created in same cycle

### Regression Tests
- Existing sub-domain lifecycle tests pass (refactored to use shared core)
- Existing domain discovery tests pass (`_propose_domains()` enhanced, not replaced)
- "general" domain never dissolved regardless of content
