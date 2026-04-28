# Context Depth Audit V2: Agentic Executor Analysis

**Predecessor**: `context-depth-audit.md` (V1 — retrieval pipeline tuning, 13 experimental runs)
**Status**: Historical investigation — superseded by V3 (`context-depth-audit-v3.md`) and by the shipped enrichment-consolidation work in v0.3.30 (see `docs/enrichment-consolidation-action-items.md` for final status). Retained as the 2026-04-11 decision record for agentic-executor-aware curation.
**Date**: 2026-04-11
**Focus**: Optimizing prompt content for agentic LLM executors that have their own codebase access

## Background

V1 solved the retrieval pipeline: test file exclusion, cross-domain filtering, full source delivery, import-graph expansion, and interleaved budget packing. The result: 8 files at 98.9% budget utilization, 15+ codebase vocabulary terms, and zero missing critical files.

V2 addresses the next question: **now that the optimizer produces codebase-grounded prompts with rich detail, how much of that detail does an agentic executor actually need?**

## The Trillion-Dollar Question

> "Are we duplicating what the agent would discover anyway, or are we steering it toward the RIGHT files faster?"

The optimized prompts go to agentic LLMs (Claude Code, Copilot, etc.) that have full codebase access via tools. They can `Read`, `Grep`, `Glob`, and explore the codebase independently. So every detail we inject falls into one of four categories:

| Category | Definition | Token ROI |
|----------|-----------|-----------|
| **ESSENTIAL** | Agent would NOT discover this, or would make the wrong decision without it | Maximum — prevents wrong implementation |
| **HELPFUL** | Agent could discover this but the prompt saves 5-15 tool calls | Medium — saves time, not direction |
| **REDUNDANT** | Agent would find this immediately from imports or obvious code paths | Zero — wastes optimizer + executor tokens |
| **WRONG** | Prompt claims something that doesn't match the codebase | Negative — actively misleads |

## Subagent Verification Experiment

### Method

A subagent simulating an agentic executor received the optimized prompt for the stale-file detection task. It was instructed to read the actual codebase and classify each claim. The subagent used 75 tool calls over 292 seconds — reading `repo_index_service.py`, `models.py`, `github_client.py`, `main.py`, `config.py`, and tracing imports, function signatures, and patterns.

### Prompt Under Test (2184 chars, 26 verifiable claims)

```
Implement an incremental staleness detector for `RepoFileIndex` as a background
asyncio task in the backend. The task compares per-file SHAs from the stored index
against the current HEAD tree from GitHub, then selectively re-embeds only the
changed files — no full reindex via `build_index`.

## Behavior
[4-step algorithm with three-way file classification]

## Scheduling & concurrency
[asyncio.Event pattern, interval, coordination via RepoIndexMeta.status]

## Constraints
[5 bullet points: token handling, SSE events, cache invalidation, fast path, architecture]
```

### Results

```
ESSENTIAL:  12/26  (46%)  — Agent would NOT discover or would get wrong
HELPFUL:     8/26  (31%)  — Agent could discover but prompt saved effort
REDUNDANT:   4/26  (15%)  — Agent would find immediately from code
WRONG:       0/26  (0%)   — Zero factual errors
```

### Classification Detail

#### ESSENTIAL (12 claims — keep these)

These are the high-value claims that prevent wrong implementation decisions:

| # | Claim | Why essential |
|---|-------|---------------|
| 1 | `_curated_cache` must be cleared after updates | Hidden module-level dict with 5-min TTL. Agent would almost certainly miss this, causing stale embeddings for up to 5 minutes after incremental updates |
| 2 | Use `RepoIndexMeta.status` as coordination signal — skip `"indexing"`/`"pending"` | Non-obvious locking mechanism. `build_index` deletes all rows before rebuilding — concurrent incremental sync would cause data loss |
| 3 | Warm-path timer pattern: `asyncio.Event` + configurable interval | Specific codebase pattern at `main.py:912-1028`. Without this, agent would use simpler `asyncio.sleep()` loop |
| 4 | Filter new files through `_is_indexable()` | Module-level function (not a method) that checks extension allowlist + size cap + test exclusion. Agent might skip this, polluting index with non-code files |
| 5 | Bounded concurrency via `Semaphore(10)` matching `build_index` | Non-obvious value. Agent might use unbounded concurrency or a different limit |
| 6 | Update `RepoIndexMeta.head_sha` on success | Without this, staleness check re-processes same changes every cycle — infinite loop |
| 7 | HEAD SHA fast-path: skip repo if SHA unchanged | Without this, every cycle fetches the full tree for every repo even when nothing changed |
| 8 | Token expiry resilience: skip repo, don't fail entire sweep | GitHub tokens expire after 8h. Single expired token could crash the background loop for all repos |
| 9 | `file_sha` stores the GitHub tree SHA (not a content hash) | Column name is ambiguous. Agent might compute a different hash and never match |
| 10 | Add as method on `RepoIndexService`, not a standalone function | Architectural placement. Without this, agent might violate layer rules |
| 11 | Schedule from `main.py` lifespan, not `on_event("startup")` | All background tasks use the lifespan pattern. Wrong pattern would break shutdown sequencing |
| 12 | Must not run concurrently with `build_index` for same repo | `build_index` does DELETE-all-then-reinsert. Concurrent incremental sync = data corruption |

