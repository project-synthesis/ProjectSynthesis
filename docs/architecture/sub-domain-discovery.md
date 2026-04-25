# Sub-Domain Discovery Architecture

_Last reviewed: 2026-04-25. Reflects the fully-organic vocabulary pivot shipped across v0.3.32 → v0.3.38 and the re-evaluation / dissolution lifecycle from v0.3.37. **v0.4.5 (PR #55)**: `enrich_domain_qualifier()` now ALSO runs **post-LLM** in `pipeline_phases.resolve_post_analyze_state` (ordered BEFORE `domain_resolver.resolve()` so the resolver sees the canonical form), so Source 1 captures qualifier syntax even when the LLM analyzer returns a bare primary. Hyphen-style sub-domain syntax from the LLM (`backend-observability`) is normalized to colon syntax (`backend: observability`) by `_normalize_llm_domain` against the live `DomainResolver` registry. Sub-domain labels themselves go through the new shared `normalize_sub_domain_label()` canonicalizer (kebab-case, max 30 chars, word-boundary truncation) — used by both vocab generation and discovery._

How sub-domains are organically discovered in the taxonomy engine.

> **Vocabulary tier note.** The Tier 1 "static `_DOMAIN_QUALIFIERS`" vocabulary described below has been **removed**. Sub-domain discovery is now fully organic: the primary vocabulary source is Haiku-generated enriched vocabulary (the former Tier 2, now **Tier 1**), with dynamic TF-IDF keywords as fallback. The three-source *signal* pipeline (domain_raw → intent_label → TF-IDF keywords) is unchanged. See the "Vocabulary: Current Architecture" section below for the live design; the "Why business domains need vocabulary" discussion is retained as the motivation record.

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

## Vocabulary: Current Architecture

Sub-domain discovery needs a **qualifier vocabulary** — a mapping of qualifier names to keyword groups (e.g., `"growth": ["metrics", "kpi", "dashboard", ...]`). This vocabulary tells the system what specializations to look for within a domain.

Two tiers provide vocabulary, tried in order:

```
Tier 1: LLM-generated enriched vocabulary (primary, organic)
  |  Haiku analyzes each domain's cluster labels + per-cluster centroid
  |  similarity matrix + intent labels + domain_raw qualifier distribution
  |  (ClusterVocabContext dataclass), generating groups in one call per domain.
  |  Cached in cluster_metadata["generated_qualifiers"] on the domain node.
  |  Refreshed when cluster count changes by ≥30% OR when Phase 4.95 runs
  |  vocab_only=True in an isolated DB session.
  |  Quality metric emitted via vocab_generated_enriched event;
  |  avg_vocab_quality surfaced in /api/health.
  |
  v  No generated vocabulary yet / LLM unavailable?
Tier 2: Dynamic TF-IDF keywords (fallback)
  |  Every domain node has signal_keywords extracted by the warm path's
  |  TF-IDF pipeline. Individual discriminative keywords (not grouped),
  |  each becomes a standalone qualifier. Weight ≥0.5 required.
  |  Works best for technical domains with specific jargon.
```

### How each tier works

**Tier 1 (LLM-generated enriched)** is the primary and preferred source. `generate_qualifier_vocabulary()` in `taxonomy/labeling.py` receives a structured `ClusterVocabContext` dataclass containing:
- Per-cluster centroid similarity matrix (cells marked `None` for unknown / not-yet-computed pairs; `_VOCAB_SIM_HIGH=0.7` flags "merge candidates", `_VOCAB_SIM_LOW=0.3` flags "truly distinct")
- Intent labels (concentrated topic signals, 3-6 words each)
- `domain_raw` qualifier distribution (what users are already saying this domain is about)

Haiku produces qualifier groups where each group's keywords are semantically coherent within the domain's actual content. Example output:

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

The generated vocabulary is cached in `cluster_metadata["generated_qualifiers"]` and reused on subsequent warm cycles. It's also consumed by `_enrich_domain_qualifier()` in `domain_detector.py` for live qualifier enrichment (appends sub-qualifiers to `domain_raw` — e.g., "backend" → "backend: auth" — so Source 1 has richer signal on future cycles).

**Tier 2 (Dynamic TF-IDF)** is the fallback when no enriched vocabulary exists yet. `signal_keywords` on the domain node become standalone single-keyword qualifiers. This works well for technical domains where keywords like "jwt" (0.9 weight) or "encryption" (1.0 weight) are strongly specific. It fails for business domains where keywords like "need" (0.23) and "product" (0.17) are too generic — the 0.5 weight threshold filters them out.

The three-source qualifier *signal* cascade is extracted into a shared pure primitive `compute_qualifier_cascade()` in `sub_domain_readiness.py`, consumed by both `_propose_sub_domains()` and `GET /api/domains/readiness` — zero-drift by construction.

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

**With enrichment**: `_enrich_domain_qualifier()` (in `domain_detector.py`) scans the prompt against `DomainSignalLoader.generated_qualifiers` + static signal keywords and appends a qualifier. Boosts coverage to ~30% for domains with populated enriched vocabulary.

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
- **No permanent idempotency lock** (changed v0.3.37): domains with existing sub-domains are re-evaluated each Phase 5. New sub-domains form alongside existing ones as signal emerges. Sub-domains are lightweight and expendable — they can be dissolved and re-created organically (see Lifecycle below).
- **`dissolved_this_cycle` flip-flop guard**: a sub-domain dissolved in the current cycle is excluded from re-creation until the next cycle, preventing oscillation.

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
 Sub-domain          Sub-domain          Sub-domain           Re-discovery
 discovery           re-evaluation       archival             (Phase 5, next cycle)
 (Phase 5)           (Phase 5,           (Phase 5.5)
                      consistency drop)
     |                   |                    |                    |
     v                   v                    v                    v
 Signal scan  -->  Consistency < 0.25? --> 0 children? Archive --> Signal returns?
 Threshold?        _dissolve_node():       1 child? Archive +      Create (skipped
 2+ clusters?      reparent children       reparent child          if on dissolved_
     |             to top-level domain,    clear resolver labels   this_cycle set)
     v             merge meta-patterns,    + all four indices
 Create sub-domain archive, clear labels
 Reparent clusters from resolver +
 Set UMAP position signal_loader +
 Update resolver   embedding/qualifier/
                   transformation/optimized
                   indices
```

Sub-domains are lightweight and expendable. Three dissolution paths:
1. **Consistency collapse** (`_reevaluate_sub_domains()`): when `consistency < SUB_DOMAIN_DISSOLUTION_CONSISTYNCY_FLOOR = 0.25` (hysteresis gap vs the 0.40–0.60 creation band). Invokes the shared `_dissolve_node()` primitive: reparents clusters to the top-level domain, merges meta-patterns into the parent (prompts never lost), archives the sub-domain, clears resolver labels, clears all four in-memory indices.
2. **Empty / near-empty** (`phase_archive_empty_sub_domains()` in Phase 5.5): when the sub-domain has 0 active-cluster children OR 1 child that dissolved. The single child is reparented to the top-level domain.
3. **Flip-flop prevention**: a sub-domain dissolved in the current cycle is added to `dissolved_this_cycle`; Phase 5 re-discovery skips labels in this set until the next cycle.

The qualifier-based label ensures stability — "jwt" is always "jwt", unlike the old HDBSCAN labels that changed every cycle ("async-resilience-patterns" → "rate-limiting-&-retry-patterns" → "warm-path-taxonomy-concurrency"). A re-discovered sub-domain receives the same label as the one that was dissolved, so UI continuity is preserved across the dissolution cycle.

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
| `services/domain_detector.py` | `_enrich_domain_qualifier()` enrichment using `DomainSignalLoader.generated_qualifiers` |
| `services/domain_signal_loader.py` | Loads enriched vocabulary (`generated_qualifiers`) + `signal_keywords` for TF-IDF fallback |
| `services/taxonomy/labeling.py` | `generate_qualifier_vocabulary()` LLM-based vocabulary generation (receives `ClusterVocabContext` dataclass) |
| `services/taxonomy/sub_domain_readiness.py` | Shared `compute_qualifier_cascade()` primitive (consumed by both discovery + `/api/domains/readiness`), `compute_domain_stability()`, `compute_sub_domain_emergence()`, `compute_domain_readiness()`, tier-crossing detector |
| `services/taxonomy/engine.py` | `_propose_sub_domains()` three-source pipeline with vocabulary tiering, `_reevaluate_sub_domains()` dissolution, shared `_dissolve_node()` primitive |
| `services/taxonomy/warm_phases.py` | Phase 5 sub-domain discovery + re-evaluation, Phase 5.5 archival, Phase 4.95 vocab-only pass in isolated DB session |
| `services/taxonomy/_constants.py` | Thresholds: `SUB_DOMAIN_QUALIFIER_*`, `SUB_DOMAIN_DISSOLUTION_CONSISTENCY_FLOOR`, `SUB_DOMAIN_ARCHIVAL_IDLE_HOURS`, `_VOCAB_SIM_HIGH`/`_VOCAB_SIM_LOW` |
| `services/taxonomy/cold_path.py` | Sub-domain parent preservation during cold path refit |
| `services/taxonomy/readiness_history.py` | JSONL snapshot writer (30-day retention, hourly buckets) backing `/api/domains/{id}/readiness/history` |
| `services/taxonomy/cluster_meta.py` | `read_meta()`/`write_meta()` for cached vocabulary in `cluster_metadata` |
| `routers/domains.py` | `GET /api/domains/readiness`, `GET /api/domains/{id}/readiness` (`?fresh=true` bypass), `GET /api/domains/{id}/readiness/history` (`?hours=`) |
