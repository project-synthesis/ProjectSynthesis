# Unified Context Enrichment System

**Date**: 2026-03-23
**Status**: Approved
**Scope**: Backend context resolution, workspace scanning, passthrough enrichment, RepoIndexService augmentation

## Problem Statement

Context resolution is scattered across 5 call sites with inconsistent logic. The passthrough tier receives no analysis, no codebase context, no applied patterns, and generic-only adaptation state. The REST endpoint bypasses `WorkspaceIntelligence` entirely. `RootsScanner` only checks 5 hardcoded filenames at root level, missing monorepo structures. `RepoIndexService` stores naive "first 30 lines" outlines disconnected from the explore pipeline. The result: passthrough optimizations are dramatically inferior to internal/sampling tiers, and workspace context varies by code path.

### Current Context Availability by Tier

| Context Source | Internal | Sampling | Passthrough |
|---|---|---|---|
| Analysis (task_type, weaknesses) | Full LLM | Full LLM | **None** (hardcoded "general") |
| Explore (GitHub codebase) | Full (Haiku synthesis) | Full (sampling LLM) | **None** (hardcoded `None`) |
| Workspace guidance | Full | Full (MCP roots + fallback) | Partial (REST bypasses WorkspaceIntelligence) |
| Adaptation state | Task-specific | Task-specific | **Generic "general" only** |
| Applied meta-patterns | Auto-injected | Auto-injected | **None** |
| Context sources in DB | Full dict | Full dict | **None** |

### Code Duplication

Context resolution is done differently in:
1. `routers/optimize.py` -- raw `RootsScanner.scan()` (no workspace intelligence)
2. `tools/_shared.py` -- `resolve_workspace_guidance()` (MCP roots + workspace intelligence)
3. `tools/optimize.py` -- calls `resolve_workspace_guidance()`
4. `tools/prepare.py` -- calls `resolve_workspace_guidance()`, `repo_full_name` is dead parameter
5. `services/pipeline.py` -- receives pre-resolved guidance

## Design Decisions

- **Full pre-enrichment for passthrough** -- Run analysis + explore + pattern injection before assembling the passthrough prompt. The external LLM gets rich context without running our optimizer.
- **No tier mixing** -- Passthrough uses zero-LLM enrichment only. No falling back to sampling or local provider for analysis. Heuristic analyzer replaces LLM analyzer.
- **Manifest-aware subdirectory scanning** -- Detect project subdirectories from manifest files (package.json, pyproject.toml, etc.) and scan those for guidance files. Smarter than blind recursion, naturally bounded.
- **Pre-computed index for passthrough codebase context** -- Use `RepoIndexService` with enriched outlines instead of live GitHub fetch + Haiku synthesis. Token-conscious: structured summaries instead of raw file contents.

## Architecture

### 1. Unified `ContextEnrichmentService`

**File**: `backend/app/services/context_enrichment.py`

Single entry point for all tiers. Returns an `EnrichedContext` dataclass containing all resolved context layers.

```python
@dataclass(frozen=True)
class EnrichedContext:
    raw_prompt: str
    workspace_guidance: str | None
    codebase_context: str | None
    adaptation_state: str | None
    applied_patterns: str | None
    analysis: HeuristicAnalysis | None   # Only for passthrough — callers read .task_type, .domain, .intent_label for DB fields
    context_sources: dict[str, bool]

class ContextEnrichmentService:
    def __init__(
        self,
        prompts_dir: Path,
        data_dir: Path,
        workspace_intel: WorkspaceIntelligence,
        embedding_service: EmbeddingService,
        heuristic_analyzer: HeuristicAnalyzer,
        github_client: GitHubClient,           # For RepoIndexService construction
        taxonomy_engine: TaxonomyEngine | None = None,
    ) -> None: ...
    # Note: RepoIndexService is created per-call (needs db session):
    #   RepoIndexService(db, self._github_client, self._embedding_service)
    # AdaptationTracker is also created per-call (needs db session):
    #   AdaptationTracker(db)

    async def enrich(
        self,
        raw_prompt: str,
        tier: str,                          # "internal" | "sampling" | "passthrough"
        db: AsyncSession,
        workspace_path: str | None = None,
        mcp_ctx: Context | None = None,
        repo_full_name: str | None = None,
        repo_branch: str | None = None,     # Defaults to "main" if None
        applied_pattern_ids: list[str] | None = None,
        preferences_snapshot: dict | None = None,
    ) -> EnrichedContext: ...
```