#### HELPFUL (8 claims — judgment call)

These save the agent 5-15 tool calls but wouldn't cause wrong decisions if omitted:

| # | Claim | Time saved |
|---|-------|-----------|
| 1 | `GitHubClient.get_tree()` and `get_branch_head_sha()` method names | Agent would find in 2-3 grep calls |
| 2 | Build `dict[file_path, tree_sha]` from tree blobs | Correct data structure, but agent would figure this out from the API response |
| 3 | Three-way classification (modified/deleted/added) | Standard algorithm, agent would derive |
| 4 | Extract structured outlines before embedding | Agent would copy the pattern from `build_index` |
| 5 | Upsert with `file_sha`, `outline`, `embedding`, `updated_at` | All four columns discoverable from the model definition |
| 6 | Default interval ~10 minutes | Design guidance, not verifiable |
| 7 | Emit SSE event with counts | Event bus pattern is well-established in the codebase |
| 8 | `query_curated_context` picks up new embeddings (the "why" behind cache clearing) | Reinforces the ESSENTIAL cache-clearing claim |

#### REDUNDANT (4 claims — should be removed)

These waste tokens — the agent discovers them immediately from imports:

| # | Claim | Why redundant |
|---|-------|--------------|
| 1 | "`RepoFileIndex` exists as a model" | First thing any agent reads from the service imports |
| 2 | "`RepoIndexMeta` has `status='ready'`" | Visible in the model definition, used throughout the service |
| 3 | "Fetch content via `GitHubClient.get_file_content()`" | Found immediately from `_read_file` in the existing service |
| 4 | "Embed via `EmbeddingService.aembed_texts()`" | Found immediately from `build_index` |

## Insight: Three Layers of Prompt Value

The analysis reveals three distinct layers of prompt content, each with different ROI for agentic executors:

```
┌─────────────────────────────────────────────────────────┐
│  LAYER 1: Vocabulary Anchors (~50 tokens)               │
│  Names: RepoFileIndex, file_sha, build_index(),         │
│  get_tree(), _curated_cache, _is_indexable              │
│  ROI: Steers agent's search to right files immediately  │
│  Verdict: ALWAYS INCLUDE                                │
├─────────────────────────────────────────────────────────┤
│  LAYER 2: Architectural Constraints (~200 tokens)       │
│  "Don't trigger full reindex"                           │
│  "Use RepoIndexMeta.status for coordination"            │
│  "Clear _curated_cache after updates"                   │
│  "Schedule from main.py lifespan"                       │
│  ROI: Prevents wrong decisions the agent can't recover  │
│  Verdict: ALWAYS INCLUDE                                │
├─────────────────────────────────────────────────────────┤
│  LAYER 3: Implementation Details (~500 tokens)          │
│  "Semaphore pattern matching build_index"               │
│  "GitHubClient.get_file_content() with concurrency"     │
│  "Extract structured outlines, embed via aembed_texts"  │
│  ROI: Describes code the agent reads anyway             │
│  Verdict: TRIM for agentic, KEEP for non-agentic        │
└─────────────────────────────────────────────────────────┘
```

**Key insight**: Layers 1-2 cost ~250 tokens and deliver 46% ESSENTIAL value. Layer 3 costs ~500 tokens and delivers mostly HELPFUL/REDUNDANT value that an agentic executor discovers independently. The optimizer currently spends equal effort on all three layers.

## Scoring System Recalibration

During the 13-run experiment, we identified and fixed three scoring biases:

### 1. Conciseness Brevity Bias (fixed)

**Problem**: The LLM scorer treated short prompts as concise. The raw prompt ("implement a background task...") scored 7-9 on conciseness simply because it was 178 chars. Any optimization that added constraints got penalized.

