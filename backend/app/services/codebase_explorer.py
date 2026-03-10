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
            "description": "Code snippets relevant to the user's prompt",
        },
        "codebase_observations": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Key observations about the codebase architecture and patterns",
        },
        "prompt_grounding_notes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Notes on how the codebase relates to or corrects the user's prompt",
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


# ── Deterministic anchor files ──────────────────────────────────────────
# ANCHOR_FILENAMES imported from codebase_patterns.py (single source of truth)


def _get_anchor_paths(tree: list[dict]) -> list[str]:
    """Return paths of deterministic anchor files present in the tree."""
    tree_paths = {e["path"] for e in tree}
    return [p for p in sorted(tree_paths) if p.split("/")[-1] in ANCHOR_FILENAMES]


def _deduplicate_files(
    ranked: list[RankedFile],
    anchors: list[str],
    cap: int,
) -> list[str]:
    """Merge ranked files and anchors, deduplicate, cap at limit.

    Anchors come first (deterministic context), then ranked by score.
    """
    seen: set[str] = set()
    result: list[str] = []

    # Anchors first
    for path in anchors:
        if path not in seen:
            seen.add(path)
            result.append(path)

    # Then ranked files
    for rf in ranked:
        if rf.path not in seen:
            seen.add(rf.path)
            result.append(rf.path)
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
    max_lines_per_file: int = 300,
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


# ── Main explore function ───────────────────────────────────────────────

async def run_explore(
    provider: LLMProvider,
    raw_prompt: str,
    repo_full_name: str,
    repo_branch: str,
    session_id: Optional[str] = None,
    github_token: Optional[str] = None,
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
    model = MODEL_ROUTING["explore"]
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
        ctx = CodebaseContext(repo=repo_full_name, branch=repo_branch)
        ctx.observations = [f"Exploration setup failed: {e}"]
        ctx.explore_quality = "failed"
        ctx_dict = asdict(ctx)
        ctx_dict["explore_failed"] = True
        ctx_dict["explore_error"] = str(e)
        yield ("explore_result", ctx_dict)
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
            yield ("explore_result", {
                "explore_quality": "failed",
                "explore_failed": True,
                "explore_error": (
                    f"Branch '{repo_branch}' not found and default branch "
                    f"lookup also failed: {fb_err}"
                ),
                "observations": [
                    f"Branch '{repo_branch}' does not exist and fallback failed."
                ],
                "tech_stack": [],
                "key_files_read": [],
                "relevant_snippets": [],
                "grounding_notes": [],
                "coverage_pct": 0,
                "files_read_count": 0,
            })
            return

    if branch_fallback:
        yield ("explore_info", {
            "branch_fallback": True,
            "original_branch": repo_branch,
            "used_branch": used_branch,
        })

    # ── Check explore result cache ────────────────────────────────────
    cache = get_cache()
    cache_key = None
    if cache:
        prompt_hash = CacheService.hash_content(raw_prompt)
        cache_key = CacheService.make_key("explore", repo_full_name, used_branch, prompt_hash)
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
        ctx = CodebaseContext(repo=repo_full_name, branch=used_branch)
        ctx.observations = ["Repository tree is empty or inaccessible"]
        ctx.explore_quality = "failed"
        ctx_dict = asdict(ctx)
        ctx_dict["explore_failed"] = True
        ctx_dict["explore_error"] = "Empty tree"
        yield ("explore_result", ctx_dict)
        return

    total_in_tree = len(tree)
    max_files = settings.EXPLORE_MAX_FILES

    # Try semantic retrieval via embedding index
    index_svc = get_repo_index_service()
    index_status = await index_svc.get_index_status(repo_full_name, used_branch)

    ranked_files: list[RankedFile] = []
    retrieval_method = "keyword_fallback"

    if index_status.is_ready:
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

    # Merge and deduplicate
    all_file_paths = _deduplicate_files(ranked_files, anchor_paths, cap=max_files)

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
        ctx = CodebaseContext(repo=repo_full_name, branch=used_branch)
        ctx.observations = ["No files could be read from repository"]
        ctx.explore_quality = "failed"
        ctx_dict = asdict(ctx)
        ctx_dict["explore_failed"] = True
        ctx_dict["explore_error"] = "No readable files"
        yield ("explore_result", ctx_dict)
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
    _MAX_CONTEXT_CHARS = 700_000  # ~175K tokens
    if len(context_payload) > _MAX_CONTEXT_CHARS:
        logger.warning(
            "Explore context exceeds %d chars (%d chars); trimming semantic files",
            _MAX_CONTEXT_CHARS, len(context_payload),
        )
        # Remove semantic-tier files (last in priority) until within budget
        # Note: this also affects key_files_read downstream (intentional —
        # trimmed files were not shown to the LLM)
        paths_by_priority = list(file_contents.keys())
        while len(context_payload) > _MAX_CONTEXT_CHARS and paths_by_priority:
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

    context = CodebaseContext(
        repo=repo_full_name,
        branch=used_branch,
        tech_stack=_normalize_string_list(parsed.get("tech_stack", [])),
        key_files_read=list(file_contents.keys()),
        relevant_snippets=_normalize_snippets(parsed.get("relevant_code_snippets", [])),
        observations=_normalize_string_list(parsed.get("codebase_observations", [])),
        grounding_notes=_normalize_string_list(parsed.get("prompt_grounding_notes", [])),
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
