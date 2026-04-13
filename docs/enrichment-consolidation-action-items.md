# Enrichment Consolidation — Action Items

> Complete roadmap of issues identified during the enrichment engine consolidation (2026-04-12). Phases 1-5 shipped. This document tracks all remaining action items discovered during implementation and E2E testing, ranked by importance with dependency tracking.

## Status Summary

| Phase | Description | Status |
|-------|------------|--------|
| Phase 1 | Task-gated curated retrieval | **Shipped** |
| Phase 2 | Strategy intelligence consolidation (merge L4+L6) | **Shipped** |
| Phase 3 | Workspace guidance collapse into codebase context | **Shipped** |
| Phase 4 | Enrichment profiles A/B/C | **Shipped** |
| Phase 5 | Cleanup and documentation | **Shipped** |
| Sprint 1 | A1 compound keywords + A2 verb disambiguation | **Shipped** |
| Sprint 2 | C1+E1+B1+B2+A3+A4 (6 items) | **Shipped** |

**Test coverage** (v0.3.30, 2026-04-13): 2107 backend tests, 1038 frontend tests, 0 errors, 0 warnings.

---

## Dependency Graph

```
Phase A: Heuristic Classifier Accuracy
  ├── A1: Compound keyword signals (P0)
  ├── A2: Technical verb disambiguation (P0)
  ├── A3: Domain signal auto-enrichment from taxonomy (P1) ← depends on warm-path hook
  └── A4: Confidence-gated LLM fallback (P1) ← depends on A1+A2 for baseline measurement

Phase B: Prompt-Context Reconciliation
  ├── B1: Tech stack divergence detection (P1) ← depends on codebase context being resolved
  └── B2: Divergence alert injection into optimizer template (P1) ← depends on B1

Phase C: Strategy Intelligence Accuracy
  ├── C1: Domain-relaxed fallback queries (P1) ← independent
  └── C2: Heuristic-to-LLM classification reconciliation (P2) ← depends on A4

Phase D: Retrieval Quality
  ├── D1: Source-type weighting in curated retrieval (P2) ← independent
  └── D2: Embedding model evaluation for code search (P3) ← independent

Phase E: Observability
  ├── E1: Heuristic vs LLM agreement rate instrumentation (P1) ← independent
  ├── E1b: Cross-process agreement bridge (P1) ← depends on E1 + A4; MCP→backend event forwarding
  └── E2: Enrichment profile effectiveness metrics (P2) ← depends on shipped profiles
```

---

## Phase A: Heuristic Classifier Accuracy

**Impact**: HIGH — Every downstream enrichment layer depends on accurate `task_type` and `domain` classification. Misclassification cascades through profile selection, strategy intelligence queries, curated retrieval gating, and pattern injection.

**Evidence from E2E testing**:
- "Design a webhook delivery system" → classified as `creative+general` instead of `coding+backend`
- "Implement a database migration system for SQLAlchemy" → domain classified as `general` instead of `database`
- Both caused `strategy_intel=none` despite abundant historical data for the correct classification

### A1: Compound Keyword Signals
**Priority**: P0
**Effort**: 1 day
**Dependencies**: None
**Blocks**: A4 (baseline measurement)

Add multi-word keyword signals that override single-word collisions:

| Compound | Category | Weight | Overrides |
|----------|----------|--------|-----------|
| "design a system" | coding | 1.2 | "design" → creative (0.7) |
| "design a service" | coding | 1.2 | "design" → creative (0.7) |
| "create a migration" | coding | 1.0 | "create" → creative (0.5) |
| "build a dashboard" | coding | 0.9 | ambiguous without context |
| "design a campaign" | writing | 1.0 | "design" → creative (0.7) |
| "generate a report" | analysis | 1.1 | "generate" → creative (0.9) |

**Files**: `heuristic_analyzer.py` — add `_COMPOUND_SIGNALS` dict, check before single-word scoring in `_score_category()`
**Tests**: Add 6+ test cases for observed failures
**Spec**: [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) section 1a

### A2: Technical Verb Disambiguation
**Priority**: P0
**Effort**: 0.5 day
**Dependencies**: None
**Blocks**: A4 (baseline measurement)

