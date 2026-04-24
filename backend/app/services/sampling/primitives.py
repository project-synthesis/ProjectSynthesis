"""Low-level sampling request primitives — extracted from ``sampling_pipeline``.

These helpers wrap the MCP ``Context.session.create_message`` API with:

* a plain text variant (``sampling_request_plain``)
* a structured tool-calling variant with text fallback on client incompatibility
  (``sampling_request_structured``)
* JSON/text extraction utilities (``extract_text``, ``extract_json_block``,
  ``parse_text_response``, ``extract_tool_use``)
* a best-effort keyword classifier for unparseable analyses
  (``build_analysis_from_text``)

All public functions were previously module-private in ``sampling_pipeline``
(with leading underscore) — they are exported here without the underscore
so the package boundary is explicit while keeping behavior identical.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from typing import Any, TypeVar

from mcp.server.fastmcp import Context
from mcp.shared.exceptions import McpError
from mcp.types import (
    CreateMessageResult,
    SamplingMessage,
    TextContent,
    ToolChoice,
    ToolResultContent,
)
from mcp.types import (
    Tool as MCPTool,
)
from pydantic import BaseModel

from app.providers.base import (
    ProviderError,
    ProviderOverloadedError,
)
from app.schemas.pipeline_contracts import AnalysisResult
from app.services.event_notification import notify_event_bus

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Timeout ceiling for individual sampling requests (seconds)
# ---------------------------------------------------------------------------

SAMPLING_TIMEOUT_SECONDS: float = 120.0


# ---------------------------------------------------------------------------
# Pydantic -> MCP Tool
# ---------------------------------------------------------------------------


def pydantic_to_mcp_tool(
    model_cls: type[BaseModel], tool_name: str, description: str,
) -> MCPTool:
    """Convert a Pydantic model class to an MCP ``Tool`` definition."""
    return MCPTool(
        name=tool_name,
        description=description,
        inputSchema=model_cls.model_json_schema(),
    )


# ---------------------------------------------------------------------------
# Sampling request primitives
# ---------------------------------------------------------------------------


async def sampling_request_plain(
    ctx: Context,
    system: str,
    user: str,
    *,
    max_tokens: int = 16384,
) -> tuple[str, str]:
    """Send a text-only sampling request.  Returns ``(text, model_id)``."""
    kwargs: dict[str, Any] = {
        "messages": [SamplingMessage(role="user", content=TextContent(type="text", text=user))],
        "system_prompt": system,
        "max_tokens": max_tokens,
    }

    try:
        result: CreateMessageResult = await asyncio.wait_for(
            ctx.session.create_message(**kwargs),
            timeout=SAMPLING_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise ProviderOverloadedError(
            f"Sampling request timed out after {SAMPLING_TIMEOUT_SECONDS}s"
        ) from exc
    except McpError as exc:
        # Most MCP errors (rate limits, server overloaded, connection drop)
        # should be retryable via standard Provider error handling.
        raise ProviderError(f"MCP sampling error: {exc}", retryable=True) from exc

    text = extract_text(result)
    model_id = getattr(result, "model", "unknown") or "unknown"
    return text, model_id


def extract_text(result: CreateMessageResult) -> str:
    """Extract text content from a ``CreateMessageResult``."""
    if hasattr(result, "content"):
        content = result.content
        if isinstance(content, list):
            texts = []
            for block in content:
                if hasattr(block, "text"):
                    texts.append(block.text)
            if texts:
                return "\n".join(texts)
            raise ValueError(f"No text blocks in content list: {content}")
        if hasattr(content, "type"):
            if content.type == "text":
                return content.text
            raise ValueError(f"Expected text content, got {content.type}")
        if isinstance(content, str):
            return content
    raise ValueError("Cannot extract text from sampling result")


def extract_json_block(text: str) -> str | None:
    """Extract the outermost JSON object from text, handling nested braces.

    Tries markdown code blocks first (most reliable), then falls back to
    brace-depth counting on bare text.
    """
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
    for block in blocks:
        block = block.strip()
        if block.startswith("{"):
            return block

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        if depth == 0:
            return text[start : i + 1]
    return None


def parse_text_response(text: str, model_cls: type[T]) -> T:
    """Parse a text response into a Pydantic model.

    Tries direct JSON parse, then code-block extraction with brace-depth
    counting to handle nested objects correctly.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return model_cls.model_validate_json(stripped)
        except Exception:
            pass

    json_block = extract_json_block(text)
    if json_block:
        try:
            return model_cls.model_validate_json(json_block)
        except Exception as exc:
            logger.debug("JSON block found but validation failed: %s", exc)

    raise ValueError(
        f"Cannot parse sampling response as {model_cls.__name__}: {text[:200]}"
    )


