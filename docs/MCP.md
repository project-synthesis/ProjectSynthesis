# Project Synthesis MCP Server

Project Synthesis exposes 18 tools (all prefixed `synthesis_`) over the Model Context Protocol (MCP), allowing Claude Code and other MCP clients to optimize prompts, query history, manage trash/restore, interact with linked GitHub repositories, and submit feedback on optimizations directly from a chat session. Tools return structured output via Pydantic models (`outputSchema` / `structuredContent`). The explore stage uses semantic retrieval (pre-built embedding index) for fast codebase analysis.

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

## Tool Reference

### Optimization tools

#### `synthesis_optimize`
Run the full 5-stage pipeline (Explore → Analyze → Strategy → Optimize → Validate) on a prompt.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `prompt` | string | yes | The raw prompt to optimize |
| `project` | string | no | Group this run under a project |
| `title` | string | no | Human-readable label for this run |
| `repo_full_name` | string | no | `owner/repo` to enable codebase-aware Explore stage |
| `repo_branch` | string | no | Branch to explore (default: `main`) |
| `github_token` | string | no | GitHub PAT; required when `repo_full_name` is set |
| `strategy` | string | no | Force a specific strategy instead of auto-selecting |
| `file_contexts` | list[dict] | no | File content objects to include as context (`{name, content}`) |
| `instructions` | list[string] | no | Additional freeform instructions for the optimizer |
| `url_contexts` | list[string] | no | URLs to fetch and include as context |

Returns: optimization record with `id`, `status`, `optimized_prompt`, `overall_score`, and stage results.

---

#### `synthesis_get_optimization`
Fetch a single optimization record by ID.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |

---

#### `synthesis_list_optimizations`
List optimizations with pagination.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | Results per page (1–100) |
| `offset` | int | 0 | Pagination offset |
| `project` | string | — | Filter by project |
| `task_type` | string | — | Filter by task type |
| `min_score` | float | — | Minimum `overall_score` threshold (1.0–10.0) |
| `search` | string | — | Full-text filter on prompt content |
| `sort` | string | `created_at` | Sort column: `created_at`, `overall_score`, `task_type`, `updated_at`, `duration_ms`, `primary_framework`, `status` |
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
Full-text search across prompt content and metadata.

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
Return aggregate statistics: total runs, average score, and task type breakdown.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project` | string | no | Scope statistics to a project |

---

#### `synthesis_tag_optimization`
Add or remove tags on an optimization. Tags are deduplicated and insertion order is preserved.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |
| `add_tags` | list[string] | no | Tags to add |
| `remove_tags` | list[string] | no | Tags to remove |
| `project` | string | no | Update the project label |
| `title` | string | no | Update the human-readable title |

---

#### `synthesis_delete_optimization`
Soft-delete an optimization record (sets `deleted_at`; purged permanently after 7 days). Use `synthesis_restore` to undo within the recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to delete |

---

#### `synthesis_batch_delete`
Batch soft-delete multiple optimization records (sets `deleted_at`; purged permanently after 7 days). All-or-nothing semantics: if any ID is not found, none are deleted. Use `synthesis_list_trash` + `synthesis_restore` to undo within the recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `ids` | list[string] | yes | UUIDs of optimizations to delete (1–50 items). Use `synthesis_list_optimizations` to discover valid IDs. |
| `user_id` | string | no | Owner filter — when set, all records must belong to this user. Omit for unscoped access (single-user/localhost mode). |

Returns `{"deleted_count": N, "ids": [...]}` on success, or `{"error": "..."}` on validation failure.

---

#### `synthesis_list_trash`
List soft-deleted optimizations still within the 7-day recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `limit` | int | no (default 20) | Max results per page (1–100) |
| `offset` | int | no (default 0) | Records to skip for pagination |

Returns a pagination envelope `{total, count, offset, items, has_more, next_offset}`. Each item includes `id`, `raw_prompt`, `title`, `deleted_at`, and `created_at`.

---

#### `synthesis_restore`
Restore a soft-deleted optimization from the trash (clears `deleted_at`). The record must still be within the 7-day recovery window.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to restore (use `synthesis_list_trash` to discover valid IDs) |

Returns `{"restored": true, "id": "..."}` on success, or `{"error": "..."}` if not found or window expired.

---

#### `synthesis_retry`
Re-run the pipeline on an existing optimization (useful after a failure or provider change).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to retry |
| `strategy` | string | no | Override the strategy for this run |
| `github_token` | string | no | GitHub PAT if the original run used a linked repo |
| `file_contexts` | list[dict] | no | File content objects to include as context (`{name, content}`) |
| `instructions` | list[string] | no | Additional freeform instructions for the optimizer |
| `url_contexts` | list[string] | no | URLs to fetch and include as context |

---

### GitHub tools

All GitHub tools require an explicit `token` parameter (a GitHub Personal Access Token with `repo` scope). No session-level state is shared between calls.

#### `synthesis_github_list_repos`
List repositories accessible to the token.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |
| `limit` | int | no (default 30) | Max repos to return (1–100) |

Returns each repo with `full_name`, `default_branch`, `language`, `private`.

---

#### `synthesis_github_read_file`
Read a file from a GitHub repository at a specific ref.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |
| `repo_full_name` | string | yes | `owner/repo` |
| `path` | string | yes | File path within the repo |
| `branch` | string | no | Branch, tag, or commit SHA (default: repo default branch) |

---

#### `synthesis_github_search_code`
Search for a pattern within a repository using the GitHub code search API.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |
| `repo_full_name` | string | yes | `owner/repo` |
| `pattern` | string | yes | Search query (GitHub code search syntax) |
| `extension` | string | no | Restrict results to files with this extension (e.g. `py`) |

Returns up to 20 matches with `path` and `name`.

---

### Feedback and refinement tools

#### `synthesis_submit_feedback`
Submit quality feedback (thumbs up/down + dimension overrides) on an optimization. Triggers background adaptation recomputation.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |
| `rating` | int | yes | Feedback rating: `-1` (negative), `0` (neutral), `1` (positive) |
| `dimension_overrides` | dict | no | Per-dimension score overrides, e.g. `{"clarity_score": 8, "specificity_score": 7}` |
| `comment` | string | no | Free-text feedback comment |

Returns the upserted feedback record as JSON. One feedback per optimization per user (upsert semantics).

Annotations: `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false`

---

#### `synthesis_get_branches`
List all refinement branches for an optimization.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |

Returns `{"branches": [...], "total": N}` where each branch includes `id`, `label`, `status`, `turn_count`, `scores`, and `optimized_prompt`.

Annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false`

---

#### `synthesis_get_adaptation_state`
Retrieve the current learned adaptation state for a user (dimension weights, retry threshold, strategy affinities).

| Parameter | Type | Required | Description |
|---|---|---|---|
| `user_id` | string | yes | User identifier |

Returns the adaptation state object or `{"error": "No adaptation found"}` if the user has insufficient feedback history.

Annotations: `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false`

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

---

## Error responses

All tools return actionable error messages as JSON strings. Common cases:

- **No LLM provider**: `synthesis_optimize` and `synthesis_retry` return `{"error": "No LLM provider configured", "hint": "..."}` when no API key is set
- **Optimization not found**: includes the ID that was looked up and a suggestion to call `synthesis_list_optimizations`
- **GitHub API error**: includes HTTP status and response body
- **Missing token/session**: raised at the explore stage if a repo is linked but no GitHub token is available
