# Project Synthesis MCP Server

Project Synthesis exposes 20 tools (all prefixed `synthesis_`) over the Model Context Protocol (MCP), allowing Claude Code and other MCP clients to optimize prompts, query history, manage trash/restore, interact with linked GitHub repositories, and submit feedback on optimizations directly from a chat session. Tools return structured output via Pydantic models (`outputSchema` / `structuredContent`). The explore stage uses semantic retrieval (pre-built embedding index) for fast codebase analysis.

## Transports

Three endpoints are available. Use the one that matches your client's capabilities.

| Endpoint | Transport | Process | Purpose |
|---|---|---|---|
| `http://127.0.0.1:8001/mcp` | Streamable HTTP | Standalone (port 8001) | Primary — Claude Code, MCP Inspector, external tooling |
| `http://localhost:8000/mcp` | Streamable HTTP | FastAPI (port 8000) | Same API, co-located with REST; use when only one port is exposed |
| `ws://localhost:8000/mcp/ws` | WebSocket | FastAPI (port 8000) | Backward-compat for older MCP clients |

**Streamable HTTP** is the modern MCP transport (protocol version `2024-11-05`). It is stateful: each client session begins with an `initialize` request that returns an `Mcp-Session-Id` header, which must be sent on all subsequent requests.

**WebSocket** is retained for clients that pre-date streamable HTTP support. It speaks the same JSON-RPC protocol over a persistent socket connection.

### Why two processes?

The **standalone process** (port 8001) runs independently of the FastAPI app. This means it can be connected to from Claude Code without running the full web UI. It detects the LLM provider at startup via its own lifespan.

The **FastAPI-mounted transports** (port 8000) share the same dynamic provider reference and database connection as the REST API. When an API key is configured or changed via the UI, tools pick up the new provider immediately without a restart. They are used when the full app is running and you want a single origin.

### Structured output

All tools return Pydantic models with `outputSchema` and `structuredContent`, enabling clients to parse responses programmatically without JSON-string extraction. Errors raise `ValueError`, which the MCP framework surfaces as `isError: true` responses to the client.

---

## Connecting from Claude Code

The project ships a `.mcp.json` that Claude Code reads automatically when you open this directory:

```json
{
  "mcpServers": {
    "synthesis_mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

> **Note:** The `.mcp.json` schema uses `"type"` (not `"transport"`) to specify the connection type. Valid values are `stdio`, `sse`, and `http`. `"http"` uses the modern streamable-HTTP transport (MCP protocol version `2024-11-05`).

Start the services, then verify the connection:

```bash
./init.sh          # starts backend (port 8000), frontend, and standalone MCP (port 8001)
claude mcp list    # should show "synthesis_mcp" as connected
```

To add it manually or to a different Claude Code workspace:

```bash
claude mcp add --transport http --scope project synthesis_mcp http://127.0.0.1:8001/mcp
```

### Docker deployment

When running via `docker compose`, the standalone MCP server runs in its own container. nginx exposes it on host port 8001.

The existing `.mcp.json` (`http://127.0.0.1:8001/mcp`) works without changes — Claude Code runs on the host machine and connects to port 8001, which nginx proxies to the MCP container.

| Context | URL | Notes |
|---|---|---|
| Host → Docker (Claude Code) | `http://127.0.0.1:8001/mcp` | nginx proxies to MCP container |
| Host → Docker (browser) | `http://localhost/mcp` | nginx proxies on port 80 |
| Container → Container | `http://mcp:8001/mcp` | Docker service name (internal only) |

> **Note:** The `.mcp.json` file is designed for local use. If Claude Code runs on a remote machine, replace `127.0.0.1` with the Docker host's IP or hostname.

---

## Connecting from an MCP Inspector or custom client

```bash
# Initialize a session
curl -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"my-client","version":"1.0"}},"id":1}'
# Response includes: Mcp-Session-Id: <session-id>

# Use that session ID on all subsequent requests
curl -X POST http://127.0.0.1:8001/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -H "Mcp-Session-Id: <session-id>" \
  -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":2}'
```

---

## Codebase-aware optimization (Explore stage)

The Explore stage reads a linked GitHub repository to ground the optimization in real codebase context — producing precise file paths, function signatures, and architectural patterns instead of generic advice.

### How it works

1. **Repo linking**: The user links a repo via the frontend UI (`POST /api/github/repos/link`), which stores it in the `linked_repos` table and triggers background semantic indexing.
2. **Explore gate**: The pipeline runs Explore when `repo_full_name AND (session_id OR github_token)` are both truthy.
3. **Token resolution**: Explore resolves GitHub credentials in order: explicit `github_token` → session-stored encrypted token (via `session_id`) → failure.
4. **Context injection**: Explore results (tech stack, file outlines, architectural observations) flow to all downstream stages (Strategy, Optimizer, Validator), grounding their output in the real codebase.

