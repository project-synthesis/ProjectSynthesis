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

- Python 3.12+ and Node.js 20+
- At least one LLM provider — Claude Code CLI (`claude login`) or an Anthropic API key

## Installation

```bash
cp .env.example .env   # fill in API keys and secrets
./init.sh              # install dependencies and start all services
```

See `.env.example` for all configuration options and [CLAUDE.md](CLAUDE.md) for full architecture details.

## Usage

```bash
./init.sh          # start all services
./init.sh status   # check service status
./init.sh restart  # restart (required after changing Python packages)
./init.sh stop     # stop all services
```

| Service | URL |
|---|---|
| Frontend | http://localhost:5199 |
| API docs | http://localhost:8000/api/docs |
| MCP server | http://127.0.0.1:8001/mcp |

## Pipeline

| Stage | What it does |
|---|---|
| **Explore** | Reads linked GitHub repository context (file tree, key files) |
| **Analyze** | Classifies prompt type, task domain, and complexity |
| **Strategy** | Selects the optimal optimization framework |
| **Optimize** | Rewrites the prompt using the chosen strategy |
| **Validate** | Scores the result across multiple dimensions (0–10) |

## MCP Server

13 tools accessible directly from Claude Code when this directory is open. See [docs/MCP.md](docs/MCP.md) for the full tool reference.

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR. Found a security issue? See [SECURITY.md](SECURITY.md) — do not open a public issue.

## License

Copyright 2026 Project Synthesis Contributors.
Licensed under the [Apache License, Version 2.0](LICENSE).
