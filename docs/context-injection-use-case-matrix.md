# Context Injection Use-Case Matrix

> Decision document for optimizing the enrichment engine across user personas, entry points, and execution tiers. Produced 2026-04-12. **Consolidation shipped in v0.3.30** — see Appendix C for decision log.

## Post-Consolidation Architecture (v0.3.30)

The enrichment engine consolidated 7 layers into 4 active context sources, gated by auto-selected profiles:

| Source | Contents | Profile Gate |
|--------|----------|--------------|
| `heuristic_analysis` | 6-layer classifier (A1-A4), domain signals, divergence detection | All profiles |
| `codebase_context` | Explore synthesis + curated retrieval + workspace fallback | `code_aware` only |
| `strategy_intelligence` | Strategy rankings + adaptation feedback + anti-patterns + domain keywords | Skip `cold_start` |
| `applied_patterns` | Meta-pattern injection via composite fusion | Skip `cold_start` |

**Profiles**: `code_aware` (coding + repo), `knowledge_work` (non-coding tasks), `cold_start` (< 10 optimizations).

## 1. Pre-Consolidation System Inventory (Historical)

> The layer numbering below reflects the pre-consolidation architecture. L1 was collapsed into L3 fallback, L4+L6 merged into `strategy_intelligence`, L7 merged into L5 pipeline. See Appendix C.

### 1.1 Context Injection Layers (Pre-consolidation: 7)

| # | Layer | Cost | Always-On | Precondition | v0.3.30 Status |
|---|-------|------|-----------|--------------|----------------|
| L1 | Workspace Guidance | Low (filesystem scan) | Yes | MCP roots or filesystem path | **Collapsed into L3 fallback** |
| L2 | Heuristic Analysis | Negligible (regex + keyword) | Yes | None | Active (+ A1-A4 accuracy pipeline) |
| L3a | Codebase Synthesis | Medium (Haiku LLM, cached) | No | Linked repo with completed index | Active |
| L3b | Curated Retrieval | High (embedding search + 80K chars) | No | Linked repo with completed index | Active (task-gated) |
| L4 | Adaptation State | Low (DB query) | No | `enable_strategy_intelligence` pref + feedback history | **Merged into strategy_intelligence** |
| L5 | Applied Patterns | Medium (composite fusion + DB) | No | Taxonomy clusters with meta-patterns | Active |
| L6 | Performance Signals | Low (DB aggregate) | No | Optimization history (3+ per strategy) | **Merged into strategy_intelligence** |
| L7 | Few-shot Examples | Medium (dual embedding search) | No | Optimization history (score >= 7.5) | Active (within L5 pipeline) |

### 1.2 Embedding Dimensions (Current: 5 fusion signals)

| Signal | Source | Bootstrap Requirement |
|--------|--------|----------------------|
| Topic (raw) | `embed(raw_prompt)` | None — always available |
| Transformation | `mean(embed(opt) - embed(raw))` per cluster | 2+ optimizations per cluster |
| Output (optimized) | `mean(embed(optimized))` per cluster | 2+ optimizations per cluster |
| Pattern (global) | GlobalPattern embeddings | 5+ clusters with avg_score >= 6.0, ≥ 2 distinct source projects (`GLOBAL_PATTERN_PROMOTION_MIN_PROJECTS`) |
| Qualifier | `QualifierIndex` per-cluster qualifier centroids (organic vocabulary from `DomainSignalLoader.generated_qualifiers`) | Enriched vocabulary generation during warm Phase 4.95 + 5 |

Weight profiles in `services/taxonomy/fusion.py:_DEFAULT_PROFILES`. Pre-qualifier stored profiles default `w_qualifier=0.0` via `PhaseWeights.from_dict()` backward-compat.

### 1.3 Execution Tiers

