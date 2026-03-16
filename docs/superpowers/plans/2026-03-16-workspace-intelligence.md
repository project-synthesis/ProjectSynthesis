# Workspace Intelligence — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Zero-config workspace awareness — when an MCP client connects, auto-discover project type, tech stack, and guidance files from workspace roots, cache the result, and inject it into every optimization. Also accept IDE-provided codebase context in `save_result`.

**Architecture:** New `WorkspaceIntelligence` service scans workspace roots for: (1) guidance files via existing `RootsScanner`, (2) project manifest files (package.json, requirements.txt, Cargo.toml, etc.) to detect tech stack, (3) builds a compact "project fingerprint" that's cached per-session. MCP tools use `Context.session.list_roots()` to auto-discover roots. The `synthesis_save_result` tool gains a `codebase_context` field for IDE-provided context.

**Tech Stack:** Python 3.14, mcp SDK (FastMCP Context), existing RootsScanner

---

## Design

### What workspace intelligence produces

A `WorkspaceProfile` — a compact text block injected as `codebase_guidance`:

```
<workspace-profile>
Project: FastAPI + SvelteKit application
Languages: Python 3.14, TypeScript
Backend: FastAPI, SQLAlchemy, aiosqlite
Frontend: SvelteKit 2, Svelte 5, Tailwind CSS 4
Testing: pytest (async), svelte-check
Package manager: pip (requirements.txt), npm

<guidance-files>
<untrusted-context source="CLAUDE.md">
... content ...
</untrusted-context>
</guidance-files>
</workspace-profile>
```

This is MORE useful than raw source code because it tells the optimizer:
- What language/framework constraints to apply
- What patterns are expected (async, runes, etc.)
- What conventions the project follows (from guidance files)

### How roots/list works

1. MCP tool receives `Context` parameter (auto-injected by FastMCP)
2. Tool calls `ctx.session.list_roots()` — returns list of `Root(uri="file:///path", name="project")`
3. We scan each root directory
4. Cache the result keyed by frozenset of root paths (invalidate on new session)

### Fallback chain

```
roots/list available → auto-scan all roots
    ↓ (not available)
workspace_path parameter → scan that path
    ↓ (not provided)
No workspace context (still works, just less informed)
```

---

## File Structure

### Create

| File | Responsibility |
|------|---------------|
| `backend/app/services/workspace_intelligence.py` | Scan roots, detect project type, build WorkspaceProfile |
| `backend/tests/test_workspace_intelligence.py` | Tests |

### Modify

| File | Changes |
|------|---------|
| `backend/app/mcp_server.py` | Add `Context` param to tools, auto-scan roots, accept `codebase_context` in save_result |
| `backend/app/services/roots_scanner.py` | Add manifest file detection |
| `backend/tests/test_mcp_tools.py` | Update tests |

---

## Chunk 1: WorkspaceIntelligence Service

### Task 1: WorkspaceIntelligence

**Files:**
- Create: `backend/app/services/workspace_intelligence.py`
- Create: `backend/tests/test_workspace_intelligence.py`

The service:
1. Takes a list of root paths
2. Scans each for guidance files (via RootsScanner)
3. Scans each for manifest files to detect tech stack
4. Returns a formatted `WorkspaceProfile` string
5. Caches the result by root paths (simple dict cache)

**Manifest detection:**

| File | Detects |
|------|---------|
| `package.json` | Node.js project — reads `dependencies` for framework (react, svelte, next, vue, etc.) |
| `requirements.txt` | Python project — reads for key packages (fastapi, django, flask, etc.) |
| `pyproject.toml` | Python project — reads `[tool.ruff]` target-version, `[project]` dependencies |
| `Cargo.toml` | Rust project |
| `go.mod` | Go project |
| `pom.xml` / `build.gradle` | Java project |
| `Gemfile` | Ruby project |
| `tsconfig.json` | TypeScript project |
| `docker-compose.yml` | Containerized deployment |

**Implementation:**

