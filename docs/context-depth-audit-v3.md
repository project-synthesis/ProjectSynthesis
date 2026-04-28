# Context Depth Audit V3: Current State and Observability

**Predecessors**: V1 (retrieval pipeline tuning), V2 (agentic executor analysis + signal matrix)
**Date**: 2026-04-11
**Status**: Historical — baseline snapshot from 2026-04-11. The Phase 2 observability items (`ContextDiagnostic` panel) landed in v0.3.27; the broader enrichment pipeline consolidated in v0.3.30 (see `docs/enrichment-consolidation-action-items.md`). For current live architecture refer to `backend/CLAUDE.md` → "Codebase context" + "Context enrichment".

## Current System State

After 13 experimental runs and 7 implementation iterations, the context enrichment pipeline now delivers:

| Metric | Before (V0) | After (V3) | Change |
|--------|-------------|------------|--------|
| Indexed files | 558 | 340 | -39% (test exclusion) |
| Context source | 500-char outlines | Full file source | Quality leap |
| Retrieval method | Similarity only | Similarity + import graph | Dependency awareness |
| Files per optimization | 5-7 | 8 (3 sim + 5 graph) | +60% with higher relevance |
| Budget utilization | 10.8% of 30K | 98.9% of 80K | Near-maximum |
| Stop reason | relevance_exhausted | budget | Constraint shifted |
| Cross-domain noise | 5 frontend files in backend prompts | 0 | Eliminated |
| Codebase vocabulary | 3-5 terms | 15+ terms | 3x precision |
| Conciseness delta | -1.5 (penalized) | +3.8 (rewarded) | Scoring fixed |

## Pipeline Observability Chain

Every optimization now produces a structured trace across 5 stages:

### Stage 1: Curated Retrieval (`curated_context`)

```
curated_context: repo=project-synthesis/ProjectSynthesis
  query_len=210  indexed=340
  above_threshold=5  cross_domain_cut=16  below_base_cut=319
  diversity_excluded=0  selected=5  top=0.453
  fetch=Xms  search=Xms
```

**Key signals**: `cross_domain_cut` shows how many files were blocked by the 0.30 floor for files from a different known domain. `above_threshold` shows how many passed the 0.20 base similarity threshold.

### Stage 2: Import-Graph Expansion (`curated_import_graph`)

```
curated_import_graph: expanded 5 files from imports of 3 ranked files
```

**Key signal**: Ratio of graph-expanded files to similarity-ranked files. Healthy ratio: 1-2x (most context comes from dependencies, not surface similarity).

### Stage 3: Retrieval Detail (`curated_retrieval_detail`)

```
curated_retrieval_detail: files=8 (sim=3 graph=5)
  budget=79094/80000 (99%)  stop=budget  near_misses=2  total=Xms
```

**Key signals**:
- `stop=budget` means we're utilizing the full cap. `stop=relevance_exhausted` means the embeddings can't find enough relevant files.
- `near_misses` shows files that scored above threshold but didn't fit the budget.
- `total` includes DB fetch, embedding, cosine search, domain filtering, import parsing, and budget packing.

### Stage 4: Enrichment Assembly (`enrichment`)

```
enrichment: tier=internal repo=project-synthesis/ProjectSynthesis
  explore=yes  curated=ready  curated_files=8  curated_top=0.453
  signals=perf  total_assembled=89K  elapsed=Xms
```

**Key signals**: `signals=perf` confirms performance signals (strategy performance, anti-patterns, domain keywords) were injected. `total_assembled` is the full context size before template rendering.

### Stage 5: Optimizer Injection (`optimize_inject`)

```
optimize_inject: trace_id=173b5912...
  input_chars=143294 (~35823 tokens)
  prompt=210  codebase=89270  guidance=42106
  adaptation=0  patterns=0  fewshot=0
```

**Key signals**: Full breakdown of what the optimizer LLM receives. `codebase=89270` is the curated retrieval + explore synthesis. `guidance=42106` is the workspace CLAUDE.md content. Token estimate at ~4 chars/token gives context window utilization.

## Configuration (current)

| Constant | Value | Purpose |
|----------|-------|---------|
| `INDEX_OUTLINE_MAX_CHARS` | 2000 | Per-file outline for embedding text |
| `INDEX_CURATED_MAX_CHARS` | 80000 | Curated retrieval budget |
| `INDEX_CURATED_MIN_SIMILARITY` | 0.20 | Base similarity floor |
| `cross_domain_min_sim` | 0.30 | Stricter floor for cross-domain files |
| `INDEX_CURATED_MAX_PER_DIR` | 3 | Diversity cap per directory |
| `INDEX_DOMAIN_BOOST` | 1.3 | Score multiplier for same-domain files |
| `MAX_CODEBASE_CONTEXT_CHARS` | 100000 | Combined explore + curated cap |
| `SCORING_FORMULA_VERSION` | 3 | Dimension weight version *(historical — v0.4.9 bumped to 4 for per-task-type weights)* |

## Dimension Weights (v3 — historical; superseded by v4 in v0.4.9)

> v0.4.9 (2026-04-28) introduced per-task-type weights via `get_dimension_weights(task_type)`. Default schema below remains for non-analysis task types. Analysis-class prompts use `ANALYSIS_DIMENSION_WEIGHTS`: clarity 0.25, specificity 0.25, structure 0.20, faithfulness 0.20, conciseness 0.10.