#### Enrichment dispatch logic

```python
async def enrich(self, ..., tier: str) -> EnrichedContext:
    # 1. Workspace guidance -- ALL tiers, same path
    guidance = await self._resolve_workspace_guidance(mcp_ctx, workspace_path)

    # 2. Analysis -- tier-dependent
    if tier == "passthrough":
        analysis = await self._heuristic_analyzer.analyze(raw_prompt, db)
        task_type = analysis.task_type
    else:
        analysis = None   # LLM analyzer runs inside pipeline
        task_type = None   # Determined by pipeline

    # 3. Codebase context -- tier-dependent
    if tier == "passthrough" and repo_full_name:
        branch = repo_branch or "main"
        codebase_context = await self._query_index_context(
            repo_full_name, branch, raw_prompt, task_type, analysis.domain, db,
        )
    else:
        codebase_context = None   # Internal/sampling handle this in pipeline

    # 4. Adaptation state -- ALL tiers, task_type-aware
    effective_task_type = task_type or "general"
    adaptation = await self._resolve_adaptation(db, effective_task_type)

    # 5. Applied patterns -- ALL tiers
    patterns = await self._resolve_patterns(
        raw_prompt, applied_pattern_ids, self._taxonomy_engine,
    )

    # 6. Context sources audit
    sources = {
        "workspace_guidance": guidance is not None,
        "codebase_context": codebase_context is not None,
        "adaptation": adaptation is not None,
        "applied_patterns": patterns is not None,
        "heuristic_analysis": analysis is not None,
    }

    return EnrichedContext(
        raw_prompt=raw_prompt,
        workspace_guidance=guidance,
        codebase_context=codebase_context,
        adaptation_state=adaptation,
        applied_patterns=patterns,
        analysis=analysis,
        context_sources=sources,
    )
```

#### Integration with existing pipelines

- **Internal pipeline** (`pipeline.py`): Receives `EnrichedContext.workspace_guidance`, `.adaptation_state`, `.applied_patterns`. Still runs its own LLM-based analyze phase (Phase 1) and explore phase (Phase 0) internally.
- **Sampling pipeline** (`sampling_pipeline.py`): Same as internal -- receives shared layers, runs its own LLM phases via sampling.
- **Passthrough**: Receives ALL layers from the enrichment service. No further LLM calls. The `assemble_passthrough_prompt()` function accepts the new parameters.

#### Call site consolidation

Every call site replaces its inline context resolution with a single `enrich()` call:

```python
# REST POST /api/optimize (routers/optimize.py)
enrichment = await context_service.enrich(
    raw_prompt=body.prompt, tier=decision.tier, db=db,
    workspace_path=body.workspace_path, repo_full_name=repo_full_name,
    applied_pattern_ids=body.applied_pattern_ids,
    preferences_snapshot=prefs_snapshot,
)

# MCP synthesis_optimize (tools/optimize.py)
enrichment = await context_service.enrich(
    raw_prompt=prompt, tier=decision.tier, db=db,
    workspace_path=workspace_path, mcp_ctx=ctx,
    repo_full_name=repo_full_name,
    applied_pattern_ids=applied_pattern_ids,
    preferences_snapshot=prefs_snapshot,
)

# MCP synthesis_prepare_optimization (tools/prepare.py)
enrichment = await context_service.enrich(
    raw_prompt=prompt, tier="passthrough", db=db,
    workspace_path=workspace_path, mcp_ctx=ctx,
    repo_full_name=repo_full_name,
)

# REST POST /api/optimize/passthrough (routers/optimize.py — manual passthrough endpoint)
enrichment = await context_service.enrich(
    raw_prompt=body.prompt, tier="passthrough", db=db,
    workspace_path=body.workspace_path,
    repo_full_name=repo_full_name,
)
```

**Note**: `POST /api/optimize/passthrough` is a fourth call site that must also be consolidated. It currently duplicates the same inline context resolution as the main optimize endpoint's passthrough path.

