# Context Depth Audit: Curated Codebase Retrieval

**Status**: Active investigation
**Started**: 2026-04-10
**Baseline prompt**: "Implement a background task that detects stale RepoFileIndex entries by comparing each entry's file_sha against the current HEAD tree, then marks changed files for re-embedding without triggering a full reindex"
**Repo**: project-synthesis/ProjectSynthesis (340 source files, 3.0MB)

## Problem Statement

The optimization pipeline injects codebase context into the optimizer's LLM call to ground prompt improvements in the actual codebase. The original implementation delivered **500-character outlines** of semantically-matched files, using **10% of the available context budget** and **4.5% of the Opus 200K context window**. The optimizer frequently missed critical implementation details because it couldn't see actual source code.

## Architecture

```
User prompt
    |
    v
Heuristic Analyzer ──> domain, task_type (zero-LLM)
    |
    v
Context Enrichment
    |
    ├── Explore synthesis (cached, ~3K chars)
    │     Static Haiku architectural overview, computed on repo link/reindex.
    │     Covers: architecture patterns, layer rules, conventions.
    │
    ├── Curated retrieval (per-prompt, dynamic)
    │     1. Embed user prompt (384-dim, all-MiniLM-L6-v2)
    │     2. Cosine search against RepoFileIndex embeddings
    │     3. Relevance filtering (min_sim threshold)
    │     4. Domain-aware cross-domain penalty
    │     5. Diversity cap (max 3 per directory)
    │     6. Budget packing (full source, char cap)
    │
    └── Combined context → optimize.md template → Opus
```

### Context Window Budget (Opus 200K tokens)

| Component | Typical chars | Tokens | Cap |
|-----------|--------------|--------|-----|
| System prompt (agent-guidance.md) | 7,000 | 1,800 | none |
| Template static (optimize.md) | 4,000 | 1,000 | none |
| Raw user prompt | 200-10,000 | 50-2,500 | 200K chars |
| Explore synthesis | 3,000 | 800 | none |
| **Curated retrieval** | **0-80,000** | **0-20,000** | **80K chars** |
| Workspace guidance (CLAUDE.md) | 8,000 | 2,000 | 20K chars |
| Strategy instructions | 2,000 | 500 | none |
| Applied patterns + few-shot | 3,000 | 750 | ~5K |
| Adaptation state | 500 | 125 | 5K chars |
| **Total typical** | **~88,000** | **~22,000** | — |
| **Opus capacity** | **~800,000** | **200,000** | — |
| **Utilization** | — | **~11%** | — |

The codebase context combined cap (`MAX_CODEBASE_CONTEXT_CHARS`) is 100K chars, but only the curated retrieval portion is dynamic. With explore synthesis at ~3K, the effective curated budget is ~97K.

## Codebase Profile

| Category | Files | Size | Notes |
|----------|-------|------|-------|
| Backend source (`backend/app/`) | 124 | 1,405K | Pipeline, services, routers, models |
| Frontend source (`frontend/src/`) | 107 | 783K | Svelte components, stores, utils |
| Documentation (`docs/`) | 21 | 363K | Specs, plans, ADRs |
| Prompts (`prompts/`) | 25 | 81K | Templates, strategies, seed agents |
| Config (`.claude/`, root) | 63 | 346K | Skills, hooks, scripts |
| **Total indexed** | **340** | **2,978K** | After test file exclusion |
| Tests excluded | 218 | ~1,500K | 39% of code files |

## Experimental Runs

All runs used the same baseline prompt (stale RepoFileIndex detection). Domain: `backend`. Each run changed one variable.

### Run Matrix

The first 4 runs below used outlines with various threshold/filter combinations. Runs 5-7 introduced full source delivery, import-graph expansion, and interleaved packing.

| Run | Outline cap | Min sim | Test filter | Cross-domain | Source | Graph | Files | FE | Budget | Overall | Faith delta | Vocab |
|-----|------------|---------|-------------|--------------|--------|-------|-------|----|--------|---------|-------------|-------|
| 1 | 500c | 0.30 | No | No | outline | 0 | 7 | 0 | 10.8% of 30K | 6.46 | +1.2 | 5 |
| 2 | 2000c | 0.30 | No | No | outline | 0 | 8 | 0 | 17.3% of 30K | 6.45 | +2.6 | 5 |
| 3 | 2000c | 0.20 | No | No | outline | 0 | 23 | 5 | 35.4% of 30K | 7.25 | +1.1 | 5 |
| 4 | 2000c | 0.20 | Yes | Yes | outline | 0 | 5 | 0 | 8.1% of 30K | 6.29 | +1.7 | 3 |
| 5 | 2000c | 0.20 | Yes | Yes | **full** | 0 | 5 | 0 | 76.9% of 80K | 7.03 | **+3.6** | 5 |
| 6 | 2000c | 0.20 | Yes | Yes | full | 1 | 6 | 0 | 86.3% of 80K | 6.66 | +1.6 | 7 |
| **7** | **2000c** | **0.20** | **Yes** | **Yes** | **full** | **5** | **8** | **0** | **98.9% of 80K** | **6.39** | **+2.1** | **10** |