| Dimension | Weight | Rationale |
|-----------|--------|-----------|
| Faithfulness | 0.26 | Most important — wrong intent = failure |
| Clarity | 0.22 | Primary value dimension |
| Specificity | 0.22 | Primary value dimension |
| Structure | 0.15 | Format-match, not format-presence |
| Conciseness | 0.15 | Reduced — optimization structurally trades conciseness for specificity |

## Performance Signals (new in V3)

Three signals injected alongside adaptation state at ~150 tokens cost:

1. **Strategy performance by domain**: `Top strategies for backend+coding: structured-output (7.2, n=12), meta-prompting (6.9, n=8)`
2. **Anti-pattern hints**: `Avoid: role-playing averaged 4.8 for backend+coding (n=5)` (strategies with overall avg < 5.5, n >= 3)
3. **Domain keywords**: `Domain vocabulary: api, endpoint, server, middleware, fastapi` (from DomainSignalLoader)

## Scoring Recalibration Impact

| Bias | Before | After | Fix |
|------|--------|-------|-----|
| Conciseness brevity-bias | Short vague prompt scored 7-9 on conciseness | Short vague prompt scores 3-4 (correctly sparse) | "SHORT IS NOT CONCISE" calibration note |
| Faithfulness originality-bias | Raw prompt scored higher than optimized | Additions serving intent are faithful enhancements | "originality-bias" anti-pattern |
| Structure format-bias | Headers required for score > 6 | Flat paragraph = 7 for simple tasks | Format-match scoring |

## Subagent Executor Verification

Prompt claim classification (26 claims verified against actual codebase):

| Category | Count | % | Token ROI |
|----------|-------|---|-----------|
| ESSENTIAL | 12 | 46% | Maximum — prevents wrong implementation |
| HELPFUL | 8 | 31% | Medium — saves 5-15 tool calls |
| REDUNDANT | 4 | 15% | Zero — agent discovers immediately |
| WRONG | 0 | 0% | N/A |

**Three-layer framework**:
- Layer 1 (Vocabulary anchors, ~50 tokens): ALWAYS INCLUDE — steers agent to right files
- Layer 2 (Architectural constraints, ~200 tokens): ALWAYS INCLUDE — prevents wrong decisions
- Layer 3 (Implementation details, ~500 tokens): Agent discovers independently from code

## Full Run History

| Run | Phase | Files | Sim | Graph | Budget | Overall | Faith delta | Conc delta | Vocab |
|-----|-------|-------|-----|-------|--------|---------|-------------|------------|-------|
| 1 | V1 baseline | 7 | 7 | 0 | 10.8% of 30K | 6.46 | +1.2 | +0.9 | 5 |
| 2 | 2000c outlines | 8 | 8 | 0 | 17.3% of 30K | 6.45 | +2.6 | -0.6 | 5 |
| 3 | 0.20 threshold | 23 | 23 | 0 | 35.4% of 30K | 7.25 | +1.1 | -1.7 | 5 |
| 4 | +test filter +cross-domain | 5 | 5 | 0 | 8.1% of 30K | 6.29 | +1.7 | -1.1 | 3 |
| 5 | Full source | 5 | 5 | 0 | 76.9% of 80K | 7.03 | +3.6 | -0.4 | 5 |
| 6 | +import graph (append) | 6 | 5 | 1 | 86.3% of 80K | 6.66 | +1.6 | -3.1 | 7 |
| 7 | +interleaved packing | 8 | 3 | 5 | 98.9% of 80K | 6.39 | +2.1 | -1.2 | 10 |
| 8 | Scoring recal | 8 | 3 | 5 | 98.9% of 80K | 6.75 | +2.1 | +0.4 | 10 |
| 9 | SSE health prompt | 10 | 3 | 7 | 84.7% of 80K | 7.41 | +2.3 | -1.4 | 17 |
| 10 | Task-depth + density | 8 | 3 | 5 | 98.9% of 80K | 7.26 | +3.3 | -1.5 | 15 |
| 11 | +perf signals + observability | 8 | 3 | 5 | 99% of 80K | 7.37 | +2.1 | +3.8 | 15+ |

## Commits (this session)

```
102c441 style: fix ruff lint
55afe6f fix: prior session fixes
15d5d56 docs: context depth audit V1+V2
fe338e0 feat: performance signals injection + pipeline observability
1fad181 fix: scoring recalibration v3
98825d7 feat: retrieval pipeline — full source, import-graph, test exclusion
3027339 feat: cross-component coherence audit — brand compliance, Navigator tabs
```

## Next Steps

1. **Phase 2: Best exemplar retrieval** (~500 tokens) — Find highest-scoring similar optimization via composite embedding and inject as concrete before/after example
2. **Diverse prompt validation** — Test on frontend, cross-cutting, documentation, and bug fix prompts
3. **Agentic mode** — Produce leaner prompts for executors with codebase access (Layers 1-2 only)
4. **Two-level import expansion** — Import-of-imports to surface files 2 hops from top-ranked
5. **Import-graph caching** — Pre-compute during build_index to avoid re-parsing on every query
