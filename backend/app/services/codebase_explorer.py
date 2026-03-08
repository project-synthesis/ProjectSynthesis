"""Stage 0: Codebase Explore

Agentic exploration of a linked GitHub repository.
Runs before the main pipeline when a repo is linked.
"""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import AsyncGenerator, Optional

import anyio

from app.config import settings
from app.prompts.explore_prompt import get_explore_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider, parse_json_robust
from app.services.codebase_tools import build_codebase_tools, get_cached_tree_size
from app.services.github_service import get_default_branch

logger = logging.getLogger(__name__)

# JSON Schema for the explore stage output.
# Passed to complete_agentic as output_schema so both providers use structured
# output (tool-as-output for AnthropicAPIProvider, output_format + submit_result
# MCP tool for ClaudeCLIProvider). No text parsing required when result.output
# is populated.
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
            "description": "Server-computed — do not set. Percentage of repository files read during exploration.",
        },
    },
    "required": ["tech_stack", "key_files_read", "codebase_observations", "prompt_grounding_notes"],
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


def _describe_tool_call(name: str, args: dict) -> str:
    """Synthesize a brief human-readable reasoning note for a tool call."""
    if name == "get_repo_summary":
        paths = args.get("paths", [])
        if paths:
            sample = ", ".join(str(p) for p in paths[:3])
            return f"Scanning {sample}" + (" and more..." if len(paths) > 3 else "...")
        return "Scanning repository overview..."
    if name == "read_file":
        path = args.get("path", "file")
        return f"Reading {path}..."
    if name == "read_multiple_files":
        paths = args.get("paths", [])
        n = len(paths)
        if n == 1:
            return f"Reading {paths[0]}..."
        sample = paths[0] if paths else "files"
        return f"Reading {n} files ({sample}, ...)..."
    if name == "search_code":
        pattern = args.get("pattern", "")
        return f'Searching for "{pattern}"...' if pattern else "Searching codebase..."
    if name == "list_repo_files":
        path = args.get("path", "")
        return f"Listing files in {path}..." if path else "Listing repository files..."
    if name == "get_file_outline":
        path = args.get("path", "file")
        return f"Getting outline of {path}..."
    if name == "submit_result":
        return "Compiling final findings..."
    # Generic fallback
    return f"Running {name}..."


