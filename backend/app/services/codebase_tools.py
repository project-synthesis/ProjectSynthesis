"""Codebase exploration tools for Stage 0.

Defines 6 ToolDefinition objects that the agentic explore stage can call.
Each tool fetches data from the linked GitHub repository.
"""

import json
import logging
import re
import time
from typing import Optional

from app.providers.base import ToolDefinition
from app.services.github_service import get_repo_tree as _svc_get_repo_tree
from app.services.github_service import read_file_content as _svc_read_file_content

logger = logging.getLogger(__name__)

# ── Module-level tree cache ──────────────────────────────────────────────────
# Keyed by (repo_full_name, branch) → (tree_entries, timestamp).
# TTL of 5 minutes prevents stale data while avoiding duplicate GitHub fetches
# when the same repo/branch is explored concurrently.
#
# Note: build_codebase_tools() is a sync function so an asyncio.Lock is not
# used here. Two simultaneous first-fetches may race, but the result is
# idempotent — the last writer wins and all entries are valid.
_TREE_CACHE: dict[tuple[str, str], tuple[list[dict], float]] = {}
_TREE_CACHE_TTL: int = 300  # 5 minutes


def _cache_get(repo_full_name: str, branch: str) -> Optional[list[dict]]:
    """Return cached tree entries if present and not expired, else None."""
    key = (repo_full_name, branch)
    entry = _TREE_CACHE.get(key)
    if entry is None:
        return None
    tree_entries, ts = entry
    if time.monotonic() - ts > _TREE_CACHE_TTL:
        del _TREE_CACHE[key]
        return None
    return tree_entries


def _cache_set(repo_full_name: str, branch: str, tree_entries: list[dict]) -> None:
    """Store tree entries in the cache with the current timestamp."""
    _TREE_CACHE[(repo_full_name, branch)] = (tree_entries, time.monotonic())


def get_cached_tree_size(repo_full_name: str, branch: str) -> int:
    """Return the number of tree entries currently cached for this repo/branch.

    Returns 0 if the tree has not been fetched yet or the cache has expired.
    Used by run_explore() to compute coverage_pct after the agentic loop.
    """
    cached = _cache_get(repo_full_name, branch)
    return len(cached) if cached is not None else 0


# Regex for get_file_outline: matches top-level function/class/interface definitions
OUTLINE_PATTERNS = re.compile(
    r'^(\s*)'
    r'(def |async def |class |function |export function |export default function |export class |export interface |export type |interface |type |const .+ = \(|module\.exports|fn |pub fn |pub struct |pub enum |pub trait |impl )',
    re.MULTILINE,
)

# Priority tiers for search_code file ordering
_TIER1_PREFIXES = ("src/", "lib/", "app/", "backend/", "server/", "core/", "pkg/")
_TIER3_PREFIXES = (
    "test/", "tests/", "spec/", "__tests__/",
    "docs/", "doc/", "examples/", "example/",
)
_TIER3_PATTERNS = re.compile(r'(^test_|_test\.|\.spec\.|\.test\.)', re.IGNORECASE)


def _search_priority(entry: dict) -> int:
    """Return sort key (lower = higher priority) for search_code file ordering."""
    path = entry["path"]
    name = path.split("/")[-1]
    # Tier 1: core source directories
    if any(path.startswith(p) for p in _TIER1_PREFIXES):
        return 1
    # Tier 4: hidden directories
    first_segment = path.split("/")[0]
    if first_segment.startswith("."):
        return 4
    # Tier 3: test/spec paths
    if any(path.startswith(p) for p in _TIER3_PREFIXES):
        return 3
    if _TIER3_PATTERNS.search(name):
        return 3
    # Tier 2: everything else
    return 2


