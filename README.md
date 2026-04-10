# Project Synthesis

AI-powered prompt optimization with a self-organizing knowledge engine. Paste a prompt, get a better version back — and the system learns what works over time.

## What It Does

Project Synthesis takes any prompt and runs it through a 3-phase optimization pipeline:

1. **Analyze** — Classifies the prompt type, identifies weaknesses, selects the best optimization strategy
2. **Optimize** — Rewrites the prompt using the selected strategy while preserving intent
3. **Score** — Independently evaluates both original and optimized on 5 dimensions (clarity, specificity, structure, faithfulness, conciseness) with hybrid scoring and randomized A/B presentation to prevent bias

The result: an optimized prompt with per-dimension score deltas showing exactly what improved.

After optimization, you can **refine iteratively** — click suggestions or type custom requests, and each turn runs a fresh pipeline pass with version tracking, branching, and rollback.

## How It Learns

The real value isn't any single optimization — it's what happens over time. Every prompt you optimize feeds a self-organizing taxonomy engine that:

- **Clusters** your prompts by semantic similarity — not by hardcoded categories, but by what they actually ask for. The system discovers your domains organically from your usage patterns
- **Extracts patterns** — reusable techniques that made prompts better. "Always specify the target audience" or "include concrete examples" emerge as meta-patterns when they consistently correlate with high scores
- **Injects proven techniques** — when you optimize a new prompt, the system searches for relevant patterns from your history and injects them automatically. You get better results because the system remembers what worked before
- **Shares cross-project knowledge** — techniques that prove universal across 2+ projects are promoted to durable global patterns and applied everywhere with a relevance boost

The taxonomy is self-tuning. Clusters split when they grow incoherent, merge when they're redundant, and retire when they go stale. Domain nodes emerge when enough related prompts accumulate. The warm path processes only clusters that changed since the last cycle. The system scales from 100 to 50,000+ prompts without configuration.

## Direction

Project Synthesis is built as a **universal prompt optimization engine**. The core pipeline — analyze, optimize, score, learn — works on any prompt type: coding tasks, marketing copy, legal briefs, business analysis, creative writing, educational content, or anything else you write for an LLM.

**Developers are the first audience**, because they already use AI heavily and have the tooling (IDEs, MCP, repos) that makes integration natural. The current scaffolding reflects this: GitHub integration for codebase context, MCP server for IDE workflows, and developer-focused seed domains. But the engine underneath is domain-agnostic.

**Adding a new vertical requires no code changes.** The extension points are all content:
- **Seed agents** — drop `.md` files in `prompts/seed-agents/` (hot-reloaded)
- **Domain keywords** — bootstrap classification for new subject areas via migration
- **Weakness signals** — domain-specific quality detection rules
- **Context providers** — pluggable integrations beyond GitHub (Google Drive, Notion, local files)

The organic domain discovery handles the rest. A marketing team's prompts would discover "copywriting", "brand-voice", "campaign" domains the same way developer prompts discovered "backend", "frontend", "devops". Cross-domain global patterns enable knowledge transfer between verticals — a technique learned in marketing can improve a developer's user-facing documentation.

See [ADR-006](docs/adr/ADR-006-universal-prompt-engine.md) for the full architectural decision and vertical rollout playbook.

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

# Start everything (auto-detects provider + VS Code bridge)
cd .. && ./init.sh start

# Open the app
open http://localhost:5199/app
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
| Taxonomy | Spectral clustering + HDBSCAN + adaptive cosine thresholds + UMAP 3D + OKLab coloring. Multi-project hierarchy (project → domain → cluster). Organic domain discovery |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2, 384-dim, CPU). Dual-backend index: numpy (default) + hnswlib HNSW (auto at ≥1000 clusters) |
| LLM | Configurable per phase — Opus, Sonnet, Haiku (via Settings) |
| Scoring | Hybrid: LLM + heuristic blending per dimension, z-score normalization (≥30 samples), divergence detection |
| MCP | Streamable HTTP on port 8001 — process-level singleton routing with dual disconnect detection |
| Versioning | `version.json` → `scripts/sync-version.sh` propagates to backend + frontend |

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Backend | 8000 | FastAPI API + pipeline orchestration |
| Frontend | 5199 | SvelteKit dev server |
| MCP Server | 8001 | 13-tool MCP server for IDE integration |

