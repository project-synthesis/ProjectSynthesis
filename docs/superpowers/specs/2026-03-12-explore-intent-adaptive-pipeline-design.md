# Explore Intent-Adaptive Pipeline Enhancement

**Date:** 2026-03-12
**Approach:** B — Pre-Explore Intent Classification + Adaptive Synthesis
**Scope:** Three layers — explore synthesis prompt, context builders, optimizer consumption

## Problem

The optimization pipeline produces surgically precise prompts when codebase context is available, but the quality depends on explore output specificity. Currently the explore synthesis prompt is generic-structural — it produces the same kind of observations regardless of whether the user's prompt is about refactoring, API design, testing, or architecture review.

A refactoring prompt needs behavioral observations (code smells, cross-cutting concerns, hardcoded values). An API design prompt needs relational observations (endpoint structure, data contracts, integration points). The explore stage should adapt its observation lens to what the optimizer will need.

Additionally, the context builder caps are too aggressive (truncating rich observations) and the optimizer lacks positive guidance on HOW to weave codebase intelligence into the final prompt.

## Architecture

### Data flow (new)

```
run_explore()
    │
    ├─ Phase 1: Retrieve relevant files (unchanged)
    │
    ├─ Phase 1.5: Intent classification (NEW)
    │   ├─ Haiku 4.5, ~200ms, ~450 tokens
    │   ├─ Input: raw prompt only (no files)
    │   ├─ Output: intent_category, observation_directives, snippet_priorities, depth
    │   └─ Fallback: {"intent_category": "general", "depth": "structural"}
    │
    ├─ Phase 2: Batch-read files (unchanged)
    │
    ├─ Phase 3: Single-shot LLM synthesis (ENHANCED)
    │   ├─ Receives intent directives in user message
    │   ├─ Enhanced system prompt with specificity/cross-cutting guidance
    │   └─ Produces 8-12 observations, 5-12 snippets (up from 5-10 / 3-8)
    │
    ├─ Post-LLM validation (unchanged)
    │
    └─ CodebaseContext (EXTENDED)
         ├─ New field: intent_category
         └─ Richer observations, snippets, grounding notes
              │
              ├─→ Context builders (ENHANCED caps)
              │     ├─ Observations: 8 → 12
              │     ├─ Grounding notes: 8 → 12
              │     ├─ Snippets: 5 → 10, content 600 → 1200 chars
              │     ├─ Key files: 10 → 20, Tech stack: 10 → 15
              │     └─ New intent header line
              │
              ├─→ Optimizer (ENHANCED injection)
              │     ├─ Intent-specific weaving guidance (positive instructions)
              │     ├─ Coverage/files metadata in header
              │     └─ Task-type additions with codebase-aware paragraphs
              │
              └─→ Validator (cap raised 2500 → 4000 chars)
```

## Section 1: Pre-Explore Intent Classification

### Function: `_classify_prompt_intent()`

**Location:** `backend/app/services/codebase_explorer.py`

**Signature:**
```python
async def _classify_prompt_intent(
    provider: LLMProvider,
    raw_prompt: str,
    model: str = "claude-haiku-4-5",
) -> dict:
```

**Output schema:**
```python
INTENT_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "intent_category": {
            "type": "string",
            "enum": [
                "refactoring", "api_design", "feature_build", "testing",
                "debugging", "architecture_review", "performance",
                "documentation", "migration", "security", "general"
            ],
        },
        "observation_directives": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-4 specific instructions for what the explore model should focus on",
        },
        "snippet_priorities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "2-3 types of code regions to prioritize in snippet extraction",
        },
        "depth": {
            "type": "string",
            "enum": ["structural", "behavioral", "relational"],
            "description": "Observation depth preference",
        },
    },
    "required": ["intent_category", "observation_directives", "snippet_priorities", "depth"],
}
```

