# Project Synthesis

AI-powered prompt optimization. Paste a prompt, get a better version back with scored improvements.

## What It Does

Project Synthesis takes a raw prompt and runs it through a 3-phase optimization pipeline:

1. **Analyze** — Classifies the prompt type, identifies weaknesses, selects the best strategy
2. **Optimize** — Rewrites the prompt using the selected strategy while preserving intent
3. **Score** — Independently evaluates both original and optimized on 5 dimensions with hybrid scoring (LLM + heuristic blending) and randomized A/B presentation to prevent bias

Models are configurable per phase via Settings (default: Opus for optimizer, Sonnet for analyzer/scorer). Scoring and explore phases can be disabled for lean 2-call runs.

The result: an optimized prompt with per-dimension score deltas showing exactly what improved.

After optimization, you can **refine iteratively** — click suggestions or type custom requests, and each turn runs a fresh pipeline pass with version tracking, branching, and rollback.

## Quick Start

**Prerequisites:**
- Python 3.12+
- Node.js 22+
- Either Claude CLI (`claude` on PATH — included with Claude Code subscriptions) or an Anthropic API key

```bash
# Set up backend
cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && deactivate

# Set up frontend
cd ../frontend && npm install

# Start everything
cd .. && ./init.sh start

# Open in browser
open http://localhost:5199
```

The backend auto-detects your provider (Claude CLI first, then API key). No configuration needed if Claude CLI is installed.

### With an API key

Set via the UI (Settings panel) or environment:

```bash
echo "ANTHROPIC_API_KEY=sk-..." > .env
./init.sh restart
```

## Architecture

```
┌──────┬────────────┬──────────────────────┬─────────────┐
│ Act. │ Navigator  │   Editor Groups      │  Inspector  │
│ Bar  │            │                      │             │
│      │ Strategies │  Prompt Editor       │  Scores     │
│      │ History    │  Result (Markdown)   │  Deltas     │
│      │ Clusters   │  Diff View           │  Sparkline  │
│      │ GitHub     │  Refinement Timeline │  Cluster    │
│      │ Settings   │  3D Taxonomy         │  Detail     │
├──────┴────────────┴──────────────────────┴─────────────┤
│                      Status Bar                        │
└────────────────────────────────────────────────────────┘
```

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy (async), aiosqlite |
| Frontend | SvelteKit 2 (Svelte 5 runes), Tailwind CSS 4 |
| Database | SQLite (WAL mode) |
| Visualization | Three.js (3D taxonomy topology with LOD, raycasting, force layout) |
| Clustering | HDBSCAN + adaptive cosine threshold + UMAP 3D + OKLab coloring |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2, 384-dim, CPU) |
| LLM | Configurable per phase — Opus, Sonnet, Haiku (via Settings) |
| Scoring | Hybrid: LLM scores blended with model-independent heuristics + z-score normalization |
| MCP | Streamable HTTP on port 8001 — process-level singleton routing with dual disconnect detection |
| Versioning | `version.json` → `scripts/sync-version.sh` propagates to backend + frontend |

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Backend | 8000 | FastAPI API + pipeline orchestration |
| Frontend | 5199 | SvelteKit dev server |
| MCP Server | 8001 | 11-tool MCP server for IDE integration |

```bash
./init.sh start     # start all (with preflight checks + health probes)
./init.sh stop      # graceful stop (process group kill, no orphans)
./init.sh restart   # stop + start
./init.sh status    # show PIDs and ports
./init.sh logs      # tail all logs
```

## Features