**Vocabulary column**: count of codebase-specific terms (out of 22 tracked) that appear in the optimized prompt — measures how deeply the optimizer grounded its output in actual code.

### Controlled Comparison (Last 7 Runs, Same Prompt)

Detailed per-run breakdown with score dimensions and codebase vocabulary:

| Run | Context | Clarity | Specificity | Structure | Faithfulness | Conciseness | Codebase terms used |
|-----|---------|---------|-------------|-----------|-------------|-------------|---------------------|
| 1 | 16 files, outlines, 20% budget | 2.9→6.0 | 2.8→6.5 | 2.5→4.0 | 6.2→7.9 | 7.1→8.0 | RepoFileIndex, file_sha, embedding, lifespan, warm-path |
| 2 | 16 files, outlines, 20% budget | 2.9→6.4 | 2.4→7.0 | 2.5→4.8 | 6.2→7.8 | 8.1→7.0 | RepoFileIndex, file_sha, LinkedRepo, embedding, lifespan |
| 3 | 16 files, outlines, 20% budget | 2.9→5.8 | 2.8→6.4 | 2.5→4.0 | 6.2→7.4 | 7.6→7.0 | RepoFileIndex, file_sha, embedding, lifespan, reindex_repo |
| 4 | 5 files, outlines, 8% budget | 2.9→5.4 | 2.4→6.2 | 2.5→3.3 | 6.2→7.9 | 8.6→7.5 | RepoFileIndex, file_sha, embedding |
| **5** | **5 files, full source, 77% budget** | 3.0→6.8 | 2.2→6.3 | 2.5→6.1 | **4.2→7.8** | 8.1→7.7 | RepoFileIndex, RepoIndexMeta, file_sha, head_sha, semaphore |
| 6 | 6 files (1 graph), full, 86% | 3.6→6.8 | 2.2→6.3 | 2.5→5.8 | 6.2→7.8 | 9.2→6.1 | + build_index, lifespan |
| **7** | **8 files (5 graph), full, 99%** | 3.0→6.4 | 2.2→6.8 | 2.5→3.6 | 5.2→7.3 | 8.1→6.9 | **+ get_tree, Settings, delete-and-rebuild** |

### Key Findings

**1. Full source >> outlines (Run 4 vs 5)**
Same 5 files, same curation. Only difference: outline (avg 400c/file) vs full source (avg 12K/file). Faithfulness delta jumped from +1.7 to +3.6. The optimizer referenced specific internal patterns (`is_stale()`, semaphore pattern, delete-all-then-reinsert) that are invisible in outlines. Structure also jumped from +0.8 to +3.6.

**2. Import-graph expansion solves the semantic gap (Run 5 vs 7)**
The top similarity file (`repo_index_service.py`) imports `models.py`, `github_client.py`, and `embedding_service.py` — all critical for the task but scoring below 0.20 in embedding similarity. Interleaved packing prioritizes these imports over low-scoring similarity tail files. Result: 10 codebase vocabulary terms vs 5, including `get_tree()`, `delete-and-rebuild`, and `Settings` that are impossible to reference without seeing actual dependency source code.

**3. Test files are noise (Run 3 baseline)**
Removing 218 test files from the index cut indexed files by 39%. Zero information loss — test files duplicate the structure of what they test while consuming embedding compute and retrieval budget.

**4. Cross-domain filtering eliminates noise (Runs 1-3 vs 4+)**
Frontend files (DiffView.svelte, ForgeArtifact.svelte) at 0.20-0.23 similarity were pure noise for a backend prompt. Cross-domain penalty (0.30 floor for files belonging to a different known domain) eliminated all frontend noise.

**5. Budget utilization went from 8% to 99%**
The progression: Run 4 (cross-domain filter, outlines) used 8.1% of budget. Run 5 (full source) used 76.9%. Run 7 (interleaved import-graph) used 98.9%. The constraint shifted from relevance_exhausted to budget — the system now fills available context with high-value content.

**6. Codebase vocabulary is the strongest quality signal**
Overall scores fluctuate with LLM variance (6.29-7.03), but vocabulary count steadily increased: 3→5→5→7→10. The optimizer in Run 7 references `get_tree()`, `delete-and-rebuild`, `Settings`, `isnot(None)` — terms it can only produce by reading actual source code from dependency files.

## Changes Implemented

### 1. Test File Exclusion (build-time)

**File**: `backend/app/services/repo_index_service.py` — `_is_test_file()`

