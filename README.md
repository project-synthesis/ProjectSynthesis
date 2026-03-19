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
- Either Claude CLI (`claude` on PATH — free for Max subscribers) or an Anthropic API key

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

The backend auto-detects your provider (Claude CLI first, then API key). No configuration needed for Max subscribers.

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
│      │ Patterns   │  Diff View           │  Sparkline  │
│      │ GitHub     │  Refinement Timeline │  Family     │
│      │ Settings   │  Radial Mindmap      │  Detail     │
├──────┴────────────┴──────────────────────┴─────────────┤
│                      Status Bar                        │
└────────────────────────────────────────────────────────┘
```

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy (async), aiosqlite |
| Frontend | SvelteKit 2 (Svelte 5 runes), Tailwind CSS 4 |
| Database | SQLite (WAL mode) |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2, 384-dim, CPU) |
| LLM | Configurable per phase — Opus, Sonnet, Haiku (via Settings) |
| Scoring | Hybrid: LLM scores blended with model-independent heuristics + z-score normalization |
| MCP | Streamable HTTP on port 8001 |

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Backend | 8000 | FastAPI API + pipeline orchestration |
| Frontend | 5199 | SvelteKit dev server |
| MCP Server | 8001 | 4-tool MCP server for IDE integration |

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
- **MCP server** — use from any MCP-compatible IDE (Claude Code, Cursor, etc.)
- **Passthrough mode** — IDE's own LLM does the optimization; server provides context + bias correction
- **Workspace scanning** — automatically discovers CLAUDE.md, AGENTS.md, .cursorrules for context injection
- **Hybrid scoring** — LLM scores blended with heuristic analysis (structure, readability, constraint density) + z-score normalization against historical distribution
- **Real-time events** — SSE-based event bus with toast notifications for file changes, MCP operations, and pipeline status
- **Pattern knowledge graph** — self-building library of prompt patterns extracted from every optimization. Clusters into families, extracts reusable meta-patterns, and suggests matching patterns on paste. D3.js radial mindmap visualization with domain grouping and cross-family similarity edges
- **Feedback loop** — thumbs up/down drives strategy affinity adaptation
- **API key management** — set/update/remove via UI with Fernet encryption at rest

## MCP Integration

The MCP server provides 4 tools:

| Tool | Purpose |
|------|---------|
| `synthesis_optimize` | Full pipeline — send a prompt, get back optimized version with scores |
| `synthesis_analyze` | Analysis + baseline scoring — task type, weaknesses, strategy recommendation, quality scores, actionable next steps |
| `synthesis_prepare_optimization` | Assemble prompt + context for your IDE's LLM to process |
| `synthesis_save_result` | Persist the IDE LLM's result with bias correction |

Connect via `.mcp.json` (auto-loaded by Claude Code) or manually at `http://127.0.0.1:8001/mcp`.

## Docker

```bash
docker compose up --build -d
# Backend + Frontend + MCP + nginx on port 80
```

## Development

```bash
# Backend tests (~30s)
cd backend && source .venv/bin/activate && pytest --cov=app -v

# Frontend type check
cd frontend && npx svelte-check

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
| `/api/history` | GET | List past optimizations (with truncated prompts) |
| `/api/feedback` | POST/GET | Submit/list feedback |
| `/api/providers` | GET | Active provider info |
| `/api/provider/api-key` | GET/PATCH/DELETE | API key management |
| `/api/preferences` | GET/PATCH | Persistent user preferences |
| `/api/strategies` | GET | List strategies (from disk, with frontmatter metadata) |
| `/api/strategies/{name}` | GET/PUT | Read/update strategy template |
| `/api/settings` | GET | Read-only server config |
| `/api/health` | GET | Health + pipeline metrics |
| `/api/events` | GET (SSE) | Real-time event stream |
| `/api/patterns/graph` | GET | Knowledge graph for radial mindmap |
| `/api/patterns/match` | POST | Match prompt against known families |
| `/api/patterns/families` | GET | List families (paginated) |
| `/api/patterns/families/{id}` | GET/PATCH | Family detail / rename |
| `/api/patterns/search` | GET | Semantic search across patterns |
| `/api/patterns/stats` | GET | Pattern count + domain distribution |
| `/api/github/auth/*` | GET/POST | GitHub OAuth flow |
| `/api/github/repos/*` | GET/POST/DELETE | Repo management |

## License

Apache 2.0