```bash
./init.sh start        # start all (provider detection + bridge install + health probes)
./init.sh stop         # graceful stop (process group kill, no orphans)
./init.sh restart      # stop + start
./init.sh status       # service health + VS Code + provider + active tier
./init.sh logs         # tail all logs
./init.sh setup-vscode # install/update VS Code bridge extension
./init.sh update [tag] # auto-update to latest release (or specific tag)
```

## Features

### Core Pipeline
- **One-shot optimization** with 5-dimension hybrid scoring (clarity, specificity, structure, faithfulness, conciseness)
- **Conversational refinement** — iterative improvement with version history, branching, and rollback
- **3 suggestions per turn** — score-driven, analysis-driven, and strategic
- **Adaptive strategies** — file-driven from `prompts/strategies/*.md` with YAML frontmatter. Add/remove/edit `.md` files and they auto-appear in the UI. `auto` strategy resolves to task-type-appropriate strategy at runtime
- **Hybrid scoring** — LLM scores blended with heuristic analysis + z-score normalization against historical distribution. Divergence flags when LLM and heuristic disagree by >2.5 points

### Knowledge Engine
- **Evolutionary taxonomy** — self-organizing hierarchical clustering with multi-project isolation. Project → domain → cluster → optimizations. Organic domain discovery from user behavior
- **Pattern extraction** — reusable techniques extracted from successful optimizations, stored as meta-patterns per cluster
- **Cross-cluster injection** — universal techniques injected across topic boundaries, ranked by composite relevance
- **Global pattern tier** — durable cross-project patterns promoted from meta-pattern siblings spanning 2+ projects, injected with 1.3x relevance boost. Validated with demotion/re-promotion hysteresis, 500 retention cap
- **Adaptive scheduling** — linear regression boundary with all-dirty vs round-robin mode. Only changed clusters processed in split/merge phases. Starvation guard prevents project neglect
- **3D taxonomy visualization** — Three.js interactive topology with LOD tiers, diegetic UI, state filter tabs, click-to-focus navigation, force-directed layout

### Developer Integration (First Vertical)
- **GitHub integration** — zero-config Device Flow OAuth (no secrets, no callback URL). Link a repo, browse files, and get codebase-aware optimization. Background indexing builds semantic file outlines + Haiku architectural synthesis on link/reindex
- **Two-layer codebase context** — cached explore synthesis (architectural overview, once per repo) + per-prompt curated retrieval (semantic file search, 30K char cap). Zero request-time LLM calls for context — all tiers receive identical pre-computed context
- **MCP server** — use from any MCP-compatible IDE (VS Code, Claude Code, etc.)
- **VS Code bridge extension** — MCP Copilot Bridge for sampling-based optimization through the IDE's own LLM
- **Passthrough mode** — IDE's own LLM does the optimization; server provides context + heuristic analysis + hybrid scoring
- **Workspace scanning** — discovers CLAUDE.md, AGENTS.md, .cursorrules and other guidance files for context injection
- **Batch taxonomy seeding** — generate diverse prompts from a project description, optimize in parallel, let taxonomy discover patterns. 5 default seed agents, user-extensible by dropping `.md` files

### Application
- **Persistent settings** — model selection per phase, pipeline toggles, default strategy. Survives restarts
- **Session persistence** — page refresh restores your last optimization
- **Markdown rendering** — optimized prompts rendered with brand-compliant markdown
- **Production diff viewer** — unified and split modes with word-level highlighting
- **Real-time events** — SSE-based event bus with toast notifications
- **Taxonomy Activity panel** — live feed of all taxonomy decision events with filters and expandable context
- **Pattern suggestion on paste** — cosine-searches active clusters, suggests matches with 1-click apply
- **Tier-aware UI** — accent color adapts to active routing tier (CLI/API, sampling, passthrough)
- **Feedback loop** — thumbs up/down drives strategy affinity adaptation + phase weight adaptation
- **Auto-update** — detects new releases on startup (3-tier: git tags, raw fetch, GitHub Releases API). Persistent StatusBar badge, one-click update dialog with changelog, detached HEAD warning, post-update validation. CLI: `./init.sh update [tag]`
- **API key management** — set/update/remove via UI with Fernet encryption at rest
- **Trace logging** — per-phase JSONL traces with daily rotation

## MCP Integration

The MCP server provides 13 tools with `synthesis_` prefix on port 8001. All tools use `structured_output=True` (return Pydantic models, expose `outputSchema` to MCP clients).

### Core pipeline tools

