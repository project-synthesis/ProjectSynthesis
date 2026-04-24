# Heuristic Analyzer Refresh

> Audit and improvement plan for the zero-LLM classifier that drives enrichment profile selection, strategy intelligence queries, and curated retrieval gating. Spawned from the [Context Injection Use-Case Matrix](context-injection-use-case-matrix.md) consolidation work (2026-04-12).

**Implementation status (updated 2026-04-24):** Tier 1 Quick Wins all shipped in v0.3.30 as the A1/A2/A3/A4 + B1/B2 accuracy pipeline. Subsequent refinements (negation-aware weakness detection, `_compute_structural_density()`, intent-label strategy override, classifier B1/B2/B6 noun coverage + rescue path, `_STATIC_SINGLE_SIGNALS` preservation, audit-verb signals) landed across v0.3.30 → v0.4.2. Remaining items below are flagged per-section with current status. The live architecture is described in `backend/CLAUDE.md` under Analysis services — treat this document as the original design record, not the source of truth for current behavior.

## Problem Statement

The heuristic analyzer (`backend/app/services/heuristic_analyzer.py`) is the first classification step in the enrichment pipeline. Every downstream layer depends on its `task_type` and `domain` output:

- **Enrichment profiles** (Phase 4) select which context layers to activate
- **Strategy intelligence** (Phase 2) queries historical performance by `task_type + domain`
- **Curated retrieval gating** (Phase 1) skips codebase context for non-coding task types
- **Pattern injection** uses domain for cross-domain filtering
- **Few-shot retrieval** filters by task type for relevant examples

A misclassification cascades: wrong task type means wrong enrichment profile, wrong strategy rankings (or none), wrong curated retrieval decision, and wrong few-shot pool. The heuristic is the bottleneck for the entire enrichment pipeline's effectiveness.

## Observed Failures

### 1. Keyword Collisions — Ambiguous Single-Word Signals

| Prompt | Heuristic Classification | LLM Classification | Root Cause |
|--------|-------------------------|--------------------|-----------| 
| "Design a webhook delivery system with retry logic..." | `creative` + `general` | `coding` + `backend` | "Design" triggers creative (weight 0.7) — no disambiguation between "design a system" vs "design a logo" |
| "Create a caching layer for the API..." | Biased toward `creative` | `coding` + `backend` | "Create" has 0.5 creative weight |
| "Build a dashboard showing real-time metrics..." | May lean `coding` | Depends on context | "Build" is 0.7 coding but "dashboard" + "metrics" could be analysis |

**Impact**: The webhook prompt got `strategy_intel=none` during E2E testing because `creative+general` had no historical strategy data. The actual `coding+backend` combination had 48 samples across 4 strategies.

### 2. No Bigram/Trigram Awareness

The analyzer tokenizes into single words and scores each independently. It cannot distinguish:

| Phrase | Intended Classification | Why Single-Word Fails |
|--------|------------------------|-----------------------|
| "Design a system" | coding | "Design" alone → creative |
| "Design a campaign" | writing/creative | "Design" alone → creative (accidentally correct) |
| "Create a migration" | coding | "Create" alone → creative |
| "Generate a report" | analysis/data | "Generate" alone → creative |
| "Build a brand" | creative/writing | "Build" alone → coding |

### 3. Static Domain Signals

`DomainSignalLoader` reads keyword signals from domain metadata in the DB. As the taxonomy engine organically discovers new domains (currently 10, ceiling 30), those new domains get zero heuristic keyword support until someone manually adds signals.

| Domain | Keyword Signals | Discovery Method |
|--------|----------------|------------------|
| backend | 10 keywords (api, endpoint, server...) | Pre-seeded |
| frontend | 9 keywords (react, svelte, css...) | Pre-seeded |
| database | 9 keywords (sql, migration, schema...) | Pre-seeded |
| devops | 8 keywords (docker, kubernetes...) | Pre-seeded |
| security | 10 keywords (auth, encryption...) | Pre-seeded |
| New organic domains | 0 keywords | Taxonomy discovery — no heuristic support |

### 4. Positional Boost Insufficient

The analyzer applies a 2x multiplier when a keyword appears in the first sentence. This helps for prompts like "Implement a REST API..." (coding keyword in position 1) but doesn't help when the misleading keyword IS in position 1 ("Design a webhook...").

