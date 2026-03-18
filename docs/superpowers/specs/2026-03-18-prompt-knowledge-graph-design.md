# Prompt Knowledge Graph — Design Spec

**Date:** 2026-03-18
**Status:** Approved
**Scope:** Self-building prompt pattern library with radial mindmap visualization, auto-suggestion, and cross-project adaptation.

---

## Problem

Optimizations accumulate as a flat chronological list with no organization. Developers cannot discover reusable patterns across prompts, projects, or frameworks. Valuable prompt engineering knowledge is locked inside individual optimization records instead of being extractable and transferable.

## Solution

A self-building knowledge graph that extracts reusable **meta-patterns** from every optimization, organizes them into **pattern families** by intent, and **auto-suggests** relevant patterns when the user submits a new prompt. The graph is visualized as an interactive **radial mindmap** that doubles as a generative prompt menu.

### Core Concepts

- **Pattern Family:** A cluster of semantically related optimizations sharing a common intent (e.g., "dependency injection refactoring", "API error handling"). Identified by an LLM-extracted `intent_label` and validated by embedding similarity.
- **Meta-Pattern:** A reusable, framework-agnostic prompt technique extracted from one or more optimizations within a family (e.g., "Enforce error boundaries at service layer with typed Result returns"). High-level enough to adapt to any framework or project.
- **Domain:** The development area a pattern targets: `backend`, `frontend`, `database`, `devops`, `security`, `fullstack`, `general`. Orthogonal to `task_type` — a "coding" prompt can be "frontend" or "database".
- **Bidirectional Enrichment:** When an existing pattern is applied to a new prompt, the optimization result also supplements the pattern family — contributing new meta-patterns or reinforcing existing ones.

### User Interaction Model

**Zero-friction by default.** The system auto-manages the knowledge graph. The only user action is approving a suggestion (1 click) or renaming a family label (optional).

**Typical flow:**
1. Developer pastes a prompt (usually LLM-generated from their IDE).
2. On paste detection (content delta > 50 chars, 300ms debounce), the system embeds the prompt and searches the knowledge graph.
3. If a pattern family matches above the suggestion threshold (cosine > 0.72), an inline banner appears with the family name, match %, and available meta-patterns.
4. User clicks [Apply] → meta-patterns injected into optimizer context. Or ignores → normal pipeline.
5. After optimization completes, a background job extracts new meta-patterns and merges them into the graph.

---

## Data Model

### Modified Table: `optimizations`

Two new columns:

| Column | Type | Purpose |
|--------|------|---------|
| `intent_label` | String, nullable | LLM-extracted intent phrase (3-6 words). Set during analyzer phase. |
| `embedding` | LargeBinary, nullable | 384-dim float32 vector of raw_prompt. Set by background job. |

### New Table: `pattern_families`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | String (UUID) | PK |
| `intent_label` | String | Human-readable family name |
| `domain` | String | `backend`, `frontend`, `database`, `devops`, `security`, `fullstack`, `general` |
| `task_type` | String | Maps to existing 7-value Literal |
| `centroid_embedding` | LargeBinary | Mean embedding of all member optimizations (384-dim) |
| `usage_count` | Integer | Times this family has been applied via auto-suggestion |
| `avg_score` | Float | Mean overall_score of member optimizations |
| `created_at` | DateTime | First seen |
| `updated_at` | DateTime | Last enriched |

### New Table: `meta_patterns`

| Column | Type | Purpose |
|--------|------|---------|
| `id` | String (UUID) | PK |
| `family_id` | String FK → pattern_families | Parent family |
| `pattern_text` | Text | Reusable pattern description |
| `embedding` | LargeBinary | 384-dim vector for sub-pattern similarity |
| `source_count` | Integer | How many optimizations contributed to this pattern |
| `created_at` | DateTime | |
| `updated_at` | DateTime | Last enriched |

### New Table: `optimization_patterns` (join)

| Column | Type | Purpose |
|--------|------|---------|
| `optimization_id` | String FK → optimizations | |
| `family_id` | String FK → pattern_families | |
| `meta_pattern_id` | String FK → meta_patterns, nullable | Specific sub-pattern if matched |
| `relationship` | String | `source` (contributed patterns) or `applied` (used existing patterns) |
| `created_at` | DateTime | |

PK: auto-increment `id` (Integer). Unique constraint on `(optimization_id, family_id, relationship)`. `meta_pattern_id` remains nullable — a record can link an optimization to a family without specifying a sub-pattern.

---

## Pipeline Integration