When a technical verb ("design", "create", "build") appears with a technical noun ("system", "service", "api", "schema", "database", "middleware") in the same sentence, boost `coding` regardless of the verb's default category.

**Files**: `heuristic_analyzer.py` — add post-classification adjustment in `_analyze_inner()`
**Tests**: Add test cases for verb+noun combinations
**Spec**: [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) section 1c

### A3: Domain Signal Auto-Enrichment from Taxonomy
**Priority**: P1
**Effort**: 2 days
**Dependencies**: Warm-path integration hook
**Blocks**: None

When the taxonomy engine discovers or labels a domain, extract top keywords from that domain's member prompts and register them as domain signals automatically. Currently, organically discovered domains get zero heuristic keyword support.

**Files**: `taxonomy/warm_phases.py` (extraction hook), `domain_signal_loader.py` (registration API)
**Risk**: Auto-generated signals could be noisy — require minimum 5 members and 0.4 coherence
**Spec**: [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) section 1b

### A4: Confidence-Gated LLM Fallback
**Priority**: P1
**Effort**: 2 days
**Dependencies**: A1+A2 shipped (to establish baseline before adding LLM cost)
**Blocks**: C2

When heuristic confidence < 0.5 AND the top two categories are within 0.2 points, defer to a fast Haiku call for classification only. Catches ambiguous cases while keeping zero-LLM path for clear prompts.

**Cost**: ~0.1s + ~500 tokens per ambiguous prompt (estimated 15-20% of prompts)
**Files**: `heuristic_analyzer.py`, `context_enrichment.py`
**Spec**: [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) section 2a

---

## Phase B: Prompt-Context Reconciliation

**Impact**: HIGH — When the prompt explicitly names a technology that conflicts with the linked codebase, the optimizer silently adopts the prompt's technology. The 86K chars of codebase context become expensive dead weight.

**Evidence from E2E testing**:
- Prompt said "PostgreSQL RLS" but codebase is SQLite/aiosqlite → optimizer produced PostgreSQL-specific output
- Curated retrieval returned irrelevant `.md` files (`.github/SECURITY.md`, seed agent files) instead of actual code
- No flag, no trace, no reconciliation

### B1: Tech Stack Divergence Detection
**Priority**: P1
**Effort**: 2 days
**Dependencies**: Codebase context must be resolved before detection runs (already the case — detection sits between enrichment and template assembly)
**Blocks**: B2

Compare technology keywords in the raw prompt against the codebase context to detect conflicts:

| Category | Conflict Rule | Examples |
|----------|--------------|---------|
| Database engines | Mutually exclusive (unless "migrate" keyword present) | postgresql ↔ sqlite, mysql ↔ mongodb |
| Frameworks | Mutually exclusive | fastapi ↔ django, react ↔ svelte |
| Languages | Mutually exclusive | python ↔ java, typescript ↔ go |
| Additions | No conflict — complementary | redis, celery, docker |

Must distinguish: genuine mismatch vs legitimate addition vs planned migration.

**Files**: `context_enrichment.py` — new `_detect_divergences()` method
**Spec**: [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) section 5 + improvement 1d

### B2: Divergence Alert Injection
**Priority**: P1
**Effort**: 1 day
**Dependencies**: B1
**Blocks**: None

Inject detected divergences into the optimizer template as `<divergence-alert>` section. The optimizer must acknowledge the conflict in its changes summary.

Store divergences in `enrichment_meta["divergences"]` for UI display in the ENRICHMENT panel.

**Files**: `context_enrichment.py`, `prompts/optimize.md` (new `{{divergence_alerts}}` variable), `ForgeArtifact.svelte` (UI display)

---

## Phase C: Strategy Intelligence Accuracy

**Impact**: MEDIUM — Strategy intelligence returns None when the heuristic domain doesn't match the DB's stored domain, even when data exists under a related domain.

**Evidence from E2E testing**:
- Migration prompt: heuristic → `general`, DB has data under `database` → `strategy_intel=none`
- Webhook prompt: heuristic → `general`, DB has data under `backend` → `strategy_intel=none`
- Job scheduler prompt: heuristic → `coding+backend` (correct) → `strategy_intel=yes`

