# ADR-007: Live Pattern Intelligence — Real-Time Context Awareness During Prompt Authoring

**Status:** Accepted (design — pre-implementation). Confirmed still unshipped as of v0.4.3 (2026-04-24). None of Tiers 1–3 (live pattern matching, enrichment preview, proactive hints) nor the `POST /api/clusters/preview-enrichment` endpoint nor the `ContextPanel.svelte` sidebar exist in the codebase. Tracked on the ROADMAP under "Live pattern intelligence".
**Date:** 2026-04-13
**Authors:** Human + Claude Opus 4.6

## Context

### The Problem

The current pattern suggestion system only triggers on **paste events** (50+ character delta). A user typing a prompt keystroke-by-keystroke receives zero guidance from the taxonomy's accumulated knowledge until they submit for optimization. By then, the system's pattern injection is invisible — the user never sees which techniques are available or why.

This creates a disconnected experience: the taxonomy engine builds a rich knowledge graph of proven patterns, domain expertise, strategy affinities, and quality signals, but none of this is surfaced during the authoring phase when it would most influence prompt quality.

### What Exists Today

| Layer | Trigger | Latency | User Visibility |
|-------|---------|---------|-----------------|
| Paste detection | 50+ char delta | 300ms debounce + ~200ms API | Banner with Apply/Skip (10s auto-dismiss) |
| Auto-injection | Optimization submit | Part of enrichment (~300ms) | None during authoring; boolean indicator post-optimization |
| Template "Use" | Manual click | ~500ms detail load | Preview card with patterns |
| Strategy intelligence | Optimization submit | Part of enrichment (~100ms) | None during authoring; post-optimization enrichment panel |

### The Opportunity

The backend already has every capability needed for real-time guidance:

1. **Embedding service** — CPU-local all-MiniLM-L6-v2, ~100-200ms per embed
2. **Embedding index** — In-memory HNSW/numpy search, ~5-10ms per query
3. **Heuristic analyzer** — Zero-LLM task/domain/intent classification, ~10-50ms
4. **Domain signal loader** — Keyword-based domain scoring, <1ms
5. **Match endpoint** — Hierarchical 2-level cascade with cross-cluster patterns, ~200-400ms total
6. **Strategy intelligence** — Score-based strategy rankings per task_type+domain, ~30-100ms
7. **Pattern injection** — Composite fusion + cross-cluster + GlobalPattern search, ~200-400ms

Total backend round-trip for a full context query: **~300-500ms** — well within typing debounce tolerances.

## Decision

Implement a **3-tier live pattern intelligence system** that progressively enriches the authoring experience as the user types, replacing the paste-only detection with continuous context awareness.

### Tier 1: Live Pattern Matching (replaces paste detection)

**Trigger**: Prompt text length >= 30 chars AND (800ms debounce since last keystroke OR 50+ char delta for paste)

**Backend**: Existing `POST /api/clusters/match` endpoint — no changes needed. The hierarchical cascade (family-level at 0.72 threshold, cluster-level fallback at 0.60) already handles varying prompt lengths.

**Frontend**: Replace the single-suggestion banner with a **persistent context sidebar panel** (collapsible, docked to right edge of editor area). Shows:

- **Matched cluster**: label, domain badge, similarity %, member count
- **Top 3 patterns**: pattern text + source_count (from `meta_patterns[]` in match response)
- **Cross-cluster patterns**: universal techniques (from `cross_cluster_patterns[]`)
- **One-click apply**: Each pattern has a checkbox; selected patterns populate `appliedPatternIds`
- **Relevance indicator**: Similarity bar (0.60 = weak match, 0.90 = strong match)