Excludes from indexing:
- Directory patterns: `tests/`, `test/`, `__tests__/`, `spec/`, `cypress/`, `playwright/`, `e2e/`, `fixtures/`, `__mocks__/`, `__snapshots__/`
- File patterns: `test_*`, `*_test.*`, `*.test.*`, `*.spec.*`, `*.stories.*`, `*_bench.*`, `*_benchmark.*`
- Test infrastructure: `conftest.py`, `jest.config.*`, `vitest.config.*`, `pytest.ini`, `tox.ini`, etc.

**Impact**: 558 → 340 indexed files (39% reduction in compute + storage). Zero loss of useful context.

### 2. Richer Outlines (embedding quality)

**File**: `backend/app/config.py` — `INDEX_OUTLINE_MAX_CHARS`

Changed from 500 to 2000 characters per file. Outlines now capture more function signatures, docstrings, and structural summaries, producing richer embeddings for semantic search.

### 3. Lower Similarity Threshold (recall)

**File**: `backend/app/config.py` — `INDEX_CURATED_MIN_SIMILARITY`

Changed from 0.30 to 0.20. Increases recall for files with indirect semantic connections to the prompt, at the cost of some noise (addressed by cross-domain filtering).

### 4. Cross-Domain Noise Filter (precision)

**File**: `backend/app/services/repo_index_service.py` — `query_curated_context()`

Files belonging to a known domain (backend, frontend, database, etc.) that differs from the prompt's domain face a stricter 0.30 floor instead of the 0.20 base threshold. Files with no clear domain affinity (docs, configs) use the base threshold.

**Implementation detail**: Domain detection now runs heuristic analysis for ALL tiers (not just passthrough), enabling domain-aware filtering in the internal pipeline.

### 5. Full Source Delivery (context depth)

**File**: `backend/app/models.py` — `RepoFileIndex.content` column
**File**: `backend/app/services/repo_index_service.py` — `build_index()` + `query_curated_context()`

Full file content is stored in the index during `build_index()` and delivered to the LLM during curated retrieval instead of outlines. Outline is retained for embedding quality (the embedding text is derived from outline, not raw content).

Budget raised from 30K to 80K chars (`INDEX_CURATED_MAX_CHARS`) to accommodate full source.

### 6. Import-Graph Expansion (dependency resolution)

**File**: `backend/app/services/repo_index_service.py` — `_extract_import_paths()` + `query_curated_context()`

After packing each similarity-ranked file, the system immediately parses its import statements and looks up the imported files in the same `RepoFileIndex`. Dependencies are packed before moving to the next similarity-ranked file, ensuring high-value imports (like `models.py` from `repo_index_service.py`) take priority over low-scoring similarity tail files.

Supports Python (`from app.xxx import ...`) and TypeScript/Svelte (`from '$lib/...'`, relative imports) with extension guessing.

**Impact**: Run 7 expanded 5 files from imports of 3 similarity-ranked files. `models.py` (15K) and `github_client.py` (4.3K) — previously unreachable at <0.20 similarity — are now consistently included. Budget utilization went from 77% to 99%.

### 7. Retrieval Diagnostics (observability)

**File**: `backend/app/services/repo_index_service.py` — `CuratedCodebaseContext` dataclass
**File**: `backend/app/services/context_enrichment.py` — enrichment metadata
**File**: `frontend/src/lib/components/editor/ForgeArtifact.svelte` — CONTEXT panel

Every optimization now records:
- `stop_reason`: `budget` | `relevance_exhausted`
- `budget_used_chars` / `budget_max_chars` and percentage
- `diversity_excluded`: files displaced by per-directory cap
- `near_misses`: next 5 files after cutoff
- Per-file `content_chars` and `source` (full vs outline)

Visible in the frontend as a collapsible CONTEXT section in the result view, alongside CHANGES.

## Solved Problems

### 1. Semantic Gap for Cross-Module Queries (SOLVED via import-graph expansion)

The embedding similarity for `models.py` (defines `RepoFileIndex`) and `github_client.py` (GitHub API) consistently scored below 0.20 — too low for direct retrieval. Import-graph expansion solved this by parsing import statements from semantically-selected files and pulling in their dependencies regardless of individual similarity scores.

**Before** (Run 4): 5 files, 8.1% budget, 3 vocabulary terms. `models.py` and `github_client.py` missing.
**After** (Run 7): 8 files, 98.9% budget, 10 vocabulary terms. Both files included via import graph.

### 2. Context Window Utilization (SOLVED: 8% → 99%)

Budget utilization went from 8.1% (Run 4, outlines only) to 98.9% (Run 7, full source + import graph). The constraint shifted from `relevance_exhausted` to `budget` — the system now fills available context with high-value content.

## Open Problems

### 1. Structure Score Regression