async def sampling_request_structured(
    ctx: Context,
    system: str,
    user: str,
    output_model: type[T],
    *,
    max_tokens: int = 16384,
    tool_name: str = "respond",
) -> tuple[T, str]:
    """Send a structured sampling request using tool calling.

    Returns ``(parsed_model_instance, model_id)``.

    Falls back to ``sampling_request_plain`` + ``parse_text_response`` if the
    client does not support ``tools`` in sampling.
    """
    tool = pydantic_to_mcp_tool(
        output_model,
        tool_name,
        f"Respond with structured {output_model.__name__} output",
    )

    try:
        kwargs: dict[str, Any] = {
            "messages": [SamplingMessage(role="user", content=TextContent(type="text", text=user))],
            "system_prompt": system,
            "max_tokens": max_tokens,
            "tools": [tool],
            "tool_choice": ToolChoice(mode="required"),
        }

        try:
            result: CreateMessageResult = await asyncio.wait_for(
                ctx.session.create_message(**kwargs),
                timeout=SAMPLING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderOverloadedError(
                f"Structured sampling request timed out after {SAMPLING_TIMEOUT_SECONDS}s"
            ) from exc
        model_id = getattr(result, "model", "unknown") or "unknown"

        parsed = extract_tool_use(result, output_model)
        if parsed is not None:
            return parsed, model_id

        text = extract_text(result)
        return parse_text_response(text, output_model), model_id

    except (TypeError, AttributeError, McpError) as exc:
        logger.warning(
            "Structured sampling fallback: client raised %s: %s — using text + schema",
            type(exc).__name__, exc,
        )
        try:
            await notify_event_bus("optimization_status", {
                "phase": "structured_fallback",
                "reason": type(exc).__name__,
            })
        except Exception:
            pass
        schema_json = _json.dumps(output_model.model_json_schema(), indent=2)
        json_instruction = (
            "\n\n---\nIMPORTANT: Respond with ONLY a valid JSON object matching "
            "this exact schema. No markdown fences, no commentary, no reasoning "
            "text — just the raw JSON:\n" + schema_json
        )
        text, model_id = await sampling_request_plain(
            ctx, system, user + json_instruction,
            max_tokens=max_tokens,
        )
        return parse_text_response(text, output_model), model_id


def extract_tool_use(result: CreateMessageResult, model_cls: type[T]) -> T | None:
    """Try to extract parsed tool_use content from a CreateMessageResult."""
    content = getattr(result, "content", None)
    if content is None:
        return None

    blocks = content if isinstance(content, list) else [content]
    for block in blocks:
        block_type = getattr(block, "type", None)
        if block_type == "tool_use":
            tool_input = getattr(block, "input", None)
            if tool_input is not None:
                if isinstance(tool_input, dict):
                    return model_cls.model_validate(tool_input)
                if isinstance(tool_input, str):
                    return model_cls.model_validate_json(tool_input)
        if isinstance(block, ToolResultContent):
            content_val = getattr(block, "content", None)
            if isinstance(content_val, str):
                try:
                    return model_cls.model_validate_json(content_val)
                except Exception:
                    pass
    return None


# ---------------------------------------------------------------------------
# Best-effort analysis from free-text (fallback when structured + JSON parse fail)
# ---------------------------------------------------------------------------


def build_analysis_from_text(
    text: str,
    default_strategy: str,
    raw_prompt: str = "",
) -> AnalysisResult:
    """Best-effort analysis extraction from free-text LLM response.

    Searches for keywords and patterns to extract task_type, domain,
    weaknesses, and strengths instead of returning all-"general" defaults.

    Scans BOTH the LLM's analysis text AND the original raw prompt for
    keywords, since the raw prompt is the most reliable signal for
    classification when the LLM response is unparseable.
    """
    lower = (text + "\n" + raw_prompt).lower()

    task_type = "general"
    type_keywords = {
        "coding": ["function", "code", "api", "class", "program", "script", "endpoint", "module",
                    "implement", "refactor", "debug", "test", "algorithm"],
        "writing": ["write", "essay", "article", "blog", "content", "copy", "draft",
                     "documentation", "readme", "tutorial"],
        "analysis": ["analyze", "evaluate", "assess", "compare", "review", "audit",
                      "investigate", "diagnose", "benchmark"],
        "creative": ["creative", "story", "poem", "design", "brainstorm", "imagine",
                      "generate ideas", "concept"],
        "data": ["data", "dataset", "sql", "query", "csv", "statistics", "visualization",
                  "etl", "transform", "aggregate"],
        "system": ["system", "architecture", "infrastructure", "deploy", "devops", "pipeline",
                    "microservice", "scalab", "latency", "bottleneck", "load balanc",
                    "distributed", "high-traffic", "orchestrat"],
    }
    best_count = 0
    for ttype, keywords in type_keywords.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_count:
            best_count = count
            task_type = ttype

    domain = "general"
    domain_keywords = {
        "backend": ["backend", "server", "api", "endpoint", "fastapi", "django", "flask",
                     "express", "rest", "graphql", "microservice", "architecture",
                     "scalab", "latency", "bottleneck", "high-traffic"],
        "frontend": ["frontend", "react", "svelte", "vue", "css", "html", "ui", "component",
                      "browser", "responsive", "tailwind"],
        "database": ["database", "sql", "query", "schema", "migration", "index",
                      "postgres", "mysql", "mongo", "redis", "orm"],
        "security": ["security", "auth", "encryption", "vulnerability", "token",
                      "oauth", "jwt", "cors", "csrf", "xss"],
        "devops": ["deploy", "docker", "ci/cd", "kubernetes", "infrastructure",
                    "terraform", "ansible", "monitoring", "observability", "nginx"],
        "fullstack": ["fullstack", "full-stack", "full stack", "end-to-end",
                      "system-wide"],
        "data": ["data science", "machine learning", "pandas", "numpy", "sklearn",
                 "dataset", "prediction", "classification", "analytics", "etl",
                 "jupyter", "notebook", "visualization", "statistics", "regression"],
    }
    best_domain_count = 0
    for dom, keywords in domain_keywords.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_domain_count:
            best_domain_count = count
            domain = dom

    weaknesses: list[str] = []
    strengths: list[str] = []
    for marker in ("weakness", "issue", "problem", "lack", "missing", "vague"):
        if marker in lower:
            weaknesses.append(f"Detected: {marker} mentioned in analysis")
    if not weaknesses:
        weaknesses = ["Analysis could not be fully parsed from sampling response"]

    for marker in ("strength", "clear", "specific", "well-structured", "good"):
        if marker in lower:
            strengths.append(f"Detected: {marker} mentioned in analysis")
    if not strengths:
        strengths = ["Prompt provided for optimization"]

    fields_extracted = sum([
        task_type != "general",
        domain != "general",
        len(weaknesses) > 1,
        len(strengths) > 1,
    ])
    confidence = 0.4 + (fields_extracted * 0.1)

    from app.utils.text_cleanup import title_case_label, validate_intent_label

    intent_label = "General"
    if raw_prompt:
        words = raw_prompt.split()[:8]
        if len(words) > 3:
            intent_label = title_case_label(" ".join(words[:6]).rstrip(".,;:!?"))
    intent_label = validate_intent_label(intent_label, raw_prompt)

    return AnalysisResult(
        task_type=task_type,  # type: ignore[arg-type]
        weaknesses=weaknesses[:5],
        strengths=strengths[:5],
        selected_strategy=default_strategy,
        strategy_rationale=f"Strategy from pipeline preferences (text analysis inferred {task_type}/{domain})",
        confidence=confidence,
        intent_label=intent_label,
        domain=domain,
    )


__all__ = [
    "SAMPLING_TIMEOUT_SECONDS",
    "build_analysis_from_text",
    "extract_json_block",
    "extract_text",
    "extract_tool_use",
    "parse_text_response",
    "pydantic_to_mcp_tool",
    "sampling_request_plain",
    "sampling_request_structured",
]
