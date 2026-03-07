"""Stage 0: Codebase Explore

Agentic exploration of a linked GitHub repository.
Runs before the main pipeline when a repo is linked.
"""

import json
import logging
import asyncio
from typing import AsyncGenerator, Optional
from dataclasses import dataclass, field, asdict

from app.providers.base import LLMProvider, MODEL_ROUTING
from app.prompts.explore_prompt import get_explore_prompt
from app.services.codebase_tools import build_codebase_tools
from app.config import settings

logger = logging.getLogger(__name__)


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
    duration_ms: int = 0


async def run_explore(
    provider: LLMProvider,
    raw_prompt: str,
    repo_full_name: str,
    repo_branch: str,
    session_id: str,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 0 codebase exploration.

    Yields:
        ("tool_call", {...}) for each tool invocation
        ("tool_result", {...}) for each tool result
        ("explore_result", CodebaseContext dict) when done
    """
    model = MODEL_ROUTING["explore"]
    system_prompt = get_explore_prompt(raw_prompt)

    # Build tools with the GitHub token context
    tools = build_codebase_tools(
        session_id=session_id,
        repo_full_name=repo_full_name,
        repo_branch=repo_branch,
    )

    # Use asyncio.Queue to bridge sync on_tool_call → async SSE stream
    event_queue: asyncio.Queue = asyncio.Queue()

    def _on_tool_call(name: str, args: dict) -> None:
        """Sync callback; puts event in queue for real-time SSE yield."""
        event_queue.put_nowait(("tool_call", {
            "tool": name,
            "input": args,
            "status": "running",
        }))

    # Run agent as a background task so we can drain events while it runs
    agent_task = asyncio.create_task(
        provider.complete_agentic(
            system=system_prompt,
            user=f"Explore the repository {repo_full_name} (branch: {repo_branch}) "
                 f"to build context for optimizing this prompt:\n\n{raw_prompt}",
            model=model,
            tools=tools,
            max_turns=15,
            on_tool_call=_on_tool_call,
        )
    )

    # Set a timeout on the background task (use running loop, not deprecated get_event_loop)
    timeout_secs = settings.EXPLORE_TIMEOUT_SECONDS
    timeout_handle = asyncio.get_running_loop().call_later(
        timeout_secs, lambda: agent_task.cancel() if not agent_task.done() else None
    )

    try:
        # Stream tool-call events in real time while agent runs
        while not agent_task.done():
            try:
                evt = event_queue.get_nowait()
                yield evt
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)  # 50ms poll — tight enough for real-time UX

        # Drain any remaining events enqueued before task completion
        while not event_queue.empty():
            yield event_queue.get_nowait()

        # Raises CancelledError or other exception if the task failed
        result = await agent_task

    except asyncio.CancelledError:
        logger.warning("Stage 0 (Explore) timed out after %ds", timeout_secs)
        # Drain any remaining events
        while not event_queue.empty():
            yield event_queue.get_nowait()
        context = CodebaseContext(repo=repo_full_name, branch=repo_branch)
        context.observations = ["Exploration timed out - partial context only"]
        yield ("explore_result", asdict(context))
        return

    except Exception as e:
        logger.error(f"Stage 0 (Explore) error: {e}")
        raise

    finally:
        timeout_handle.cancel()

    # Parse the agent's response into a CodebaseContext
    context = CodebaseContext(repo=repo_full_name, branch=repo_branch)
    try:
        parsed = json.loads(result.text)
        context.tech_stack = parsed.get("tech_stack", [])
        context.key_files_read = parsed.get("key_files_read", [])
        context.relevant_snippets = parsed.get("relevant_code_snippets", [])
        context.observations = parsed.get("codebase_observations", [])
        context.grounding_notes = parsed.get("prompt_grounding_notes", [])
        context.files_read_count = len(context.key_files_read)
    except (json.JSONDecodeError, TypeError):
        context.observations = [result.text[:500] if result.text else "No output"]

    yield ("explore_result", asdict(context))