**`ContextResolver` relationship**: The enrichment service delegates prompt validation (length checks) and per-source capping (MAX_GUIDANCE_CHARS, MAX_CODEBASE_CONTEXT_CHARS, MAX_ADAPTATION_CHARS) to `ContextResolver` methods internally. `ContextResolver` is NOT replaced -- it becomes a utility called by the enrichment service for sanitization. The enrichment service owns orchestration; `ContextResolver` owns validation/capping.

### 2. Enhanced `RootsScanner` -- Manifest-Aware Subdirectory Discovery

**File**: `backend/app/services/roots_scanner.py`

#### Expanded guidance file list

```python
GUIDANCE_FILES = [
    "CLAUDE.md",
    "AGENTS.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    ".windsurfrules",
    "GEMINI.md",
    ".clinerules",
    "CONVENTIONS.md",
]
```

#### Manifest-aware subdirectory detection

```python
# Manifest files that indicate a project subdirectory
MANIFEST_FILES = [
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
]

_SKIP_DIRS = {
    "node_modules", ".venv", "__pycache__", ".git", "dist", "build",
    ".next", ".svelte-kit", "target", "vendor", ".tox", "eggs",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "coverage",
}

def discover_project_dirs(root: Path) -> list[Path]:
    """Detect immediate subdirectories containing manifest files."""
    project_dirs: list[Path] = []
    if not root.is_dir():
        return project_dirs
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue
        for manifest in MANIFEST_FILES:
            if (child / manifest).is_file():
                project_dirs.append(child)
                break
    return project_dirs
```

Skip directories: see `_SKIP_DIRS` set above (16 entries covering package managers, build output, caches, and VCS directories).

#### Discovery flow

```
1. Scan root/ for guidance files (existing behavior)
2. discover_project_dirs(root) -> [backend/, frontend/, ...]
3. For each project dir, scan for guidance files
4. Deduplicate by SHA256 content hash (root copy wins)
5. Respect total budget (MAX_GUIDANCE_CHARS shared pool)
```

#### Content deduplication

```python
def _deduplicate_sections(
    self, sections: list[tuple[str, str, str]]
) -> list[tuple[str, str, str]]:
    """Deduplicate (label, content, hash) tuples. First occurrence wins."""
    seen_hashes: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for label, content, content_hash in sections:
        if content_hash not in seen_hashes:
            seen_hashes.add(content_hash)
            unique.append((label, content, content_hash))
    return unique
```

#### Shared utility extraction

`discover_project_dirs()` is extracted as a module-level function so `WorkspaceIntelligence._detect_stack()` can reuse it instead of duplicating manifest detection. `WorkspaceIntelligence` calls `discover_project_dirs()` to find subdirectories, then runs stack detection on root + each subdirectory.

### 3. Heuristic Analyzer -- Zero-LLM Classification

**File**: `backend/app/services/heuristic_analyzer.py`

#### Output model

```python
@dataclass(frozen=True)
class HeuristicAnalysis:
    task_type: str       # coding | writing | analysis | creative | data | system | general
    domain: str          # backend | frontend | database | devops | security | fullstack | general
    intent_label: str    # 3-6 word phrase
    weaknesses: list[str]
    strengths: list[str]
    recommended_strategy: str
    confidence: float    # 0.0-1.0
```

#### Classification layers

**Layer 1: Keyword classifier**

Weighted keyword sets per task_type and domain. Positional weighting: keywords in the first sentence score 2x. Multi-label with confidence scoring, highest wins.

