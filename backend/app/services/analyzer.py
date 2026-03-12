"""Stage 1: Analyze

Classifies the prompt and identifies optimization opportunities.
Uses claude-sonnet for structured JSON extraction with streaming.
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.prompts.analyzer_prompt import get_analyzer_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider, parse_json_robust
from app.services.cache_service import get_cache
from app.services.context_builders import (
    build_codebase_summary,
    format_file_contexts,
    format_instructions,
    format_url_contexts,
)

logger = logging.getLogger(__name__)

_ANALYZE_CACHE_TTL = 86400  # 24 hours


async def run_analyze(
    provider: LLMProvider,
    raw_prompt: str,
    codebase_context: Optional[dict] = None,
    file_contexts: list[dict] | None = None,        # N24: attached file content
    url_fetched_contexts: list[dict] | None = None, # N26: pre-fetched URL content
    instructions: list[str] | None = None,          # N37: user output constraints
    model: str | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 1 analysis on the raw prompt.

    Yields:
        ("step_progress", {"step": "analyze", "content": chunk}) for each streamed chunk
        ("analysis", dict) with keys: task_type, weaknesses, strengths, complexity,
                                       recommended_frameworks, codebase_informed
    """
    system_prompt = get_analyzer_prompt()

    # Cache check: same prompt + same context type flags + same system prompt = same classification
    cache = get_cache()
    if cache:
        context_flags = (
            f"{bool(codebase_context)}:{bool(file_contexts)}"
            f":{bool(url_fetched_contexts)}:{bool(instructions)}"
        )
        prompt_hash = cache.hash_content(raw_prompt)
        flags_hash = cache.hash_content(context_flags)
        sys_hash = cache.hash_content(system_prompt)
        analyze_cache_key = cache.make_key("analyze_v3", prompt_hash, flags_hash, sys_hash)
        cached = await cache.get(analyze_cache_key)
        if cached is not None:
            cached["analysis_quality"] = "cached"
            yield ("analysis", cached)
            return
    else:
        analyze_cache_key = None

    user_message = f"Analyze this prompt:\n\n---\n{raw_prompt}\n---"

    # N21: use build_codebase_summary (not raw json.dumps)
    if codebase_context:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            user_message += f"\n\nCodebase intelligence (navigational context for classification):\n{codebase_summary}"

    # N24: inject attached file content
    user_message += format_file_contexts(file_contexts)

    # N26: inject pre-fetched URL content
    user_message += format_url_contexts(url_fetched_contexts)

    # N37: inject output constraints so analyzer can flag incompatibilities
    user_message += format_instructions(instructions)

    model = model or MODEL_ROUTING["analyze"]

    # Stream with background task + queue (same pattern as optimizer.py).
    # This lets step_progress events flow to the client in real time while
    # we accumulate the full text for JSON extraction.
    full_text = ""
    stream_failed = False
    chunk_queue: asyncio.Queue = asyncio.Queue()

    async def _stream_worker() -> None:
        try:
            async for chunk in provider.stream(system_prompt, user_message, model):
                await chunk_queue.put(chunk)
        finally:
            await chunk_queue.put(None)  # Sentinel — always sent even on error

    stream_task = asyncio.create_task(_stream_worker())
    timeout_handle = asyncio.get_running_loop().call_later(
        settings.ANALYZE_TIMEOUT_SECONDS,
        lambda: stream_task.cancel() if not stream_task.done() else None,
    )

    try:
        while True:
            chunk = await chunk_queue.get()
            if chunk is None:
                break
            full_text += chunk
            yield ("step_progress", {"step": "analyze", "content": chunk})
        await stream_task
    except asyncio.CancelledError:
        logger.warning("Analyze stage streaming timed out after %ds", settings.ANALYZE_TIMEOUT_SECONDS)
        stream_failed = True
    except Exception as e:
        logger.error(f"Stage 1 (Analyze) streaming failed: {e}")
        stream_failed = True
    finally:
        timeout_handle.cancel()
        if not stream_task.done():
            stream_task.cancel()

    # JSON extraction from streamed text (parse_json_robust handles text-prefixed JSON)
    result = None
    if not stream_failed and full_text:
        try:
            result = parse_json_robust(full_text)
            result["analysis_quality"] = "full"
        except Exception:
            pass

    # Fallback to complete_json when streaming failed or parse failed
    if not result:
        try:
            result = await asyncio.wait_for(
                provider.complete_json(system_prompt, user_message, model),
                timeout=settings.ANALYZE_TIMEOUT_SECONDS,
            )
            result["analysis_quality"] = "full"
        except Exception as e:
            logger.error(f"Stage 1 (Analyze) failed: {e}")
            # Return sensible defaults so downstream stages can still run
            result = {
                "task_type": "general",
                "weaknesses": ["Analysis failed - using defaults"],
                "strengths": [],
                "complexity": "moderate",
                "recommended_frameworks": [],
                "analysis_quality": "fallback",
            }
            # codebase_informed will be set by the setdefault block below

    # Ensure required fields
    result.setdefault("task_type", "general")
    result.setdefault("weaknesses", [])
    result.setdefault("strengths", [])
    result.setdefault("complexity", "moderate")
    result.setdefault("recommended_frameworks", [])

    # Derive codebase_informed from explore quality rather than bare boolean.
    # Maps explore_quality → codebase_informed: complete→True, partial→"partial",
    # failed→"failed", absent→False.  This enables context_builders to emit
    # accurate quality notes downstream.
    if codebase_context:
        eq = codebase_context.get("explore_quality", "complete")
        codebase_informed: bool | str = (
            True if eq == "complete" else eq  # "partial" or "failed"
        )
    else:
        codebase_informed = False
    result.setdefault("codebase_informed", codebase_informed)

    # Cache successful results
    if cache and analyze_cache_key and result.get("analysis_quality") == "full":
        await cache.set(analyze_cache_key, result, ttl_seconds=_ANALYZE_CACHE_TTL)

    yield ("analysis", result)