### Analyzer Phase (Synchronous)

Extend `prompts/analyze.md` to also extract:
- `intent_label` — concise 3-6 word intent phrase
- `domain` — one of the 7 domain values

Added to `AnalysisResult` in `pipeline_contracts.py` as **optional fields with defaults** (`intent_label: str = "general"`, `domain: str = "general"`). This ensures backward compatibility — if the LLM omits either field, the pipeline continues with safe defaults rather than aborting. The `analyze.md` prompt template must be updated atomically with the schema change to instruct the LLM to output these fields. Persisted to `optimizations` row alongside existing `task_type`. Same LLM call — two additional fields.

The `domain` is orthogonal to `task_type`:
- `task_type` = what kind of prompt (coding, writing, analysis, creative, data, system, general)
- `domain` = what area of development (backend, frontend, database, devops, security, fullstack, general)

### Post-Completion Background Job

New service: `PatternExtractorService` (`backend/app/services/pattern_extractor.py`)

Triggered after the pipeline completes and publishes the `optimization_created` event. Hook point: subscribe to `optimization_created` on the event bus and spawn the extraction as an `asyncio.create_task()` (not `BackgroundTasks`, since the pipeline runs inside an SSE generator that doesn't inject them).

**Extraction flow:**

```
1. Embed raw_prompt via embedding_service → 384-dim vector
2. Save embedding to optimizations.embedding
3. Cosine search against pattern_families.centroid_embedding
4. If best match > FAMILY_MERGE_THRESHOLD (0.78):
     → Merge into existing family
     → Update centroid as running mean: new_centroid = (old * n + new) / (n + 1)
     → Increment usage_count, recompute avg_score
     → Update domain to majority domain of members (recount on merge)
5. If no match above threshold:
     → Create new pattern_family with this optimization's embedding as initial centroid
6. Haiku LLM call: extract meta_patterns from the full optimization record
     Input: raw_prompt + optimized_prompt + intent_label + strategy_used + domain
     Output: list of 1-5 reusable meta-pattern descriptions
7. For each extracted meta_pattern:
     → Embed it
     → Cosine search existing meta_patterns within the family
     → If match > PATTERN_MERGE_THRESHOLD (0.82): enrich existing
       (increment source_count, update pattern_text if the new version is richer)
     → If no match: create new meta_pattern
8. Write optimization_patterns join records (relationship: "source")
9. Publish "pattern_updated" event on event bus
```

**New prompt template:** `prompts/extract_patterns.md` — instructs Haiku to extract framework-agnostic meta-patterns from a completed optimization.

### Auto-Suggestion on Paste

New service: `PatternMatcherService` (`backend/app/services/pattern_matcher.py`)

**Endpoint:** `POST /api/patterns/match`
- Request: `{ "prompt_text": "..." }`
- Response: `{ "family": { "id", "intent_label", "domain", "usage_count", "avg_score" }, "meta_patterns": [{ "id", "pattern_text", "source_count" }], "similarity": 0.87 }` or `null` (no match)

**Flow:**
```
1. Embed prompt_text via embedding_service
2. Cosine search against in-memory family centroid cache
3. If best match > SUGGESTION_THRESHOLD (0.72):
     → Return family + its meta_patterns + similarity score
4. Else: return null
```

Suggestion threshold (0.72) is intentionally lower than merge threshold (0.78) — suggest broadly, merge conservatively.

**When user applies a suggestion:**
- Frontend sends `applied_pattern_ids: [...]` as part of the optimize request
- Pipeline injects meta-pattern texts into the optimizer prompt context
- After completion, `optimization_patterns` records are written with `relationship: "applied"`

### Threshold Summary

| Threshold | Value | Purpose |
|-----------|-------|---------|
| `FAMILY_MERGE_THRESHOLD` | 0.78 | Cosine similarity to merge into existing family |
| `PATTERN_MERGE_THRESHOLD` | 0.82 | Cosine similarity to enrich existing meta-pattern |
| `SUGGESTION_THRESHOLD` | 0.72 | Cosine similarity to trigger auto-suggestion |
| `PASTE_CHAR_DELTA` | 50 | Minimum character change to trigger similarity check |
| `PASTE_DEBOUNCE_MS` | 300 | Debounce delay after paste/bulk-input detection |

All thresholds are configurable constants in `pattern_extractor.py` and `pattern_matcher.py`.

---

## Knowledge Graph Service & API

### Core Service: `KnowledgeGraphService`

Location: `backend/app/services/knowledge_graph.py`

**In-memory cache:**
- On startup: load all `pattern_families.centroid_embedding` and `meta_patterns.embedding` into numpy arrays
- On `pattern_updated` event: refresh the affected family's embeddings
- Lazy initialization — if zero families exist, the cache is empty and no work is done

**Methods:**
- `get_graph(depth: int = 2, family_id: str | None = None)` — full mindmap structure or subtree
- `search_patterns(query: str, top_k: int = 5)` — semantic search across families and sub-patterns
- `get_family_detail(family_id: str)` — family + meta_patterns + linked optimizations
- `get_stats()` — total families, total patterns, domain distribution, top families by usage

### Router: `backend/app/routers/patterns.py`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/patterns/graph` | GET | Full mindmap data |
| `/api/patterns/graph?family_id=X` | GET | Subtree for a specific family |
| `/api/patterns/match` | POST | Similarity check for auto-suggestion |
| `/api/patterns/families` | GET | List all families (paginated) |
| `/api/patterns/families/{id}` | GET | Family detail |
| `/api/patterns/families/{id}` | PATCH | Rename family (user label override) |
| `/api/patterns/search` | GET | Semantic search: `?q=error+handling&top_k=5` |

### Graph Response Shape

```json
{
  "center": {
    "total_families": 12,
    "total_patterns": 47,
    "total_optimizations": 156
  },
  "families": [
    {
      "id": "uuid",
      "intent_label": "dependency injection refactoring",
      "domain": "backend",
      "task_type": "coding",
      "usage_count": 8,
      "avg_score": 7.4,
      "meta_patterns": [
        {
          "id": "uuid",
          "pattern_text": "Service layer decoupling with interface contracts",
          "source_count": 3
        }
      ],
    }
  ],
  "edges": [
    {
      "from": "family-uuid-1",
      "to": "family-uuid-2",
      "shared_patterns": 2,
      "weight": 0.71
    }
  ]
}
```

The `edges` array captures cross-family relationships. **Edge computation algorithm:** For every pair of families, compute cosine similarity between their centroid embeddings. If similarity > `EDGE_THRESHOLD` (0.55), an edge is created with `weight` = the cosine similarity. Additionally, if an optimization's embedding is within `FAMILY_MERGE_THRESHOLD` of two different family centroids (near the merge boundary), this contributes +1 to `shared_patterns` count for that pair. Edges with `weight` < 0.55 and `shared_patterns` = 0 are pruned. This is computed lazily on `GET /api/patterns/graph` and cached until the next `pattern_updated` event.

### Event Integration

`pattern_updated` events flow through the existing event bus → SSE → frontend. The mindmap auto-refreshes, matching the existing pattern for `optimization_created` → History refresh.

---

## Frontend

### Radial Mindmap

**Activity Bar:** New icon (constellation/node icon) between History and GitHub. Switches Navigator to pattern library view.

**Two rendering modes:**

1. **Navigator mode (240px):** Compact list of families grouped by domain. Each row shows intent_label, usage_count badge, avg_score. Click to expand inline and see meta-patterns. Quick-browse mode.

2. **Expanded mode:** Click expand icon or double-click a family → opens full radial mindmap as an **editor tab** (like result tabs and diff tabs). Full editor area width. Interactive: zoom, pan, click nodes.

**Mindmap rendering:** SVG via D3.js (`d3-force` layout, `d3-zoom` interaction).

- **Center:** User summary (total families, total patterns)
- **Ring 1:** Domains, color-coded:
  - backend = `#a855f7` (purple)
  - frontend = `#f59e0b` (amber)
  - database = `#10b981` (green)
  - security = `#ef4444` (red)
  - devops = `#3b82f6` (blue)
  - fullstack = `#00e5ff` (cyan)
  - general = `#6b7280` (gray)
- **Ring 2:** Pattern families within each domain. Node size proportional to `usage_count`.
- **Ring 3:** Meta-patterns within each family (shown on hover/click).
- **Edges:** Cross-family connections as curved lines, opacity proportional to edge `weight`.

**Interactions:**
- Click family node → Inspector panel shows family detail (meta_patterns, linked optimizations, avg_score)
- Click meta-pattern node → option to "Apply to current prompt"
- Right-click family → rename (PATCH endpoint)

### Auto-Suggestion Banner

New component: `PatternSuggestion.svelte` (`frontend/src/lib/components/editor/`)

Positioned between prompt input and optimize button. Appears when `PatternMatcherService` returns a match.

```
┌─────────────────────────────────────────────────────────┐
│ ⟡ Matches "DI refactoring" pattern (87%)                │
│   3 meta-patterns available · avg score 7.4             │
│                                        [Apply] [Skip]   │
└─────────────────────────────────────────────────────────┘
```

- Slide-in animation (~200ms)
- Auto-dismiss after 10 seconds if no interaction
- [Apply] → injects `applied_patterns` into forge store → pipeline reads them
- [Skip] → dismiss immediately
- Hitting Optimize while banner is visible = implicit Skip (zero friction default)

### New Store: `patterns.svelte.ts`

- `families` — loaded from `/api/patterns/graph`
- `suggestion` — current auto-suggestion state (match result or null)
- `checkForPatterns(text: string)` — debounced embed + match call
- `applySuggestion()` / `dismissSuggestion()` — user actions
- Listens to `pattern_updated` SSE events for auto-refresh

### Paste Detection in `PromptEdit.svelte`

```
on:paste → if content delta > 50 chars → patternsStore.checkForPatterns(text)
on:input → if content delta > 50 chars within 100ms → same (covers programmatic insertion)
```

---

## Testing

### Backend

| Test file | Covers |
|-----------|--------|
| `tests/test_pattern_extractor.py` | Family creation, merge logic (above/below threshold), centroid running mean, meta-pattern deduplication, Haiku call mocking |
| `tests/test_pattern_matcher.py` | Similarity search, suggestion threshold, empty graph (cold start), response shape |
| `tests/test_knowledge_graph.py` | Graph building, edge computation, cache refresh on events, search ranking |
| `tests/test_patterns_router.py` | All endpoints, pagination, PATCH rename, error cases |

### Key Edge Cases

- **Cold start:** Zero families — matcher returns null, mindmap shows onboarding empty state
- **Single family:** No edges, mindmap shows single-node view
- **Intent label collision:** Same label, different domain — embedding distance + domain prevents false merge
- **Centroid drift:** Family centroid stabilizes as members grow (running mean converges)
- **Concurrent extraction:** Two optimizations completing simultaneously for the same family — atomic DB writes prevent corruption

### Frontend

- `PatternSuggestion.svelte` — renders on match, dismisses on skip/timeout, null = hidden
- `patterns.svelte.ts` — paste detection, debounce, apply injects into forge store
- Mindmap component — renders from graph data, click events, zoom/pan

---

## Rollout Strategy

Four independent, shippable phases:

### Phase 1: Backend Infrastructure
- Alembic migration (new tables + columns)
- `PatternExtractorService` + `PatternMatcherService` + `KnowledgeGraphService`
- `patterns` router with all endpoints
- `extract_patterns.md` prompt template
- Backend tests
- **Effect:** Background job silently builds graph from every new optimization. No UI yet.

### Phase 2: Auto-Suggestion
- `PatternSuggestion.svelte` component
- Paste detection in `PromptEdit.svelte`
- `patterns.svelte.ts` store
- Pipeline integration (`applied_patterns` parameter)
- **Effect:** Users see suggestions on paste. Graph continues building silently.

### Phase 3: Radial Mindmap
- Activity bar icon + Navigator compact view
- Editor tab full mindmap (D3.js)
- Inspector integration for family detail
- **Effect:** Users can explore and interact with their pattern portfolio.

### Phase 4: Cross-Project Adaptation
- When applying a pattern, optimizer receives meta-patterns + current workspace context (linked repo, workspace intelligence)
- Adaptation prompt template that maps meta-patterns to the specific framework/project
- **Effect:** "Apply to Spring Boot what you learned from FastAPI" comes alive.

---

## Configuration

All thresholds are named constants, tunable without code changes:

```python
# pattern_extractor.py
FAMILY_MERGE_THRESHOLD = 0.78
PATTERN_MERGE_THRESHOLD = 0.82

# pattern_matcher.py
SUGGESTION_THRESHOLD = 0.72

# knowledge_graph.py
EDGE_THRESHOLD = 0.55

# Frontend constants
PASTE_CHAR_DELTA = 50
PASTE_DEBOUNCE_MS = 300
SUGGESTION_AUTO_DISMISS_MS = 10_000
```

## Dependencies

**No new infrastructure dependencies.** Uses:
- Existing SQLite (new tables via Alembic)
- Existing `all-MiniLM-L6-v2` embedding service
- Existing event bus for real-time updates
- Existing FastAPI background tasks
- New: D3.js (`d3-force`, `d3-zoom`) for mindmap rendering (frontend npm dependency)