### 5. Prompt–Context Divergence (Undetected)

When the raw prompt explicitly names a technology that **conflicts** with the linked codebase's actual stack, the pipeline has no detection or reconciliation mechanism. The optimizer receives both signals and silently picks one (usually the prompt's explicit mention), producing output that may be architecturally wrong.

**Observed failure (2026-04-12 E2E testing):**

| Prompt Says | Codebase Context Says | Optimizer Did | Correct Behavior |
|-------------|----------------------|---------------|------------------|
| "Add row-level security to our **PostgreSQL** schema" | Project uses **SQLite/aiosqlite** (from explore synthesis + curated retrieval) | Produced PostgreSQL-specific RLS policies with `SET app.current_tenant_id` | Should flag the conflict: "Prompt specifies PostgreSQL but codebase uses SQLite. Is this a planned migration, or should the solution target SQLite?" |

**Why this matters:** The user pays for codebase context (synthesis + curated retrieval consumed 86K chars of context window) but the optimizer ignores it when the prompt contradicts it. The context becomes expensive dead weight.

**Divergence categories:**

| Category | Example | Expected Handling |
|----------|---------|-------------------|
| **Tech stack mismatch** | Prompt says PostgreSQL, codebase is SQLite | Flag conflict, ask if this is a migration or an error |
| **Framework mismatch** | Prompt says Django, codebase is FastAPI | Flag conflict, suggest FastAPI-native approach |
| **New addition** | Prompt says "add Redis caching", codebase has no Redis | Legitimate — treat as new dependency, note it in changes |
| **Upgrade/migration** | Prompt says "migrate to PostgreSQL", codebase is SQLite | Legitimate — optimize for the migration scenario |
| **Language mismatch** | Prompt says Java, codebase is Python | Flag conflict — likely wrong repo context |

**Key distinction:** Not all divergences are errors. "Add Redis" is an addition. "Migrate to PostgreSQL" is intentional. "Add RLS to our PostgreSQL" when the codebase is SQLite is ambiguous — the optimizer should surface the ambiguity rather than silently picking a side.

**Current gap:** Zero infrastructure for this. The enrichment pipeline resolves codebase context and the prompt independently, then hands both to the LLM with no reconciliation layer. The LLM may or may not notice the conflict depending on attention, context window position, and prompt length.

## Proposed Improvements

### Tier 1: Quick Wins (No Architecture Change)

#### 1a. Compound Keyword Signals

Add multi-word signals that score higher than their individual words:

```python
_COMPOUND_SIGNALS = {
    "coding": [
        ("design a system", 1.2), ("design a service", 1.2),
        ("design a schema", 1.1), ("build a service", 1.1),
        ("create a migration", 1.0), ("create an endpoint", 1.0),
        ("implement a", 1.0), ("refactor the", 1.0),
    ],
    "writing": [
        ("write a blog", 1.2), ("draft an email", 1.2),
        ("design a campaign", 1.0), ("create content", 1.0),
    ],
    "analysis": [
        ("generate a report", 1.1), ("build a dashboard", 0.9),
        ("analyze the data", 1.1),
    ],
}
```

Compound signals checked BEFORE single-word signals. If a compound match fires, its category gets a bonus that overrides the single-word collision.

**Files**: `heuristic_analyzer.py` (add `_COMPOUND_SIGNALS` dict, check in `_score_category()`)
**Risk**: Low — additive, no existing behavior changed
**Test**: Add test cases for the observed failures

#### 1b. Domain Signal Auto-Enrichment from Taxonomy

When the taxonomy engine discovers a new domain or labels a cluster, extract the top keywords from that domain's member prompts and register them as domain signals automatically.

```python
# In warm_phases.py, after domain discovery:
keywords = extract_domain_keywords(domain_members, top_k=8)
signal_loader.register_domain(domain_label, keywords)
```

**Files**: `taxonomy/warm_phases.py`, `domain_signal_loader.py`
**Risk**: Medium — auto-generated signals could be noisy
**Mitigation**: Require minimum 5 members and 0.4 coherence before extracting keywords

#### 1c. Technical Verb Disambiguation

Maintain a set of "technical verbs" that, when followed by technical nouns, boost `coding` regardless of the verb's default category:

```python
_TECHNICAL_VERBS = {"design", "create", "build", "set up", "configure"}
_TECHNICAL_NOUNS = {"system", "service", "api", "endpoint", "schema", "database",
                    "middleware", "pipeline", "queue", "cache", "scheduler"}
```

