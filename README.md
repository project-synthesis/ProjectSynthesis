# Project Synthesis

> **This project is in its early days.** The foundation is real and working, but there's a long road ahead — and that's the exciting part.

Your prompts carry intent, but most of it gets lost in translation. Project Synthesis closes that gap. It takes a raw prompt and runs it through a multi-stage pipeline that understands what you're actually trying to accomplish, then rewrites it so the AI on the other end gets the full picture.

Link a GitHub repository and it goes deeper: it reads your codebase, maps the architecture, and bakes that context directly into the prompt. The result reads like it was written by someone who already knows your code.

Every optimization streams in real time, scores across multiple quality dimensions, and gives you a diff so you can see exactly what changed and why.

## Getting Started

```bash
cp .env.docker.example .env.docker
docker compose up --build -d
```

All secrets (including the Redis password) are auto-generated on first startup.

Open **http://localhost** — the in-app setup flow walks you through LLM provider configuration and GitHub App authentication setup (both required for full functionality). Once authenticated, linking a specific repository for codebase-aware optimization is optional.

**LLM provider options** (choose one):
- **Claude Max subscription** (recommended) — if the `claude` CLI is installed and authenticated on the host, the app detects it automatically at startup. Zero API cost, nothing to configure.
- **Anthropic API key** — enter your `sk-ant-...` key through the in-app setup flow or set `ANTHROPIC_API_KEY` in `.env.docker`.

## How It Works

Your prompt moves through five stages — **Explore, Analyze, Strategy, Optimize, Validate** — each one building on the last. Explore reads your linked repo for architectural context. Analyze classifies the task. Strategy picks the right optimization framework. Optimize rewrites the prompt. Validate scores the result and tells you if it's actually better.

There's also an [MCP server](docs/MCP.md) that exposes the full API as tools, so you can run optimizations directly from Claude Code without touching the browser.

## Where This Is Going

What exists today is a working core — a five-stage pipeline, codebase-aware context injection, real-time streaming, GitHub integration, and an MCP interface. It works, and it works well for what it does. But it's a fraction of what we have in mind.

The roadmap is wide open and growing. Some of the directions we're exploring:

- **Prompt chains and composition** — multi-step prompt workflows where one optimization feeds into the next, building compound instructions that handle complex tasks no single prompt can
- **Team workspaces** — shared optimization history, collective strategy refinement, and organizational prompt libraries that get smarter as your team uses them
- **Custom strategy authoring** — define your own optimization frameworks tuned to your domain, your codebase, your way of thinking
- **Deeper codebase understanding** — richer semantic indexing, cross-repository awareness, dependency graph analysis, and architectural pattern recognition that makes context injection even more precise
- **Quality feedback loops** — track how optimized prompts perform in practice and feed that signal back into the optimization pipeline itself
- **Plugin and extension system** — open the pipeline to community-built stages, custom validators, and domain-specific analyzers

Some of these are closer than others. Some will change shape as we learn what matters most. That's the nature of building something in the open — the path reveals itself as you walk it.

If any of this resonates with you, come build with us. The architecture is designed to grow, and there's room for ideas we haven't thought of yet.

## Current Status

Project Synthesis is under **active development**. The core pipeline is stable and functional, but you should expect rough edges, evolving APIs, and occasional breaking changes. We're iterating fast and prioritizing substance over polish.

What you can count on today:
- Five-stage optimization pipeline with real-time SSE streaming
- GitHub repository integration with semantic codebase indexing
- MCP server for CLI-native workflows
- Works with Claude Max subscription (zero API cost) or Anthropic API key
- Encrypted credential storage with in-app configuration
- Docker deployment with auto-generated secrets

What's still taking shape:
- API stability (endpoints may shift as the architecture matures)
- Documentation depth (improving steadily)
- Test coverage (515+ tests and growing, targeting 90%+)

We tag releases when meaningful milestones land. Watch the repo if you want to follow along.

---

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)
[![Python 3.14](https://img.shields.io/badge/python-3.14-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Claude](https://img.shields.io/badge/Powered_by-Claude-cc785c.svg?logo=anthropic&logoColor=white)](https://www.anthropic.com/claude)
[![MCP](https://img.shields.io/badge/MCP-enabled-6366f1.svg)](docs/MCP.md)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg?logo=docker&logoColor=white)](docker-compose.yml)

`RAG` · `prompt engineering` · `agentic AI` · `codebase-aware` · `semantic search` · `real-time streaming` · `SSE` · `vector embeddings` · `multi-stage pipeline` · `MCP server` · `Claude API` · `FastAPI` · `SvelteKit` · `Svelte 5` · `Tailwind CSS 4` · `SQLAlchemy` · `sentence-transformers` · `Redis` · `Docker Compose` · `self-hosted`

[Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Terms](docs/TERMS.md) · [Architecture](CLAUDE.md)