- **One-shot optimization** with 5-dimension hybrid scoring (clarity, specificity, structure, faithfulness, conciseness)
- **Conversational refinement** — iterative improvement with version history, branching, and rollback
- **3 suggestions per turn** — score-driven, analysis-driven, and strategic
- **Adaptive strategies** — file-driven from `prompts/strategies/*.md` with YAML frontmatter. Add/remove/edit `.md` files and they auto-appear in the UI via real-time file watching
- **Inline strategy editor** — click to edit strategy templates directly from the sidebar with live disk save
- **Persistent settings** — model selection per phase, pipeline toggle (explore/scoring/adaptation), default strategy. Survives restarts via `data/preferences.json`
- **Session persistence** — page refresh restores your last optimization from the database
- **Markdown rendering** — optimized prompts rendered with brand-compliant markdown (headers, code blocks, lists, tables)
- **Production diff viewer** — unified and split modes with word-level highlighting
- **GitHub integration** — link a repo for codebase-aware optimization via semantic embedding search
- **MCP server** — use from any MCP-compatible IDE (VS Code, Claude Code, etc.)
- **VS Code bridge extension** — MCP Copilot Bridge (`VSGithub/mcp-copilot-extension/`) connects via Streamable HTTP, declares sampling capability, and forwards `sampling/createMessage` to VS Code's Language Model API. 10s health check with auto-reconnect
- **Passthrough mode** — IDE's own LLM does the optimization; server provides context + heuristic analysis + hybrid scoring with domain validation. Zero-LLM `HeuristicAnalyzer` classifies task type, domain, weaknesses, and recommends strategy without any LLM calls
- **Force sampling / passthrough toggles** — pin the pipeline to use the IDE's LLM (`force_sampling`) or always assemble for external processing (`force_passthrough`). Mutually exclusive, enforced server-side and client-side
- **Sampling capability detection** — ASGI middleware with dual-layer guard (RoutingManager state + class-level SSE tracking) prevents non-sampling clients from overwriting sampling state. Process-level singleton RoutingManager survives per-session lifespan churn. Two disconnect signals: full (all SSE closed) vs sampling-only (bridge leaves, Claude Code stays). Frontend receives real-time SSE events for tier changes
- **Tier-aware UI** — entire UI adapts accent color to active routing tier: cyan for CLI/API, green for sampling, yellow for passthrough. Includes brand logo, activity bar, tabs, buttons, inputs, headings, and all interactive elements. Settings sections (Provider/Connection/Routing, System) morph content per tier
- **Per-phase model display** — actual model IDs used by the IDE are captured per phase and displayed in real time (Navigator IDE Model section + Inspector metadata). Stored in `models_by_phase` DB column for audit trail
- **Workspace scanning** — automatically discovers CLAUDE.md, AGENTS.md, GEMINI.md, .cursorrules, .clinerules, CONVENTIONS.md for context injection. Monorepo-aware: `discover_project_dirs()` scans manifest-containing subdirectories with SHA256 deduplication
- **Hybrid scoring** — LLM scores blended with heuristic analysis (structure, readability, constraint density) + z-score normalization against historical distribution. Dimension-specific weights prevent single-model bias. Divergence flags when LLM and heuristic disagree by >2.5 points
- **Real-time events** — SSE-based event bus with toast notifications for file changes, MCP operations, and pipeline status
- **Evolutionary taxonomy engine** — self-organizing hierarchical clustering that groups optimizations into a navigable taxonomy. Three execution paths: hot (per-optimization multi-embedding + nearest-node search), warm (periodic re-clustering with speculative lifecycle mutations + domain discovery + stale cluster pruning), cold (full refit + UMAP 3D projection + OKLab coloring + Haiku labeling). Spectral clustering as primary split algorithm (finds sub-communities in uniform-density spaces where HDBSCAN fails); HDBSCAN as fallback. Split children start as candidates — warm-path Phase 0.5 evaluates and promotes (coherence ≥ 0.30) or rejects (members reassigned to nearest active cluster). Quality-gated: 5-dimension Q_system score (coherence, separation, coverage, DBCV, stability) prevents regressions. Snapshot audit trail for recovery
- **Multi-signal embedding fusion** — composite queries blend 4 signals (topic, transformation, output, pattern) with per-phase adaptive weights for richer pattern matching. Score-weighted centroids give high-quality optimizations more cluster influence. TransformationIndex enables technique-space search across domains
- **Cross-cluster pattern injection** — universal techniques (high `global_source_count`) are injected regardless of topic cluster, ranked by composite relevance formula. Benefits all routing tiers (internal, passthrough, MCP)
- **Unified domain taxonomy** — domains are `PromptCluster` nodes with `state="domain"`, discovered organically from user behavior. No hardcoded domain constants — `DomainResolver` resolves from DB, `DomainSignalLoader` provides heuristic keyword signals from domain node metadata. Warm path proposes new domains when coherent sub-populations emerge under "general". Five stability guardrails prevent drift: color pinning, retire exemption, merge approval, separate coherence floor, split isolation. Stats cache with trend tracking
- **3D taxonomy visualization** — Three.js interactive topology with LOD tiers (far/mid/near) based on persistence thresholds. Diegetic UI (Dead Space-inspired): ambient telemetry only, controls auto-hide on right-edge hover, metrics via Q key, inline hint card on first visit. State filter tabs dim non-matching nodes (25% opacity) while matching glow at 100%. Click-to-focus navigation, raycasting hover, billboard labels, force-directed collision resolution, Ctrl+F search
- **Pattern suggestion on paste** — embeds pasted text, cosine-searches active clusters (≥0.72), suggests matching clusters with 1-click apply (50-char delta, 300ms debounce, 10s auto-dismiss). Applied patterns injected into optimizer context
- **Bidirectional history–clusters navigation** — history items show intent labels and domain badges. Loading an optimization auto-selects its cluster in Inspector. Clicking a linked optimization in a cluster detail loads it in the editor. Live cluster link: background pattern extraction triggers automatic UI sync via SSE
- **Batch taxonomy seeding** — explore-driven pipeline generates diverse prompts from a project description, optimizes them in parallel through the full pipeline, and lets taxonomy discover clusters/domains/patterns organically. 5 default seed agents (coding, architecture, analysis, testing, documentation) with YAML frontmatter — user-extensible by dropping `.md` files. Embedding-based deduplication. Provider-aware concurrency (CLI=10, API=5, sampling=2). SeedModal in topology view with agent selector, progress bar, and result card. 9 observability events for MLOps monitoring
- **Taxonomy Activity panel** — collapsible bottom panel below the 3D topology showing a live feed of all taxonomy decision events (hot/warm/cold/seed path, 12+ operation types). Filter by path, operation, or errors-only. Expandable context for each event. Click the `↗` button on score events to load the optimization in the editor. Seeds from ring buffer; falls back to JSONL history after server restart. Cross-process: MCP score events reach the panel via HTTP POST + ring buffer mirroring
- **StatusBar breadcrumb** — shows CLI/API/SAMPLING/PASSTHROUGH tier badge + `[domain] › intent_label` for the active optimization with domain color coding. Editor tabs use intent labels as titles
- **Feedback loop** — thumbs up/down drives strategy affinity adaptation + phase weight adaptation (EMA toward successful fusion profiles)
- **API key management** — set/update/remove via UI with Fernet encryption at rest
- **Per-phase effort controls** — configure `low`/`medium`/`high`/`max` effort per pipeline phase (analyzer, optimizer, scorer) via Settings. Applies to internal tier; sampling tier defers to the IDE's model selection
- **Structured repo indexing** — type-aware file outlines (Python classes/functions/imports, TypeScript exports, Svelte runes) with domain-boosted semantic retrieval and token-budget packing
- **Unified context enrichment** — single `ContextEnrichmentService.enrich()` entry point resolves workspace guidance, heuristic analysis, codebase context, adaptation state, and applied patterns for all routing tiers
- **Trace logging** — per-phase JSONL traces to `data/traces/` with daily rotation and configurable retention