```python
_TASK_TYPE_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "coding": [
        ("implement", 1.0), ("refactor", 1.0), ("debug", 0.9),
        ("function", 0.7), ("API", 0.8), ("endpoint", 0.8),
        ("bug", 0.9), ("test", 0.7), ("deploy", 0.6),
        ("class", 0.6), ("module", 0.6), ("database", 0.5),
    ],
    "writing": [
        ("write", 0.6), ("draft", 0.9), ("blog", 1.0),
        ("article", 1.0), ("essay", 1.0), ("copy", 0.8),
        ("tone", 0.7), ("audience", 0.6), ("narrative", 0.8),
    ],
    "analysis": [
        ("analyze", 1.0), ("compare", 0.9), ("evaluate", 0.9),
        ("review", 0.7), ("assess", 0.9), ("critique", 0.8),
        ("pros and cons", 0.9), ("trade-off", 0.8),
    ],
    "creative": [
        ("create", 0.5), ("design", 0.7), ("brainstorm", 1.0),
        ("imagine", 0.9), ("story", 1.0), ("generate ideas", 0.9),
        ("creative", 0.8), ("invent", 0.9),
    ],
    "data": [
        ("data", 0.6), ("dataset", 0.9), ("ETL", 1.0),
        ("pipeline", 0.6), ("transform", 0.6), ("schema", 0.7),
        ("query", 0.7), ("aggregate", 0.8), ("visualization", 0.7),
    ],
    "system": [
        ("system prompt", 1.0), ("agent", 0.7), ("workflow", 0.6),
        ("automate", 0.8), ("orchestrate", 0.9), ("configure", 0.7),
        ("setup", 0.5), ("infrastructure", 0.7),
    ],
}

_DOMAIN_SIGNALS: dict[str, list[tuple[str, float]]] = {
    "backend": [
        ("API", 0.8), ("endpoint", 0.9), ("server", 0.8),
        ("middleware", 0.9), ("FastAPI", 1.0), ("Django", 1.0),
        ("database", 0.6), ("authentication", 0.7),
    ],
    "frontend": [
        ("React", 1.0), ("Svelte", 1.0), ("component", 0.8),
        ("CSS", 0.9), ("UI", 0.8), ("layout", 0.7),
        ("responsive", 0.8), ("Tailwind", 0.9),
    ],
    "database": [
        ("SQL", 1.0), ("migration", 0.9), ("schema", 0.8),
        ("query", 0.7), ("index", 0.6), ("PostgreSQL", 1.0),
        ("SQLite", 1.0), ("ORM", 0.8),
    ],
    "devops": [
        ("Docker", 1.0), ("CI/CD", 1.0), ("deploy", 0.8),
        ("Kubernetes", 1.0), ("terraform", 1.0), ("nginx", 0.9),
        ("pipeline", 0.5), ("monitoring", 0.7),
    ],
    "security": [
        ("auth", 0.7), ("encryption", 1.0), ("vulnerability", 1.0),
        ("CORS", 0.9), ("JWT", 0.9), ("OAuth", 0.9),
        ("sanitize", 0.8), ("injection", 0.9),
    ],
}
```

**Layer 2: Structural analyzer**

Detects prompt structure patterns that inform both classification and weakness detection:
- Has code blocks (triple backtick)? Strong coding signal.
- Has bullet/numbered lists? Structured intent, strength: "well-organized requirements."
- Question form (starts with interrogative)? Analysis/general signal.
- Imperative form (starts with verb)? Coding/creative signal.
- Length ratio (words per sentence, total word count) informs complexity assessment.

**Layer 3: Weakness detector**

Rule-based checks that produce actionable weakness descriptions:

| Check | Weakness |
|---|---|
| No constraints or requirements mentioned | "lacks constraints -- no boundaries for the output" |
| No success criteria or expected outcome | "no measurable outcome defined" |
| No audience or persona specified | "target audience unclear" |
| Vague quantifiers ("some", "various", "many", "a few") | "vague language reduces precision" |
| Missing context for technical prompts (coding task_type but no language/framework) | "insufficient technical context" |
| Prompt < 50 words for non-trivial task_type | "prompt underspecified for task complexity" |
| No examples or references provided | "no examples to anchor expected output" |
| Ambiguous scope ("everything", "all aspects") | "scope too broad -- consider narrowing focus" |

Strength detection (inverse):
| Check | Strength |
|---|---|
| Contains code blocks or structured examples | "includes concrete examples" |
| Specifies constraints or requirements | "clear constraints defined" |
| Mentions specific technologies or tools | "specific technical context provided" |
| Has clear success criteria | "measurable outcome specified" |
| Well-structured with sections/lists | "well-organized prompt structure" |

**Layer 4: Adaptation-aware strategy selector**

Queries `AdaptationTracker` with the detected task_type (not "general"). Uses affinity data from past feedback.

Static fallback mapping (when no adaptation data exists):
```python
_DEFAULT_STRATEGY_MAP: dict[str, str] = {
    "coding": "structured-output",
    "writing": "role-playing",
    "analysis": "chain-of-thought",
    "creative": "role-playing",
    "data": "structured-output",
    "system": "meta-prompting",
    "general": "auto",
}
```