**System prompt for intent classification:**
```
You are a prompt intent classifier for a codebase exploration system.

Given a user's prompt, classify what KIND of codebase intelligence a downstream
prompt optimizer will need to write a surgically precise optimized version.

Your classification drives what the codebase explorer focuses on:
- "structural" depth: module layout, file organization, component locations
- "behavioral" depth: function behaviors, hardcoded values, conditional branches, side effects
- "relational" depth: dependencies, data flow, integration points, contracts between modules

Observation directives tell the explorer WHAT to look for. Be specific:
  Good: "Identify behavioral patterns with specific values (hardcoded constants, magic numbers)"
  Bad: "Look at the code structure"

Snippet priorities tell the explorer WHICH code regions to extract:
  Good: "Functions with conditional branching or multiple code paths"
  Bad: "Important functions"
```

**Intent category mapping (reference for the classifier):**

| Category | Depth | Typical directives |
|----------|-------|--------------------|
| refactoring | behavioral | Code smells, cross-cutting concerns, hardcoded values, duplication, behavioral patterns with specific values |
| api_design | relational | Endpoint structure, request/response shapes, middleware chain, data contracts |
| feature_build | structural | Module layout, extension points, existing patterns to follow, conventions |
| testing | behavioral | Test coverage signals, testability barriers, mock patterns, fixture setup |
| debugging | behavioral | Error paths, state mutations, side effects, exception handling patterns |
| architecture_review | relational | Dependency graph, layer violations, coupling points, module boundaries |
| performance | behavioral | Hot paths, caching patterns, I/O boundaries, concurrency primitives |
| documentation | structural | Public APIs, module purposes, data flow overview, configuration surface |
| migration | relational | Dependencies, integration boundaries, version-specific patterns |
| security | behavioral | Auth flows, input validation, credential handling, encryption patterns |
| general | structural | Module layout, key abstractions, data flow patterns |

**LLM call:** `provider.complete_json(system=INTENT_SYSTEM_PROMPT, user=raw_prompt, model=model, schema=INTENT_CLASSIFICATION_SCHEMA)`. The schema parameter is required — without it, Haiku may return free-form category names that don't match the enum values, causing silent fallback to "general" even when intent was correctly identified.

**Timeout:** 8 seconds (expected latency ~200ms; generous margin without adding meaningful pipeline delay)

**Failure behavior:** On any error (timeout, parse failure, exception), log warning and return:
```python
{"intent_category": "general", "depth": "structural", "observation_directives": [], "snippet_priorities": []}
```

No SSE error event emitted — silent degradation. The synthesis prompt still works with its enhanced baseline instructions.

**Cost:** ~300 tokens input, ~150 tokens output per call.

## Section 2: Enhanced Explore Synthesis Prompt

### Changes to `get_explore_synthesis_prompt()` in `explore_synthesis_prompt.py`

**Observation count:** "5–10" → "8–12, adapted to the observation directives provided"

**New specificity guidance** (inserted after observation instructions, before grounding notes):

> For every observation, be microscopically specific. Include function/method names,
> variable names, hardcoded values, and line ranges where visible. Do not write
> "the provider uses conditional logic" — write "AnthropicAPIProvider._make_extra()
> (anthropic_api.py:55-77) branches on _THINKING_MODELS membership and schema
> presence, producing three output paths: adaptive thinking, JSON output_config,
> or plain completion."

**New cross-cutting guidance:**

> When the observation directives indicate behavioral or relational depth, trace
> patterns ACROSS module boundaries. If you see the same concern handled differently
> in multiple files (e.g., caching, error handling, configuration), describe each
> instance with specific function names and contrast the approaches.

**Snippet count:** "3–8" → "5–12, prioritized by the snippet priorities directive"

**Snippet context enhancement** — replace "what this code defines/handles" with:

> Describe WHAT the code does behaviorally, not just structurally. Include specific
> values, branch conditions, and behavioral characteristics. Bad: "stream method for
> CLI provider". Good: "stream() method: text blocks converted to word-boundary chunks
> via hardcoded CHUNK_TARGET=60 and 3ms inter-chunk sleep. Simulated streaming strategy
> differs from AnthropicAPIProvider.stream() which uses SDK text_stream."