## MCP Integration

The MCP server provides 12 tools with `synthesis_` prefix on port 8001. All tools use `structured_output=True` (return Pydantic models, expose `outputSchema` to MCP clients).

### Core pipeline tools

| Tool | Purpose |
|------|---------|
| `synthesis_optimize` | Full pipeline — send a prompt, get back optimized version with scores. 5 execution paths: force_passthrough → force_sampling → provider → sampling fallback → passthrough fallback |
| `synthesis_analyze` | Analysis + baseline scoring — task type, weaknesses, strategy recommendation, quality scores, actionable next steps |
| `synthesis_prepare_optimization` | Assemble prompt + context for your IDE's LLM to process (step 1 of passthrough workflow) |
| `synthesis_save_result` | Persist the IDE LLM's result with hybrid scoring and domain validation (step 3 of passthrough workflow) |

### Workflow tools

| Tool | Purpose |
|------|---------|
| `synthesis_health` | System capabilities check — provider, tiers, strategies, stats. Call at session start |
| `synthesis_strategies` | List available optimization strategies with metadata |
| `synthesis_history` | Paginated optimization history with sort/filter |
| `synthesis_get_optimization` | Full optimization detail by ID or trace_id |
| `synthesis_match` | Knowledge graph search for similar clusters, reusable patterns, and cross-cluster universal techniques |
| `synthesis_feedback` | Submit quality feedback (thumbs_up/thumbs_down) to drive strategy adaptation |
| `synthesis_refine` | Iteratively improve an optimized prompt with specific instructions |
| `synthesis_seed` | Batch-seed the taxonomy — generate + optimize + persist + cluster in one call |