**Layer 5: Historical learning**

Queries past completed optimizations with same task_type from DB. Each strategy must have >= 3 completed optimizations to be considered (minimum sample size). Excludes passthrough scoring modes to avoid skewing from different score distributions. Uses the highest-scoring strategy if it meets the 6.0 quality threshold. This makes the heuristic adaptive over time without any LLM calls.

All keyword matching in the heuristic analyzer is **case-insensitive** -- prompts are lowercased before matching against signal dictionaries.

```python
async def _learn_from_history(
    self, db: AsyncSession, task_type: str,
) -> str | None:
    """Query historical strategy performance for this task_type.

    Excludes passthrough scoring modes to avoid score distribution skew.
    Requires >= 3 optimizations per strategy for statistical significance.
    """
    result = await db.execute(
        select(
            Optimization.strategy_used,
            func.avg(Optimization.overall_score).label("avg_score"),
            func.count().label("count"),
        )
        .where(
            Optimization.task_type == task_type,
            Optimization.status == "completed",
            Optimization.overall_score.isnot(None),
            Optimization.scoring_mode.notin_(["heuristic", "hybrid_passthrough"]),
        )
        .group_by(Optimization.strategy_used)
        .having(func.count() >= 3)
    )
    rows = result.all()
    if not rows:
        return None
    best = max(rows, key=lambda r: r.avg_score)
    if best.avg_score >= 6.0:  # Minimum quality threshold
        return best.strategy_used
    return None
```

**Layer 6: Intent label generation**

Template-based from task_type + domain + first verb extraction:
```python
def _generate_intent_label(
    self, raw_prompt: str, task_type: str, domain: str,
) -> str:
    first_verb = self._extract_first_verb(raw_prompt)
    if domain != "general":
        return f"{first_verb} {domain} {task_type} task"
    return f"{first_verb} {task_type} task"
```

Falls back to `"{task_type} optimization"` if verb extraction fails.

### 4. Augmented `RepoIndexService`

**File**: `backend/app/services/repo_index_service.py`

#### 4a: Structured `FileOutline` extraction

Replace the naive `_extract_outline()` (first 30 non-empty lines) with type-aware extraction.

```python
@dataclass
class FileOutline:
    file_path: str
    file_type: str              # "python", "typescript", "config", "docs", etc.
    structural_summary: str     # Key definitions (classes, functions, exports)
    imports_summary: str | None # Condensed dependency list
    doc_summary: str | None     # Module docstring (first paragraph only)
    size_lines: int
    size_bytes: int
```

**Extraction rules by file type**:

| File type | What to extract |
|---|---|
| Python (`.py`) | Module docstring (first paragraph), `class` + `def` signatures with their docstring first lines, `__all__` exports |
| TypeScript/JS (`.ts`, `.js`, `.tsx`, `.jsx`) | JSDoc summary, `export` declarations, `interface`/`type` definitions, `function`/`class` signatures |
| Svelte (`.svelte`) | `<script>` exports, prop declarations (`export let`), component name from filename |
| Config (`.json`, `.yaml`, `.toml`) | Top-level keys only, first 15 lines if file is small (<50 lines) |
| Markdown (`.md`) | H1 + H2 headings as outline, first paragraph |
| SQL (`.sql`) | `CREATE TABLE`/`CREATE INDEX` statements, function signatures |
| Other code | Class/function signature regex + first 20 non-empty lines |

Each outline is capped at **500 chars** (vs current unlimited). Tighter outlines = more files fit in retrieval budget.

**Implementation approach**: A `_extract_structured_outline()` function with a dispatch table keyed by file extension. Each extractor uses simple regex patterns -- no AST parsing (keeps it fast and dependency-free).

```python
_OUTLINE_EXTRACTORS: dict[str, Callable[[str, str], FileOutline]] = {
    ".py": _extract_python_outline,
    ".ts": _extract_typescript_outline,
    ".js": _extract_typescript_outline,  # Same parser
    ".tsx": _extract_typescript_outline,
    ".jsx": _extract_typescript_outline,
    ".svelte": _extract_svelte_outline,
    ".json": _extract_config_outline,
    ".yaml": _extract_config_outline,
    ".yml": _extract_config_outline,
    ".toml": _extract_config_outline,
    ".md": _extract_markdown_outline,
    ".sql": _extract_sql_outline,
}
# Fallback: _extract_generic_outline for unlisted extensions
```