| Tool | Purpose |
|------|---------|
| `synthesis_optimize` | Full pipeline — send a prompt, get back optimized version with scores |
| `synthesis_analyze` | Analysis + baseline scoring — task type, weaknesses, strategy recommendation |
| `synthesis_prepare_optimization` | Assemble prompt + context for your IDE's LLM to process (passthrough step 1) |
| `synthesis_save_result` | Persist the IDE LLM's result with hybrid scoring (passthrough step 3) |

### Workflow tools

| Tool | Purpose |
|------|---------|
| `synthesis_health` | System capabilities check — provider, tiers, strategies, stats |
| `synthesis_strategies` | List available optimization strategies with metadata |
| `synthesis_history` | Paginated optimization history with sort/filter |
| `synthesis_get_optimization` | Full optimization detail by ID or trace_id |
| `synthesis_match` | Knowledge graph search for similar clusters and reusable patterns |
| `synthesis_feedback` | Submit quality feedback to drive strategy adaptation |
| `synthesis_refine` | Iteratively improve an optimized prompt with specific instructions |
| `synthesis_seed` | Batch-seed the taxonomy — generate + optimize + persist + cluster |
| `synthesis_explain` | Plain-English explanation of what an optimization changed and why |

Connect via `.mcp.json` (auto-loaded by Claude Code) or manually at `http://127.0.0.1:8001/mcp`.

## Docker

```bash
docker compose up --build -d
# Backend + Frontend + MCP + nginx on port 80
```

## Development

```bash
# Backend tests (~180s, 1932 tests)
cd backend && source .venv/bin/activate && pytest --cov=app -v

# Frontend type check
cd frontend && npx svelte-check

# Frontend tests (969 tests)
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
| `/api/history` | GET | List past optimizations |
| `/api/feedback` | POST/GET | Submit/list feedback |
| `/api/providers` | GET | Active provider info |
| `/api/provider/api-key` | GET/PATCH/DELETE | API key management |
| `/api/preferences` | GET/PATCH | Persistent user preferences |
| `/api/strategies` | GET | List strategies |
| `/api/strategies/{name}` | GET/PUT | Read/update strategy template |
| `/api/health` | GET | Health + pipeline metrics + project/global pattern stats |
| `/api/events` | GET (SSE) | Real-time event stream |
| `/api/domains` | GET | List domain nodes |
| `/api/domains/{id}/promote` | POST | Promote cluster to domain |
| `/api/clusters` | GET | List clusters (paginated, state/domain filter) |
| `/api/clusters/{id}` | GET | Cluster detail (children, breadcrumb, optimizations, project breakdown) |
| `/api/clusters/{id}` | PATCH | Rename/state override |
| `/api/clusters/match` | POST | Match prompt against clusters |
| `/api/clusters/tree` | GET | Flat node list for 3D viz (`?project_id=` for project subtree) |
| `/api/clusters/stats` | GET | Q metrics + sparkline |
| `/api/clusters/recluster` | POST | Cold-path refit |
| `/api/clusters/activity` | GET | Taxonomy decision event feed |
| `/api/seed` | POST | Batch-seed taxonomy |
| `/api/seed/agents` | GET | List available seed agents |
| `/api/update/status` | GET | Auto-update check result (version, tag, changelog) |
| `/api/update/apply` | POST (202) | Trigger update + restart (two-phase) |
| `/api/github/auth/device` | POST | Request device code (zero-config OAuth) |
| `/api/github/auth/device/poll` | POST | Poll for device authorization |
| `/api/github/auth/login` | GET | Callback OAuth login (fallback) |
| `/api/github/auth/callback` | GET | Callback OAuth redirect |
| `/api/github/auth/me` | GET | Current GitHub user info |
| `/api/github/repos` | GET | List user's GitHub repos |
| `/api/github/repos/link` | POST | Link repo to session (triggers background indexing) |
| `/api/github/repos/linked` | GET | Get linked repo for session |
| `/api/github/repos/unlink` | DELETE | Remove linked repo |
| `/api/github/repos/{owner}/{repo}/tree` | GET | Recursive file tree |
| `/api/github/repos/{owner}/{repo}/files/{path}` | GET | Read single file |
| `/api/github/repos/{owner}/{repo}/branches` | GET | List branches |
| `/api/github/repos/index-status` | GET | Indexing status for linked repo |
| `/api/github/repos/reindex` | POST | Trigger re-indexing |

## Roadmap

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for planned improvements and [`docs/adr/`](docs/adr/) for architectural decisions.

## License

Apache 2.0
