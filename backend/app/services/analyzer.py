"""Stage 1: Analyze

Classifies the prompt and identifies optimization opportunities.
Uses claude-sonnet for structured JSON extraction with streaming.
"""

import json
import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.prompts.analyzer_prompt import get_analyzer_prompt
from app.providers.base import MODEL_ROUTING, LLMProvider
from app.schemas.pipeline_outputs import AnalyzeOutput
from app.services.cache_service import CacheService, get_cache
from app.services.context_builders import (
    build_codebase_summary,
    format_file_contexts,
    format_instructions,
    format_url_contexts,
)
from app.services.stage_runner import extract_json_with_fallback, stream_with_timeout

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

    # Cache check: same prompt + same context content + same system prompt = same classification.
    # Fix #9: hash full context content so different repos, file attachments, or
    # instructions produce distinct cache keys (not just presence-flag booleans).
    cache = get_cache()
    if cache:
        context_sig_parts = [
            CacheService.hash_content(
                json.dumps(codebase_context, sort_keys=True, default=str)
            ) if codebase_context else "",
            CacheService.hash_content(
                json.dumps(file_contexts, default=str)
            ) if file_contexts else "",
            CacheService.hash_content(
                json.dumps(url_fetched_contexts, default=str)
            ) if url_fetched_contexts else "",
            CacheService.hash_content(
                json.dumps(instructions, default=str)
            ) if instructions else "",
        ]
        context_sig = ":".join(context_sig_parts)
        prompt_hash = CacheService.hash_content(raw_prompt)
        flags_hash = CacheService.hash_content(context_sig)
        sys_hash = CacheService.hash_content(system_prompt)
        analyze_cache_key = CacheService.make_key("analyze_v3", prompt_hash, flags_hash, sys_hash)
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

    stream_ok = False
    full_text = ""
    async for status, text in stream_with_timeout(
        provider, system_prompt, user_message, model,
        settings.ANALYZE_TIMEOUT_SECONDS, "Stage 1 (Analyze)",
    ):
        if status == "chunk":
            yield ("step_progress", {"step": "analyze", "content": text})
        elif status == "done":
            full_text = text  # type: ignore[assignment]
            stream_ok = True
        elif status == "timeout":
            full_text = text or ""  # type: ignore[assignment]

    result = await extract_json_with_fallback(
        provider, system_prompt, user_message, model,
        settings.ANALYZE_TIMEOUT_SECONDS, "Stage 1 (Analyze)",
        full_text, stream_ok,
        quality_key="analysis_quality",
        quality_value_success="full",
        quality_value_fallback_json="full",
        default_result={
            "task_type": "general",
            "weaknesses": ["Analysis failed - using defaults"],
            "strengths": [],
            "complexity": "moderate",
            "recommended_frameworks": [],
            "analysis_quality": "fallback",
        },
        output_type=AnalyzeOutput,
    )

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
