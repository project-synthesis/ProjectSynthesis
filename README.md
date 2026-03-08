# Project Synthesis

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**AI-powered prompt optimization with a 5-stage analysis pipeline.**

Project Synthesis runs your prompts through a structured pipeline — **Explore → Analyze → Strategy → Optimize → Validate** — producing a measurably improved result with per-dimension scoring, diff view, and full trace visibility.

## Features

- **5-stage pipeline** — each stage streams results in real time with full trace visibility
- **GitHub integration** — link a repository so the Explore stage reads your codebase as context
- **Branch-aware** — browse and select branches directly from the repo picker
- **Scoring** — Validate stage returns per-dimension scores (0–10) with actionable feedback
- **Diff view** — side-by-side comparison of original vs optimized prompt
- **History** — all optimization runs stored locally with sort and filter
- **MCP server** — 13 tools exposing the full API to Claude Code and other MCP clients
- **Two LLM providers** — Claude Code CLI (Max subscription, zero cost) or Anthropic API key

## Prerequisites

- Python 3.12+
- Node.js 20+
- At least one LLM provider:

  **Option A (preferred)** — Claude Code CLI with Max subscription:
  ```bash
  npm install -g @anthropic-ai/claude-code
  claude login
  ```

  **Option B** — Anthropic API key (set `ANTHROPIC_API_KEY` in `.env`)

## Configuration

Copy the example env file and fill in the required values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | If not using CLI | Anthropic API key |
| `GITHUB_APP_CLIENT_ID` | For GitHub OAuth | OAuth client ID of your GitHub App |
| `GITHUB_APP_CLIENT_SECRET` | For GitHub OAuth | OAuth client secret |
| `GITHUB_TOKEN_ENCRYPTION_KEY` | For GitHub OAuth | Fernet key for token encryption at rest |
| `SECRET_KEY` | Yes | Session signing key — change in production |

Generate a Fernet key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

GitHub OAuth requires a GitHub App with callback URL set to `http://localhost:8000/auth/github/callback`. Create one at [github.com/settings/apps/new](https://github.com/settings/apps/new).

## Quick Start

```bash
./init.sh          # Install dependencies and start all services
./init.sh status   # Check service status
./init.sh restart  # Restart all services (required after changing Python packages)
./init.sh stop     # Stop all services
```

Services start at:
- Frontend: http://localhost:5199
- API + docs: http://localhost:8000/api/docs
- MCP server: http://127.0.0.1:8001/mcp

## Services

| Service | Port | Entry point |
|---|---|---|
| FastAPI backend | 8000 | `backend/app/main.py` |
| SvelteKit frontend | 5199 | `frontend/src/` |
| MCP server (standalone) | 8001 | `backend/app/mcp_server.py` |

Logs: `data/backend.log`, `data/frontend.log`, `data/mcp.log`

## Pipeline stages

| Stage | What it does |
|---|---|
| **Explore** | Reads linked GitHub repository context (file tree, key files) |
| **Analyze** | Classifies prompt type, task domain, and complexity |
| **Strategy** | Selects the optimal optimization framework |
| **Optimize** | Rewrites the prompt using the chosen strategy |
| **Validate** | Scores the result across multiple dimensions (0–10) |

## Development

```bash
# Backend tests
cd backend && source .venv/bin/activate && pytest

# TypeScript check
cd frontend && npx tsc --noEmit

# Backend only (with hot reload)
cd backend && source .venv/bin/activate && \
  python -m uvicorn app.main:asgi_app --host 0.0.0.0 --port 8000 --reload

# Frontend only
cd frontend && npm run dev
```

## MCP Server

Project Synthesis exposes 13 tools via MCP, accessible directly from Claude Code when this directory is open (configured via `.mcp.json`). See [docs/MCP.md](docs/MCP.md) for the full tool reference and connection instructions.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on branching, code style, and the PR process.

Found a security issue? See [SECURITY.md](SECURITY.md) for responsible disclosure instructions — do not open a public issue.

## License

Copyright 2026 Project Synthesis Contributors.
Licensed under the [Apache License, Version 2.0](LICENSE).