Python extractor example:
```python
def _extract_python_outline(path: str, content: str) -> FileOutline:
    lines = content.splitlines()
    # Module docstring: first triple-quote block, first paragraph only
    doc = _extract_module_docstring(lines)
    # Class/function signatures: lines matching ^class or ^def
    sigs = _extract_signatures(lines, patterns=[
        r'^class\s+\w+',
        r'^(?:async\s+)?def\s+\w+',
    ])
    # Imports: condensed to "imports: os, sys, pathlib, ..."
    imports = _extract_imports(lines)
    # __all__ exports
    exports = _extract_python_all(content)

    structural = "\n".join(sigs[:15])  # Cap at 15 signatures
    return FileOutline(
        file_path=path,
        file_type="python",
        structural_summary=structural[:500],
        imports_summary=imports,
        doc_summary=doc,
        size_lines=len(lines),
        size_bytes=len(content.encode()),
    )
```

#### 4b: Richer embeddings

Embed a composite text combining multiple signals for better semantic matching:

```python
def _build_embedding_text(path: str, outline: FileOutline) -> str:
    """Combine path + structural info for richer embedding."""
    parts = [path]
    if outline.doc_summary:
        parts.append(outline.doc_summary)
    if outline.structural_summary:
        parts.append(outline.structural_summary)
    return " | ".join(parts)[:1000]  # Cap embedding input at 1000 chars
```

This means a query like "authentication middleware" will match `backend/app/middleware/auth.py` even if the filename doesn't contain "authentication" -- because the embedding captures the docstring content.

#### 4c: Curated retrieval -- `query_curated_context()`

New method alongside existing `query_relevant_files()`:

```python
@dataclass
class CuratedCodebaseContext:
    context_text: str           # Formatted for template injection
    files_included: int
    total_files_indexed: int
    index_freshness: str        # "fresh" | "stale" | "unknown"
    top_relevance_score: float

async def query_curated_context(
    self,
    repo_full_name: str,
    branch: str,
    query: str,
    task_type: str | None = None,
    domain: str | None = None,
    max_chars: int = 8000,
) -> CuratedCodebaseContext | None:
```

**Retrieval pipeline**:

1. **Semantic search** -- Cosine similarity against index embeddings.
2. **Relevance filtering** -- Drop results below 0.3 similarity threshold.
3. **Domain boosting** -- If `domain` is known from heuristic analyzer, apply a 1.3x multiplier to files matching domain path patterns:
   - `domain="backend"` -> `backend/`, `server/`, `api/`, `app/`
   - `domain="frontend"` -> `frontend/`, `src/components/`, `src/lib/`
   - `domain="database"` -> `models/`, `migrations/`, `*.sql`, `alembic/`
   - `domain="devops"` -> `docker`, `ci/`, `.github/workflows/`
   - `domain="security"` -> `auth`, `security/`, `middleware/`
4. **Diversity selection** -- Cap at 3 files per directory to ensure breadth across the codebase.
5. **Budget packing** -- Greedily pack outlines into `max_chars`, highest relevance first. Each entry formatted as:
   ```
   ## path/to/file.py (relevance: 0.87)
   Module docstring first paragraph.
   Classes: ClassName, OtherClass
   Functions: func_a(args), func_b(args)
   Exports: __all__ = [...]
   ```
6. **Freshness reporting** -- Check `RepoIndexMeta.head_sha` against last known HEAD. Report "fresh", "stale", or "unknown".

### 5. Passthrough Template Updates

**File**: `prompts/passthrough.md`

Add new optional sections that the enrichment service populates. Section placement order in the template (matching `optimize.md` structure for consistency):