### C1: Domain-Relaxed Fallback Queries
**Priority**: P1
**Effort**: 1 day
**Dependencies**: None (independent of Phase A)
**Blocks**: None

When `resolve_performance_signals()` returns None for the exact `task_type+domain` pair, fall back to:
1. Query by `task_type` only (across all domains) — broader signal, still useful
2. Use the top strategies from the broader query with a reduced confidence indicator

This ensures strategy intelligence fires even when the heuristic domain is wrong, at the cost of slightly less precise rankings.

**Files**: `context_enrichment.py` — modify `resolve_performance_signals()` to add fallback query
**Tests**: Add test for fallback behavior

### C2: Heuristic-to-LLM Classification Reconciliation
**Priority**: P2
**Effort**: 3 days
**Dependencies**: A4 (confidence-gated LLM fallback)
**Blocks**: None

When the LLM analyzer (pipeline phase 1) classifies differently from the heuristic, record the disagreement and adjust keyword weights over time. Feed-forward: use the LLM's classification to update domain signals for future heuristic calls.

**Files**: New `signal_adjuster.py`, `heuristic_analyzer.py`, `pipeline.py`
**Risk**: Requires careful weight decay to avoid oscillation
**Spec**: [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) section 2b

---

## Phase D: Retrieval Quality

**Impact**: MEDIUM — Curated retrieval sometimes returns irrelevant files (`.github/SECURITY.md`, seed agent definitions) when the prompt topic doesn't match the codebase's actual source code.

**Evidence from E2E testing**:
- PostgreSQL RLS prompt → top 5 files were all `.md` and `.json` config files with < 0.26 similarity
- No actual Python source files matched because the prompt topic (PostgreSQL RLS) doesn't exist in the codebase (which uses SQLite)

### D1: Source-Type Weighting in Curated Retrieval
**Priority**: P2
**Effort**: 2 days
**Dependencies**: None
**Blocks**: None

Apply source-type multipliers to curated retrieval similarity scores:
- Python/TypeScript/Svelte source files: 1.0x (baseline)
- Config files (`.json`, `.yaml`, `.toml`): 0.7x
- Documentation (`.md`): 0.5x (except README.md: 0.8x)
- GitHub community files (`.github/`): 0.3x
- Prompt/seed files (`prompts/`): 0.2x

This prevents documentation and community files from outranking actual source code.

**Files**: `repo_index_service.py` — add source-type multiplier in `query_curated_context()`

### D2: Embedding Model Evaluation for Code Search
**Priority**: P3
**Effort**: 1 week
**Dependencies**: None
**Blocks**: None

Evaluate code-specialized embedding models (e.g., `code-search-ada`, `voyage-code-2`) against the current `all-MiniLM-L6-v2` for curated retrieval precision. The current model is general-purpose and may not capture code semantics well enough for technical prompts.

**Files**: `embedding_service.py`, `config.py`
**Risk**: Model size/latency trade-offs, potential cold-start if switching models (requires full re-indexing)

---

## Phase E: Observability

**Impact**: LOW-MEDIUM — Enables data-driven decisions for all other phases.

### E1: Heuristic vs LLM Agreement Rate Instrumentation
**Priority**: P1
**Effort**: 1 day
**Dependencies**: None
**Blocks**: C2 (provides the data C2 needs to adjust weights)

After the LLM analyzer completes in pipeline phase 1, compare its `task_type` and `domain` with the heuristic's classification. Log disagreements as taxonomy events and expose in `GET /api/health`.

Metrics:
- `heuristic_task_type_agreement_rate`: % of prompts where heuristic and LLM agree on task_type
- `heuristic_domain_agreement_rate`: % of prompts where heuristic and LLM agree on domain
- `strategy_intelligence_hit_rate`: % of enrichments where strategy_intel is not None

**Files**: `pipeline.py`, `sampling_pipeline.py`, `health.py`

### E1b: Cross-Process Agreement Bridge
**Priority**: P1
**Effort**: 1 day
**Dependencies**: E1 shipped, A4 shipped
**Blocks**: None