| Tier | LLM Location | Caller Gate | Full Pipeline | Context Sources |
|------|-------------|-------------|---------------|-----------------|
| **Internal** | Local (CLI/API key) | REST or MCP | Yes (3 phases) | 4 (profile-gated) |
| **Sampling** | Hybrid Phase Routing (v0.4.2): analyze/score/suggest on internal; optimize via IDE LLM MCP sampling. Falls back to all-sampling when no internal provider. | MCP only (`_can_sample()` enforces `caller == "mcp"`) | Yes (3 phases) | 4 (profile-gated) |
| **Passthrough** | External (user's LLM) | REST or MCP | No (assembled template returned) | 4 (embedded in template) |

### 1.4 Entry Points

| Entry Point | Caller Type | Available Tiers | Typical User |
|-------------|-------------|-----------------|--------------|
| **Web UI** | REST | Internal, Passthrough | Any user |
| **IDE MCP Tool** | MCP | Internal, Sampling, Passthrough | Developer with IDE |
| **REST API Direct** | REST | Internal, Passthrough | CI/CD, automation, future integrations |
| **Batch Seed** | REST/MCP | Internal | Power user, taxonomy bootstrapping |

---

## 2. User Personas

### 2.1 Current Personas

| ID | Persona | Connection State | Primary Task Types | Entry Point |
|----|---------|-----------------|-------------------|-------------|
| **P1** | Developer (full stack) | IDE + GitHub repo | coding, system, data | IDE MCP tools |
| **P2** | Developer (IDE only) | IDE, no repo | coding, writing | IDE MCP tools |
| **P3** | Developer (web only) | Web UI, no IDE/repo | coding, writing, analysis | Web UI |
| **P4** | Power user (web) | Web UI + GitHub repo | coding, analysis, creative | Web UI |

### 2.2 Future Personas (ADR-006 Verticals)

| ID | Persona | Integration Provider | Primary Task Types | Entry Point |
|----|---------|---------------------|-------------------|-------------|
| **P5** | Marketing/Content | Google Drive, Notion | writing, creative | Web UI |
| **P6** | Product/PM | Notion, Confluence | writing, analysis | Web UI |
| **P7** | Legal/Compliance | Document mgmt system | writing, analysis, system | Web UI |
| **P8** | Education/Training | LMS provider | writing, creative, analysis | Web UI |
| **P9** | Data/Analytics | Database connections | data, analysis, coding | Web UI or API |
| **P10** | Automation/CI | REST API | coding, system | REST API direct |

---

## 3. Use-Case Matrix

### 3.1 Layer Value by Persona

Scale: **H** = High value (core to workflow), **M** = Medium (helpful), **L** = Low (marginal), **0** = Zero (not applicable), **N** = Not yet available

| Layer | P1 Dev+IDE+Repo | P2 Dev+IDE | P3 Dev Web | P4 Power+Repo | P5 Marketing | P6 Product | P7 Legal | P10 CI/CD |
|-------|----------------|------------|------------|---------------|-------------|-----------|---------|----------|
| L1 Workspace | H | H | 0 | 0 | 0 | 0 | 0 | 0 |
| L2 Heuristic | H | H | H | H | H | H | H | H |
| L3a Synthesis | H | 0 | 0 | H | N | N | N | N |
| L3b Curated | H | 0 | 0 | M | N | N | N | N |
| L4 Adaptation | M | M | M | M | H | H | H | L |
| L5 Patterns | H | H | M | H | H | H | H | M |
| L6 Perf Signals | M | M | L | M | M | M | M | H |
| L7 Few-shot | H | H | M | H | H | H | H | H |

### 3.2 Layer Value by Task Type

| Layer | coding | writing | analysis | creative | data | system | general |
|-------|--------|---------|----------|----------|------|--------|---------|
| L1 Workspace | H | L | L | 0 | M | H | 0 |
| L2 Heuristic | H | H | H | H | H | H | M |
| L3a Synthesis | H | L | M | 0 | M | H | 0 |
| L3b Curated | H | L | M | 0 | H | H | 0 |
| L4 Adaptation | M | H | M | H | M | M | M |
| L5 Patterns | H | H | H | M | H | H | M |
| L6 Perf Signals | M | M | L | L | M | M | L |
| L7 Few-shot | H | H | M | H | H | H | M |

### 3.3 Layer Value by Connection State

| Layer | No connection | IDE only | Repo only | IDE + Repo | Future provider |
|-------|--------------|----------|-----------|------------|-----------------|
| L1 Workspace | 0 | H | 0 | H | 0 |
| L2 Heuristic | H | H | H | H | H |
| L3a Synthesis | 0 | 0 | H | H | N (per provider) |
| L3b Curated | 0 | 0 | H | H | N (per provider) |
| L4 Adaptation | M | M | M | M | M |
| L5 Patterns | M | M | M | H | M |
| L6 Perf Signals | M | M | M | M | M |
| L7 Few-shot | M | M | M | H | M |

---

## 4. Overlap & Redundancy Analysis

### 4.1 Identified Overlaps

```
OVERLAP 1: Performance Signals (L6) ≈ Adaptation State (L4)
├── Both answer: "which strategy should the optimizer use?"
├── L6 source: automated score aggregates from DB
├── L4 source: manual feedback (thumbs up/down)
├── Unique L4 value: blocking gate (< 0.3 approval), degenerate detection
├── Unique L6 value: domain keywords, anti-pattern warnings
└── Verdict: PARTIAL OVERLAP — unique signals exist in both

OVERLAP 2: Applied Patterns (L5) ≈ Few-shot Examples (L7)
├── Both answer: "what worked before for prompts like this?"
├── L5 source: abstract meta-pattern rules from taxonomy
├── L7 source: concrete raw→optimized pairs ranked by score
├── Unique L5 value: cross-cluster and global patterns (generalized techniques)
├── Unique L7 value: concrete demonstrations with actual transformations
└── Verdict: COMPLEMENTARY — different abstraction levels serve different purposes.
    Abstract rules guide strategy; concrete examples demonstrate execution.

OVERLAP 3: Workspace Guidance (L1) ≈ Codebase Synthesis (L3a)
├── Both answer: "what is this project's tech stack?"
├── L1 source: manifest scanning (package.json, pyproject.toml)
├── L3a source: Haiku LLM synthesis of full codebase
├── When both present: L3a strictly subsumes L1
├── When only L1: IDE connected but no repo linked
└── Verdict: L1 is a FALLBACK for L3a, not an independent layer
```

### 4.2 Diminishing Returns Assessment

| Layer | Bootstrap Cost | Value at 0 history | Value at 50 history | Value at 500 history |
|-------|---------------|-------------------|--------------------|--------------------|
| L1 Workspace | None | Full (if IDE) | Full | Full |
| L2 Heuristic | None | Full | Full | Full |
| L3a Synthesis | Repo link + index | Full (if repo) | Full | Full |
| L3b Curated | Repo link + index | Full (if repo) | Full | Full |
| L4 Adaptation | 5+ feedbacks | Zero | Medium | High |
| L5 Patterns | 10+ optimizations | Zero | Medium | High |
| L6 Perf Signals | 3+ per strategy | Zero | Low | Medium |
| L7 Few-shot | 1+ scored opt | Zero | High | High |

**Cold-start observation:** For a brand-new user with no history and no repo, only L2 (heuristic analysis) provides value. Everything else is either absent (L1, L3) or empty (L4-L7). The pipeline degrades gracefully but the first 10-20 optimizations run with minimal enrichment.

### 4.3 Context Window Budget

At full enrichment, the context window consumption is:

| Layer | Typical Size | Max Cap | % of 200K window |
|-------|-------------|---------|-------------------|
| L1 Workspace | 2-5K chars | 20K | 1-2.5% |
| L2 Heuristic | 200-500 chars | unbounded | < 0.3% |
| L3a Synthesis | 3-8K chars | — | 1.5-4% |
| L3b Curated | 20-80K chars | 80K | 10-40% |
| L4 Adaptation | 500-2K chars | 5K | 0.3-1% |
| L5 Patterns | 500-2K chars | ~3K | 0.3-1% |
| L6 Perf Signals | 200-500 chars | ~1K | < 0.3% |
| L7 Few-shot | 2-4K chars | 4K | 1-2% |
| **Total** | **28-102K chars** | **133K** | **14-51%** |

**L3b Curated Retrieval dominates the budget** at up to 40% of the context window. For non-coding prompts ("write a marketing email"), this is wasted context that could instead be used for longer few-shot examples or deeper pattern context.

---

## 5. Recommended Architecture: Tier-Adapted Enrichment

> **Shipped in v0.3.30.** The profiles below were implemented as `select_enrichment_profile()` in `context_enrichment.py`. L1-L7 nomenclature in the tables is pre-consolidation — see the Post-Consolidation Architecture table at the top for current source names. L4+L6 → `strategy_intelligence`, L1 → fallback within `codebase_context`, L7 → within `applied_patterns` pipeline.

### 5.1 Core Principle

**Match enrichment depth to the use case, not the tier.**

Currently all three tiers receive identical context layers. The tier determines *how* context is processed (local LLM vs. MCP sampling vs. assembled template), but not *what* context is gathered. This wastes budget on layers that don't serve the user's actual task.

### 5.2 Proposed Enrichment Profiles

Instead of one-size-fits-all enrichment, define three enrichment **profiles** based on detected use-case signals:

#### Profile A: Code-Aware (Task type: coding, system, data + repo linked)

All layers active. Curated retrieval at full budget. This is the current behavior and correct for developer workflows with codebase context.

| Layer | Status | Budget |
|-------|--------|--------|
| L1 Workspace Guidance | Active (fallback to L3a) | 20K |
| L2 Heuristic Analysis | Active | — |
| L3a Codebase Synthesis | Active | included |
| L3b Curated Retrieval | **Active — full budget** | 80K |
| L4 Adaptation State | Active | 5K |
| L5 Applied Patterns | Active | ~3K |
| L6 Performance Signals | Active | ~1K |
| L7 Few-shot Examples | Active | 4K |

#### Profile B: Knowledge Work (Task type: writing, analysis, creative + any connection)

Codebase context is deprioritized. Few-shot examples and patterns get expanded budget since prior successful transformations are the primary value signal.

| Layer | Status | Budget |
|-------|--------|--------|
| L1 Workspace Guidance | **Skipped** (not relevant) | 0 |
| L2 Heuristic Analysis | Active | — |
| L3a Codebase Synthesis | **Skipped** (unless task mentions code) | 0 |
| L3b Curated Retrieval | **Skipped** (unless task mentions code) | 0 |
| L4 Adaptation State | Active | 5K |
| L5 Applied Patterns | **Active — expanded** | ~5K |
| L6 Performance Signals | Active | ~1K |
| L7 Few-shot Examples | **Active — expanded budget** | 8K (2x) |

**Budget freed:** ~80K chars from L3b, redistributed to L5 and L7.

#### Profile C: Cold Start (< 10 optimizations in history, any task type)

Minimal enrichment. Focus on heuristic classification and whatever codebase context exists. Patterns and few-shots are empty or near-empty — don't waste API calls querying for them.

| Layer | Status | Budget |
|-------|--------|--------|
| L1 Workspace Guidance | Active (if available) | 20K |
| L2 Heuristic Analysis | Active | — |
| L3a Codebase Synthesis | Active (if repo) | included |
| L3b Curated Retrieval | Active (if repo) | 80K |
| L4 Adaptation State | **Skipped** (no feedback yet) | 0 |
| L5 Applied Patterns | **Skipped** (no clusters yet) | 0 |
| L6 Performance Signals | **Skipped** (no history yet) | 0 |
| L7 Few-shot Examples | **Skipped** (no scored examples) | 0 |

**Saves:** 4 unnecessary DB queries per optimization during bootstrap.

### 5.3 Profile Selection Logic

```
if optimization_count < 10:
    profile = C (cold start)
elif task_type in (coding, system, data) AND repo_linked:
    profile = A (code-aware)
else:
    profile = B (knowledge work)
```

This is a pure function of observable state — no new preferences required.

---

## 6. Consolidation Recommendations (All Shipped in v0.3.30)

### 6.1 Merge: L6 Performance Signals + L4 Adaptation State → "Strategy Intelligence"

**Rationale:** Both layers answer "which strategy works for this task type and domain." Merging eliminates redundant DB queries and provides a single coherent strategy recommendation.

**Implementation:**
- Single `resolve_strategy_intelligence()` method combining:
  - Score-based rankings (from L6)
  - Feedback-based affinity (from L4)
  - Blocking gate (from L4)
  - Degenerate detection (from L4)
  - Anti-patterns (from L6)
  - Domain keywords (from L6)
- Single template variable: `{{strategy_intelligence}}`
- Single preference gate: `enable_strategy_intelligence`

**Savings:** 1 fewer layer to track, 1 fewer template variable, unified rendering.

### 6.2 Collapse: L1 Workspace Guidance → Fallback within L3 Codebase Context

**Rationale:** When a repo is linked, L3a synthesis already contains everything L1 detects. L1 only has unique value when IDE is connected without a repo — a narrow case.

**Implementation:**
- `_resolve_codebase_context()` tries synthesis first
- If no synthesis available, falls back to workspace guidance scan
- Single output field: `codebase_context` (already the case)
- Remove `workspace_guidance` as a separate field from `EnrichedContext`

**Savings:** 1 fewer field, simpler template, no behavior change for users.

### 6.3 Do NOT Merge: L5 Applied Patterns and L7 Few-shot Examples

**Rationale (revised from initial assessment):** These serve genuinely different purposes that become critical at scale:
- **L5 Patterns** are generalized, cross-project techniques (ADR-005 GlobalPatterns). They transfer knowledge *across* users and projects. This is the horizontal scaling mechanism.
- **L7 Few-shots** are specific input→output demonstrations from the user's own history. They personalize the optimization to *this* user's style.

Merging them would lose the distinction between "universal technique" and "personal example." As the integration store brings non-developer verticals, global patterns become the primary cross-vertical knowledge transfer mechanism — they must remain independent.

### 6.4 Add: Task-Gated Codebase Context (Profile B)

**Rationale:** L3b curated retrieval consumes up to 40% of context window but provides zero value for non-coding prompts. The heuristic analyzer already classifies task type before enrichment runs.

**Implementation:**
```python
# In enrich(), after heuristic analysis:
skip_codebase = (
    effective_task_type in ('writing', 'creative')
    and not any(kw in raw_prompt.lower() for kw in ('code', 'api', 'function', 'database', 'schema'))
)
if not skip_codebase:
    codebase_context = await self._resolve_codebase_context(...)
```

**Savings:** ~80K chars freed for non-coding prompts, faster enrichment, no quality loss.

---

## 7. Tier Adaptation Strategy

### 7.1 Current State: Tier-Agnostic Enrichment

All three tiers receive identical `EnrichedContext`. The tier determines execution path, not enrichment depth. This is correct for feature parity but wasteful.

### 7.2 Proposed: Tier-Aware Optimization

| Aspect | Internal | Sampling | Passthrough |
|--------|----------|----------|-------------|
| Enrichment Profile | A, B, or C (auto-selected) | A, B, or C (auto-selected) | A, B, or C (auto-selected) |
| Few-shot budget | Standard (4K) | Standard (4K) | **Expanded (8K)** — external LLM benefits from more examples since it doesn't have pipeline context |
| Pattern detail | Standard | Standard | **Expanded** — include pattern source clusters and scores for transparency |
| Codebase context | Full | Full | **Summarized** — synthesis only, skip curated (external LLM can't reference file paths) |

**Rationale for passthrough differences:** The external LLM in passthrough mode receives a single assembled prompt. It doesn't have the multi-phase pipeline to iteratively refine. More concrete examples and less raw codebase context produces better results in single-shot mode.

---

## 8. Future Vertical Integration Impact

### 8.1 Integration Store (ADR-006 Prerequisite)

When the integration store ships, L3 (codebase context) becomes **provider-agnostic:**

| Provider | Content Type | L3a Synthesis | L3b Curated Retrieval |
|----------|-------------|---------------|----------------------|
| GitHub | Source code | Architecture overview | Semantic file search |
| Google Drive | Documents | Content theme summary | Relevant doc retrieval |
| Notion | Pages/DBs | Workspace structure | Related page retrieval |
| Confluence | Wiki pages | Knowledge base summary | Topic-matched pages |
| Local filesystem | Mixed | Project structure | File search |
| Figma | Design files | Component inventory | Related design specs |

**Key insight:** The `ContextProvider` protocol (`list_documents()` + `fetch_document()`) maps directly to L3's two-tier architecture (synthesis = summarize all documents, curated = retrieve relevant documents per prompt). No new layers needed — L3 generalizes.

### 8.2 Cross-Vertical Pattern Sharing

ADR-005's GlobalPattern tier enables knowledge transfer across verticals:

```
Developer optimizes: "Write API error handling middleware"
  → Pattern extracted: "Enumerate specific failure modes before writing handlers"

Marketing user optimizes: "Write objection-handling email sequence"
  → Same pattern surfaces: "Enumerate specific failure modes before writing handlers"
  → Reframed by optimizer: "Address specific customer objections before presenting solutions"
```

This requires L5 (Applied Patterns) to remain independent of L7 (Few-shots) since patterns are the cross-vertical bridge while few-shots are persona-specific.

### 8.3 Vertical-Specific Enrichment Profiles

As verticals are added, enrichment profiles can be extended:

| Profile | Trigger | L3 Provider | L5 Pattern Source | L7 Few-shot Pool |
|---------|---------|-------------|-------------------|------------------|
| A: Code-Aware | coding/system/data + repo | GitHub / Local FS | Coding clusters + global | Coding optimizations |
| B: Knowledge Work | writing/analysis/creative | — | Global patterns only | Same-task-type optimizations |
| D: Content Marketing | writing/creative + Google Drive | Google Drive | Marketing clusters + global | Marketing optimizations |
| E: Product Spec | analysis/writing + Notion | Notion | Product clusters + global | Product optimizations |

Profiles D and E are content-only additions (per ADR-006 playbook) — they require new seed agents and domain keywords but no engine changes.

---

## 9. Metrics & Observability

### 9.1 What to Measure

To validate enrichment profile effectiveness, track:

| Metric | Source | Purpose |
|--------|--------|---------|
| `enrichment_profile` | EnrichedContext metadata | Which profile was selected |
| `layers_active` | context_sources dict | Which layers actually produced content |
| `context_budget_used_pct` | enrichment_meta | How much of the context window was consumed |
| `score_by_profile` | Optimization.overall_score | Quality correlation per profile |
| `score_by_layer_presence` | Cross-reference context_sources × score | Per-layer quality contribution |
| `enrichment_duration_ms` | Timing | Performance cost per profile |
| `skip_codebase_rate` | Profile B activations | How often codebase context is correctly skipped |

### 9.2 Decision Triggers

| Signal | Action |
|--------|--------|
| Profile B scores within 5% of Profile A for coding tasks | L3b curated retrieval is not contributing — consider always skipping for that domain |
| L6+L4 merged "Strategy Intelligence" score delta < 2% vs separate layers | Consolidation validated |
| Cold-start users (Profile C) score > 80% of established users | Enrichment layers beyond L2 are luxury, not necessity |
| Cross-vertical GlobalPattern injection lifts score > 5% | Validates L5 independence from L7 |

---

## 10. Implementation Sequence

### Phase 1: Task-Gated Codebase Context (Low risk, high impact)
- Add `skip_codebase` logic in `enrich()` based on task type
- Gate L3b behind coding/system/data task types (with keyword escape hatch)
- Measure: context_budget_used_pct and score_by_profile for non-coding prompts
- **Files:** `services/context_enrichment.py`

### Phase 2: Enrichment Profiles (Medium risk, medium impact)
- Implement Profile A/B/C selection logic
- Add `enrichment_profile` to EnrichedContext metadata
- Cold-start skip for L4-L7 when history < 10
- Measure: enrichment_duration_ms by profile, score correlation
- **Files:** `services/context_enrichment.py`, `config.py`

### Phase 3: Strategy Intelligence Consolidation (Medium risk, low impact)
- Merge L6 + L4 into single `resolve_strategy_intelligence()`
- Unified template variable
- Rename preference gate
- **Files:** `services/context_enrichment.py`, `services/adaptation_tracker.py`, `prompts/optimize.md`, `prompts/adaptation.md`

### Phase 4: Workspace Guidance Collapse (Low risk, low impact)
- Make L1 a fallback within L3's resolution logic
- Remove `workspace_guidance` field from EnrichedContext
- Update all prompt templates
- **Files:** `services/context_enrichment.py`, `prompts/*.md`

### Phase 5: Passthrough-Specific Enrichment (Low risk, medium impact)
- Expand few-shot budget for passthrough tier
- Summarize-only codebase context for passthrough
- Include pattern provenance metadata
- **Files:** `services/passthrough.py`, `services/context_enrichment.py`

---

## Appendix A: Complete Layer × Persona × Task Matrix

```
Legend: H=High M=Medium L=Low 0=Zero N=Not yet available
Connection: [I]=IDE [R]=Repo [W]=Web only

                    P1[IR]  P2[I]  P3[W]  P4[WR]  P5[W]  P6[W]  P10[API]
                    ------  -----  -----  ------  -----  -----  --------
L1 Workspace         H       H      0      0       0      0      0
L2 Heuristic         H       H      H      H       H      H      H
L3a Synthesis        H       0      0      H       N      N      N
L3b Curated          H       0      0      M       N      N      N
L4 Adaptation        M       M      M      M       H      H      L
L5 Patterns          H       H      M      H       H      H      M
L6 Perf Signals      M       M      L      M       M      M      H
L7 Few-shot          H       H      M      H       H      H      H

By task type:        coding  writing analysis creative data  system general
                     ------  ------- -------- -------- ----  ------ -------
L1 Workspace          H       L       L        0       M     H      0
L2 Heuristic          H       H       H        H       H     H      M
L3a Synthesis         H       L       M        0       M     H      0
L3b Curated           H       L       M        0       H     H      0
L4 Adaptation         M       H       M        H       M     M      M
L5 Patterns           H       H       H        M       H     H      M
L6 Perf Signals       M       M       L        L       M     M      L
L7 Few-shot           H       H       M        H       H     H      M
```

## Appendix B: Embedding Fusion Signal Bootstrap Timeline

```
Optimizations:  0     5     10    25    50    100   500
                |     |     |     |     |     |     |
Topic (raw)     ████████████████████████████████████████  Always available
Transform       ░░░░░░████████████████████████████████    Needs 2+ per cluster
Output (opt)    ░░░░░░████████████████████████████████    Needs 2+ per cluster
Pattern         ░░░░░░░░░░░░░░░░░░░░██████████████████    Needs 5+ clusters, avg >= 6.0

Effective fusion: 1-signal → 3-signal → 4-signal
                  (topic)    (topic+     (full
                              transform+  composite)
                              output)
```

## Appendix C: Decision Log

| Date | Decision | Rationale | Status |
|------|----------|-----------|--------|
| 2026-04-12 | Do NOT merge L5 (Patterns) and L7 (Few-shots) | Cross-vertical knowledge transfer via GlobalPatterns requires L5 independence. ADR-005/006 alignment. | Validated |
| 2026-04-12 | Merge L6 (Perf Signals) + L4 (Adaptation) | Overlapping strategy guidance with unique sub-signals. Consolidation reduces template complexity. | **Shipped** — Phase 2 |
| 2026-04-12 | Collapse L1 (Workspace) into L3 (Codebase) | L1 is a strict subset of L3a when repo is linked. Fallback-only role doesn't justify independent layer. | **Shipped** — Phase 3 |
| 2026-04-12 | Task-gate L3b (Curated Retrieval) for non-coding | 40% context window for zero-value codebase files on writing/creative prompts. Heuristic task type gates this. | **Shipped** — Phase 1 |
| 2026-04-12 | Introduce enrichment profiles (A/B/C) | One-size-fits-all enrichment wastes budget. Profile selection is a pure function of observable state. | **Shipped** — Phase 4 |
| 2026-04-12 | Heuristic analyzer refresh needed | Task-gating and profile selection depend on accurate `task_type` classification. "Design a webhook system" misclassified as `creative` instead of `coding`. See [Heuristic Analyzer Refresh](heuristic-analyzer-refresh.md). | **Shipped** — v0.3.30 (A1+A2+A3+A4) |
| 2026-04-12 | Prompt-context divergence detection needed | PostgreSQL prompt vs SQLite codebase produced wrong-stack output. See [Heuristic Analyzer Refresh](heuristic-analyzer-refresh.md) section 5 + improvement 1d. | **Shipped** — v0.3.30 (B1+B2) |
| 2026-04-12 | Full action items roadmap created | 12 action items across 5 phases (A-E) with dependency tracking. See [Enrichment Consolidation Action Items](enrichment-consolidation-action-items.md). | Sprint 2 complete, Sprint 3 remaining |