Connect via `.mcp.json` (auto-loaded by Claude Code) or manually at `http://127.0.0.1:8001/mcp`.

## Docker

```bash
docker compose up --build -d
# Backend + Frontend + MCP + nginx on port 80
```

## Development

```bash
# Backend tests (~100s, 1530+ tests)
cd backend && source .venv/bin/activate && pytest --cov=app -v

# Frontend type check
cd frontend && npx svelte-check

# Frontend tests (880+ tests)
cd frontend && npm test

# Frontend build
cd frontend && npm run build
```

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/optimize` | POST (SSE) | Run optimization pipeline |
| `/api/optimize/{trace_id}` | GET | Get result (SSE reconnection) |
| `/api/refine` | POST (SSE) | Run refinement turn |
| `/api/refine/{id}/versions` | GET | List refinement versions |
| `/api/refine/{id}/rollback` | POST | Fork from a version |
| `/api/history` | GET | List past optimizations (includes intent_label, domain, cluster_id) |
| `/api/feedback` | POST/GET | Submit/list feedback |
| `/api/providers` | GET | Active provider info |
| `/api/provider/api-key` | GET/PATCH/DELETE | API key management |
| `/api/preferences` | GET/PATCH | Persistent user preferences |
| `/api/strategies` | GET | List strategies (from disk, with frontmatter metadata) |
| `/api/strategies/{name}` | GET/PUT | Read/update strategy template |
| `/api/settings` | GET | Read-only server config |
| `/api/health` | GET | Health + pipeline metrics |
| `/api/events` | GET (SSE) | Real-time event stream |
| `/api/domains` | GET | List domain nodes (labels, colors, metadata) |
| `/api/domains/{id}/promote` | POST | Promote cluster to domain status |
| `/api/clusters` | GET | List clusters (paginated, state/domain filter) |
| `/api/clusters/{id}` | GET | Cluster detail (children, breadcrumb, optimizations) |
| `/api/clusters/{id}` | PATCH | Rename/state override |
| `/api/clusters/match` | POST | Match prompt against clusters |
| `/api/clusters/tree` | GET | Flat node list for 3D viz |
| `/api/clusters/stats` | GET | Q metrics + sparkline |
| `/api/clusters/templates` | GET | Proven templates |
| `/api/clusters/recluster` | POST | Cold-path HDBSCAN + UMAP refit |
| `/api/clusters/reassign` | POST | Replay hot-path with current adaptive threshold |
| `/api/clusters/repair` | POST | Rebuild join records, meta-patterns, coherence |
| `/api/clusters/backfill-scores` | POST | Recompute cluster avg_score/scored_count |
| `/api/clusters/activity` | GET | Ring buffer of recent taxonomy decision events (filters: path, op, errors_only) |
| `/api/clusters/activity/history` | GET | Paginated JSONL history of taxonomy decision events by date |
| `/api/seed` | POST | Batch-seed taxonomy (generate + optimize + persist + cluster) |
| `/api/seed/agents` | GET | List available seed agents with metadata |
| `/api/github/auth/*` | GET/POST | GitHub OAuth flow |
| `/api/github/repos/*` | GET/POST/DELETE | Repo management |

## Roadmap

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for planned improvements. Key upcoming items:

- **Agglomerative cold-path rewrite** — replace HDBSCAN in cold path with Hierarchical Agglomerative Clustering + dendrogram cut (makes recluster actually useful)
- **Sub-domain cluster-count trigger** — trigger sub-domain discovery based on cluster count within a domain
- **Unified scoring service** — consolidate duplicated scoring orchestration across all pipeline tiers

## License

Apache 2.0
