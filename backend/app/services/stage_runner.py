"""Shared streaming stage infrastructure.

Encapsulates the queue+worker+timeout pattern and the JSON extraction
fallback chain used by all pipeline stages (analyze, strategy, validate, optimize).
"""

import asyncio
import logging
from typing import AsyncGenerator

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ValidationError

from app.providers.base import LLMProvider, parse_json_robust

logger = logging.getLogger(__name__)


async def stream_with_timeout(
    provider: LLMProvider,
    system_prompt: str,
    user_message: str,
    model: str,
    timeout_seconds: float,
    stage_name: str,
) -> AsyncGenerator[tuple[str, str | None], None]:
    """Stream LLM output with background task + queue + timeout.

    Yields:
        ("chunk", text)     — each streamed text chunk
        ("done", full_text) — streaming completed successfully
        ("timeout", partial_text_or_empty) — streaming timed out
        ("error", None)     — streaming failed with an exception
    """
    full_text = ""
    chunk_queue: asyncio.Queue = asyncio.Queue()

    async def _stream_worker() -> None:
        try:
            async for chunk in provider.stream(system_prompt, user_message, model):
                await chunk_queue.put(chunk)
        finally:
            await chunk_queue.put(None)  # Sentinel — always sent even on error

    stream_task = asyncio.create_task(_stream_worker())
    timeout_handle = asyncio.get_running_loop().call_later(
        timeout_seconds,
        lambda: stream_task.cancel() if not stream_task.done() else None,
    )

    try:
        while True:
            chunk = await chunk_queue.get()
            if chunk is None:
                break
            full_text += chunk
            yield ("chunk", chunk)
        await stream_task
        yield ("done", full_text)
    except asyncio.CancelledError:
        logger.warning("%s streaming timed out after %ds", stage_name, timeout_seconds)
        yield ("timeout", full_text or "")
    except Exception as e:
        logger.error("%s streaming failed: %s", stage_name, e)
        yield ("error", None)
    finally:
        timeout_handle.cancel()
        if not stream_task.done():
            stream_task.cancel()


async def extract_json_with_fallback(
    provider: LLMProvider,
    system_prompt: str,
    user_message: str,
    model: str,
    timeout_seconds: float,
    stage_name: str,
    full_text: str,
    stream_ok: bool,
    quality_key: str,
    quality_value_success: str | None,
    quality_value_fallback_json: str | None = None,
    default_result: dict | None = None,
    output_type: type[PydanticBaseModel] | None = None,
) -> dict:
    """Parse JSON from streamed text, falling back to complete_json/complete_parsed, then to default.

    Args:
        full_text: Accumulated text from streaming.
        stream_ok: Whether streaming completed without errors.
        quality_key: Key to set on the result dict (e.g. "analysis_quality").
        quality_value_success: Value for quality_key on successful parse. If None, skipped.
        quality_value_fallback_json: Value when complete_json fallback is used.
        default_result: Fallback dict when all extraction fails.
        output_type: Optional Pydantic model class.  When provided:
            (a) validates the streamed-text JSON against the model, falling
                through on ``ValidationError``;
            (b) uses ``complete_parsed()`` instead of ``complete_json()`` in the
                fallback path for server-side schema enforcement + Pydantic
                type safety.

    Returns:
        Parsed JSON dict with quality_key set.
    """
    result = None

    # Try parsing streamed text first
    if stream_ok and full_text:
        try:
            parsed_dict = parse_json_robust(full_text)
            # L8: Validate with Pydantic when output_type is provided
            if output_type is not None:
                try:
                    validated = output_type.model_validate(parsed_dict)
                    parsed_dict = validated.model_dump()
                except ValidationError as ve:
                    logger.warning(
                        "%s Pydantic validation failed on streamed text: %s",
                        stage_name, ve,
                    )
                    parsed_dict = None  # type: ignore[assignment]
            if parsed_dict is not None:
                result = parsed_dict
                if quality_value_success is not None:
                    result[quality_key] = quality_value_success
        except Exception:
            pass

    # Fallback to complete_parsed (when output_type) or complete_json
    if not result:
        try:
            if output_type is not None:
                parsed_model = await asyncio.wait_for(
                    provider.complete_parsed(
                        system_prompt, user_message, model, output_type,
                    ),
                    timeout=timeout_seconds,
                )
                result = parsed_model.model_dump()
            else:
                result = await asyncio.wait_for(
                    provider.complete_json(system_prompt, user_message, model),
                    timeout=timeout_seconds,
                )
            if quality_value_fallback_json is not None:
                result[quality_key] = quality_value_fallback_json
            elif quality_value_success is not None:
                result[quality_key] = quality_value_success
        except Exception as e:
            logger.error("%s JSON extraction failed: %s", stage_name, e)
            if default_result is not None:
                result = default_result.copy()
            else:
                result = {}

    return result