```python
class WorkspaceIntelligence:
    def __init__(self) -> None:
        self._cache: dict[frozenset[str], str] = {}
        self._scanner = RootsScanner()

    def analyze(self, roots: list[Path]) -> str | None:
        cache_key = frozenset(str(r) for r in roots)
        if cache_key in self._cache:
            return self._cache[cache_key]

        guidance = self._scanner.scan_roots(roots)
        stack = self._detect_stack(roots)
        profile = self._build_profile(stack, guidance)

        if profile:
            self._cache[cache_key] = profile
        return profile

    def _detect_stack(self, roots: list[Path]) -> dict:
        """Scan manifest files to detect project type and tech stack."""
        ...

    def _build_profile(self, stack: dict, guidance: str | None) -> str | None:
        """Format as a compact <workspace-profile> block."""
        ...
```

**Tests (6):**
1. `test_detect_python_project` — has requirements.txt with fastapi
2. `test_detect_node_project` — has package.json with svelte
3. `test_detect_multi_stack` — has both Python + Node
4. `test_includes_guidance_files` — CLAUDE.md content included
5. `test_caches_by_roots` — second call returns cached result
6. `test_empty_roots` — returns None

- [ ] **Steps: TDD cycle → commit**

```bash
git add backend/app/services/workspace_intelligence.py backend/tests/test_workspace_intelligence.py
git commit -m "feat: implement WorkspaceIntelligence with project detection and caching"
```

---

## Chunk 2: MCP Integration

### Task 2: Wire roots/list into MCP tools + add codebase_context to save_result

**Files:**
- Modify: `backend/app/mcp_server.py`
- Modify: `backend/tests/test_mcp_tools.py`

**Changes to `synthesis_optimize`:**

```python
from mcp.server.fastmcp import Context

@mcp.tool()
async def synthesis_optimize(
    prompt: str,
    strategy: str | None = None,
    repo_full_name: str | None = None,
    workspace_path: str | None = None,
    ctx: Context = None,  # Auto-injected by FastMCP
) -> dict:
    # Auto-discover workspace roots (zero-config)
    guidance = await _resolve_workspace_guidance(ctx, workspace_path)
    ...
```

**Changes to `synthesis_prepare_optimization`:**

Same pattern — use `ctx` to auto-discover roots.

**Changes to `synthesis_save_result`:**

Add `codebase_context: str | None = None` parameter:
```python
@mcp.tool()
async def synthesis_save_result(
    trace_id: str,
    optimized_prompt: str,
    ...
    codebase_context: str | None = None,  # NEW: IDE-provided context
) -> dict:
    # Store codebase_context on the optimization record
    if opt and codebase_context:
        opt.codebase_context_snapshot = codebase_context[:settings.MAX_CODEBASE_CONTEXT_CHARS]
```

**Shared helper:**

```python
_workspace_intel = WorkspaceIntelligence()

async def _resolve_workspace_guidance(ctx: Context | None, workspace_path: str | None) -> str | None:
    """Resolve workspace guidance: try roots/list first, fall back to workspace_path."""
    roots = []

    # Try MCP roots/list (zero-config)
    if ctx:
        try:
            roots_result = await ctx.session.list_roots()
            for root in roots_result.roots:
                if root.uri.startswith("file://"):
                    roots.append(Path(root.uri.removeprefix("file://")))
        except Exception:
            pass  # Client doesn't support roots

    # Fallback: explicit workspace_path
    if not roots and workspace_path:
        roots = [Path(workspace_path)]

    if not roots:
        return None

    return _workspace_intel.analyze(roots)
```

**Tests:**
1. `test_optimize_with_roots_context` — mock ctx.session.list_roots, verify guidance injected
2. `test_prepare_with_roots_context` — same
3. `test_save_result_stores_codebase_context` — verify field persisted
4. `test_fallback_to_workspace_path` — no roots → uses workspace_path

- [ ] **Steps: implement → test → commit**

```bash
git add backend/app/mcp_server.py backend/tests/test_mcp_tools.py
git commit -m "feat: wire roots/list auto-scanning and codebase_context into MCP tools"
```

---

## Exit Conditions

1. WorkspaceIntelligence detects Python and Node project types from manifest files
2. Guidance files (CLAUDE.md etc.) included in workspace profile
3. MCP tools auto-call `list_roots()` when Context is available
4. Fallback to `workspace_path` when roots not available
5. `synthesis_save_result` accepts and persists `codebase_context`
6. Results cached per-session by root paths
7. All tests pass
