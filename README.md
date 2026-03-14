<h1 align="center">🧬 Project Synthesis</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/Apache_2.0-blue.svg?logo=apache&logoColor=white" alt="License"></a>
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/PRs_Welcome-brightgreen.svg?logo=git&logoColor=white" alt="PRs Welcome"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python_3.14-3776AB.svg?logo=python&logoColor=white" alt="Python"></a>
  <a href="https://www.anthropic.com/claude"><img src="https://img.shields.io/badge/Powered_by_Claude-cc785c.svg?logo=anthropic&logoColor=white" alt="Claude"></a>
  <a href="docs/MCP.md"><img src="https://img.shields.io/badge/MCP_Enabled-6366f1.svg?logo=anthropic&logoColor=white" alt="MCP"></a>
  <a href="docker-compose.yml"><img src="https://img.shields.io/badge/Docker_Ready-2496ED.svg?logo=docker&logoColor=white" alt="Docker"></a>
  <br>
  <img src="https://img.shields.io/badge/FastAPI-009688.svg?logo=fastapi&logoColor=white" alt="FastAPI">
  <img src="https://img.shields.io/badge/SvelteKit-FF3E00.svg?logo=svelte&logoColor=white" alt="SvelteKit">
  <img src="https://img.shields.io/badge/Tailwind_CSS_4-06B6D4.svg?logo=tailwindcss&logoColor=white" alt="Tailwind CSS">
  <img src="https://img.shields.io/badge/Redis-FF4438.svg?logo=redis&logoColor=white" alt="Redis">
  <img src="https://img.shields.io/badge/SQLAlchemy-D71F00.svg?logo=sqlalchemy&logoColor=white" alt="SQLAlchemy">
  <img src="https://img.shields.io/badge/sentence--transformers-EE4C2C.svg?logo=pytorch&logoColor=white" alt="sentence-transformers">
</p>

> **This project is in its early days.** The foundation is real and working, but there's a long road ahead — and that's the exciting part.

Your prompts carry intent, but most of it gets lost in translation. Project Synthesis closes that gap. It takes a raw prompt and runs it through a multi-stage pipeline that understands what you're actually trying to accomplish, then rewrites it so the AI on the other end gets the full picture.

Link a GitHub repository and it goes deeper: it reads your codebase, maps the architecture, and bakes that context directly into the prompt. The result reads like it was written by someone who already knows your code.

Everything happens inside a data-dense, VS Code-style IDE — prompts stream in real time, scores render across multiple quality dimensions, and diffs show exactly what changed and why. The interface is built for the same people who live in terminals and code editors: compact, keyboard-driven, and information-rich without getting in the way.

## 🚀 Getting Started

```bash
cp .env.docker.example .env.docker
docker compose up --build -d
```

All secrets (including the Redis password) are auto-generated on first startup.

Open **http://localhost** — the in-app setup flow walks you through LLM provider configuration and GitHub App authentication setup (both required for full functionality). Once authenticated, linking a specific repository for codebase-aware optimization is optional.

**LLM provider options** (choose one):
- **Claude Max subscription** (recommended) — if the `claude` CLI is installed and authenticated on the host, the app detects it automatically at startup. Zero API cost, nothing to configure.
- **Anthropic API key** — enter your `sk-ant-...` key through the in-app setup flow or set `ANTHROPIC_API_KEY` in `.env.docker`.

## ⚙️ How It Works

Your prompt moves through five stages — **Explore, Analyze, Strategy, Optimize, Validate** — each one building on the last. Explore reads your linked repo for architectural context. Analyze classifies the task. Strategy picks the right optimization framework. Optimize rewrites the prompt. Validate scores the result and tells you if it's actually better.

There's also an [MCP server](docs/MCP.md) that exposes the full API as tools, so you can run optimizations directly from Claude Code without touching the browser.

## 🗺️ Where This Is Going

What exists today is a working core — a five-stage pipeline, codebase-aware context injection, real-time streaming, GitHub integration, and an MCP interface. It works, and it works well for what it does. But it's a fraction of what we have in mind.

The roadmap is wide open and growing. Some of the directions we're exploring:

- **Prompt chains and composition** — multi-step prompt workflows where one optimization feeds into the next, building compound instructions that handle complex tasks no single prompt can
- **Team workspaces** — shared optimization history, collective strategy refinement, and organizational prompt libraries that get smarter as your team uses them
- **Custom strategy authoring** — define your own optimization frameworks tuned to your domain, your codebase, your way of thinking
- **Deeper codebase understanding** — richer semantic indexing, cross-repository awareness, dependency graph analysis, and architectural pattern recognition that makes context injection even more precise
- **Plugin and extension system** — open the pipeline to community-built stages, custom validators, and domain-specific analyzers

Some of these are closer than others. Some will change shape as we learn what matters most. That's the nature of building something in the open — the path reveals itself as you walk it.

If any of this resonates with you, come build with us. The architecture is designed to grow, and there's room for ideas we haven't thought of yet.

## 📊 Current Status

Project Synthesis is under **active development**. The core pipeline is stable and functional, but you should expect rough edges, evolving APIs, and occasional breaking changes. We're iterating fast and prioritizing substance over polish.

What you can count on today:
- ✅ Five-stage optimization pipeline with real-time SSE streaming
- ✅ Adaptive feedback loops — your ratings tune pipeline weights, strategy selection, and retry thresholds
- ✅ Result intelligence — verdict, dimension insights, trade-offs, and next actions for every optimization
- ✅ GitHub repository integration with semantic codebase indexing
- ✅ 20-tool MCP server for CLI-native workflows
- ✅ Works with Claude Max subscription (zero API cost) or Anthropic API key
- ✅ Encrypted credential storage with in-app configuration
- ✅ Docker deployment with auto-generated secrets

What's still taking shape:
- 🔧 API stability (endpoints may shift as the architecture matures)
- 📖 Documentation depth (improving steadily)
- 🧪 Test coverage (885+ tests and growing, targeting 90%+)

We tag releases when meaningful milestones land. Watch the repo if you want to follow along.

---

<p align="center">
  <a href="CHANGELOG.md">Changelog</a> · <a href="CONTRIBUTING.md">Contributing</a> · <a href="SECURITY.md">Security</a> · <a href="docs/TERMS.md">Terms</a> · <a href="CLAUDE.md">Architecture</a>
</p>
