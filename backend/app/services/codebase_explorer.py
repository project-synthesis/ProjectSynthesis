"""Stage 0: Codebase Explore — Semantic Retrieval + Single-Shot Synthesis

Replaces the previous 15-25 turn agentic exploration loop with a deterministic
three-phase pipeline:

  1. Vector retrieval: embed prompt → cosine search pre-built index → top-K files
  2. Parallel file reads: batch-read ranked files + deterministic anchors
  3. Single-shot synthesis: one LLM call to produce CodebaseContext

Reduces explore from ~3-8 minutes to ~45-90 seconds without quality loss.
Background indexing (see repo_index_service.py) runs when a repo is linked.
"""

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncGenerator, Optional

import anyio

from app.config import settings
from app.prompts.explore_synthesis_prompt import get_explore_synthesis_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider, parse_json_robust
from app.services.cache_service import CacheService, get_cache
from app.services.codebase_patterns import ANCHOR_FILENAMES
from app.services.github_service import (
    get_branch_head_sha,
    get_default_branch,
    get_repo_tree,
    read_file_content,
)
from app.services.repo_index_service import (
    RankedFile,
    get_repo_index_service,
)

logger = logging.getLogger(__name__)


# ── LLM output normalization helpers ──────────────────────────────────

def _normalize_string_list(raw: Any) -> list[str]:
    """Coerce LLM output to a flat list of strings.

    Handles: list[str], list[dict] (extracts first string value),
    list[list] (joins), single string (wraps), None (empty list).
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return [str(raw)]
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            # Extract the most useful string value from the dict
            for key in (
                "detail", "text", "description", "observation",
                "note", "content", "summary",
            ):
                if key in item and isinstance(item[key], str):
                    result.append(item[key])
                    break
            else:
                # Fallback: join all string values
                vals = [str(v) for v in item.values() if v]
                if vals:
                    result.append(" — ".join(vals))
        elif isinstance(item, list):
            result.append(" ".join(str(x) for x in item))
        else:
            result.append(str(item))
    return result


def _normalize_snippets(raw: Any) -> list[dict]:
    """Coerce LLM snippet output to list[dict] with file/lines/context keys."""
    if not isinstance(raw, list):
        return []
    result: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            result.append({
                "file": str(item.get("file", item.get("path", "unknown"))),
                "lines": str(item.get("lines", item.get("line_range", ""))),
                "context": str(
                    item.get("context", item.get("description", item.get("content", "")))
                ),
            })
        elif isinstance(item, str):
            result.append({"file": "unknown", "lines": "", "context": item})
    return result


_LINE_REF_PATTERNS = [
    re.compile(r"lines?\s+(\d+)\s*[-\u2013]\s*(\d+)"),          # "lines 233-240" or "line 45-62"
    re.compile(r"line\s+(\d+)(?!\s*[-\u2013])"),                 # "line 42" (single)
    re.compile(r"\bL(\d+)\b"),                                    # "L42"
    re.compile(r"\.(?:py|ts|js|svelte|go|rs|java):(\d+)"),       # "pipeline.py:233"
]

_EXECUTION_CLAIM_INDICATORS = re.compile(
    r"does NOT|is NOT set|is NOT|but doesn't|but does not|missing|"
    r"never set|not implemented|not called|not used|not defined|"
    r"is broken|is wrong|should be|needs to be|fails to|"
    r"incorrectly|improperly|bug|defect|flaw",
    re.IGNORECASE,
)


def _validate_explore_output(
    snippets: list[dict],
    observations: list[str],
    grounding_notes: list[str],
    file_contents: dict[str, str],
    max_lines_shown: int,
) -> tuple[list[dict], list[str], list[str]]:
    """Validate LLM explore output against what was actually shown.

    Flags unverifiable claims with [unverified] suffixes.
    Returns (snippets, observations, grounding_notes) with flags applied.
    """
    _UNVERIFIED = " [unverified \u2014 beyond visible range]"
    _UNVERIFIED_TRUNC = " [unverified \u2014 file truncated at line {}]"

    # Build a set of file stems for fuzzy matching in observations
    file_stems = {}
    for path in file_contents:
        filename = path.split("/")[-1]
        file_stems[filename] = path
        stem = filename.rsplit(".", 1)[0]
        file_stems[stem] = path

    def _parse_line_range(lines_str: str) -> tuple[int, int] | None:
        """Parse '45-62' or '45' into (start, end). Returns None if unparseable."""
        lines_str = lines_str.strip()
        m = re.match(r"(\d+)\s*[-\u2013]\s*(\d+)", lines_str)
        if m:
            return int(m.group(1)), int(m.group(2))
        m = re.match(r"(\d+)$", lines_str)
        if m:
            n = int(m.group(1))
            return n, n
        return None

    def _max_line_for_file(file_path: str) -> int:
        """Return the max visible line for a file, or 0 if unknown."""
        content = file_contents.get(file_path)
        if content is None:
            return 0
        return min(max_lines_shown, content.count("\n") + 1)

    # Validate snippets
    validated_snippets = []
    for snip in snippets:
        snip = dict(snip)  # copy
        file_path = snip.get("file", "")
        lines_str = snip.get("lines", "")
        rng = _parse_line_range(lines_str) if lines_str else None

        if file_path not in file_contents:
            snip["context"] = snip.get("context", "") + _UNVERIFIED
        elif rng:
            max_line = _max_line_for_file(file_path)
            if rng[1] > max_line:
                snip["context"] = snip.get("context", "") + _UNVERIFIED
        validated_snippets.append(snip)

    # Validate observations and grounding notes
    def _flag_text(text: str) -> str:
        for pattern in _LINE_REF_PATTERNS:
            for m in pattern.finditer(text):
                groups = m.groups()
                line_num = int(groups[-1])  # last group is always a line number
                if line_num > max_lines_shown:
                    return text + _UNVERIFIED
        return text

    def _is_truncated(file_path: str) -> bool:
        """Check if a file was truncated by looking for the truncation marker."""
        content = file_contents.get(file_path, "")
        return "[TRUNCATED" in content

    def _flag_execution_claim(text: str) -> str:
        """Flag execution-layer claims that slipped past the synthesis prompt.

        The explore phase is an intelligence layer — it should not make
        correctness judgments. Any claim matching execution-layer language
        (e.g. "is NOT called", "missing", "broken") gets flagged so
        downstream stages treat it with appropriate skepticism.
        """
        if _EXECUTION_CLAIM_INDICATORS.search(text):
            # Check if the claim references a truncated file
            for filename, path in file_stems.items():
                if filename in text and _is_truncated(path):
                    return text + _UNVERIFIED_TRUNC.format(max_lines_shown)
            # Even non-truncated execution claims are flagged — the explore
            # phase should provide navigation, not correctness verdicts
            return text + " [unverified — explore provides context, not audit]"
        return text

    validated_obs = [_flag_text(o) for o in observations]
    validated_notes = [_flag_execution_claim(_flag_text(n)) for n in grounding_notes]

    return validated_snippets, validated_obs, validated_notes


# JSON Schema for the explore stage output.
# Used by complete_json for structured output enforcement.
EXPLORE_OUTPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "tech_stack": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of technologies, frameworks, and languages used",
        },
        "key_files_read": {
            "type": "array",
            "items": {"type": "string"},
            "description": "File paths that were read during exploration",
        },
        "relevant_code_snippets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file": {"type": "string"},
                    "lines": {"type": "string"},
                    "context": {"type": "string"},
                },
                "required": ["file", "context"],
            },
            "description": (
                "Code snippets structurally relevant to the prompt intent"
                " — entry points, interfaces, data shapes"
            ),
        },
        "codebase_observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Architectural observations: project structure, data flow,"
                " component relationships, patterns"
            ),
        },
        "prompt_grounding_notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Context intelligence: maps prompt intent to codebase"
                " locations, key abstractions, and navigation hints"
            ),
        },
        "coverage_pct": {
            "type": "integer",
            "description": "Server-computed — do not set.",
        },
    },
    "required": [
        "tech_stack",
        "key_files_read",
        "codebase_observations",
        "prompt_grounding_notes",
    ],
}


@dataclass
class CodebaseContext:
    repo: str = ""
    branch: str = "main"
    tech_stack: list[str] = field(default_factory=list)
    key_files_read: list[str] = field(default_factory=list)
    relevant_snippets: list[dict] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    grounding_notes: list[str] = field(default_factory=list)
    files_read_count: int = 0
    coverage_pct: int = 0
    duration_ms: int = 0
    explore_quality: str = "complete"


# Code extension set for module stem matching
_CODE_EXTENSIONS = frozenset({
    ".py", ".ts", ".js", ".jsx", ".tsx", ".svelte", ".vue",
    ".go", ".rs", ".java", ".rb", ".php", ".cs", ".swift",
    ".yaml", ".yml", ".toml", ".json", ".md", ".txt",
})

# Regex for extracting filename-like tokens from prompt text
_FILENAME_PATTERN = re.compile(r"[\w./-]*\w+\.\w{1,10}")


def _extract_prompt_referenced_files(
    raw_prompt: str,
    tree: list[dict],
    max_matches_per_ref: int | None = None,
) -> list[str]:
    """Extract file paths mentioned in the prompt, validated against the repo tree.

    Three-tier matching (exact path > filename > module stem).
    Ambiguous references (>max_matches_per_ref matches) are skipped.
    """
    if max_matches_per_ref is None:
        max_matches_per_ref = settings.EXPLORE_MAX_AMBIGUOUS_MATCHES

    # Normalize prompt: backslashes to forward slashes
    normalized = raw_prompt.replace("\\", "/")

    # Strip URL-like strings to prevent false matches
    normalized = re.sub(r"https?://\S+", "", normalized)

    tree_paths = [e["path"] for e in tree]
    result: list[str] = []
    seen: set[str] = set()

    def _add(path: str) -> None:
        if path not in seen:
            seen.add(path)
            result.append(path)

    # Tier 1: Exact path match — check each tree path against the prompt
    for tp in tree_paths:
        if tp in normalized:
            _add(tp)

    # Tier 2: Filename match — extract filename-like tokens from prompt
    candidates = _FILENAME_PATTERN.findall(normalized)

    for candidate in candidates:
        filename = candidate.split("/")[-1]
        if not any(filename.endswith(ext) for ext in _CODE_EXTENSIONS):
            continue
        matches = [tp for tp in tree_paths if tp.endswith("/" + filename) or tp == filename]
        if 0 < len(matches) <= max_matches_per_ref:
            for m in matches:
                _add(m)

    # Tier 3: Module stem match — words that match a code file's stem
    words = set(normalized.lower().split())
    words = {w.strip(".,;:!?()[]{}\"'") for w in words}
    words = {w for w in words if len(w) >= 3 and "/" not in w and "." not in w}

    for word in words:
        matches = [
            tp for tp in tree_paths
            if tp.split("/")[-1].rsplit(".", 1)[0].lower() == word
            and any(tp.endswith(ext) for ext in _CODE_EXTENSIONS)
        ]
        if 0 < len(matches) <= max_matches_per_ref:
            for m in matches:
                _add(m)

    return result


# ── Deterministic anchor files ──────────────────────────────────────────
# ANCHOR_FILENAMES imported from codebase_patterns.py (single source of truth)


def _get_anchor_paths(tree: list[dict]) -> list[str]:
    """Return paths of deterministic anchor files present in the tree."""
    tree_paths = {e["path"] for e in tree}
    return [p for p in sorted(tree_paths) if p.split("/")[-1] in ANCHOR_FILENAMES]


def _merge_file_lists(
    prompt_referenced: list[str],
    anchors: list[str],
    semantic_ranked: list[RankedFile] | list[str],
    cap: int,
) -> list[str]:
    """Merge three file tiers with deduplication, respecting priority order.

    Priority: prompt_referenced > anchors > semantic_ranked.
    Files appearing in multiple tiers count once at their highest priority.
    Semantic results are trimmed first when the cap is hit.
    """
    seen: set[str] = set()
    result: list[str] = []

    # Tier 1: Prompt-referenced (highest priority)
    for path in prompt_referenced:
        if path not in seen:
            seen.add(path)
            result.append(path)

    # Tier 2: Anchors
    for path in anchors:
        if path not in seen:
            seen.add(path)
            result.append(path)

    # Tier 3: Semantic ranked (fill remaining)
    for item in semantic_ranked:
        path = item.path if hasattr(item, "path") else str(item)
        if path not in seen:
            seen.add(path)
            result.append(path)
        if len(result) >= cap:
            break

    return result[:cap]


# ── Keyword fallback (when embeddings unavailable) ──────────────────────

_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "out", "off", "over",
    "under", "again", "further", "then", "once", "and", "but", "or",
    "nor", "not", "so", "if", "when", "that", "this", "it", "i", "my",
    "me", "we", "our", "you", "your", "how", "what", "which", "who",
})


def _keyword_fallback(
    tree: list[dict],
    prompt: str,
    top_k: int = 15,
) -> list[RankedFile]:
    """Rank files by keyword match when embedding index is unavailable."""
    # Extract keywords from prompt
    words = set(prompt.lower().split())
    keywords = {w.strip(".,;:!?()[]{}\"'") for w in words} - _STOPWORDS
    keywords = {k for k in keywords if len(k) >= 3}

    if not keywords:
        return []

    scored: list[tuple[str, dict, int]] = []
    for entry in tree:
        path_lower = entry["path"].lower()
        score = sum(1 for kw in keywords if kw in path_lower)
        if score > 0:
            scored.append((entry["path"], entry, score))

    scored.sort(key=lambda x: x[2], reverse=True)
    return [
        RankedFile(
            path=path,
            score=float(score),
            sha=entry.get("sha", ""),
            size_bytes=entry.get("size_bytes", 0),
        )
        for path, entry, score in scored[:top_k]
    ]


# ── File content assembly ───────────────────────────────────────────────

async def _batch_read_files(
    token: str,
    repo_full_name: str,
    tree: list[dict],
    file_paths: list[str],
    max_lines_per_file: int = 500,
) -> dict[str, str]:
    """Read multiple files in parallel from GitHub.

    Returns dict of {path: content}. Failed reads are silently skipped.
    """
    tree_map = {e["path"]: e for e in tree}
    semaphore = asyncio.Semaphore(settings.EXPLORE_FILE_READ_CONCURRENCY)
    results: dict[str, str] = {}

    async def _read_one(path: str) -> None:
        async with semaphore:
            entry = tree_map.get(path)
            if not entry:
                return
            sha = entry.get("sha", "")
            if not sha:
                return
            try:
                content = await read_file_content(token, repo_full_name, sha)
                if content:
                    lines = content.split("\n")
                    if len(lines) > max_lines_per_file:
                        content = "\n".join(lines[:max_lines_per_file])
                        content += (
                            f"\n\n[TRUNCATED — only lines 1–{max_lines_per_file} of {len(lines)} shown. "
                            f"Do NOT reference or make claims about lines beyond {max_lines_per_file}.]"
                        )
                    results[path] = content
            except Exception as e:
                logger.debug("Failed to read %s: %s", path, e)

    tasks = [_read_one(p) for p in file_paths]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results


def _format_files_for_llm(file_contents: dict[str, str]) -> str:
    """Format file contents with line numbers for the LLM.

    Each line is prefixed with its 1-indexed number (right-aligned, 4 digits)
    so the LLM can reference accurate line numbers in its observations.
    """
    parts: list[str] = []
    for path, content in file_contents.items():
        numbered = "\n".join(
            f"{i:>4} | {line}"
            for i, line in enumerate(content.split("\n"), 1)
        )
        parts.append(f"=== {path} ===\n{numbered}\n")
    return "\n".join(parts)


def _make_failed_result(
    repo: str,
    branch: str,
    error: str,
    observations: list[str] | None = None,
) -> tuple[str, dict]:
    """Build a standardised failed explore result event.

    Centralises the failure-context pattern used in multiple early-return paths
    inside ``run_explore()``.
    """
    ctx = CodebaseContext(repo=repo, branch=branch)
    ctx.observations = observations or [error]
    ctx.explore_quality = "failed"
    ctx_dict = asdict(ctx)
    ctx_dict["explore_failed"] = True
    ctx_dict["explore_error"] = error
    return ("explore_result", ctx_dict)


# ── Main explore function ───────────────────────────────────────────────

async def run_explore(
    provider: LLMProvider,
    raw_prompt: str,
    repo_full_name: str,
    repo_branch: str,
    session_id: Optional[str] = None,
    github_token: Optional[str] = None,
    model: str | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 0 codebase exploration via semantic retrieval + single-shot synthesis.

    Three-phase pipeline:
      1. Retrieve relevant files (vector search or keyword fallback)
      2. Batch-read file contents in parallel
      3. Single-shot LLM synthesis → CodebaseContext

    Token resolution order:
      1. ``github_token`` — passed directly (MCP path, no session needed)
      2. ``session_id``   — decrypt from DB (browser OAuth path)

    Yields:
        ("tool_call", {...})      — progress events for UI
        ("agent_text", {...})     — status messages
        ("explore_info", {...})   — branch fallback notification
        ("explore_result", {...}) — final CodebaseContext dict
    """
    start_ms = time.monotonic()
    model = model or MODEL_ROUTING["explore"]
    used_branch = repo_branch
    branch_fallback = False

    # ── Token resolution ──────────────────────────────────────────────
    try:
        if github_token:
            token = github_token
        elif session_id:
            from app.services.github_client import _get_decrypted_token
            token = await _get_decrypted_token(session_id)
        else:
            raise ValueError(
                "run_explore requires either github_token or session_id"
            )
    except Exception as e:
        logger.error("Stage 0 (Explore) token resolution failed: %s", e)
        yield _make_failed_result(
            repo_full_name, repo_branch, str(e),
            observations=[f"Exploration setup failed: {e}"],
        )
        return

    # ── Branch validation ─────────────────────────────────────────────
    try:
        def _check_branch_sync() -> None:
            from github import Auth, Github
            g = Github(auth=Auth.Token(token))
            repo = g.get_repo(repo_full_name)
            repo.get_branch(repo_branch)

        await anyio.to_thread.run_sync(_check_branch_sync)
    except Exception as branch_err:
        logger.warning(
            "Stage 0 (Explore): branch %r not found for %s (%s); "
            "falling back to default branch",
            repo_branch, repo_full_name, branch_err,
        )
        try:
            used_branch = await get_default_branch(token, repo_full_name)
            branch_fallback = True
        except Exception as fb_err:
            logger.warning("Could not get default branch: %s", fb_err)
            yield _make_failed_result(
                repo_full_name, repo_branch,
                f"Branch '{repo_branch}' not found and default branch "
                f"lookup also failed: {fb_err}",
                observations=[f"Branch '{repo_branch}' does not exist and fallback failed."],
            )
            return

    if branch_fallback:
        yield ("explore_info", {
            "branch_fallback": True,
            "original_branch": repo_branch,
            "used_branch": used_branch,
        })

    # ── Fetch current branch HEAD SHA (single lightweight API call) ──
    current_sha = await get_branch_head_sha(token, repo_full_name, used_branch)

    # ── Check explore result cache (SHA-aware key) ───────────────────
    cache = get_cache()
    cache_key = None
    if cache:
        prompt_hash = CacheService.hash_content(raw_prompt)
        # Include HEAD SHA in cache key — new push = new SHA = automatic cache miss
        cache_key = CacheService.make_key(
            "explore", repo_full_name, used_branch, current_sha or "", prompt_hash,
        )
        cached = await cache.get(cache_key)
        if cached:
            logger.info("Stage 0 (Explore) cache hit for %s@%s", repo_full_name, used_branch)
            yield ("agent_text", {"content": "Using cached exploration results..."})
            yield ("explore_result", cached)
            return

    # ── Phase 1: Retrieve relevant files ──────────────────────────────
    yield ("agent_text", {"content": "Retrieving relevant files from repository index..."})
    yield ("tool_call", {
        "tool": "semantic_retrieval",
        "input": {"repo": repo_full_name, "branch": used_branch},
        "status": "running",
    })

    # Fetch the tree (needed for anchors and file reading)
    tree = await get_repo_tree(token, repo_full_name, used_branch)
    if not tree:
        yield _make_failed_result(
            repo_full_name, used_branch, "Empty tree",
            observations=["Repository tree is empty or inaccessible"],
        )
        return

    total_in_tree = len(tree)
    max_files = settings.EXPLORE_MAX_FILES

    # Try semantic retrieval via embedding index
    index_svc = get_repo_index_service()
    index_status = await index_svc.get_index_status(repo_full_name, used_branch)

    ranked_files: list[RankedFile] = []
    retrieval_method = "keyword_fallback"

    # ── Check if the index is stale (branch HEAD changed since last build) ──
    index_stale = (
        current_sha is not None
        and index_status.head_sha is not None
        and current_sha != index_status.head_sha
    )

    if index_stale:
        logger.info(
            "Branch HEAD changed (%s → %s) for %s@%s, triggering background reindex",
            index_status.head_sha[:8], current_sha[:8],
            repo_full_name, used_branch,
        )
        # Trigger background rebuild only if not already building (prevents duplicate tasks)
        if index_status.status != "building":
            asyncio.create_task(index_svc.build_index(token, repo_full_name, used_branch))
        # Skip semantic retrieval for this run — keyword fallback reads fresh content
        retrieval_method = "keyword_fallback"
    elif index_status.is_ready:
        try:
            ranked_files = await index_svc.query_relevant_files(
                repo_full_name, used_branch, raw_prompt, top_k=max_files,
            )
            retrieval_method = "semantic_index"
            logger.info(
                "Stage 0 (Explore): semantic retrieval returned %d files for %s@%s",
                len(ranked_files), repo_full_name, used_branch,
            )
        except Exception as e:
            logger.warning("Semantic retrieval failed, using keyword fallback: %s", e)
    elif index_status.status == "building":
        # Wait briefly for index to finish
        yield ("agent_text", {"content": "Waiting for repository index to complete..."})
        wait_timeout = settings.EXPLORE_INDEX_WAIT_TIMEOUT
        waited = 0
        while waited < wait_timeout:
            await asyncio.sleep(2)
            waited += 2
            index_status = await index_svc.get_index_status(repo_full_name, used_branch)
            if index_status.status != "building":
                break

        if index_status.is_ready:
            try:
                ranked_files = await index_svc.query_relevant_files(
                    repo_full_name, used_branch, raw_prompt, top_k=max_files,
                )
                retrieval_method = "semantic_index"
            except Exception as e:
                logger.warning("Semantic retrieval failed after wait: %s", e)

    # Keyword fallback if no semantic results
    if not ranked_files:
        ranked_files = _keyword_fallback(tree, raw_prompt, top_k=max_files - 5)
        retrieval_method = "keyword_fallback"

    # Get anchor files (README, manifests, etc.)
    anchor_paths = _get_anchor_paths(tree)

    # Merge and deduplicate (3-tier priority: prompt-referenced > anchors > semantic)
    prompt_file_paths = _extract_prompt_referenced_files(raw_prompt, tree)
    all_file_paths = _merge_file_lists(
        prompt_referenced=prompt_file_paths,
        anchors=anchor_paths,
        semantic_ranked=ranked_files,
        cap=max_files,
    )

    yield ("tool_call", {
        "tool": "semantic_retrieval",
        "input": {
            "method": retrieval_method,
            "files_selected": len(all_file_paths),
            "index_status": index_status.status,
        },
        "status": "complete",
    })

    # ── Phase 2: Parallel file reads ──────────────────────────────────
    yield ("agent_text", {
        "content": f"Reading {len(all_file_paths)} files from {repo_full_name}...",
    })
    yield ("tool_call", {
        "tool": "batch_read_files",
        "input": {"count": len(all_file_paths)},
        "status": "running",
    })

    # Dynamic line budget: divide total budget across files, capped per file
    max_lines = min(
        settings.EXPLORE_MAX_LINES_PER_FILE,
        settings.EXPLORE_TOTAL_LINE_BUDGET // max(1, len(all_file_paths)),
    )
    file_contents = await _batch_read_files(
        token, repo_full_name, tree, all_file_paths,
        max_lines_per_file=max_lines,
    )

    yield ("tool_call", {
        "tool": "batch_read_files",
        "input": {"count": len(file_contents), "requested": len(all_file_paths)},
        "status": "complete",
    })

    if not file_contents:
        yield _make_failed_result(
            repo_full_name, used_branch, "No readable files",
            observations=["No files could be read from repository"],
        )
        return

    # ── Phase 3: Single-shot LLM synthesis ────────────────────────────
    yield ("agent_text", {"content": "Synthesizing codebase analysis..."})
    yield ("tool_call", {
        "tool": "llm_synthesis",
        "input": {"model": model, "files": len(file_contents)},
        "status": "running",
    })

    system_prompt = get_explore_synthesis_prompt()
    # Runtime char guard — prevent context overflow on repos with long lines
    context_payload = _format_files_for_llm(file_contents)
    max_context_chars = settings.EXPLORE_MAX_CONTEXT_CHARS
    if len(context_payload) > max_context_chars:
        logger.warning(
            "Explore context exceeds %d chars (%d chars); trimming semantic files",
            max_context_chars, len(context_payload),
        )
        # Remove semantic-tier files (last in priority) until within budget
        # Note: this also affects key_files_read downstream (intentional —
        # trimmed files were not shown to the LLM)
        paths_by_priority = list(file_contents.keys())
        while len(context_payload) > max_context_chars and paths_by_priority:
            removed = paths_by_priority.pop()
            del file_contents[removed]
            context_payload = _format_files_for_llm(file_contents)
    user_message = (
        f"User's prompt to optimize:\n{raw_prompt}\n\n"
        f"Repository: {repo_full_name} (branch: {used_branch})\n"
        f"Total files in repo: {total_in_tree}\n"
        f"Files provided below: {len(file_contents)}\n\n"
        f"{context_payload}"
    )

    try:
        parsed = await asyncio.wait_for(
            provider.complete_json(
                system=system_prompt,
                user=user_message,
                model=model,
                schema=EXPLORE_OUTPUT_SCHEMA,
            ),
            timeout=settings.EXPLORE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning("Stage 0 (Explore) synthesis timed out")
        parsed = {}
    except Exception as e:
        logger.error("Stage 0 (Explore) synthesis failed: %s", e)
        # Try without schema enforcement as fallback
        try:
            raw_text = await provider.complete(
                system=system_prompt,
                user=user_message,
                model=model,
            )
            parsed = parse_json_robust(raw_text)
        except Exception as e2:
            logger.error("Stage 0 (Explore) fallback synthesis also failed: %s", e2)
            parsed = {}

    yield ("tool_call", {
        "tool": "llm_synthesis",
        "input": {},
        "status": "complete",
    })

    # ── Build CodebaseContext ─────────────────────────────────────────
    duration_ms = int((time.monotonic() - start_ms) * 1000)

    # Step 1: Normalize LLM output
    tech_stack = _normalize_string_list(parsed.get("tech_stack", []))
    snippets = _normalize_snippets(parsed.get("relevant_code_snippets", []))
    observations = _normalize_string_list(parsed.get("codebase_observations", []))
    grounding_notes = _normalize_string_list(parsed.get("prompt_grounding_notes", []))

    # Step 2: Validate against what was actually shown to the LLM
    try:
        snippets, observations, grounding_notes = _validate_explore_output(
            snippets, observations, grounding_notes,
            file_contents=file_contents,
            max_lines_shown=max_lines,
        )
    except Exception as val_err:
        logger.warning("Explore output validation failed, using unvalidated data: %s", val_err)

    # Step 3: Construct CodebaseContext with validated data
    context = CodebaseContext(
        repo=repo_full_name,
        branch=used_branch,
        tech_stack=tech_stack,
        key_files_read=list(file_contents.keys()),
        relevant_snippets=snippets,
        observations=observations,
        grounding_notes=grounding_notes,
        files_read_count=len(file_contents),
        coverage_pct=min(100, round(len(file_contents) / max(1, total_in_tree) * 100)),
        duration_ms=duration_ms,
        explore_quality="complete" if parsed else "partial",
    )

    ctx_dict = asdict(context)

    # Cache the result
    if cache and cache_key and context.explore_quality == "complete":
        try:
            await cache.set(cache_key, ctx_dict, ttl_seconds=settings.EXPLORE_RESULT_CACHE_TTL)
        except Exception as e:
            logger.debug("Failed to cache explore result: %s", e)

    logger.info(
        "Stage 0 (Explore) completed for %s@%s: %d files, %dms, quality=%s, method=%s",
        repo_full_name, used_branch, context.files_read_count,
        duration_ms, context.explore_quality, retrieval_method,
    )

    yield ("explore_result", ctx_dict)
