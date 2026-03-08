# PromptForge MCP Server

PromptForge exposes 14 tools over the Model Context Protocol (MCP), allowing Claude Code and other MCP clients to optimize prompts, query history, and interact with linked GitHub repositories directly from a chat session.

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

The **FastAPI-mounted transports** (port 8000) share the same provider instance and database connection as the REST API. They are used when the full app is running and you want a single origin.

---

## Connecting from Claude Code

The project ships a `.mcp.json` that Claude Code reads automatically when you open this directory:

```json
{
  "mcpServers": {
    "promptforge": {
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
claude mcp list    # should show "promptforge" as connected
```

To add it manually or to a different Claude Code workspace:

```bash
claude mcp add --transport http --scope project promptforge http://127.0.0.1:8001/mcp
```

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

#### `optimize`
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

#### `get_optimization`
Fetch a single optimization record by ID.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |

---

#### `list_optimizations`
List optimizations with pagination.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `limit` | int | 20 | Results per page (1–100) |
| `offset` | int | 0 | Pagination offset |
| `project` | string | — | Filter by project |
| `task_type` | string | — | Filter by task type |
| `min_score` | int | — | Minimum `overall_score` threshold |
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

#### `search_optimizations`
Full-text search across prompt content and metadata.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `query` | string | yes | Search terms |
| `limit` | int | no (default 10) | Results per page (1–100) |
| `offset` | int | no (default 0) | Pagination offset |

Returns the same pagination envelope as `list_optimizations`.

---

#### `get_by_project`
Fetch all optimizations belonging to a project.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project` | string | yes | Project identifier |
| `limit` | int | no | Max results (default 50) |
| `include_prompts` | bool | no | Include prompt text in results (default: `true`) |

---

#### `get_stats`
Return aggregate statistics: total runs, average score, and task type breakdown.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `project` | string | no | Scope statistics to a project |

---

#### `tag_optimization`
Add or remove tags on an optimization. Tags are deduplicated and insertion order is preserved.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization |
| `add_tags` | list[string] | no | Tags to add |
| `remove_tags` | list[string] | no | Tags to remove |
| `project` | string | no | Update the project label |
| `title` | string | no | Update the human-readable title |

---

#### `delete_optimization`
Permanently delete an optimization record.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `optimization_id` | string | yes | UUID of the optimization to delete |

---

#### `retry_optimization`
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

#### `github_validate_token`
Check whether a token is valid and return the authenticated user's login and scopes.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |

---

#### `github_list_repos`
List repositories accessible to the token.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |
| `limit` | int | no (default 30) | Max repos to return (1–100) |

Returns each repo with `full_name`, `default_branch`, `language`, `private`.

---

#### `github_read_file`
Read a file from a GitHub repository at a specific ref.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |
| `repo_full_name` | string | yes | `owner/repo` |
| `path` | string | yes | File path within the repo |
| `branch` | string | no | Branch, tag, or commit SHA (default: repo default branch) |

---

#### `github_search_code`
Search for a pattern within a repository using the GitHub code search API.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT |
| `repo_full_name` | string | yes | `owner/repo` |
| `pattern` | string | yes | Search query (GitHub code search syntax) |
| `extension` | string | no | Restrict results to files with this extension (e.g. `py`) |

Returns up to 20 matches with `path` and `name`.

---

#### `github_set_token`
Validate and store a GitHub Personal Access Token for reuse. The token is encrypted at rest. Once stored, other GitHub tools can accept an empty string as `token` and will fall back to the stored value.

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | yes | GitHub PAT to validate and store |

---

## Tool annotations

Every tool is decorated with MCP tool annotations to help clients understand side effects:

| Annotation | Meaning |
|---|---|
| `readOnlyHint: true` | Tool only reads data, no mutations |
| `readOnlyHint: false` | Tool may write or mutate state |
| `destructiveHint: true` | Mutation cannot be undone (delete) |
| `idempotentHint: true` | Safe to call multiple times with same args |
| `openWorldHint: true` | Makes calls to external services (GitHub API, Claude) |

---

## Provider detection

The MCP server detects the LLM provider once at startup, in order of preference:

1. **Claude CLI** (`claude` on PATH with Max subscription) — zero API cost
2. **Anthropic API** (`ANTHROPIC_API_KEY` env var) — pay-per-token

The detected provider is injected into all tool calls via the FastMCP lifespan context. Tools never call detect_provider() on each invocation.

---

## Error responses

All tools return actionable error messages as JSON strings. Common cases:

- **Optimization not found**: includes the ID that was looked up and a suggestion to call `list_optimizations`
- **GitHub API error**: includes HTTP status, response body, and a suggestion to validate the token with `github_validate_token`
- **Missing token/session**: raised at the explore stage if a repo is linked but no GitHub token is available