If a technical verb + technical noun appear in the same sentence, apply a coding boost.

**Files**: `heuristic_analyzer.py` (add to `_analyze_inner()` after initial classification)
**Risk**: Low — additive post-classification adjustment

#### 1d. Prompt–Context Divergence Detection

Compare technology keywords in the raw prompt against the codebase context (explore synthesis + workspace guidance) to detect conflicts before the optimizer phase.

**Implementation:**

```python
# In context_enrichment.py, after codebase context + heuristic analysis resolve:

def detect_divergence(raw_prompt: str, codebase_context: str | None) -> list[Divergence]:
    """Compare prompt tech mentions against codebase stack."""
    prompt_techs = extract_tech_mentions(raw_prompt)    # {"postgresql", "redis"}
    codebase_techs = extract_tech_mentions(codebase_context)  # {"sqlite", "aiosqlite", "fastapi"}
    
    divergences = []
    for tech in prompt_techs:
        if tech not in codebase_techs:
            # Check if it's a known alternative to something in the codebase
            conflict = find_conflict(tech, codebase_techs)  # postgresql ↔ sqlite
            if conflict:
                divergences.append(Divergence(
                    prompt_tech=tech, codebase_tech=conflict,
                    category="stack_mismatch",  # vs "new_addition" vs "migration"
                ))
    return divergences
```

**Conflict detection rules:**
- Database engines: `{postgresql, mysql, sqlite, mongodb}` — mutually exclusive unless "migrate" keyword present
- Frameworks: `{fastapi, django, flask, express}` — mutually exclusive
- Languages: `{python, javascript, typescript, java, go, rust}` — mutually exclusive
- Additions (no conflict): `{redis, celery, rabbitmq, docker}` — complementary, not alternatives

**Output:** Inject divergence warnings into the optimizer template as a `<divergence-alert>` section. The optimizer must acknowledge the conflict in its changes summary — either "addressed as migration" or "corrected to match codebase."

**Trace:** Store divergences in `enrichment_meta["divergences"]` for UI display in the ENRICHMENT panel.

**Files**: `context_enrichment.py` (new `_detect_divergences()` method), `prompts/optimize.md` (new `{{divergence_alerts}}` variable)
**Risk**: Medium — needs a curated tech taxonomy to avoid false positives
**Priority**: **P1** — directly impacts output correctness for codebase-aware optimizations

### Tier 2: Structural Improvements (Moderate Architecture Change)

#### 2a. Confidence-Gated Fallback to LLM Classification

When heuristic confidence is below a threshold (e.g., < 0.5), and the top two categories are within 0.2 points of each other, defer to a fast LLM call (Haiku) for classification only. This would catch the ambiguous cases while keeping the zero-LLM path for clear-cut prompts.

**Cost**: ~0.1s + ~500 tokens per ambiguous prompt (estimated 15-20% of prompts)
**Files**: `heuristic_analyzer.py`, `context_enrichment.py`
**Risk**: Medium — adds LLM dependency to the enrichment hot path

#### 2b. Feedback-Driven Signal Weight Learning

When the LLM analyzer later classifies the prompt (in the analyze phase), compare its classification with the heuristic's. If they disagree, adjust the keyword weights toward the LLM's classification over time.

```python
# After LLM analysis completes:
if llm_task_type != heuristic_task_type:
    signal_adjuster.record_disagreement(
        prompt_tokens, heuristic_task_type, llm_task_type
    )
    # Periodically: reduce weight of misleading keywords, boost missing ones
```

**Files**: New `signal_adjuster.py`, `heuristic_analyzer.py`, `pipeline.py`
**Risk**: Medium-high — requires careful weight decay to avoid oscillation

### Tier 3: Architecture Evolution (ADR-Level Change)

#### 3a. Semantic Classification via Embedding Nearest-Neighbor

Instead of keyword matching, embed the prompt and find the nearest cluster centroid. The cluster's task_type distribution becomes the classification. This leverages the taxonomy engine's organic learning.

**Pros**: Automatically improves as taxonomy grows, no manual keyword curation
**Cons**: Requires populated taxonomy (cold-start problem), embedding latency (~50ms)
**Files**: New classifier, `context_enrichment.py`

#### 3b. Two-Phase Classification