```markdown
## User Prompt
{{raw_prompt}}

## Analysis                          <!-- NEW section, after raw_prompt -->
{{analysis_summary}}
<!-- Formatted by HeuristicAnalyzer.format_analysis_summary() as:
Task type: coding
Domain: backend
Weaknesses:
- lacks constraints — no boundaries for the output
- no examples to anchor expected output
Strengths:
- specific technical context provided
- well-organized prompt structure
Recommended strategy: structured-output (confidence: 0.82)
-->

## Strategy Instructions
{{strategy_instructions}}

## Codebase Context                  <!-- NEW section, after strategy -->
{{codebase_context}}
<!-- From RepoIndexService.query_curated_context() — structured file outlines -->

## Workspace Context
{{codebase_guidance}}

## Proven Patterns                   <!-- NEW section, after workspace -->
{{applied_patterns}}
<!-- From taxonomy engine auto-injection — bullet list of reusable patterns -->

## Adaptation History
{{adaptation_state}}

## Scoring Rubric
{{scoring_rubric_excerpt}}

## Instructions
[existing passthrough instructions]
```

Each new section is wrapped in its own heading. The `PromptLoader` strips empty XML/heading sections when the variable is `None`, so sections gracefully disappear when context is unavailable.

**File**: `prompts/manifest.json`

Update `passthrough.md` entry to declare new optional variables:
```json
{
  "passthrough.md": {
    "required": ["raw_prompt", "scoring_rubric_excerpt"],
    "optional": [
      "strategy_instructions",
      "codebase_guidance",
      "codebase_context",
      "adaptation_state",
      "analysis_summary",
      "applied_patterns"
    ]
  }
}
```

**File**: `backend/app/services/passthrough.py`

`assemble_passthrough_prompt()` gains new parameters:

```python
def assemble_passthrough_prompt(
    prompts_dir: Path,
    raw_prompt: str,
    strategy_name: str | None = None,
    codebase_guidance: str | None = None,
    codebase_context: str | None = None,      # NEW
    adaptation_state: str | None = None,
    analysis_summary: str | None = None,       # NEW
    applied_patterns: str | None = None,       # NEW
) -> tuple[str, str]:
```

### 6. DB Persistence Improvements

Passthrough optimizations now receive:
- `task_type` from heuristic analyzer (not hardcoded "general")
- `domain` from heuristic analyzer (not hardcoded "general")
- `intent_label` from heuristic analyzer
- `context_sources` dict (previously `None` for passthrough)
- `strategy_used` from adaptation-aware recommendation

The `Optimization` record is populated with enriched fields at creation time (in `routers/optimize.py` and `tools/optimize.py` passthrough paths), not just at save time.

**Precedence on save**: When `passthrough_save` updates the record, external LLM values take precedence over heuristic values: `opt.task_type = body.task_type or opt.task_type or "general"`. This means the heuristic provides a good default for the pending record, but if the external LLM returns a more specific task_type, it wins. Same for domain and intent_label.

### 7. Configuration

**File**: `backend/app/config.py`

New settings:

```python
INDEX_OUTLINE_MAX_CHARS: int = Field(
    default=500,
    description="Maximum characters per file outline in RepoIndexService.",
)
INDEX_CURATED_MAX_CHARS: int = Field(
    default=8000,
    description="Maximum characters for curated codebase context in passthrough.",
)
INDEX_CURATED_MIN_SIMILARITY: float = Field(
    default=0.3,
    description="Minimum cosine similarity threshold for curated retrieval.",
)
INDEX_CURATED_MAX_PER_DIR: int = Field(
    default=3,
    description="Maximum files per directory in curated retrieval (diversity cap).",
)
INDEX_DOMAIN_BOOST: float = Field(
    default=1.3,
    description="Similarity multiplier for files matching the detected domain.",
)
```

## Target Context Availability (After Implementation)

| Context Source | Internal | Sampling | Passthrough |
|---|---|---|---|
| Analysis | Full LLM (Sonnet) | Full LLM (sampling) | **Heuristic (zero-LLM)** |
| Explore / Codebase context | Full (Haiku synthesis) | Full (sampling LLM) | **Curated index retrieval** |
| Workspace guidance | WorkspaceIntelligence | WorkspaceIntelligence | **WorkspaceIntelligence** (unified) |
| Adaptation state | Task-specific | Task-specific | **Task-specific** (heuristic task_type) |
| Applied meta-patterns | Auto-injected | Auto-injected | **Auto-injected** |
| Context sources in DB | Full dict | Full dict | **Full dict** |

## File Plan

### New Files

| File | Purpose |
|---|---|
| `backend/app/services/context_enrichment.py` | Unified `ContextEnrichmentService` + `EnrichedContext` dataclass |
| `backend/app/services/heuristic_analyzer.py` | Rule-based classifier: task_type, domain, weaknesses, strategy recommendation, historical learning |

