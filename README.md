# Project Synthesis

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![GitHub Marketplace](https://img.shields.io/badge/GitHub_Marketplace-Project_Synthesis-purple?logo=github)](https://github.com/marketplace/project-synthesis)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Website](https://img.shields.io/badge/Website-projectsynthesis.ai-00bcd4)](https://projectsynthesis.ai)

**Multi-Agent Development Platform powered by Claude AI.**

Project Synthesis transforms complex development tasks into collaborative AI workflows through a spec-driven pipeline: **Explore → Analyze → Strategy → Optimize → Validate** — then orchestrates 16 specialized agents across domains to build production-ready software from your specification.

## Prerequisites

At least one LLM provider:

- **Option A (preferred)**: Claude Code CLI with Max subscription
  ```bash
  npm install -g @anthropic-ai/claude-code
  claude login
  ```
- **Option B**: Anthropic API key — copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`

## Quick Start

```bash
./init.sh          # Install dependencies and start all services
./init.sh status   # Check service status
./init.sh restart  # Restart all services
./init.sh stop     # Stop all services
```

## Services

| Service | Port | Purpose |
|---|---|---|
| API backend | 8000 | FastAPI + pipeline orchestration |
| Frontend | 5199 | SvelteKit UI |
| MCP server | 8001 | 13 tools for Claude Code integration |

## MCP Server

Project Synthesis exposes 13 tools via MCP, accessible directly from Claude Code when this directory is open. See [docs/MCP.md](docs/MCP.md) for the full tool reference.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on branching, code style, and the PR process.

Found a security issue? See [SECURITY.md](SECURITY.md) for responsible disclosure instructions — do not open a public issue.

## License

Copyright 2026 Project Synthesis Contributors.
Licensed under the [Apache License, Version 2.0](LICENSE).