Split heuristic analysis into:
1. **Fast phase** (current keywords, ~1ms): used for curated retrieval gating
2. **Enriched phase** (after curated retrieval returns): re-classify with codebase context for strategy intelligence and pattern injection

This acknowledges that early classification is inherently lower quality and avoids cascading misclassification errors.

## Implementation Priority

| Improvement | Impact | Effort | Dependencies | Priority |
|-------------|--------|--------|--------------|----------|
| 1a. Compound keywords | High — fixes observed failures | 1 day | None | **P0** |
| 1c. Technical verb disambiguation | High — prevents "Design a system" misclass | 0.5 day | None | **P0** |
| 1d. Prompt–context divergence detection | **Critical** — prevents wrong-stack output | 2 days | Codebase context resolved first | **P1** |
| 1b. Domain signal auto-enrichment | Medium — scales with taxonomy | 2 days | Warm-path integration | P1 |
| 2a. Confidence-gated LLM fallback | High — catches all edge cases | 2 days | Haiku availability | P1 |
| 2b. Feedback-driven weight learning | Medium — self-improving | 3 days | LLM+heuristic disagreement tracking | P2 |
| 3a. Semantic classification | High — eliminates keywords entirely | 1 week | Populated taxonomy | P3 |
| 3b. Two-phase classification | Medium — decouples early/late | 3 days | Enrichment refactor | P3 |

## Metrics

To track improvement, measure:

| Metric | Source | Baseline |
|--------|--------|----------|
| Heuristic vs LLM task_type agreement rate | Compare `analysis.task_type` (heuristic) with LLM `AnalysisResult.task_type` | Unknown — instrument first |
| Heuristic vs LLM domain agreement rate | Same comparison for domain | Unknown |
| Strategy intelligence hit rate | `strategy_intel=yes` / total enrichments | Currently ~50% (rough estimate from E2E testing) |
| Misclassification-induced empty enrichment | Prompts where heuristic says non-coding but LLM says coding | Unknown |

## Implementation Status (v0.3.30)

All Tier 1 and most Tier 2 items shipped in v0.3.30:

| Item | Spec ID | Status | Implementation |
|------|---------|--------|----------------|
| Compound keyword signals | A1 / Tier 1a | **Shipped** | `_TASK_TYPE_SIGNALS` with multi-word entries |
| Technical verb disambiguation | A2 / Tier 1c | **Shipped** | `_check_technical_disambiguation()` in `heuristic_analyzer.py` |
| Domain signal auto-enrichment | A3 / Tier 1b | **Shipped** | `domain_signal_extractor.py` + warm-path hooks |
| Confidence-gated LLM fallback | A4 / Tier 2a | **Shipped** | `_classify_with_llm()` with `enable_llm_classification_fallback` preference |
| Classification agreement tracking | E1 | **Shipped** | `classification_agreement.py` singleton |
| Semantic embedding nearest-neighbor | Tier 3a | Deferred | Requires architecture evaluation |

Full tracking: [`docs/enrichment-consolidation-action-items.md`](enrichment-consolidation-action-items.md)

## Relationship to Context Injection Matrix

This document is a dependency of the [Context Injection Use-Case Matrix](context-injection-use-case-matrix.md):

- **Phase 1** (Task-Gated Curated Retrieval) gates on `task_type` — shipped
- **Phase 2** (Strategy Intelligence) queries by `task_type + domain` — shipped with C1 domain-relaxed fallback
- **Phase 4** (Enrichment Profiles) selects profile based on `task_type` — shipped

## Files Reference

| File | Role | Status |
|------|------|--------|
| `backend/app/services/heuristic_analyzer.py` | 6-layer classifier with A1-A4 | Shipped |
| `backend/app/services/domain_signal_loader.py` | Domain keyword signal registry | Shipped (+ `register_signals()`) |
| `backend/app/services/domain_signal_extractor.py` | TF-IDF keyword extraction (A3) | Shipped |
| `backend/app/services/context_enrichment.py` | Enrichment orchestrator | Shipped (profiles + divergence + strategy intel) |
| `backend/app/services/classification_agreement.py` | E1 agreement tracking | Shipped |
| `backend/app/services/taxonomy/warm_phases.py` | A3 domain signal hooks | Shipped |
| `backend/tests/test_heuristic_analyzer.py` | 62 tests for classifier | Shipped |