**Key behavior changes from current paste detection**:
- `PASTE_CHAR_DELTA` lowered from 50 to 30 (catch smaller pastes)
- New `TYPING_DEBOUNCE_MS` = 800ms (longer than paste debounce, prevents excessive API calls during active typing)
- New `MIN_PROMPT_LENGTH` = 30 chars (don't match on fragments)
- Remove auto-dismiss timer — panel stays visible and updates continuously
- Abort in-flight match requests when new input arrives (prevent stale results)

**Latency budget**: 800ms debounce + ~300ms API = ~1100ms from last keystroke to updated suggestions. Acceptable — user is still typing.

### Tier 2: Contextual Enrichment Preview (new)

**Trigger**: Prompt text length >= 80 chars AND match found AND 2s since last significant change (paragraph-level debounce)

**Backend**: New lightweight endpoint `POST /api/clusters/preview-enrichment` that returns a preview of what the enrichment pipeline WOULD produce without running the full optimization. Reuses existing functions:

```python
async def preview_enrichment(prompt: str, db: AsyncSession) -> dict:
    """Lightweight enrichment preview — no LLM calls, no provider needed."""
    analyzer = HeuristicAnalyzer()
    analysis = await analyzer.analyze(prompt, db, enable_llm_fallback=False)

    strategy_intel, _ = await resolve_strategy_intelligence(
        db, analysis.task_type, analysis.domain or "general",
    )

    # Pattern preview (what auto_inject would find)
    pattern_preview = await _preview_patterns(prompt, db)

    return {
        "task_type": analysis.task_type,
        "domain": analysis.domain,
        "intent_label": analysis.intent_label,
        "confidence": analysis.confidence,
        "weaknesses": analysis.weaknesses,
        "strengths": analysis.strengths,
        "top_strategies": _extract_top_strategies(strategy_intel),
        "recommended_strategy": _extract_recommended(strategy_intel),
        "pattern_count": len(pattern_preview),
        "patterns": pattern_preview[:5],
    }
```

**Frontend**: Extends the context sidebar panel with a second section below patterns:
- **Classification**: Task type badge + domain badge + confidence indicator
- **Detected weaknesses**: Bulleted list (e.g., "Missing error handling constraints", "No output format specified")
- **Recommended strategy**: Strategy name with tooltip showing why
- **Enrichment preview**: "N patterns + strategy intelligence + codebase context will be injected"

**Purpose**: The user sees BEFORE submitting what the system detected and what it will inject. This transforms optimization from a black box into a transparent partnership.

### Tier 3: Proactive Guidance (new)

**Trigger**: Specific content patterns detected client-side (no backend call needed for detection, backend call for resolution)

**Client-side detection rules** (lightweight regex/keyword checks in the frontend):

| Signal | Detection | Guidance |
|--------|-----------|----------|
| Code mentions without repo | Prompt contains `function`, `class`, `API`, `endpoint` but no repo linked | "Link a GitHub repo for codebase-aware optimization" |
| Vague intent | Prompt < 50 chars after 10s idle | "Add specific constraints, examples, or output format for better results" |
| Strategy mismatch | Detected task_type from Tier 2 doesn't match selected strategy | "Your prompt looks like {task_type} — consider {recommended_strategy} strategy" |
| Template available | Tier 1 match has `state="template"` | "Proven template available: {label} — apply its patterns?" |
| High-confidence domain | Tier 2 domain confidence > 0.8 | Show domain-specific weakness hints from taxonomy |
| Refinement opportunity | User is editing a previously optimized prompt (loaded from history) | "Refine instead of re-optimizing to preserve score history" |

**Frontend**: Subtle inline hints below the textarea (not blocking, not modal). Uses the existing toast system for ephemeral hints, or a dedicated hint row between textarea and action buttons.

## Architecture

### New Backend Endpoint

```
POST /api/clusters/preview-enrichment
Request:  { prompt_text: str }
Response: {
    task_type: str,
    domain: str,
    intent_label: str,
    confidence: float,
    weaknesses: str[],
    strengths: str[],
    top_strategies: { name: str, avg_score: float, sample_count: int }[],
    recommended_strategy: str | null,
    matched_cluster: { id, label, domain, similarity, state } | null,
    patterns: { text: str, source: str, cluster_label: str }[],
    pattern_count: int,
    has_codebase_context: bool,
    has_few_shot: bool,
}
```

**Implementation**: Composes existing services — zero new business logic:
- `HeuristicAnalyzer.analyze()` (zero-LLM path, `enable_llm_fallback=False`)
- `resolve_strategy_intelligence()` (DB queries only)
- `auto_inject_patterns()` (embedding search, no provenance recording)
- `_should_skip_curated()` check for codebase context availability

**Performance**: ~300-500ms total (all CPU/DB, no LLM calls). Acceptable for 2s debounce.

### Frontend State Model

```typescript
// New fields in clusters.svelte.ts
liveContext = $state<LiveContext | null>(null);
liveContextLoading = $state(false);

interface LiveContext {
    // Tier 1: Pattern match
    matchedCluster: { id: string; label: string; domain: string; similarity: number; state: string } | null;
    patterns: { text: string; source: string; cluster_label: string; id: string }[];
    crossClusterPatterns: { text: string; source_count: number; id: string }[];

    // Tier 2: Enrichment preview
    taskType: string;
    domain: string;
    intentLabel: string;
    confidence: number;
    weaknesses: string[];
    strengths: string[];
    topStrategies: { name: string; avgScore: number; sampleCount: number }[];
    recommendedStrategy: string | null;
    hasCodbase: boolean;
    hasFewShot: boolean;
    patternCount: number;
}
```

### Debounce Strategy

```
Keystroke → 800ms idle → Tier 1 (match patterns)
                           ↓ match found + prompt >= 80 chars
                         2000ms idle → Tier 2 (enrichment preview)
                                        ↓ signals detected
                                      Tier 3 (proactive hints, client-side)

Paste (50+ char delta) → 300ms → Tier 1 immediately (existing fast path)
```

**Abort controller**: Each tier maintains its own `AbortController`. New input aborts in-flight requests. Tiers are independent — Tier 2 doesn't block Tier 1.

### Component Architecture

```
PromptEdit.svelte
├── <textarea> (prompt input)
├── PatternSuggestion.svelte (DEPRECATED — replaced by context panel)
├── ProactiveHints.svelte (NEW — Tier 3 inline hints)
└── strategy dropdown

ContextPanel.svelte (NEW — docked right of editor, collapsible)
├── PatternMatches (Tier 1)
│   ├── Matched cluster header (label, domain, similarity)
│   ├── Pattern checkboxes (select for explicit injection)
│   └── Cross-cluster patterns section
├── EnrichmentPreview (Tier 2)
│   ├── Classification badges (task_type, domain, confidence)
│   ├── Weakness indicators
│   └── Strategy recommendation
└── Panel controls (collapse, pin, auto-apply toggle)
```

### Migration Path

1. **Phase A**: Build `POST /api/clusters/preview-enrichment` endpoint
2. **Phase B**: Build `ContextPanel.svelte` with Tier 1 (pattern matching) — replaces `PatternSuggestion.svelte`
3. **Phase C**: Wire Tier 2 (enrichment preview) into the context panel
4. **Phase D**: Add Tier 3 (proactive hints) as `ProactiveHints.svelte`
5. **Phase E**: Remove deprecated `PatternSuggestion.svelte` and paste-only detection constants

Each phase is independently shippable. Phase B alone is a significant UX improvement.

## Use Cases Enabled

### UC-1: Pattern-Guided Authoring
User types "Build a REST API with authentication" → context panel immediately shows:
- Matched cluster: "API Authentication Patterns" (0.78 similarity)
- Patterns: "Use JWT with refresh token rotation", "Separate auth middleware from route handlers", "Include rate limiting per-endpoint"
- User checks 2 patterns → they'll be explicitly injected during optimization

### UC-2: Weakness-Aware Editing
User types a vague prompt → Tier 2 shows:
- Weaknesses: "No output format specified", "Missing error handling constraints"
- User adds constraints before submitting → optimization starts from a stronger base

### UC-3: Strategy Discovery
User types a data analysis prompt → Tier 2 shows:
- Task type: "analysis", Domain: "data"
- Recommended strategy: "structured-output" (avg 8.2 from 15 samples)
- User switches strategy dropdown before submitting

### UC-4: Template Surfacing
User types something similar to an existing template → Tier 1 shows:
- "Proven template available: Data Pipeline Design (0.85 similarity, 12 members)"
- User clicks "Apply Patterns" → template patterns pre-selected without loading the template's prompt

### UC-5: Codebase Context Awareness
User mentions "refactor the auth module" without repo linked → Tier 3 shows:
- Hint: "Link a GitHub repo for codebase-aware optimization — auth module context will be injected"

### UC-6: Refinement Nudge
User loads a past optimization and starts editing → Tier 3 shows:
- Hint: "This prompt was previously optimized (score 8.1). Use Refine to build on existing scores."

### UC-7: Cross-Project Knowledge Transfer
User in Project B types something matching Project A's template → Tier 1 shows:
- Cross-cluster patterns from Project A's taxonomy
- GlobalPatterns with 1.3x boost flagged as "proven across projects"

## Consequences

### Positive
- Users see the taxonomy's knowledge BEFORE submitting — transforms the black-box into a transparent partner
- Pattern adoption increases — users can cherry-pick which techniques to apply
- Weakness detection shifts left — users fix issues during authoring, not after scoring
- Strategy recommendations reduce trial-and-error
- Template discovery becomes organic — users find relevant templates without browsing the topology

### Negative
- Additional API calls during typing (~1 req/sec worst case, mitigated by debounce)
- Embedding service CPU load increases (mitigated by request abort + debounce)
- Context panel adds visual complexity to the editor (mitigated by collapse/pin controls)
- Users may over-rely on suggestions (mitigated by showing confidence levels)

### Risks
- **Latency regression**: If embedding service is slow, suggestions arrive too late. Mitigation: show cached Tier 1 results immediately, update when new results arrive.
- **Suggestion fatigue**: Too many suggestions → user ignores all. Mitigation: Tier 3 hints are rate-limited (max 1 per 30s), patterns auto-collapse to top 3.
- **Cold start**: Empty taxonomy → no suggestions. Mitigation: graceful degradation — panel shows "Optimize your first prompt to start building pattern intelligence" instead of empty state.

## Files to Create/Modify

### New Files
| File | Purpose |
|------|---------|
| `backend/app/routers/preview.py` | `POST /api/clusters/preview-enrichment` endpoint |
| `frontend/src/lib/components/editor/ContextPanel.svelte` | Live context sidebar panel (Tiers 1+2) |
| `frontend/src/lib/components/editor/ProactiveHints.svelte` | Inline guidance hints (Tier 3) |

### Modified Files
| File | Changes |
|------|---------|
| `backend/app/main.py` | Register preview router |
| `frontend/src/lib/stores/clusters.svelte.ts` | Add `liveContext` state, `updateLiveContext()` method, new debounce timers |
| `frontend/src/lib/components/editor/PromptEdit.svelte` | Replace PatternSuggestion with ContextPanel integration, add ProactiveHints |
| `frontend/src/lib/api/clusters.ts` | Add `previewEnrichment()` API function |

### Deprecated (Phase E)
| File | Reason |
|------|--------|
| `frontend/src/lib/components/editor/PatternSuggestion.svelte` | Replaced by ContextPanel Tier 1 |

## References

- ADR-005: Taxonomy scaling architecture (multi-project patterns, GlobalPattern lifecycle)
- ADR-006: Universal prompt engine (domain-agnostic pattern discovery)
- `backend/app/services/taxonomy/matching.py`: Hierarchical match cascade
- `backend/app/services/context_enrichment.py`: Unified enrichment pipeline
- `backend/app/services/heuristic_analyzer.py`: Zero-LLM classification
- `backend/app/services/pattern_injection.py`: Auto-injection + few-shot retrieval

## Implementation status

**As of 2026-04-19: not yet started.** No phases A–E have shipped. Specifically:

- `POST /api/clusters/preview-enrichment` — not implemented (no `backend/app/routers/preview.py`)
- `ContextPanel.svelte` — not created
- `ProactiveHints.svelte` — not created
- Existing paste-only `PatternSuggestion.svelte` remains in place unchanged

Tracked under `docs/ROADMAP.md` → "Live Pattern Intelligence" in the Planned section. All composed primitives referenced in the Architecture section (`HeuristicAnalyzer`, `resolve_strategy_intelligence`, `auto_inject_patterns`, hierarchical match cascade, embedding index) are shipped — implementation risk is UI orchestration, not backend capability.