### Modified Files

| File | Change |
|---|---|
| `backend/app/services/roots_scanner.py` | Add `discover_project_dirs()`, expanded guidance file list (8 files), content deduplication by SHA256 |
| `backend/app/services/workspace_intelligence.py` | Use `discover_project_dirs()` from scanner, remove duplicated manifest detection |
| `backend/app/services/repo_index_service.py` | `FileOutline` dataclass, type-aware extraction, composite embeddings, `query_curated_context()` + `CuratedCodebaseContext` |
| `backend/app/services/passthrough.py` | Accept `analysis_summary`, `codebase_context`, `applied_patterns` parameters |
| `backend/app/routers/optimize.py` | Replace inline context resolution with `ContextEnrichmentService.enrich()` call in all 3 handlers: `POST /api/optimize`, `POST /api/optimize/passthrough`, and `POST /api/optimize/passthrough/save` |
| `backend/app/tools/optimize.py` | Replace inline context resolution with `ContextEnrichmentService.enrich()` call |
| `backend/app/tools/prepare.py` | Replace inline context resolution with `ContextEnrichmentService.enrich()`, wire `repo_full_name` |
| `backend/app/tools/_shared.py` | Remove `resolve_workspace_guidance()` (moved into enrichment service), add `get_context_service()` / `set_context_service()` |
| `prompts/passthrough.md` | Add `{{analysis_summary}}`, `{{codebase_context}}`, `{{applied_patterns}}` optional sections |
| `prompts/manifest.json` | Update passthrough.md variable declarations |
| `backend/app/config.py` | Add `INDEX_OUTLINE_MAX_CHARS`, `INDEX_CURATED_MAX_CHARS`, `INDEX_CURATED_MIN_SIMILARITY`, `INDEX_CURATED_MAX_PER_DIR`, `INDEX_DOMAIN_BOOST` |

### Unchanged Files

| File | Reason |
|---|---|
| `backend/app/services/pipeline.py` | Internal pipeline keeps its own LLM-based explore/analyze. Receives enriched workspace + adaptation + patterns. |
| `backend/app/services/sampling_pipeline.py` | Same -- receives enriched context, runs own LLM phases. |
| `backend/app/services/codebase_explorer.py` | Still used by internal/sampling tiers for Haiku synthesis. |
| `backend/app/services/context_resolver.py` | Remains as validation/capping utility. Enrichment service calls it for sanitization. |

## Error Handling

- **Heuristic analyzer failures**: Return `HeuristicAnalysis` with `task_type="general"`, `confidence=0.0`, empty weaknesses/strengths. Never block the pipeline.
- **RepoIndex not ready/stale**: `query_curated_context()` returns `None`. Passthrough proceeds without codebase context (graceful degradation).
- **Taxonomy engine unavailable**: `applied_patterns` is `None`. No crash.
- **Workspace scanning failures**: Individual file read errors logged and skipped. Total failure returns `None`.
- **All enrichment layers are optional**: If every layer returns `None`, the passthrough prompt is still valid (just less enriched, same as today).
- **RepoFileIndex migration**: Existing `outline` column stores plain text (old format). The `query_curated_context()` method formats outlines at query time from whatever is stored. Newly indexed files get structured outlines; old rows degrade gracefully (treated as generic outlines). A full re-index (`build_index`) refreshes all rows to the new format. No schema migration needed — the column type (`Text`) is unchanged.

## Testing Strategy

- **Heuristic analyzer**: Unit tests with known prompts for each task_type/domain. Edge cases: empty prompt, multi-language prompt, ambiguous signals. Verify confidence scoring and weakness detection completeness.
- **Enhanced RootsScanner**: Test with mock filesystem: monorepo structure, duplicate files, budget exhaustion, skip-dir filtering.
- **RepoIndexService**: Test `FileOutline` extractors per file type. Test `query_curated_context()` with mock index data: domain boosting, diversity selection, budget packing.
- **ContextEnrichmentService**: Integration tests verifying correct dispatch per tier. Verify context_sources dict is accurate. Verify passthrough gets all layers.
- **Passthrough template**: Verify rendered output includes new sections when provided, omits cleanly when `None`.