**Grounding notes enhancement** — append after existing quality examples:

> When the observation directives specify behavioral depth, grounding notes should
> include execution-level detail that an optimizer can weave directly into a prompt:
> specific function signatures, parameter types, return shapes, and concrete values.
> The optimizer will use these to write surgically precise instructions — give it
> the ammunition.

**Quantitative metadata instruction** (new):

> When visible in the codebase, note quantitative signals: test file count vs source
> file count (proxy for coverage), number of TODO/FIXME comments, number of
> configuration sources, dependency count. These help downstream stages calibrate
> effort estimates and constraint severity.

### Changes to user message construction in `codebase_explorer.py`

Current (lines 808-814):
```python
user_message = (
    f"User's prompt to optimize:\n{raw_prompt}\n\n"
    f"Repository: {repo_full_name} (branch: {used_branch})\n"
    f"Total files in repo: {total_in_tree}\n"
    f"Files provided below: {len(file_contents)}\n\n"
    f"{context_payload}"
)
```

New:
```python
# Build directive section (empty string if no directives)
directive_section = ""
if intent.get("observation_directives") or intent.get("snippet_priorities"):
    parts = [
        f"\nObservation directives (adapt your analysis accordingly):",
        f"  Intent: {intent.get('intent_category', 'general')}",
        f"  Depth: {intent.get('depth', 'structural')}",
    ]
    if intent.get("observation_directives"):
        parts.append("  Focus areas:")
        for d in intent["observation_directives"]:
            parts.append(f"    - {d}")
    if intent.get("snippet_priorities"):
        parts.append("  Snippet priorities:")
        for p in intent["snippet_priorities"]:
            parts.append(f"    - {p}")
    directive_section = "\n".join(parts) + "\n"

user_message = (
    f"User's prompt to optimize:\n---\n{raw_prompt}\n---\n"
    f"{directive_section}\n"
    f"Repository: {repo_full_name} (branch: {used_branch})\n"
    f"Total files in repo: {total_in_tree}\n"
    f"Files provided below: {len(file_contents)}\n\n"
    f"{context_payload}"
)
```

### Unchanged

- Intelligence-vs-execution guardrail (lines 33-56)
- JSON-only output rule (lines 124-126)
- Anti-hallucination rules (lines 117-123)
- `EXPLORE_OUTPUT_SCHEMA` — no field additions; richer content fits existing string arrays

## Section 3: Context Builder Enhancement

### Changes to `build_codebase_summary()` in `context_builders.py`

**Cap changes:**

| Field | Current | New |
|-------|---------|-----|
| Observations | 8 | 12 |
| Grounding notes | 8 | 12 |
| Snippets | 5 | 10 |
| Snippet content | 600 chars | 1200 chars |
| Key files | 10 | 20 |
| Tech stack | 10 | 15 |

**New intent header** — after repo line, before files_read_count:
```python
intent = codebase_context.get("intent_category")
depth = codebase_context.get("depth", "")
if intent and intent != "general":
    parts.append(f"Intent focus: {intent}" + (f" (depth: {depth})" if depth else ""))
```

### Changes to `validator.py`

Codebase summary truncation cap: 2500 → 4000 chars (line 116).

### Token budget note

With raised caps, `build_codebase_summary()` output can grow 2-3x. This is intentional — the optimizer (Opus) handles long context well and benefits from richer detail. The validator's 4000-char truncation acts as the safety valve for that stage, where only faithfulness-relevant data matters. No total-length cap is added to `build_codebase_summary()` itself; per-field caps remain the only guard.

### Unchanged

- Quality warning logic (partial/failed)
- Flat text format (no semantic grouping)
- `build_analysis_summary()`, `build_strategy_summary()` — untouched

## Section 4: Optimizer Consumption Enhancement

### 4a. Context injection block in `optimizer.py` (lines 156-176)

Replace the current DO/DO NOT block with a structured block including intent metadata and positive weaving guidance:

```python
# Intent-specific weaving guidance
_WEAVING_GUIDANCE: dict[str, str] = {
    "refactoring": (
        "- Construct a prioritized Scope section mapping observations to specific files/functions\n"
        "- Use coverage % and test file counts to calibrate effort estimates\n"
        "- Extract architectural constraints from project docs and make them explicit"
    ),
    "api_design": (
        "- Use endpoint observations to define the API surface\n"
        "- Reference data contracts and integration points as explicit interface requirements"
    ),
    "feature_build": (
        "- Reference existing patterns the executor should follow\n"
        "- Name extension points and module boundaries"
    ),
    "testing": (
        "- Use coverage signals and testability observations to scope what needs testing\n"
        "- Reference mock patterns and test infrastructure"
    ),
    "debugging": (
        "- Map error paths and state mutations into a structured investigation plan\n"
        "- Reference specific functions and their behavioral characteristics"
    ),
    "architecture_review": (
        "- Use dependency and coupling observations to define review dimensions\n"
        "- Reference layer violations and cross-cutting concerns as explicit review criteria"
    ),
    "performance": (
        "- Reference hot paths, I/O boundaries, and caching patterns as profiling targets"
    ),
    "security": (
        "- Map auth flows and credential handling into explicit review scope\n"
        "- Reference input validation patterns and encryption usage"
    ),
}
_DEFAULT_WEAVING = (
    "- Use file paths, function names, and data shapes to make instructions precise\n"
    "- Let codebase specifics inform the precision of your instructions"
)
```

**Behavioral shift:** The current optimizer only reads `codebase_context` indirectly via `build_codebase_summary()`. The new code reads both raw dict fields (`intent_category`, `coverage_pct`, `files_read_count` for the header metadata) AND the formatted summary (for the body). This is intentional — the header needs structured values, the body needs the human-readable summary.

New injection format:
```python
intent_cat = codebase_context.get("intent_category", "general")
coverage = codebase_context.get("coverage_pct", 0)
files_read = codebase_context.get("files_read_count", 0)
weaving = _WEAVING_GUIDANCE.get(intent_cat, _DEFAULT_WEAVING)

user_message += (
    "\n\n--- Codebase reference (INTELLIGENCE LAYER — for YOUR understanding only) ---\n"
    f"Intent focus: {intent_cat} · Coverage: {coverage}% · {files_read} files\n\n"
    "Weaving guidance (how to USE this context in the optimized prompt):\n"
    f"{weaving}\n\n"
    "Guardrails:\n"
    "- Do NOT relay exploration findings, observations, or context notes\n"
    "- Do NOT add 'Codebase Context' or 'Background' sections\n"
    "- Do NOT treat observations marked [unverified] as fact\n"
    "- Do NOT delegate investigation tasks to the executor\n"
    "- Do NOT invent specifics beyond what appears below\n\n"
    f"{codebase_summary}\n"
    "--- End codebase reference ---"
)
```

### 4b. Task-type-specific additions in `optimizer_prompts.py`

Append codebase-aware paragraph to relevant task types:

**coding:**
> When codebase context is available: construct a Scope section that maps observations
> to ordered priorities with specific file paths and function names. Use quantitative
> metrics (coverage %, file counts) to calibrate effort levels in any estimation
> guidance. Extract layer rules or architectural constraints from observations and
> make them explicit constraints the executor must respect.

**analysis:**
> When codebase context is available: use architectural observations to define analysis
> dimensions. Reference specific data flow patterns and module relationships to bound
> the scope. Turn cross-cutting observations into explicit review criteria.

**reasoning:**
> When codebase context is available: reference specific functions, data structures, and
> module relationships to make reasoning steps concrete. Use architectural observations
> to frame the reasoning scope.

**general / other:**
> When codebase context is available: use file paths, function names, and data shapes
> to make instructions precise wherever the observations provide specifics.