**Fix**: Added calibration note to `scoring.md`: "SHORT IS NOT CONCISE. A vague one-sentence prompt scores low on conciseness because it is SPARSE — it communicates almost nothing per word."

**Impact**: Conciseness delta flipped from -1.2 (penalizing optimization) to +0.4 (rewarding density).

### 2. Faithfulness Originality Bias (fixed)

**Problem**: The scorer gave the raw prompt higher faithfulness than the optimized version because it was "closer to what the user wrote." Faithfulness scores for the identical raw prompt ranged from 4.2 to 6.2 (spread of 2.0).

**Fix**: Added anti-pattern note: "Do NOT give the original prompt higher faithfulness simply because it's closer to what the user wrote. Additions that serve the original goal are faithful enhancements."

**Impact**: Faithfulness variance reduced, original prompt scores more consistently.

### 3. Structure Format Bias (fixed)

**Problem**: The scorer required headers and bullet lists for a score above 6, even when a dense paragraph was the appropriate format for a low-complexity task.

**Fix**: Rewrote the structure dimension to score FORMAT MATCH — a flat paragraph scores 7 for a 2-constraint task, the same as headers for an 8-constraint task. "Penalize mismatches in either direction."

### 4. Dimension Weight Rebalance (v3)

| Dimension | v2 weight | v3 weight | Rationale |
|-----------|-----------|-----------|-----------|
| Clarity | 0.20 | **0.22** | Primary value dimension |
| Specificity | 0.20 | **0.22** | Primary value dimension |
| Structure | 0.15 | 0.15 | Unchanged |
| Faithfulness | 0.25 | **0.26** | Most important — wrong intent = failure |
| Conciseness | 0.20 | **0.15** | Reduced — optimization structurally trades conciseness for specificity |

## Optimizer Instruction Updates

### Task-Type Depth Scaling (new)

The optimizer now scales output depth based on task type:

| Task type | Depth | Char range | Format |
|-----------|-------|------------|--------|
| Specs, PRDs, architecture plans | **High** | 1000-3000+ | `##` headed sections, exhaustive |
| Agentic tasks (multi-step autonomous) | **High** | 1000-3000+ | `##` sections, failure modes, rollback |
| Multi-concern features | **High** | 800-2000 | `##` per concern |
| Single-concern features | **Medium** | 400-1000 | Paragraph + bullet constraints |
| Refactoring tasks | **Medium** | 400-800 | What to change, why, what must not break |
| Bug fixes | **Low** | 100-400 | Symptom, expected behavior, reproduction |
| Simple questions | **Low** | 50-200 | Sharpen question + context |

### Density Over Brevity (updated)

Old guidance: "Be concise where the strategy permits."
New guidance: "Maximize useful detail, not brevity. A 1500-char prompt with 10 load-bearing constraints is better than a 500-char prompt that omits 7 of them."

### Dynamic Format (updated)

Old guidance: Fixed format rules based on constraint count.
New guidance: LLM decides format based on task scope, risk surface, and executor needs. A senior developer's "would I ask about this before starting?" test determines whether a detail earns its place.

## Current Configuration

| Constant | Value | File |
|----------|-------|------|
| `INDEX_OUTLINE_MAX_CHARS` | 2000 | `config.py` |
| `INDEX_CURATED_MAX_CHARS` | 80000 | `config.py` |
| `INDEX_CURATED_MIN_SIMILARITY` | 0.20 | `config.py` |
| `_CROSS_DOMAIN_MIN_SIM` | 0.30 | `repo_index_service.py` |
| `INDEX_CURATED_MAX_PER_DIR` | 3 | `config.py` |
| `INDEX_DOMAIN_BOOST` | 1.3 | `config.py` |
| `MAX_CODEBASE_CONTEXT_CHARS` | 100000 | `config.py` |
| `SCORING_FORMULA_VERSION` | 3 | `pipeline_contracts.py` *(historical — v0.4.9 bumped to 4 for per-task-type weights via `get_dimension_weights(task_type)`; analysis-class uses clarity/specificity/structure 0.25/0.25/0.20, faithfulness/conciseness 0.20/0.10)* |

## Full Run History (V1 + V2)