**Problem discovered during E2E validation (2026-04-12):** The `ClassificationAgreement` singleton is per-process. MCP tool calls run the pipeline in the MCP server process, where the agreement tracker accumulates data. But the health endpoint (`GET /api/health`) runs in the backend process, which has a separate singleton with `total=0`. Agreement data generated by MCP optimizations is invisible to the health endpoint.

**Evidence:**
- Connection pool prompt via MCP: heuristic=`coding+general`, LLM=`coding+backend` — task_type agrees, domain disagrees. Logged in MCP process.
- Blog post prompt via MCP: heuristic=`writing+general`, LLM=`writing+backend` — task_type agrees, domain disagrees. Logged in MCP process.
- `GET /api/health` returned `classification_agreement: null` because the backend process singleton had zero recordings.

**Same architectural constraint as**: `injection_effectiveness` (computed by warm path in backend process, not exposed from MCP process).

**Solution**: Forward agreement data from MCP process to backend process via the existing cross-process HTTP POST mechanism (`event_notification.py` → `POST /api/events/_publish`). On each `record()` call, publish a `classification_agreement` event. The backend process's event handler accumulates into its own singleton. Both processes contribute to the health endpoint's aggregate.

**Files**:
- `backend/app/services/classification_agreement.py` — add `publish_to_backend()` call in `record()`
- `backend/app/services/event_notification.py` — add `classification_agreement` event type
- `backend/app/routers/events.py` — handle incoming `classification_agreement` events, update backend singleton

### E2: Enrichment Profile Effectiveness Metrics
**Priority**: P2
**Effort**: 2 days
**Dependencies**: Shipped enrichment profiles (Phase 4 — done)
**Blocks**: None

Track per-profile score distributions to validate that the profiles don't degrade output quality:
- Mean overall score by profile (code_aware vs knowledge_work vs cold_start)
- Context window utilization by profile (chars used / budget)
- Enrichment duration by profile (ms)

Expose in `GET /api/health` and dashboard.

**Files**: `context_enrichment.py` (timing), `health.py` (aggregation)

---

## Implementation Sequence

```
Sprint 1 (P0 — Critical path, 1.5 days)
├── A1: Compound keyword signals
└── A2: Technical verb disambiguation

Sprint 2 (P1 — High impact, 7 days) ✅ ALL SHIPPED (2026-04-13)
├── C1: Domain-relaxed fallback queries ✅ SHIPPED
├── E1: Heuristic vs LLM agreement rate ✅ SHIPPED
├── B1: Tech stack divergence detection ✅ SHIPPED
├── B2: Divergence alert injection + UI ✅ SHIPPED
├── A3: Domain signal auto-enrichment ✅ SHIPPED
├── A4: Confidence-gated LLM fallback ✅ SHIPPED
└── E1b: Cross-process agreement bridge (documented, scheduled for Sprint 3)

Sprint 3 (P2 — Medium impact, 7 days)
├── C2: Heuristic-to-LLM reconciliation (after A4 + E1b)
├── D1: Source-type weighting in curated retrieval
└── E2: Enrichment profile effectiveness metrics

Sprint 4 (P3 — Long-term, 1.5 weeks)
├── D2: Embedding model evaluation
└── 3a: Semantic classification via embedding nearest-neighbor (from heuristic-analyzer-refresh.md)
```

---

## Cross-Reference Index

| Document | Purpose |
|----------|---------|
| [`docs/context-injection-use-case-matrix.md`](context-injection-use-case-matrix.md) | Use-case analysis that identified the consolidation opportunities. Appendix C tracks shipped/planned decisions. |
| [`docs/heuristic-analyzer-refresh.md`](heuristic-analyzer-refresh.md) | Detailed spec for heuristic classifier improvements (Phases A, B). Includes observed failures, improvement tiers, priority matrix, and metrics. |
| [`docs/ROADMAP.md`](ROADMAP.md) | Project roadmap entries for "LLM Domain Classification Accuracy" and "Prompt-context divergence detection." |
| [`docs/CHANGELOG.md`](CHANGELOG.md) | Unreleased section documents all Phase 1-5 changes. |
| [`docs/adr/ADR-006-universal-prompt-engine.md`](adr/ADR-006-universal-prompt-engine.md) | Universal engine architecture — vertical expansion depends on heuristic accuracy for non-developer task types. |