**Task types that do NOT get codebase-aware paragraphs:** `math`, `writing`, `creative`, `extraction`, `classification`, `formatting`, `medical`, `legal`, `education`. These task types rarely involve codebase context — the generic optimizer instructions ("absorb as background intelligence") are sufficient.

### Unchanged

- `_BASE_OPTIMIZER_PROMPT` core instructions (1-9)
- Metadata output format (`<optimization_meta>`)
- Non-codebase paths (no repo linked) — completely unaffected

## Section 5: CodebaseContext & Pipeline Integration

### CodebaseContext extension

```python
@dataclass
class CodebaseContext:
    # ... existing fields ...
    intent_category: str = ""  # classified prompt intent
    depth: str = ""            # observation depth (structural, behavioral, relational)
```

Both fields are set in `run_explore()` from the intent classification result:
```python
context.intent_category = intent["intent_category"]
context.depth = intent.get("depth", "")
```

### run_explore() orchestration

Intent classification inserted between file retrieval and synthesis:

```python
# After file retrieval, before synthesis
intent = await _classify_prompt_intent(provider, raw_prompt)

yield ("tool_call", {
    "tool": "intent_classification",
    "input": {"prompt_length": len(raw_prompt)},
    "output": {"intent": intent["intent_category"], "depth": intent["depth"]},
    "status": "complete",
})

# Build user message with directives (Section 2)
# ... synthesis call ...

# After building CodebaseContext:
context.intent_category = intent["intent_category"]
```

### Failure isolation

If `_classify_prompt_intent()` fails: log warning, return default dict, explore continues. No SSE error event — silent degradation.

### Cache key

Unchanged. Cache key is `(repo, branch, sha, prompt_hash)`. Intent classification is deterministic for the same prompt text, so cached results remain valid.

### SSE event

One `("tool_call", {...})` event for intent classification observability.

### Pipeline changes

None. `pipeline.py` passes `CodebaseContext` as a dict — new fields propagate automatically.

## Files Modified

| File | Change |
|------|--------|
| `backend/app/services/codebase_explorer.py` | Add `_classify_prompt_intent()`, `INTENT_CLASSIFICATION_SCHEMA`, intent call in `run_explore()`, new field on `CodebaseContext` |
| `backend/app/prompts/explore_synthesis_prompt.py` | Rewrite with specificity/cross-cutting/quantitative guidance, adapt counts |
| `backend/app/services/context_builders.py` | Raise caps, add intent header |
| `backend/app/services/optimizer.py` | New `_WEAVING_GUIDANCE` dict, restructured context injection block |
| `backend/app/prompts/optimizer_prompts.py` | Codebase-aware paragraphs in task-type additions |
| `backend/app/services/validator.py` | Raise summary cap 2500 → 4000 chars |

## Files NOT Modified

- `backend/app/services/pipeline.py` — dict passthrough, no changes
- `backend/app/services/analyzer.py` — consumes codebase context unchanged
- `backend/app/services/strategy.py` — consumes codebase context unchanged
- `backend/app/prompts/analyzer_prompt.py` — unchanged
- `backend/app/prompts/strategy_prompt.py` — unchanged
- `backend/app/prompts/validator_prompt.py` — unchanged
- `EXPLORE_OUTPUT_SCHEMA` — unchanged (richer content fits existing fields)

## Risk Assessment

**Low risk:**
- Context builder cap changes — additive, no behavioral change
- Optimizer weaving guidance — positive instructions, guardrails preserved
- CodebaseContext new field — default empty string, backward compatible

**Medium risk:**
- Explore synthesis prompt rewrite — LLM output quality depends on prompt clarity. The enhanced instructions are more prescriptive, which could overconstrain Haiku on unfamiliar codebases. Mitigation: the intent directives are injected via user message (not system prompt), so they're soft guidance.
- Intent classification failure path — must not block or slow the pipeline. Mitigation: 15s timeout + silent fallback.

**No risk:**
- Pipeline.py — zero changes
- Non-codebase paths — completely unaffected (no repo linked = no explore = no changes visible)
