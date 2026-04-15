# Sub-Domain Discovery Architecture

How sub-domains are organically discovered in the taxonomy engine.

## The Three-Source Signal Pipeline

Sub-domains form when a specialization signal crosses an adaptive consistency threshold within a domain. Three sources detect these signals, tried in priority order for each optimization:

```
                    Optimization arrives
                           |
                    +------v------+
                    |  Source 1   |  Parse qualifier from domain_raw
                    |  (Primary)  |  e.g., "backend: auth" -> "auth"
                    +------+------+
                           |
                      qualifier?
                     /          \
                   yes           no
                    |             |
                    |      +------v------+
                    |      |  Source 2   |  Match intent_label against
                    |      |  (Vocab)   |  qualifier vocabulary
                    |      +------+------+
                    |             |
                    |        qualifier?
                    |       /          \
                    |     yes           no
                    |      |             |
                    |      |      +------v------+
                    |      |      |  Source 3   |  Match raw_prompt against
                    |      |      |  (Dynamic)  |  signal_keywords from DB
                    |      |      +------+------+
                    |      |             |
                    +------+------+------+
                           |
                    qualifier or None
                           |
                    +------v------+
                    |   Count     |  Aggregate per domain:
                    |   & Gate    |  threshold = max(40%, 60% - 0.4%/member)
                    +------+------+  min 5 members, min 2 clusters
                           |
                    +------v------+
                    |  Create     |  Sub-domain node if gate passes
                    |  Sub-Domain |
                    +-------------+
```

## Vocabulary: The Three Tiers

Sub-domain discovery needs a **qualifier vocabulary** — a mapping of qualifier names to keyword groups (e.g., `"growth": ["metrics", "kpi", "dashboard", ...]`). This vocabulary tells the system what specializations to look for within a domain.

Three tiers provide vocabulary, tried in order:

```
Tier 1: Static vocabulary (_DOMAIN_QUALIFIERS)
  |  Hand-curated keyword groups for known domains
  |  Defined in heuristic_analyzer.py
  |  Covers: backend, frontend, devops, saas, database, security, fullstack, data
  |
  v  Not in static vocab?
Tier 2: LLM-generated vocabulary
  |  Haiku analyzes the domain's cluster labels and generates
  |  qualifier groups automatically. One LLM call per domain.
  |  Cached in cluster_metadata["generated_qualifiers"]
  |  Refreshed when cluster count changes by ≥30%
  |
  v  No LLM provider available?
Tier 3: Dynamic TF-IDF keywords (signal_keywords)
  |  Individual keywords from TF-IDF extraction, already
  |  stored on every domain node. No grouping — each keyword
  |  is a standalone qualifier. Weight ≥0.5 required.
  |  Works best for technical domains with specific jargon.
```

### How each tier works

**Tier 1 (Static)** provides the highest-quality qualifiers. Each entry groups 5-10 semantically related keywords under a qualifier name. These groups are curated for precision — "auth" includes "jwt", "token", "oauth", "session" because a human knows these are the same specialization. The static vocabulary also powers Source 1 enrichment: `_enrich_domain_qualifier()` in the heuristic analyzer scans prompts against the vocabulary and appends qualifiers to `domain_raw` (e.g., "backend" → "backend: auth"). This means future warm cycles have richer Source 1 signal.

**Tier 2 (LLM-generated)** bridges the gap for domains without static entries. When a domain first becomes eligible for sub-domain discovery and has no static vocabulary, the system sends its cluster labels to Haiku:

```
Input:  Domain "ci-cd" with clusters:
        - Ci/cd Testing Pipeline (5 members)
        - Integration Test Automation (1 member)
        - Kubernetes Microservices Deployment (2 members)
        - Production Deployment Documentation (3 members)

Output: {
          "testing": ["testing", "test", "automation", "coverage", "unit", ...],
          "deployment": ["deployment", "production", "release", "rollout", ...],
          "infrastructure": ["kubernetes", "container", "docker", "k8s", ...],
          ...
        }
```