async def run_explore(
    provider: LLMProvider,
    raw_prompt: str,
    repo_full_name: str,
    repo_branch: str,
    session_id: Optional[str] = None,
    github_token: Optional[str] = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 0 codebase exploration.

    Token resolution order:
      1. ``github_token`` — passed directly (MCP path, no session needed)
      2. ``session_id``   — decrypt from DB (browser OAuth/PAT path)
    At least one must be provided; a ValueError is raised if neither is.

    Yields:
        ("tool_call", {...}) for each tool invocation in real time
        ("explore_result", CodebaseContext dict) when done
    """
    model = MODEL_ROUTING["explore"]
    system_prompt = get_explore_prompt(raw_prompt)

    # Resolve GitHub token and build tools up front; yield a fallback result if
    # setup fails so pipeline.py always receives an explore_result event.
    branch_fallback = False
    used_branch = repo_branch

    try:
        if github_token:
            token = github_token
        elif session_id:
            from app.services.github_client import _get_decrypted_token
            token = await _get_decrypted_token(session_id)
        else:
            raise ValueError(
                "run_explore requires either github_token or session_id to authenticate with GitHub"
            )

        # Attempt to build tools with the requested branch; if the branch
        # doesn't exist, fall back to the repository's default branch.
        # All failures trigger the fallback — we don't distinguish between
        # 404 and auth errors; both are unrecoverable without user intervention.
        try:
            def _check_branch_sync() -> None:
                from github import Auth, Github
                g = Github(auth=Auth.Token(token))
                repo = g.get_repo(repo_full_name)
                repo.get_branch(repo_branch)  # probe the requested branch, not the fallback

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
                logger.info(
                    "Stage 0 (Explore): using default branch %r for %s",
                    used_branch, repo_full_name,
                )
            except Exception as fb_err:
                logger.warning("Could not get default branch: %s", fb_err)
                yield ("explore_result", {
                    "explore_quality": "failed",
                    "explore_failed": True,
                    "explore_error": (
                        f"Branch '{repo_branch}' not found and default branch lookup "
                        f"also failed: {fb_err}"
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

        tools = build_codebase_tools(
            token=token,
            repo_full_name=repo_full_name,
            repo_branch=used_branch,
        )
    except Exception as e:
        logger.error(f"Stage 0 (Explore) setup error: {e}")
        context = CodebaseContext(repo=repo_full_name, branch=repo_branch)
        context.observations = [f"Exploration setup failed: {e}"]
        context.explore_quality = "failed"
        yield ("explore_result", asdict(context))
        return

    # Emit branch_fallback SSE event before starting the agentic loop so
    # pipeline.py and the frontend know which branch was actually used.
    if branch_fallback:
        yield ("explore_info", {
            "branch_fallback": True,
            "original_branch": repo_branch,
            "used_branch": used_branch,
        })

    # Use asyncio.Queue to bridge the sync on_tool_call callback → async SSE stream.
    # This lets tool-call events reach the client in real time while the agent runs,
    # rather than buffering them all until completion.
    event_queue: asyncio.Queue = asyncio.Queue()
    # Accumulate partial output from the agent as it streams; this is updated
    # by the on_tool_call callback whenever the agent submits a partial result
    # (ClaudeCLI path) or when we parse intermediate messages.  On timeout the
    # partial context is surfaced rather than a bare timeout message.
    _partial_output: dict = {}

    def _on_tool_call(name: str, args: dict) -> None:
        """Sync callback; enqueues event for immediate SSE yield.

        Emits a reasoning entry before each tool call so the activity feed
        shows intent → action rather than bare tool calls. Also captures
        partial structured output when the agent calls submit_result
        (ClaudeCLIProvider path) so that timeout recovery can surface any
        data accumulated before the deadline.
        """
        # Emit a reasoning entry before the tool call so the feed shows
        # intent → action rather than bare tool calls.
        reasoning = _describe_tool_call(name, args)
        event_queue.put_nowait(("agent_text", {"content": reasoning}))
        event_queue.put_nowait(("tool_call", {
            "tool": name,
            "input": args,
            "status": "running",
        }))
        # Capture partial output from submit_result calls
        if name == "submit_result" and isinstance(args, dict):
            _partial_output.update(args)

    def _on_agent_text(text: str) -> None:
        """Sync callback; enqueues reasoning text events for immediate SSE yield."""
        event_queue.put_nowait(("agent_text", {"content": text}))

    # Run the agentic call as a background task so we can drain events while it runs.
    agent_task = asyncio.create_task(
        provider.complete_agentic(
            system=system_prompt,
            user=(
                f"Explore the repository {repo_full_name} (branch: {used_branch}) "
                f"to build context for optimizing this prompt:\n\n{raw_prompt}"
                "\n\nIMPORTANT: When you have finished exploring, you MUST call "
                "the `submit_result` tool with your complete findings. "
                "Do not write findings as plain text — call submit_result."
            ),
            model=model,
            tools=tools,
            max_turns=25,
            on_tool_call=_on_tool_call,
            on_agent_text=_on_agent_text,
            output_schema=EXPLORE_OUTPUT_SCHEMA,
        )
    )

    # Enforce timeout via call_later so the timeout also covers the drain loop.
    # Uses get_running_loop() — safe in async context (avoids Python 3.12 deprecation).
    timeout_secs = settings.EXPLORE_TIMEOUT_SECONDS
    timeout_handle = asyncio.get_running_loop().call_later(
        timeout_secs, lambda: agent_task.cancel() if not agent_task.done() else None
    )

    try:
        # Stream tool-call events in real time while the agent is running.
        while not agent_task.done():
            try:
                evt = event_queue.get_nowait()
                yield evt
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)  # 50ms poll — tight enough for real-time UX

        # Drain any events that arrived in the final moments before task completion.
        while not event_queue.empty():
            yield event_queue.get_nowait()

        # Re-raises CancelledError or any exception from the agent task.
        result = await agent_task

    except asyncio.CancelledError:
        logger.warning("Stage 0 (Explore) timed out after %ds", timeout_secs)
        while not event_queue.empty():
            yield event_queue.get_nowait()
        context = CodebaseContext(repo=repo_full_name, branch=used_branch)
        # Surface any partial structured data already captured from submit_result
        # calls (ClaudeCLI path) so downstream stages get the most context possible.
        if _partial_output:
            context.tech_stack = _partial_output.get("tech_stack", [])
            context.key_files_read = _partial_output.get("key_files_read", [])
            context.relevant_snippets = _partial_output.get("relevant_code_snippets", [])
            context.observations = _partial_output.get("codebase_observations", [])
            context.grounding_notes = _partial_output.get("prompt_grounding_notes", [])
            context.files_read_count = len(context.key_files_read)
            total_in_tree = get_cached_tree_size(repo_full_name, used_branch)
            context.coverage_pct = min(
                100, round(context.files_read_count / max(1, total_in_tree) * 100)
            )
            # Prepend a timeout notice to observations
            context.observations = [
                f"Exploration timed out after {timeout_secs}s — partial context only"
            ] + context.observations
        else:
            context.observations = [
                f"Exploration timed out after {timeout_secs}s — partial context only"
            ]
        ctx_dict = asdict(context)
        ctx_dict["explore_failed"] = True
        ctx_dict["explore_quality"] = "failed" if context.files_read_count == 0 else "partial"
        ctx_dict["explore_error"] = f"Timed out after {timeout_secs}s"
        yield ("explore_result", ctx_dict)
        return

    except BaseException as e:
        # Catch BaseException (not just Exception) to handle anyio's BaseExceptionGroup
        # which is raised by TaskGroup failures in ClaudeCLIProvider.
        logger.error(f"Stage 0 (Explore) error: {type(e).__name__}: {e}")
        while not event_queue.empty():
            yield event_queue.get_nowait()
        context = CodebaseContext(repo=repo_full_name, branch=used_branch)
        context.observations = [f"Exploration failed: {type(e).__name__}: {e}"]
        ctx_dict = asdict(context)
        ctx_dict["explore_failed"] = True
        ctx_dict["explore_quality"] = "failed"
        ctx_dict["explore_error"] = f"{type(e).__name__}: {e}"
        yield ("explore_result", ctx_dict)
        return

    finally:
        timeout_handle.cancel()

    # Parse the agent's response into a CodebaseContext.
    context = CodebaseContext(repo=repo_full_name, branch=used_branch)

    if result.output:
        # Structured output from submit_result tool or SDK output_format —
        # already a validated dict, no text parsing needed.
        parsed = result.output
        context.tech_stack = parsed.get("tech_stack", [])
        context.key_files_read = parsed.get("key_files_read", [])
        context.relevant_snippets = parsed.get("relevant_code_snippets", [])
        context.observations = parsed.get("codebase_observations", [])
        context.grounding_notes = parsed.get("prompt_grounding_notes", [])
        context.files_read_count = len(context.key_files_read)
    else:
        # Fallback: model produced free-form text instead of calling submit_result.
        # Use 3-strategy robust JSON parsing as a last resort.
        try:
            parsed = parse_json_robust(result.text)
            context.tech_stack = parsed.get("tech_stack", [])
            context.key_files_read = parsed.get("key_files_read", [])
            context.relevant_snippets = parsed.get("relevant_code_snippets", [])
            context.observations = parsed.get("codebase_observations", [])
            context.grounding_notes = parsed.get("prompt_grounding_notes", [])
            context.files_read_count = len(context.key_files_read)
        except (ValueError, TypeError):
            context.observations = [result.text[:500] if result.text else "No output from exploration"]

    # Compute coverage_pct: how many of the tree's files did the agent read?
    total_in_tree = get_cached_tree_size(repo_full_name, used_branch)
    context.coverage_pct = min(
        100, round(context.files_read_count / max(1, total_in_tree) * 100)
    )

    yield ("explore_result", asdict(context))
