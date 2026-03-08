# PromptForge — Intelligent Prompt Optimization Engine

AI-powered prompt optimization engine that transforms raw prompts into structured, high-quality versions through a 5-stage pipeline: Explore → Analyze → Strategy → Optimize → Validate.

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

## MCP Server

PromptForge exposes 13 tools via MCP, accessible directly from Claude Code when this directory is open. See [docs/MCP.md](docs/MCP.md) for the full tool reference.