def build_codebase_tools(
    token: str,
    repo_full_name: str,
    repo_branch: str,
) -> list[ToolDefinition]:
    """Build the 6 codebase exploration tool definitions.

    Each tool handler is an async function that fetches data from GitHub.
    """

    async def _get_tree() -> list[dict]:
        """Return the repo file tree, using the module-level TTL cache."""
        cached = _cache_get(repo_full_name, repo_branch)
        if cached is not None:
            return cached
        try:
            # github_service already filters excluded files and applies size limits
            tree = await _svc_get_repo_tree(token, repo_full_name, repo_branch)
        except Exception as e:
            logger.error(f"Failed to get repo tree: {e}")
            tree = []
        _cache_set(repo_full_name, repo_branch, tree)
        return tree

    # ---- Tool 1: list_repo_files ----
    async def list_repo_files_handler(args: dict) -> str:
        path_prefix = args.get("path_prefix", "")
        max_results = args.get("max_results", 200)

        tree = await _get_tree()
        filtered = tree
        if path_prefix:
            filtered = [e for e in tree if e["path"].startswith(path_prefix)]

        entries = filtered[:max_results]
        output = [{"path": e["path"], "size_bytes": e.get("size_bytes", 0)} for e in entries]
        return json.dumps(output, indent=2)

    list_repo_files = ToolDefinition(
        name="list_repo_files",
        description=(
            "List all files in the linked repository. Returns the complete file tree "
            "with paths, sizes, and SHA hashes. Use to browse the repository structure "
            "and find specific files to read."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path_prefix": {
                    "type": "string",
                    "description": "Filter to files under this path. Empty = all files.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 200,
                    "description": "Max entries to return. Default 200.",
                },
            },
            "required": [],
        },
        handler=list_repo_files_handler,
    )

    # ---- Tool 2: read_file ----
    async def read_file_handler(args: dict) -> str:
        path = args.get("path", "")
        max_lines = args.get("max_lines", 200)

        if not path:
            return "Error: 'path' is required."

        tree = await _get_tree()
        entry = next((e for e in tree if e["path"] == path), None)
        if not entry:
            return f"Error: File '{path}' not found in repository."

        sha = entry.get("sha", "")
        if not sha:
            return f"Error: No SHA found for '{path}'."

        try:
            content = await _svc_read_file_content(token, repo_full_name, sha)
            if content is None:
                return f"Error: File '{path}' could not be read from repository."
            lines = content.split("\n")
            total_lines = len(lines)
            if total_lines > max_lines:
                content = "\n".join(lines[:max_lines])
                content += (
                    f"\n\n[TRUNCATED: showing lines 1\u2013{max_lines} of {total_lines}. "
                    "Use search_code to locate specific sections, or get_file_outline "
                    "to see the structure first.]"
                )
            return content
        except Exception as e:
            return f"Error reading '{path}': {e}"

    read_file = ToolDefinition(
        name="read_file",
        description=(
            "Read the full content of a specific file from the linked repository. "
            "Use the exact path from list_repo_files. For large files (>50KB) "
            "only the first 200 lines are returned."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Exact file path as returned by list_repo_files",
                },
                "max_lines": {
                    "type": "integer",
                    "default": 200,
                },
            },
            "required": ["path"],
        },
        handler=read_file_handler,
    )

    # ---- Tool 3: search_code ----
    async def search_code_handler(args: dict) -> str:
        pattern = args.get("pattern", "")
        file_extension = args.get("file_extension", "")
        max_results = args.get("max_results", 30)

        if not pattern:
            return "Error: 'pattern' is required."

        tree = await _get_tree()
        if file_extension:
            ext = file_extension if file_extension.startswith(".") else f".{file_extension}"
            tree = [e for e in tree if e["path"].endswith(ext)]

        # Sort by priority tier before scanning; stable sort preserves original order within tier
        sorted_tree = sorted(tree, key=_search_priority)

        matches = []
        files_checked = 0
        for entry in sorted_tree[:50]:  # Max 50 files per search
            sha = entry.get("sha", "")
            if not sha:
                continue

            try:
                content = await _svc_read_file_content(token, repo_full_name, sha)
                if content is None:
                    continue
                files_checked += 1
                for i, line in enumerate(content.split("\n"), 1):
                    if pattern.lower() in line.lower():
                        matches.append(f"{entry['path']}:{i}: {line.strip()}")
                        if len(matches) >= max_results:
                            break
            except Exception:
                continue

            if len(matches) >= max_results:
                break

        if not matches:
            return f"No matches found for '{pattern}' (searched {files_checked} files)."

        return "\n".join(matches)

    search_code = ToolDefinition(
        name="search_code",
        description=(
            "Search for a text pattern across all files in the linked repository. "
            "Returns matching lines with file paths and line numbers. "
            "Use for finding API calls, imports, function definitions, etc."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Literal string or simple pattern to search for",
                },
                "file_extension": {
                    "type": "string",
                    "description": "Filter to files with this extension (e.g. 'py', 'ts', 'md'). Empty = all.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 30,
                },
            },
            "required": ["pattern"],
        },
        handler=search_code_handler,
    )

    # ---- Tool 4: read_multiple_files ----
    async def read_multiple_files_handler(args: dict) -> str:
        paths = args.get("paths", [])
        if not paths:
            return "Error: 'paths' is required and must be a non-empty list."
        if len(paths) > 8:
            paths = paths[:8]

        tree = await _get_tree()
        tree_map = {e["path"]: e for e in tree}

        output_parts = []
        for path in paths:
            entry = tree_map.get(path)
            if not entry:
                output_parts.append(f"=== {path} ===\nError: File not found.\n")
                continue

            sha = entry.get("sha", "")
            if not sha:
                output_parts.append(f"=== {path} ===\nError: No SHA for file.\n")
                continue
            try:
                content = await _svc_read_file_content(token, repo_full_name, sha)
                if content is None:
                    output_parts.append(f"=== {path} ===\nError: File could not be read.\n")
                    continue
                lines = content.split("\n")
                total_lines = len(lines)
                if total_lines > 200:
                    content = "\n".join(lines[:200])
                    content += (
                        f"\n\n[TRUNCATED: showing lines 1\u2013200 of {total_lines}. "
                        "Use search_code to locate specific sections, or get_file_outline "
                        "to see the structure first.]"
                    )
                output_parts.append(f"=== {path} ===\n{content}\n")
            except Exception as e:
                output_parts.append(f"=== {path} ===\nError: {e}\n")

        return "\n".join(output_parts)

    read_multiple_files = ToolDefinition(
        name="read_multiple_files",
        description=(
            "Read up to 8 files at once. More efficient than calling read_file multiple times. "
            "Returns each file separated by a clear header."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths (max 8)",
                },
            },
            "required": ["paths"],
        },
        handler=read_multiple_files_handler,
    )

    # ---- Tool 5: get_repo_summary ----
    async def get_repo_summary_handler(args: dict) -> str:
        tree = await _get_tree()

        # Key files to look for
        summary_files = [
            "README.md", "README.rst", "README",
            "package.json", "pyproject.toml", "Cargo.toml",
            "setup.py", "go.mod", "go.sum",
            "Makefile", "Dockerfile", "docker-compose.yml",
            "CONTRIBUTING.md", "architecture.md", "ARCHITECTURE.md",
            "openapi.yaml", "openapi.json", ".env.example", "CLAUDE.md",
        ]

        parts = []

        # Root directory listing
        root_entries = sorted(set(
            e["path"].split("/")[0] for e in tree
        ))
        parts.append(f"Repository: {repo_full_name} (branch: {repo_branch})")
        parts.append(f"Total files: {len(tree)}")
        parts.append("\nRoot directory:\n" + "\n".join(f"  {e}" for e in root_entries[:30]))

        # Fetch key files
        tree_map = {e["path"]: e for e in tree}
        total_lines = len("\n".join(parts).split("\n"))

        for fname in summary_files:
            if total_lines >= 400:
                break
            entry = tree_map.get(fname)
            if not entry:
                continue

            sha = entry.get("sha", "")
            if not sha:
                continue
            try:
                content = await _svc_read_file_content(token, repo_full_name, sha)
                if content is None:
                    continue
                lines = content.split("\n")
                total_file_lines = len(lines)
                max_lines = min(100, 400 - total_lines)
                if total_file_lines > max_lines:
                    content = "\n".join(lines[:max_lines]) + (
                        f"\n\n[TRUNCATED: showing lines 1\u2013{max_lines} of {total_file_lines}. "
                        "Use search_code to locate specific sections, or get_file_outline "
                        "to see the structure first.]"
                    )
                parts.append(f"\n=== {fname} ===\n{content}")
                total_lines += len(content.split("\n"))
            except Exception:
                continue

        return "\n".join(parts)

    get_repo_summary = ToolDefinition(
        name="get_repo_summary",
        description=(
            "Get a pre-compiled orientation package: root directory listing, README, "
            "package manifests (package.json, pyproject.toml, go.mod, etc.), and "
            "Dockerfile/docker-compose — all in a single turn. Equivalent to 5–8 "
            "individual file reads but costs only 1 turn. Call this first before any "
            "other tool to orient yourself efficiently."
        ),
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
        handler=get_repo_summary_handler,
    )

    # ---- Tool 6: get_file_outline ----
    async def get_file_outline_handler(args: dict) -> str:
        path = args.get("path", "")
        if not path:
            return "Error: 'path' is required."

        tree = await _get_tree()
        entry = next((e for e in tree if e["path"] == path), None)
        if not entry:
            return f"Error: File '{path}' not found in repository."

        sha = entry.get("sha", "")
        if not sha:
            return f"Error: No SHA found for '{path}'."

        try:
            content = await _svc_read_file_content(token, repo_full_name, sha)
            if content is None:
                return f"Error: File '{path}' could not be read from repository."
        except Exception as e:
            return f"Error reading '{path}': {e}"

        all_lines = content.split("\n")
        outline_lines = []
        for match in OUTLINE_PATTERNS.finditer(content):
            indent_len = len(match.group(1))
            # Include top-level (0) and class members (2–4 spaces); skip deep nesting (>=8)
            if indent_len >= 8:
                continue
            # Compute line number from match start
            line_no = content[: match.start()].count("\n") + 1
            line_text = all_lines[line_no - 1].rstrip()
            outline_lines.append((line_no, line_text))

        if not outline_lines:
            return f"No outline found for {path} (binary or template file?)"

        total = len(outline_lines)
        truncated = total > 100
        display = outline_lines[:100]

        outline = "\n".join(f"  L{ln}: {text}" for ln, text in display)
        if truncated:
            outline += f"\n[100 of {total} definitions shown]"

        return f"path: {path}\n\n{outline}"

    get_file_outline = ToolDefinition(
        name="get_file_outline",
        description=(
            "Get function, class, and interface signatures from a file without reading full content. "
            "Useful for understanding the structure of large files before deciding which section to read."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path from list_repo_files",
                },
            },
            "required": ["path"],
        },
        handler=get_file_outline_handler,
    )

    return [
        get_repo_summary,    # first: orientation (README, manifests, root dir) in 1 turn
        read_multiple_files, # batch reads — more efficient than individual read_file calls
        read_file,           # single file read with optional line range
        search_code,         # pattern search across the full tree
        list_repo_files,     # filtered file tree listing
        get_file_outline,    # function/class signatures without full content
    ]