| Run | Phase | Changes | Files | Graph | Budget | Overall | Faith delta | Conc delta | Vocab |
|-----|-------|---------|-------|-------|--------|---------|-------------|------------|-------|
| 1 | V1 | Baseline: 500c outlines, 0.30 sim | 7 | 0 | 10.8% of 30K | 6.46 | +1.2 | +0.9 | 5 |
| 2 | V1 | 2000c outlines | 8 | 0 | 17.3% of 30K | 6.45 | +2.6 | -0.6 | 5 |
| 3 | V1 | 0.20 sim threshold | 23 | 0 | 35.4% of 30K | 7.25 | +1.1 | -1.7 | 5 |
| 4 | V1 | + test filter + cross-domain | 5 | 0 | 8.1% of 30K | 6.29 | +1.7 | -1.1 | 3 |
| 5 | V1 | Full source, 80K cap | 5 | 0 | 76.9% of 80K | 7.03 | +3.6 | -0.4 | 5 |
| 6 | V1 | + import graph (append) | 6 | 1 | 86.3% of 80K | 6.66 | +1.6 | -3.1 | 7 |
| 7 | V1 | + interleaved packing | 8 | 5 | 98.9% of 80K | 6.39 | +2.1 | -1.2 | 10 |
| 8 | V2 | Scoring recalibration | 8 | 5 | 98.9% of 80K | 6.75 | +2.1 | **+0.4** | 10 |
| 9 | V2 | Task-type depth scaling | 8 | 5 | 98.9% of 80K | 7.41 | +2.1 | -1.5 | 15 |
| 10 | V2 | Density over brevity | 8 | 5 | 98.9% of 80K | 7.26 | +3.3 | -1.5 | 15+ |
| 11 | V2 | Subagent verification | — | — | — | — | — | — | — |

Note: Run 9 was the SSE health monitoring prompt (different task type — frontend). Runs 10+ use the stale-file prompt with cumulative improvements.

## Next Steps

### 1. Agentic Executor Optimization

Test whether stripping Layer 3 (implementation details) from the prompt produces equivalent executor outcomes. Hypothesis: Layers 1-2 alone (~250 tokens) steer the agent to the right files and decisions; Layer 3 (~500 tokens) is discovered independently.

**Experiment design**:
- **Control**: Current full prompt → subagent executor → measure tool calls + implementation quality
- **Treatment**: Prompt with only ESSENTIAL claims → same subagent → compare

### 2. Dual-Mode Output

Consider two output modes in the optimizer:
- **Agentic mode**: Vocabulary anchors + architectural constraints only. Assumes executor has codebase access.
- **Specification mode**: Full detail including implementation patterns. For PRDs, specs, and non-agentic executors.

The task type classification already exists — the optimizer could select mode based on `task_type` + executor context.

### 3. Diverse Prompt Validation

The V1-V2 analysis used two prompts (backend stale-file + frontend SSE health). Need to validate on:
- Cross-cutting prompts (touching both backend and frontend)
- Documentation/writing prompts (different domain entirely)
- Simple bug fix prompts (should produce minimal output)
- Architecture/spec prompts (should produce exhaustive output)

## Phase 3: Quality Signal Matrix (in progress)

The optimizer currently uses ~40% of available quality signals. Six new signals identified — total cost: ~710 tokens (< 1% of context window).

### Signal Inventory

| Signal | Status | Tokens | Value |
|--------|--------|--------|-------|
| Heuristic analysis | Injected | ~100 | Task type, domain, weaknesses |
| Codebase context | Injected | ~20K | Full source files via import graph |
| Adaptation state | Injected | ~125 | Strategy affinities |
| Applied patterns | Injected | ~400 | Cluster + global meta-patterns |
| Few-shot examples | Injected | ~500 | 2 similar past optimizations |
| Strategy instructions | Injected | ~500 | Strategy-specific techniques |
| **Learned phase weights** | **Computed, not injected** | **~30** | Phase emphasis for this task type |
| **Strategy perf by domain** | **Computed, not injected** | **~60** | Best strategies for domain+task_type |
| **Domain keywords** | **Computed, not injected** | **~40** | Vocabulary anchors |
| **Best exemplar** | **Not captured** | **~500** | Highest-scoring similar optimization |
| **Anti-pattern hints** | **Not captured** | **~60** | Strategies that failed for this domain |
| **Output coherence** | **Computed, not injected** | **~20** | Consistency vs diversity signal |

### Token Budget Summary

```
Current optimizer input:     ~31,000 tokens (17% of Opus 200K)
New signals:                 +   710 tokens
Total after enrichment:      ~31,710 tokens (17.4%)
Headroom remaining:          ~168,000 tokens
```

Implementation tracked in: `/home/drei/.claude/plans/ancient-dazzling-flamingo.md`