### MCP auto-resolution

MCP clients (Claude Code, MCP Inspector) have no browser session, so they historically lacked the `session_id` needed for token resolution. This caused the Explore stage to silently skip on every MCP call.

**Current behavior**: When `repo_full_name` is omitted from an MCP tool call, the server **auto-resolves** the most recently linked repo and its associated `session_id` from the database. This means:

- **No parameters needed**: Call `synthesis_optimize` with just `prompt` — if the user has linked a repo via the UI, Explore runs automatically.
- **Explicit override**: Pass `repo_full_name` and/or `github_token` to target a different repo or use different credentials.
- **Graceful fallback**: If no linked repo exists or token resolution fails, Explore is skipped and the pipeline continues without codebase context. A diagnostic `stage: skipped` event is emitted with the specific reason.

### When Explore is skipped

When no codebase context is available, the pipeline emits a diagnostic event:

```json
{"stage": "explore", "status": "skipped", "reason": "no repository linked"}
```

All downstream stages (Strategy, Optimizer, Validator) receive explicit "no codebase context" guardrails that prevent them from fabricating tech stacks, file paths, or framework names not present in the user's original prompt.

---

## Tool Reference

### Optimization tools

#### `synthesis_optimize`
Run the full 5-stage pipeline (Explore → Analyze → Strategy → Optimize → Validate) on a prompt.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | yes | The raw prompt to optimize |
| `project` | string | no | Group this run under a project |
| `title` | string | no | Human-readable label for this run |
| `repo_full_name` | string | no | `owner/repo` for codebase-aware Explore stage. **Auto-resolved** from the most recently linked repo when omitted. |
| `repo_branch` | string | no | Branch to explore (default: `main` or the linked repo's branch) |
| `github_token` | string | no | GitHub PAT for explicit credentials. When omitted, the server resolves stored credentials from the linked repo's session or falls back to a GitHub App installation token. |
| `strategy` | string | no | Force a specific framework: `chain-of-thought`, `constraint-injection`, `context-enrichment`, `CO-STAR`, `few-shot-scaffolding`, `persona-assignment`, `RISEN`, `role-task-format`, `step-by-step`, `structured-output` |
| `file_contexts` | list[dict] | no | File content objects to include as context (`{name, content}`) |
| `instructions` | list[string] | no | Output constraints (e.g. "always use bullet points"). These take absolute priority in the optimized prompt. |
| `url_contexts` | list[string] | no | URLs to fetch and include as context |

Returns: `PipelineResult` with `optimization_id`, `analysis`, `strategy`, `optimization`, and `validation` stage results. The `optimization.optimized_prompt` field contains the final result.

**User association**: MCP-created records are automatically associated with the most recently active frontend user, ensuring they appear in the UI's history view.

---

#### `synthesis_retry`
Re-run the pipeline on an existing optimization. Creates a new record linked via `retry_of`. Loads the original prompt and repo settings from the stored record.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to retry |
| `strategy` | string | no | Override the strategy for this run |
| `github_token` | string | no | GitHub PAT. When omitted and the original had a linked repo, credentials are auto-resolved from the stored session. |
| `file_contexts` | list[dict] | no | File content objects to include as context (`{name, content}`) |
| `instructions` | list[string] | no | Additional freeform instructions for the optimizer |
| `url_contexts` | list[string] | no | URLs to fetch and include as context |

Returns: `PipelineResult` with `optimization_id` and `retry_of` linking to the original.

---

#### `synthesis_get_optimization`
Fetch a single optimization record by ID.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |

Returns: `OptimizationRecord` with all fields including scores, prompts, stage durations, token usage, and metadata.

---

#### `synthesis_list_optimizations`
List optimizations with filtering, sorting, and pagination.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | Results per page (1–100) |
| `offset` | int | 0 | Pagination offset |
| `project` | string | — | Filter by project |
| `task_type` | string | — | Filter by task type (`coding`, `writing`, `analysis`, `general`) |
| `min_score` | float | — | Minimum `overall_score` threshold (1.0–10.0) |
| `search` | string | — | Full-text filter on prompt content and title |
| `sort` | string | `created_at` | Sort column: `created_at`, `overall_score`, `task_type`, `updated_at`, `duration_ms`, `primary_framework`, `status`, `refinement_turns`, `branch_count` |
| `order` | string | `desc` | `asc` or `desc` |

Returns a pagination envelope:
```json
{
  "total": 84,
  "count": 20,
  "offset": 0,
  "items": [...],
  "has_more": true,
  "next_offset": 20
}
```

---

#### `synthesis_search_optimizations`
Full-text search across prompt content, optimized prompts, and titles.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | string | yes | Search terms |
| `limit` | int | no (default 10) | Results per page (1–100) |
| `offset` | int | no (default 0) | Pagination offset |

Returns the same pagination envelope as `synthesis_list_optimizations`.

---

#### `synthesis_get_by_project`
Fetch all optimizations belonging to a project.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project` | string | yes | Project identifier |
| `limit` | int | no | Max results (default 50) |
| `include_prompts` | bool | no | Include prompt text in results (default: `true`) |

---

#### `synthesis_get_stats`
Return aggregate statistics: total runs, average score, task type and framework breakdowns, token usage, and cost.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project` | string | no | Scope statistics to a project |

---

#### `synthesis_tag_optimization`
Add or remove tags on an optimization. Supports optimistic locking via `expected_version`.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |
| `add_tags` | list[string] | no | Tags to add |
| `remove_tags` | list[string] | no | Tags to remove |
| `project` | string | no | Update the project label (empty string clears) |
| `title` | string | no | Update the human-readable title (empty string clears) |
| `expected_version` | int | no | Expected `row_version` for optimistic locking; rejected if mismatched |

---

#### `synthesis_delete_optimization`
Soft-delete an optimization record (sets `deleted_at`; purged permanently after 7 days). Use `synthesis_restore` to undo within the recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to delete |

---

#### `synthesis_batch_delete`
Batch soft-delete multiple optimization records. All-or-nothing semantics: if any ID is not found, none are deleted.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `ids` | list[string] | yes | UUIDs of optimizations to delete (1–50 items) |
| `user_id` | string | no | Owner filter — when set, all records must belong to this user |

Returns `{"deleted_count": N, "ids": [...]}`.

---

#### `synthesis_list_trash`
List soft-deleted optimizations still within the 7-day recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `limit` | int | no (default 20) | Max results per page (1–100) |
| `offset` | int | no (default 0) | Records to skip for pagination |

Returns a pagination envelope. Each item includes `id`, `raw_prompt`, `title`, `deleted_at`, and `created_at`.

---

#### `synthesis_restore`
Restore a soft-deleted optimization from the trash (clears `deleted_at`). Must be within the 7-day recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to restore |

Returns `{"restored": true, "id": "..."}`.

---

### GitHub tools

GitHub tools read repository content via the GitHub API. The `token` parameter accepts a GitHub Personal Access Token with `repo` scope. When omitted or empty, the server attempts to generate a GitHub App installation token automatically.

#### `synthesis_github_list_repos`
List repositories accessible to the token.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | no | GitHub PAT. Omit to use platform bot credentials. |
| `limit` | int | no (default 30) | Max repos to return (1–100) |

Returns each repo with `full_name`, `default_branch`, `language`, `private`.

---

#### `synthesis_github_read_file`
Read a file from a GitHub repository at a specific ref.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `full_name` | string | yes | `owner/repo` |
| `path` | string | yes | File path within the repo |
| `token` | string | no | GitHub PAT. Omit to use platform bot credentials. |
| `branch` | string | no | Branch, tag, or commit SHA (default: repo default branch) |

---

#### `synthesis_github_search_code`
Search for a pattern within a repository using the GitHub code search API.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `full_name` | string | yes | `owner/repo` |
| `pattern` | string | yes | Search query (GitHub code search syntax) |
| `token` | string | no | GitHub PAT. Omit to use platform bot credentials. |
| `extension` | string | no | Restrict results to files with this extension (e.g. `py`) |

Returns up to 20 matches with `path` and `name`.

---

### Feedback and refinement tools

#### `synthesis_submit_feedback`
Submit quality feedback (thumbs up/down + dimension overrides) on an optimization. Triggers background adaptation recomputation that tunes pipeline parameters (dimension weights, retry threshold, strategy affinities) for the user.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |
| `rating` | int | yes | Feedback rating: `-1` (negative), `0` (neutral), `1` (positive) |
| `dimension_overrides` | dict | no | Per-dimension score overrides (1–10), e.g. `{"clarity_score": 8, "specificity_score": 7}`. Valid dimensions: `clarity_score`, `specificity_score`, `structure_score`, `faithfulness_score`, `conciseness_score`. |
| `comment` | string | no | Free-text feedback comment (max 2000 chars) |
| `corrected_issues` | list[string] | no | Issue categories observed in the output (max 50). Valid values: `lost_constraints`, `added_hallucinations`, `changed_intent`, `wrong_audience`, `verbosity`, `vague_instructions`, `poor_structure`, `weak_examples`. Fidelity group: `lost_constraints`, `added_hallucinations`, `changed_intent`, `wrong_audience`. Quality group: `verbosity`, `vague_instructions`, `poor_structure`, `weak_examples`. |

One feedback per optimization per user (upsert semantics). Adaptation starts from the first feedback with progressive damping.

---

#### `synthesis_get_branches`
List all refinement branches for an optimization. Refinement branches are created via the REST API's `/refine` endpoint (SSE streaming); this tool is a read-only viewer.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |

Returns `{"branches": [...], "total": N}` where each branch includes `id`, `label`, `status`, `turn_count`, `scores`, and `optimized_prompt`.

---

#### `synthesis_get_adaptation_state`
Retrieve the current learned adaptation state for a user (dimension weights, retry threshold, strategy affinities). These parameters tune the pipeline's behavior based on accumulated feedback.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | string | yes | User identifier |

Returns the adaptation state object with `dimension_weights`, `strategy_affinities`, `retry_threshold`, and `feedback_count`.

---

#### `synthesis_get_framework_performance`
Retrieve per-framework performance data for a specific task type. Returns composite scores (quality × satisfaction × recency decay), attempt counts, and trend indicators for each framework the user has used.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `task_type` | string | yes | Task type to query (e.g. `coding`, `writing`, `analysis`, `general`) |
| `user_id` | string | no | User identifier. When omitted, auto-resolves the most recently active user. |

Returns a list of framework performance entries with `framework`, `composite_score`, `attempt_count`, `avg_scores`, and `trend`.

Annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.

---

#### `synthesis_get_adaptation_summary`
Retrieve a human-readable adaptation dashboard for a user. Includes priority dimensions, active issue guardrails, framework preferences, issue resolution tracking, and recent adaptation events.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | string | no | User identifier. When omitted, auto-resolves the most recently active user. |

Returns a summary object with `priorities`, `active_guardrails`, `framework_preferences`, `issue_tracking`, and `recent_events`.

Annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`.

---

## Tool annotations

Every tool is decorated with `ToolAnnotations(title=...)` to provide a human-readable display name and help clients understand side effects:

| Annotation | Meaning |
|---|---|
| `readOnlyHint: true` | Tool only reads data, no mutations |
| `readOnlyHint: false` | Tool may write or mutate state |
| `destructiveHint: true` | Mutation cannot be undone (delete) |
| `idempotentHint: true` | Safe to call multiple times with same args |
| `openWorldHint: true` | Makes calls to external services (GitHub API, Claude) |

---

## Provider detection

The MCP server resolves the LLM provider in order of preference:

1. **Claude CLI** (`claude` on PATH with Max subscription) — zero API cost
2. **Anthropic API** (`ANTHROPIC_API_KEY` env var or in-app key) — pay-per-token

**Standalone mode** (port 8001): detects the provider once at startup via the FastMCP lifespan. Restart to pick up new keys.

**FastAPI-mounted** (port 8000): uses a dynamic provider getter that resolves to `app.state.provider` on each tool call. API keys configured via the UI take effect immediately — no restart needed.

If no provider is available, `synthesis_optimize` and `synthesis_retry` return a JSON error with a configuration hint instead of crashing.

> **Note:** When running inside a Claude Code session (`CLAUDECODE=1` env var), the CLI provider probe is skipped to prevent nested session crashes. Start the MCP server with `env -u CLAUDECODE python -m app.mcp_server` to use the CLI provider from within Claude Code.

---

## User association

MCP tools have no authentication layer (the server is localhost-only). To ensure MCP-created records appear in the frontend's history view:

- **Optimization records**: `synthesis_optimize` and `synthesis_retry` auto-resolve the most recently active user from the database and set `user_id` on the record.
- **Feedback records**: `synthesis_submit_feedback` associates feedback with the resolved user, ensuring adaptation weights are computed for the correct user.

This means MCP-created optimizations appear alongside UI-created ones in the frontend history without manual user ID management.

---

## Error responses

All tools return actionable error messages as JSON strings. Common cases:

- **No LLM provider**: `synthesis_optimize` and `synthesis_retry` return a configuration hint when no API key is set
- **Optimization not found**: includes the ID that was looked up and a suggestion to call `synthesis_list_optimizations`
- **GitHub API error**: includes HTTP status, context description, and a specific hint per status code (401: check token, 403: check permissions, 404: verify repo/path, 429: rate limit)
- **Explore skipped**: emitted as a `stage` event with `status: "skipped"` and a `reason` field explaining why (no repository linked, no GitHub credentials, token resolution failed)
- **Version conflict**: `synthesis_tag_optimization` with `expected_version` returns the current version for retry
