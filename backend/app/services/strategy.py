"""Stage 2: Strategy

Selects the optimal optimization framework combination.
Uses claude-opus for deep reasoning about framework selection.
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.prompts.strategy_prompt import get_strategy_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider, parse_json_robust
from app.services.cache_service import CacheService, get_cache
from app.services.context_builders import (
    build_analysis_summary,
    build_codebase_summary,
    format_file_contexts,
    format_url_contexts,
)
from app.services.strategy_selector import heuristic_strategy_fallback

logger = logging.getLogger(__name__)

_STRATEGY_CACHE_TTL = 86400  # 24 hours (prompt-specific; was 7 days when keyed only on task_type)


async def run_strategy(
    provider: LLMProvider,
    raw_prompt: str,
    analysis: dict,
    codebase_context: Optional[dict] = None,
    strategy_override: Optional[str] = None,
    file_contexts: list[dict] | None = None,
    url_fetched_contexts: list[dict] | None = None,
    instructions: list[str] | None = None,
) -> AsyncGenerator[tuple[str, dict], None]:
    """Run Stage 2 strategy selection.

    Yields:
        ("step_progress", {"step": "strategy", "content": chunk}) for each streamed chunk
        ("strategy", dict) with keys: primary_framework, secondary_frameworks,
                                       rationale, approach_notes
    """
    # Cache check: keyed on prompt content + analysis signature so different
    # prompts with the same task_type get distinct strategies.
    cache = get_cache()
    task_type = analysis.get("task_type", "general")
    complexity = analysis.get("complexity", "moderate")
    cache_key = None
    if cache:
        prompt_hash = CacheService.hash_content(raw_prompt)
        analysis_hash = CacheService.hash_content(
            f"{task_type}:{complexity}:"
            f"{','.join(analysis.get('weaknesses', [])[:5])}:"
            f"{','.join(analysis.get('recommended_frameworks', []))}"
        )
        cache_key = CacheService.make_key("strategy", prompt_hash, analysis_hash)

    if cache and cache_key and not strategy_override:
        cached = await cache.get(cache_key)
        if cached is not None:
            cached["strategy_source"] = "cached"
            yield ("strategy", cached)
            return

    system_prompt = get_strategy_prompt()

    user_message = (
        f"Raw prompt:\n---\n{raw_prompt}\n---\n\n"
        f"Analysis result:\n{build_analysis_summary(analysis)}"
    )
    if codebase_context:
        codebase_summary = build_codebase_summary(codebase_context)
        if codebase_summary:
            user_message += f"\n\nCodebase context:\n{codebase_summary}"

    # Inject attached files / URLs / user constraints so strategy selection
    # can account for domain-specific signals (the strategy prompt already
    # instructs the LLM to consider these).
    user_message += format_file_contexts(file_contexts)
    user_message += format_url_contexts(url_fetched_contexts)

    if instructions:
        constraint_block = "\n".join(f"  - {i}" for i in instructions[:10])
        user_message += (
            f"\n\nUser-specified output constraints:\n{constraint_block}"
        )

    model = MODEL_ROUTING["strategy"]

    # Stream with background task + queue (same pattern as optimizer.py).
    full_text = ""
    stream_failed = False
    chunk_queue: asyncio.Queue = asyncio.Queue()

    async def _stream_worker() -> None:
        try:
            async for chunk in provider.stream(system_prompt, user_message, model):
                await chunk_queue.put(chunk)
        finally:
            await chunk_queue.put(None)

    stream_task = asyncio.create_task(_stream_worker())
    timeout_handle = asyncio.get_running_loop().call_later(
        settings.STRATEGY_TIMEOUT_SECONDS,
        lambda: stream_task.cancel() if not stream_task.done() else None,
    )

    try:
        while True:
            chunk = await chunk_queue.get()
            if chunk is None:
                break
            full_text += chunk
            yield ("step_progress", {"step": "strategy", "content": chunk})
        await stream_task
    except asyncio.CancelledError:
        logger.warning("Strategy stage streaming timed out after %ds", settings.STRATEGY_TIMEOUT_SECONDS)
        stream_failed = True
    except Exception as e:
        logger.error(f"Stage 2 (Strategy) streaming failed: {e}")
        stream_failed = True
    finally:
        timeout_handle.cancel()
        if not stream_task.done():
            stream_task.cancel()

    # JSON extraction from streamed text
    result = None
    if not stream_failed and full_text:
        try:
            result = parse_json_robust(full_text)
            result["strategy_source"] = "llm"
        except Exception:
            pass

    # Fallback to complete_json when streaming failed or parse failed
    if not result:
        try:
            result = await asyncio.wait_for(
                provider.complete_json(system_prompt, user_message, model),
                timeout=settings.STRATEGY_TIMEOUT_SECONDS,
            )
            result["strategy_source"] = "llm_json"
        except Exception as e:
            logger.error(f"Stage 2 (Strategy) failed: {e}. Using heuristic fallback.")
            result = heuristic_strategy_fallback(analysis.get("task_type", "general"))
            result["strategy_source"] = "heuristic"

    # Ensure required fields
    result.setdefault("primary_framework", "CO-STAR")
    result.setdefault("secondary_frameworks", [])
    result.setdefault("rationale", "")
    result.setdefault("approach_notes", "")

    # Cache successful LLM results
    if cache and cache_key and not strategy_override and result.get("strategy_source") in ("llm", "llm_json"):
        await cache.set(cache_key, result, ttl_seconds=_STRATEGY_CACHE_TTL)

    yield ("strategy", result)