The generated vocabulary is cached in `cluster_metadata["generated_qualifiers"]` and reused on subsequent warm cycles. It's refreshed when the domain's cluster count changes by ≥30% (meaning the domain's structure has shifted enough to warrant new groupings). This tier requires zero manual intervention — new domains that emerge organically get vocabulary automatically.

**Tier 3 (Dynamic TF-IDF)** is the final fallback. Every domain node has `signal_keywords` extracted by the warm path's TF-IDF pipeline. These are individual discriminative keywords (not grouped), and each one becomes a standalone qualifier. This works well for technical domains where keywords like "jwt" (0.9 weight) or "encryption" (1.0 weight) are strongly specific. It fails for business domains where keywords like "need" (0.23) and "product" (0.17) are too generic — the 0.5 weight threshold filters them out.

### Why business domains need vocabulary

The fundamental difference between technical and business domains:

| | Technical domains | Business domains |
|---|---|---|
| **Keywords** | Specialized jargon (jwt, kubernetes, graphql) | Shared business language (pricing, metrics, growth) |
| **TF-IDF** | High weights (0.8-1.0) — terms are domain-specific | Low weights (0.1-0.3) — terms cross many domains |
| **Source 3** | Works — individual keywords are discriminative | Fails — keywords are too generic globally |
| **Vocabulary needed?** | Optional (Tier 3 handles it) | Yes (Tier 1 or 2 required) |

The vocabulary provides two things that TF-IDF cannot:

1. **Semantic grouping**: "metrics", "kpi", "dashboard" are separate TF-IDF keywords but one qualifier group ("growth"). Without grouping, counts fragment across synonyms and never cross the threshold.

2. **Context-aware discrimination**: "pricing" appears in saas, backend, and frontend prompts globally (TF-IDF scores it low). But within saas, "pricing" concentrates in specific clusters while "onboarding" concentrates in others. The vocabulary says "when pricing appears in a saas prompt, it signals a saas specialization" — contextual discrimination that global TF-IDF cannot provide.

## The Signal Sources in Detail

### Source 1: domain_raw Qualifiers

When the heuristic analyzer classifies a prompt, it may emit a qualified domain like `"security: auth"`. The `parse_domain()` function extracts the qualifier. This works for ANY domain — no vocabulary needed.

**Coverage without enrichment**: ~4% of prompts (only when the LLM naturally produces a qualified classification).

**With enrichment**: `_enrich_domain_qualifier()` scans the prompt against the Tier 1 static vocabulary and appends a qualifier. Boosts coverage to ~30% for domains with static entries.

**Noise filtering**: LLM-generated qualifiers like "backend auth middleware" are validated against the known vocabulary (static + dynamic keywords). Unknown qualifiers are dropped to prevent count fragmentation.

### Source 2: intent_label Matching

Matches the 3-6 word intent label against qualifier vocabulary keyword groups. Works with any vocabulary tier (static, LLM-generated, or dynamic).

**Why intent_label and not raw_prompt**: The intent label is the most concentrated topic signal — 3-6 words summarizing what the prompt is about. A single keyword hit in the intent label is more meaningful than a keyword appearing somewhere in a 500-word prompt.

### Source 3: Dynamic TF-IDF Keywords

Matches the full raw_prompt against `signal_keywords` stored on the domain node. These are individual keywords, not grouped.

**Intent-label boost**: When a keyword appears in both the raw_prompt AND the intent_label, its effective weight gets +0.5. This prioritizes keywords that represent the core topic over keywords that merely appear in the prompt body.

**Adaptive hit threshold**: Keywords with weight ≥0.8 (strongly discriminative) need only 1 hit. Lower-weight keywords need 2+ hits to avoid noise.

## The Adaptive Threshold

Sub-domain creation requires crossing a consistency gate that scales with domain size:

```
threshold = max(40%, 60% - 0.4% per member)

 Members | Threshold | Meaning
---------|-----------|--------
      10 |      56%  | Small domain — need strong concentration
      20 |      52%  | Medium domain
      30 |      48%  | Getting larger — relax slightly
      50 |      40%  | Large domain — floor reached
     100 |      40%  | Very large — floor holds
```

The logic: small domains need a higher bar because a few outlier prompts can skew percentages. Large domains have enough data that 40% consistency (e.g., 40 of 100 prompts) represents genuine specialization.

### Additional gates

- **Minimum 5 optimizations** with the qualifier (statistical significance)
- **Minimum 2 distinct clusters** with the qualifier (prevents 1:1 wrapper sub-domains where a single cluster gets wrapped in a sub-domain node for no navigational value)
- **Domain must not already have sub-domains** (idempotency — prevents the churn cycle where HDBSCAN used to create duplicate sub-domains every warm cycle)

## Sub-Domain Readiness

At any point, you can see how close each domain is to forming sub-domains. This is what the readiness dashboard shows for the current taxonomy:

```
  ○ SAAS             71 prompts · 14 clusters · vocab: static
    pricing          ████████░░░░░░░░░░░░    17% / 40%  (12/71)  3 clusters  17 more to go
    growth           ███████░░░░░░░░░░░░░    15% / 40%  (11/71)  3 clusters  18 more to go
    investor         █████░░░░░░░░░░░░░░░    11% / 40%  (8/71)   5 clusters  21 more to go
    onboarding       ████░░░░░░░░░░░░░░░░    10% / 40%  (7/71)   2 clusters  22 more to go

  ○ FRONTEND         10 prompts · 8 clusters · vocab: static
    components       █████████████████░░░    50% / 56%  (5/10)   4 clusters  1 more to go

  ● SECURITY         14 prompts · 6 clusters
    ✓ jwt (2 clusters) — formed at 64.3% consistency
```

Each progress bar shows how much signal has accumulated toward the threshold. The "X more to go" count tells you how many prompts with that qualifier are needed before a sub-domain forms. This is the organic growth model — sub-domains emerge from accumulated evidence, not from a single prompt or a configuration change.

## Lifecycle

```
 Discovery            Archival              Re-discovery
 (Phase 5)            (Phase 5.5)           (Phase 5, next cycle)
     |                    |                       |
     v                    v                       v
 Signal scan  -->  0 children? Archive  -->  Signal still strong?
 Threshold?        1 child? Archive +       2+ clusters? Create
 2+ clusters?      reparent child           Stable qualifier label
     |
     v
 Create sub-domain
 Reparent clusters
 Set UMAP position
 Update resolver
```

Sub-domains are lightweight and expendable. They are archived when their children dissolve or get reassigned, and re-created when the signal re-emerges. The qualifier-based label ensures stability — "jwt" is always "jwt", unlike the old HDBSCAN labels that changed every cycle ("async-resilience-patterns" → "rate-limiting-&-retry-patterns" → "warm-path-taxonomy-concurrency").

## Observability

Every step of the pipeline is logged via `TaxonomyEventLogger.log_decision()`:

| Event | Decision | What it tells you |
|---|---|---|
| Domain scanned | `sub_domain_signal_scan` | Per-domain: total opts, qualifier counts, source breakdown (raw/intent/dynamic) |
| Vocabulary generated | `sub_domain_vocab_generated` | LLM created qualifier groups for a domain — shows group names and cluster count |
| Dynamic vocab loaded | `sub_domain_dynamic_vocab` | TF-IDF keywords loaded — shows keyword count and top keywords |
| Qualifier evaluated | `sub_domain_qualifier_eval` | Per-qualifier: count, consistency %, threshold %, pass/fail |
| Intent fallback | `sub_domain_intent_fallback` | How many opts matched via intent_label, which qualifiers |
| Sub-domain created | `sub_domain_created` | Qualifier name, parent domain, clusters reparented, consistency |
| Sub-domain skipped | `sub_domain_skipped` | Why it didn't create: already_exists, single_cluster |
| Domain skipped | `sub_domain_domain_skipped` | Domain already has sub-domains (idempotency guard) |

These events are written to `data/taxonomy_events/decisions-YYYY-MM-DD.jsonl` and streamed via SSE for real-time monitoring.

## Files

| File | Role |
|---|---|
| `heuristic_analyzer.py` | `_DOMAIN_QUALIFIERS` static vocabulary, `_enrich_domain_qualifier()` enrichment |
| `labeling.py` | `generate_qualifier_vocabulary()` LLM-based vocabulary generation |
| `engine.py` | `_propose_sub_domains()` three-source pipeline with vocabulary tiering |
| `warm_phases.py` | `phase_archive_empty_sub_domains()` cleanup of empty/single-child sub-domains |
| `_constants.py` | Thresholds: `SUB_DOMAIN_QUALIFIER_*`, `SUB_DOMAIN_ARCHIVAL_IDLE_HOURS` |
| `cold_path.py` | Step 12: sub-domain parent preservation during HDBSCAN refit |
| `domain_signal_loader.py` | Loads `signal_keywords` for Tier 3 dynamic keywords |
| `cluster_meta.py` | `read_meta()`/`write_meta()` for cached vocabulary in `cluster_metadata` |