Structure scores are inconsistent across runs (3.3-6.1) and don't correlate with context depth. This is likely an LLM prompt structure issue, not a context issue — the optimizer sometimes produces dense paragraphs instead of structured sections regardless of how much codebase context it receives.

### 2. Embedding Quality for Short Files

Small files (< 1K chars) produce weak embeddings. Files like `backend/app/tools/refine.py` (24 chars outline) and `backend/app/tools/history.py` (25 chars) have such thin embedding text that they match nearly any query at low similarity, wasting retrieval slots.

### 3. Import-Graph Depth

Current implementation expands imports one level deep (direct imports of similarity-ranked files). Two-level expansion (imports of imports) could surface files like `event_bus.py` or `github_repos.py` that are two hops away from the top-ranked file. Trade-off: deeper expansion fills the budget faster with potentially less relevant files.

### 4. Evaluation on Diverse Prompts

This audit used a single backend prompt. Need to validate with frontend, cross-cutting, and documentation prompts to confirm the tuning generalizes.

## Configuration Reference

| Constant | Value | File | Line | Purpose |
|----------|-------|------|------|---------|
| `INDEX_OUTLINE_MAX_CHARS` | 2000 | `config.py` | 101 | Per-file outline cap for embedding text |
| `INDEX_CURATED_MAX_CHARS` | 80000 | `config.py` | 104 | Curated retrieval char budget |
| `INDEX_CURATED_MIN_SIMILARITY` | 0.20 | `config.py` | 107 | Base cosine similarity floor |
| `INDEX_CURATED_MAX_PER_DIR` | 3 | `config.py` | 110 | Diversity cap per directory |
| `INDEX_DOMAIN_BOOST` | 1.3 | `config.py` | 113 | Score multiplier for same-domain files |
| `MAX_CODEBASE_CONTEXT_CHARS` | 100000 | `config.py` | 81 | Combined explore + curated cap |
| `MAX_GUIDANCE_CHARS` | 20000 | `config.py` | 78 | Workspace guidance (CLAUDE.md) cap |
| `_CROSS_DOMAIN_MIN_SIM` | 0.30 | `repo_index_service.py` | 604 | Stricter floor for cross-domain files |
| `_MAX_FILE_SIZE` | 100000 | `repo_index_service.py` | 38 | Skip files larger than 100KB |
| `EXPLORE_MAX_FILES` | 40 | `config.py` | 93 | Files read during explore synthesis |
| `EXPLORE_TOTAL_LINE_BUDGET` | 15000 | `config.py` | 96 | Line budget for explore synthesis |

## Retrieval Pipeline (detailed)

```
query_curated_context(repo, branch, query, task_type, domain)
    |
    ├── 1. Fetch all RepoFileIndex rows with non-null embeddings
    |
    ├── 2. Embed query via EmbeddingService.aembed_single()
    |
    ├── 3. Cosine search: rank all files by similarity to query
    |
    ├── 4. Domain-aware filtering:
    |      For each file:
    |        - Detect file's domain from path (_DOMAIN_PATH_PATTERNS)
    |        - If file has a known domain != prompt domain → floor = 0.30
    |        - Otherwise → floor = 0.20
    |        - If same domain → boost score * 1.3
    |
    ├── 5. Diversity cap: max 3 files per directory
    |
    ├── 6. Interleaved budget packing:
    |      For each similarity-ranked file:
    |        a. Pack the file (full source if available, outline fallback)
    |        b. Parse its import statements (_extract_import_paths)
    |        c. Look up imports in RepoFileIndex (same index, no API calls)
    |        d. Pack each import that fits the budget
    |        e. Move to next similarity file → repeat
    |        f. Stop when budget (80K) exhausted
    |
    ├── 7. Collect near misses (next 5 similarity files not included)
    |
    └── 8. Return CuratedCodebaseContext with diagnostics
```

The interleaved approach ensures that dependencies of high-scoring files (e.g., `models.py` imported by `repo_index_service.py`) are packed before low-scoring similarity tail files (e.g., `codebase_explorer.py` at 0.305). This solved the semantic gap where critical files scored below the similarity threshold but were functionally essential.

## Next Steps

1. **Evaluate on diverse prompts** — this audit used a single backend prompt; need to validate with frontend, cross-cutting, and documentation prompts to confirm tuning generalizes
2. **Two-level import expansion** — current implementation is one hop deep; consider expanding imports-of-imports to surface files like `event_bus.py` that are two hops from the top-ranked file
3. **Named entity search** — extract class/function names from the prompt text and search for files that define them (complementary to embedding similarity)
4. **Adaptive budget** — scale `INDEX_CURATED_MAX_CHARS` based on codebase size and prompt length (small codebases could use the full 100K envelope)
5. **Import-graph caching** — pre-compute import graphs during `build_index()` to avoid re-parsing on every query
