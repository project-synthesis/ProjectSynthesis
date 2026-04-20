"""Sampling-based pipeline — runs optimization phases via MCP sampling/createMessage.

Extracted from mcp_server.py.  When the MCP client supports sampling, these
functions execute the full analyze → optimize → score → suggest pipeline
through the IDE's LLM instead of requiring a local provider.

Key features over a plain text-only sampling call:
- **Structured output via tool calling** — each phase sends a Pydantic-derived
  ``Tool`` schema via ``tools`` + ``tool_choice`` so the IDE returns typed JSON.
  Falls back to text parsing if the client does not support tool calling in
  sampling.
- **Per-phase model capture** — the actual model used by the IDE is recorded
  from each ``CreateMessageResult.model`` field and persisted to DB.
- **Feature parity** with the internal CLI/API pipeline: explore, adaptation,
  applied patterns, suggest, intent drift, z-score normalization.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import re
import time
import uuid
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
from sqlalchemy import select

from app.config import DATA_DIR, PROMPTS_DIR
from app.database import async_session_factory
from app.models import Optimization
from app.schemas.pipeline_contracts import (
    DIMENSION_WEIGHTS,
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.event_notification import notify_event_bus
from app.services.heuristic_scorer import HeuristicScorer
from app.services.pattern_injection import (
    InjectedPattern,
    auto_inject_patterns,
    format_injected_patterns,
)
from app.services.pipeline_constants import (
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    VALID_TASK_TYPES,
    compute_optimize_max_tokens,
    resolve_effective_strategy,
    semantic_check,
    semantic_upgrade_general,
)
from app.services.preferences import PreferencesService
from app.services.project_service import resolve_repo_project
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.utils.text_cleanup import split_prompt_and_changes

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Timeout ceiling for individual sampling requests (seconds)
# ---------------------------------------------------------------------------

_SAMPLING_TIMEOUT_SECONDS: float = 120.0

# ---------------------------------------------------------------------------
# Helpers: Pydantic → MCP Tool
# ---------------------------------------------------------------------------


def _pydantic_to_mcp_tool(
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


async def _sampling_request_plain(
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
            timeout=_SAMPLING_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        raise TimeoutError(
            f"Sampling request timed out after {_SAMPLING_TIMEOUT_SECONDS}s"
        ) from None

    # Extract text — handle both single-content and list responses
    text = _extract_text(result)
    model_id = getattr(result, "model", "unknown") or "unknown"
    return text, model_id


def _extract_text(result: CreateMessageResult) -> str:
    """Extract text content from a ``CreateMessageResult``."""
    # Single content field (common)
    if hasattr(result, "content"):
        content = result.content
        # List of content blocks
        if isinstance(content, list):
            texts = []
            for block in content:
                if hasattr(block, "text"):
                    texts.append(block.text)
            if texts:
                return "\n".join(texts)
            raise ValueError(f"No text blocks in content list: {content}")
        # Single content object
        if hasattr(content, "type"):
            if content.type == "text":
                return content.text
            raise ValueError(f"Expected text content, got {content.type}")
        # Plain string
        if isinstance(content, str):
            return content
    raise ValueError("Cannot extract text from sampling result")


def _extract_json_block(text: str) -> str | None:
    """Extract the outermost JSON object from text, handling nested braces.

    Tries markdown code blocks first (most reliable), then falls back to
    brace-depth counting on bare text.
    """
    # Try markdown code blocks first (```json ... ```)
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text)
    for block in blocks:
        block = block.strip()
        if block.startswith("{"):
            return block

    # Fall back to brace-depth counting (find outermost {...})
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


def _parse_text_response(text: str, model_cls: type[T]) -> T:
    """Parse a text response into a Pydantic model.

    Tries direct JSON parse, then code-block extraction with brace-depth
    counting to handle nested objects correctly.
    """
    # Try direct JSON parse (LLM returned pure JSON)
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return model_cls.model_validate_json(stripped)
        except Exception:
            pass

    # Try extracting JSON from markdown or surrounding text
    json_block = _extract_json_block(text)
    if json_block:
        try:
            return model_cls.model_validate_json(json_block)
        except Exception as exc:
            logger.debug("JSON block found but validation failed: %s", exc)

    raise ValueError(
        f"Cannot parse sampling response as {model_cls.__name__}: {text[:200]}"
    )


# Text cleanup utilities: strip_meta_header() and split_prompt_and_changes()
# live in app.utils.text_cleanup (shared with MCP save_result and REST
# passthrough save paths).  Imported at module top.


async def _sampling_request_structured(
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

    Falls back to ``_sampling_request_plain`` + ``_parse_text_response`` if
    the client does not support ``tools`` in sampling.
    """
    tool = _pydantic_to_mcp_tool(
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
                timeout=_SAMPLING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"Structured sampling request timed out after {_SAMPLING_TIMEOUT_SECONDS}s"
            ) from None
        model_id = getattr(result, "model", "unknown") or "unknown"

        # Try to extract tool_use content from response
        parsed = _extract_tool_use(result, output_model)
        if parsed is not None:
            return parsed, model_id

        # If no tool_use found, try text parsing
        text = _extract_text(result)
        return _parse_text_response(text, output_model), model_id

    except (TypeError, AttributeError, McpError) as exc:
        # Client doesn't support tools in sampling (McpError from VS Code,
        # TypeError/AttributeError from other clients) — fall back to plain
        # text with explicit JSON schema instruction appended.
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
        text, model_id = await _sampling_request_plain(
            ctx, system, user + json_instruction,
            max_tokens=max_tokens,
        )
        return _parse_text_response(text, output_model), model_id


def _extract_tool_use(result: CreateMessageResult, model_cls: type[T]) -> T | None:
    """Try to extract parsed tool_use content from a CreateMessageResult."""
    content = getattr(result, "content", None)
    if content is None:
        return None

    # List of content blocks
    blocks = content if isinstance(content, list) else [content]
    for block in blocks:
        # Check for ToolUseContent or similar
        block_type = getattr(block, "type", None)
        if block_type == "tool_use":
            tool_input = getattr(block, "input", None)
            if tool_input is not None:
                if isinstance(tool_input, dict):
                    return model_cls.model_validate(tool_input)
                if isinstance(tool_input, str):
                    return model_cls.model_validate_json(tool_input)
        # Some clients return ToolResultContent
        if isinstance(block, ToolResultContent):
            content_val = getattr(block, "content", None)
            if isinstance(content_val, str):
                try:
                    return model_cls.model_validate_json(content_val)
                except Exception:
                    pass
    return None



def _build_analysis_from_text(
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
    # Combine both sources for keyword detection
    lower = (text + "\n" + raw_prompt).lower()

    # Infer task_type from keywords
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

    # Infer domain from keywords
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
    # Score-based domain selection (not first-match) to handle overlapping keywords
    best_domain_count = 0
    for dom, keywords in domain_keywords.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_domain_count:
            best_domain_count = count
            domain = dom

    # Extract weaknesses and strengths from structured sections if present
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

    # Confidence scales with how much we extracted
    fields_extracted = sum([
        task_type != "general",
        domain != "general",
        len(weaknesses) > 1,
        len(strengths) > 1,
    ])
    confidence = 0.4 + (fields_extracted * 0.1)  # 0.4 to 0.8

    # Generate a short intent label from the raw prompt
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


# ---------------------------------------------------------------------------
# Sampling pipeline: full optimize flow
# ---------------------------------------------------------------------------


async def run_sampling_pipeline(
    ctx: Context,
    prompt: str,
    strategy_override: str | None,
    *,
    repo_full_name: str | None = None,
    project_id: str | None = None,
    applied_pattern_ids: list[str] | None = None,
    codebase_context: str | None = None,  # pre-computed by enrichment service
    heuristic_task_type: str | None = None,
    heuristic_domain: str | None = None,
    divergence_alerts: str | None = None,
    pre_resolved_strategy_intelligence: str | None = None,  # pre-computed by enrichment
) -> dict:
    """Run the full pipeline via MCP sampling (IDE's LLM).

    Phases:
        0. Explore (optional — codebase context injection)
        1. Analyze — classify, detect weaknesses, select strategy
        2. Optimize — rewrite using strategy + patterns + adaptation
        3. Score — blind A/B evaluation with hybrid blending
        4. Suggest — actionable next steps (non-fatal)
        + Intent drift detection (non-fatal)

    Each phase is a separate sampling request, mirroring the internal pipeline.

    ``project_id`` is frozen at entry by the caller (MCP tool handler) — no
    persist-time resolution happens inside the pipeline (B1). Falls back to
    legacy ``resolve_repo_project()`` only when the caller did not supply
    one, preserving backward compatibility for older MCP clients.
    """
    start = time.monotonic()
    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
    system_prompt = loader.load("agent-guidance.md")

    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()

    model_ids: dict[str, str] = {}
    warnings: list[str] = []
    phase_durations: dict[str, int] = {}
    trace_id = str(uuid.uuid4())

    context_sources: dict[str, Any] = {
        "explore": False,
        "patterns": False,
        "adaptation": False,
        "workspace": codebase_context is not None,
    }

    # Trace logger — optional; skip if directory cannot be created
    from app.services.trace_logger import TraceLogger

    try:
        trace_logger: TraceLogger | None = TraceLogger(DATA_DIR / "traces")
    except OSError:
        logger.warning("Could not create traces directory; trace logging disabled")
        trace_logger = None

    # Notify: pipeline start
    await notify_event_bus("optimization_start", {
        "trace_id": trace_id,
        "provider": "mcp_sampling",
    })

    # ------------------------------------------------------------------
    # Phase 0: Codebase context (pre-computed by enrichment service)
    # ------------------------------------------------------------------
    # The enrichment service already ran explore synthesis + curated
    # retrieval. Use the pre-computed result instead of a separate LLM call.
    if codebase_context:
        context_sources["explore"] = True
        logger.info(
            "Sampling pipeline: using pre-computed codebase context (%d chars)",
            len(codebase_context),
        )
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "explore", "state": "complete",
        })
    else:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "explore", "state": "skipped",
        })

    # Enrichment trace — log what context was provided (no LLM call)
    if trace_logger and context_sources:
        trace_logger.log_phase(
            trace_id=trace_id, phase="enrichment",
            duration_ms=0,
            tokens_in=0, tokens_out=0,
            model="none", provider="mcp_sampling",
            result={
                "has_codebase_context": codebase_context is not None,
                "context_chars": len(codebase_context) if codebase_context else 0,
                "repo_full_name": repo_full_name,
                "context_sources": context_sources,
            },
        )

    # ------------------------------------------------------------------
    # Phase 1: Analyze
    # ------------------------------------------------------------------
    await notify_event_bus("optimization_status", {
        "trace_id": trace_id, "phase": "analyzing", "state": "running",
    })
    phase_t0 = time.monotonic()
    logger.info("Sampling pipeline Phase 1: Analyze")

    # Resolve blocked strategies (low approval rate) to filter from analyzer input
    blocked_strategies: set[str] = set()
    strategy_intel_enabled = prefs.get(
        "pipeline.enable_strategy_intelligence", prefs_snapshot,
    )
    if strategy_intel_enabled and not strategy_override:
        try:
            from app.models import StrategyAffinity
            from app.services.adaptation_tracker import AdaptationTracker
            async with async_session_factory() as _db:
                _result = await _db.execute(select(StrategyAffinity))
                _all_rows = _result.scalars().all()
                _by_strategy: dict[str, list[float]] = {}
                for _row in _all_rows:
                    _total = (_row.thumbs_up or 0) + (_row.thumbs_down or 0)
                    if _total >= AdaptationTracker._MIN_FEEDBACK_FOR_GATE:
                        _by_strategy.setdefault(_row.strategy, []).append(_row.approval_rate)
                for _strat, _rates in _by_strategy.items():
                    _avg = sum(_rates) / len(_rates)
                    if _avg < AdaptationTracker._BLOCK_THRESHOLD:
                        blocked_strategies.add(_strat)
                        logger.info(
                            "Sampling: strategy '%s' blocked pre-analysis: avg_approval=%.2f",
                            _strat, _avg,
                        )
        except Exception as exc:
            logger.debug("Sampling adaptation pre-filter unavailable: %s", exc)

    available_strategies = strategy_loader.format_available(blocked=blocked_strategies)
    try:
        from app.tools._shared import get_domain_resolver as _get_dr_early
        _early_resolver = _get_dr_early()
        known_domains = (
            ", ".join(sorted(_early_resolver.domain_labels))
            if _early_resolver.domain_labels
            else "backend, frontend, database, data, devops, security, fullstack, general"
        )
    except Exception:
        known_domains = "backend, frontend, database, data, devops, security, fullstack, general"
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": available_strategies,
        "known_domains": known_domains,
    })

    try:
        analysis, analyze_model = await _sampling_request_structured(
            ctx, system_prompt, analyze_msg, AnalysisResult,
        )
    except Exception:
        # Last resort fallback
        logger.warning("Structured analysis parsing failed, using fallback")
        text, analyze_model = await _sampling_request_plain(
            ctx, system_prompt, analyze_msg,
        )
        try:
            analysis = _parse_text_response(text, AnalysisResult)
        except Exception:
            analysis = _build_analysis_from_text(text, strategy_override or "auto", raw_prompt=prompt)
    model_ids["analyze"] = analyze_model
    phase_durations["analyze_ms"] = int((time.monotonic() - phase_t0) * 1000)
    if trace_logger:
        trace_logger.log_phase(
            trace_id=trace_id, phase="analyze",
            duration_ms=phase_durations["analyze_ms"],
            tokens_in=0, tokens_out=0,
            model=analyze_model, provider="mcp_sampling",
            result={"task_type": analysis.task_type, "strategy": analysis.selected_strategy},
        )
    await notify_event_bus("optimization_status", {
        "trace_id": trace_id, "phase": "analyzing", "state": "complete",
    })

    # Semantic check + domain confidence gate (shared with internal pipeline)
    confidence = semantic_check(analysis.task_type, prompt, analysis.confidence)

    # Upgrade "general" to a specific type when strong keywords are present
    effective_task_type = semantic_upgrade_general(analysis.task_type, prompt)
    if effective_task_type != analysis.task_type:
        analysis.task_type = effective_task_type  # type: ignore[assignment]

    # Resolve domain via domain nodes (replaces hardcoded VALID_DOMAINS whitelist)
    _raw_domain = getattr(analysis, "domain", None) or "general"
    try:
        from app.tools._shared import get_domain_resolver
        _resolver = get_domain_resolver()
        effective_domain = await _resolver.resolve(_raw_domain, confidence, raw_prompt=prompt)
        logger.info(
            "Domain resolved (sampling): raw='%s' confidence=%.2f → '%s'",
            _raw_domain, confidence, effective_domain,
        )
    except (ValueError, Exception) as _dr_exc:
        logger.warning(
            "Domain resolution failed (sampling): raw='%s' error=%s → 'general'",
            _raw_domain, _dr_exc,
        )
        effective_domain = "general"

    # E1: Heuristic vs LLM classification agreement tracking
    if heuristic_task_type is not None:
        try:
            from app.services.classification_agreement import get_classification_agreement
            get_classification_agreement().record(
                heuristic_task_type=heuristic_task_type,
                heuristic_domain=heuristic_domain or "general",
                llm_task_type=effective_task_type,
                llm_domain=effective_domain,
                prompt_snippet=prompt[:80],
            )
        except Exception:
            logger.debug("Classification agreement tracking failed", exc_info=True)

    # Domain mapping (Spec Section 4.2, 4.4)
    domain_raw = (getattr(analysis, "domain", None) or "general")[:MAX_DOMAIN_RAW_LENGTH]  # pre-gate, truncated
    cluster_id = None

    try:
        from app.services.taxonomy import get_engine

        _sampling_engine = get_engine()
        async with async_session_factory() as _db:
            mapping = await _sampling_engine.map_domain(
                domain_raw=domain_raw,
                db=_db,
                applied_pattern_ids=applied_pattern_ids,
            )
        cluster_id = mapping.cluster_id

        if cluster_id:
            logger.info(
                "Domain mapped (sampling): '%s' -> '%s'",
                domain_raw, mapping.taxonomy_label,
            )
    except Exception as exc:
        logger.warning("Domain mapping failed (sampling, non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Pre-Phase: Auto-inject cluster meta-patterns
    # ------------------------------------------------------------------
    auto_injected_patterns: list[InjectedPattern] = []
    auto_injected_cluster_ids: list[str] = []
    # Always auto-inject; merges with explicit patterns via format_injected_patterns()
    try:
        from app.services.taxonomy import get_engine as _get_inject_engine

        _inject_engine = _get_inject_engine()
        if _inject_engine is not None:
            async with async_session_factory() as _inject_db:
                # NOTE: optimization_id is intentionally NOT passed here.
                # Injection provenance records are written explicitly in the
                # persist block below (~line 1031) using the same DB session
                # as the Optimization record, avoiding FK violations.
                auto_injected_patterns, auto_injected_cluster_ids = (
                    await auto_inject_patterns(
                        raw_prompt=prompt,
                        taxonomy_engine=_inject_engine,
                        db=_inject_db,
                        trace_id=trace_id,
                        project_id=project_id,
                    )
                )
            if auto_injected_patterns:
                context_sources["cluster_injection"] = True
                # Store pattern texts for UI attribution (ForgeArtifact)
                context_sources.setdefault("enrichment_meta", {})
                if isinstance(context_sources.get("enrichment_meta"), dict):
                    context_sources["enrichment_meta"]["applied_pattern_texts"] = [
                        {
                            "text": ip.pattern_text,
                            "source": ip.source or "cluster",
                            "cluster_label": ip.cluster_label or "",
                            "similarity": round(ip.similarity, 3) if ip.similarity else None,
                        }
                        for ip in auto_injected_patterns
                    ]
    except Exception as exc:
        logger.warning("Sampling auto-injection failed (non-fatal): %s", exc)

    # Pre-compute prompt embedding once for strategy recommendation + few-shot
    _prompt_embedding = None
    try:
        from app.services.embedding_service import EmbeddingService as _EmbSvc

        _prompt_embedding = await _EmbSvc().aembed_single(prompt)
    except Exception:
        pass  # consumers will embed independently as fallback

    # Score-informed strategy recommendation from historical data
    data_recommendation = None
    try:
        from app.services.pipeline_constants import recommend_strategy_from_history

        async with async_session_factory() as _rec_db:
            data_recommendation = await recommend_strategy_from_history(
                raw_prompt=prompt,
                db=_rec_db,
                available_strategies=strategy_loader.list_strategies(),
                trace_id=trace_id,
                prompt_embedding=_prompt_embedding,
            )
    except Exception:
        logger.debug("Sampling strategy recommendation unavailable. trace_id=%s", trace_id)

    # Strategy resolution chain (shared with internal pipeline)
    effective_strategy = resolve_effective_strategy(
        selected_strategy=analysis.selected_strategy,
        available=strategy_loader.list_strategies(),
        blocked_strategies=blocked_strategies,
        confidence=confidence,
        strategy_override=strategy_override,
        trace_id=trace_id,
        data_recommendation=data_recommendation,
        task_type=analysis.task_type,
    )

    # ------------------------------------------------------------------
    # Phase 2: Optimize
    # ------------------------------------------------------------------
    await notify_event_bus("optimization_status", {
        "trace_id": trace_id, "phase": "optimizing", "state": "running",
    })
    phase_t0 = time.monotonic()
    logger.info("Sampling pipeline Phase 2: Optimize (strategy=%s)", effective_strategy)
    strategy_instructions = strategy_loader.load(effective_strategy)
    analysis_summary = (
        f"Task type: {analysis.task_type}\n"
        f"Domain: {effective_domain}\n"
        f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
        f"Strengths: {', '.join(analysis.strengths)}\n"
        f"Strategy: {effective_strategy}\n"
        f"Rationale: {analysis.strategy_rationale}"
    )

    # 4b: Resolve applied meta-patterns (read-only — usage incremented post-commit)
    applied_patterns_text: str | None = None
    applied_cluster_ids: set[str] = set()
    if applied_pattern_ids:
        applied_patterns_text, applied_cluster_ids = await _resolve_applied_pattern_text(
            applied_pattern_ids,
        )
    if applied_patterns_text is not None:
        context_sources["patterns"] = True

    # Merge auto-injected patterns (when no explicit applied_pattern_ids)
    applied_patterns_text = format_injected_patterns(
        auto_injected_patterns, applied_patterns_text,
    )
    if applied_patterns_text is not None:
        context_sources["patterns"] = True

    # Merge auto-injected cluster IDs for usage tracking
    applied_cluster_ids.update(auto_injected_cluster_ids)

    # 4c: Strategy intelligence (unified adaptation + performance signals)
    #     Prefer pre-resolved value from enrichment service to avoid redundant DB queries.
    strategy_intelligence: str | None = pre_resolved_strategy_intelligence
    enable_si = prefs.get(
        "pipeline.enable_strategy_intelligence", prefs_snapshot,
    )
    if not enable_si:
        strategy_intelligence = None
    elif strategy_intelligence is None:
        # Fallback: resolve on-demand when enrichment didn't provide
        try:
            from app.services.context_enrichment import resolve_strategy_intelligence
            async with async_session_factory() as _si_db:
                strategy_intelligence, _ = await resolve_strategy_intelligence(
                    _si_db, analysis.task_type, analysis.domain or "general",
                )
        except Exception:
            logger.debug("Strategy intelligence resolution failed in sampling pipeline")

    if strategy_intelligence is not None:
        context_sources["strategy_intelligence"] = True

    # Few-shot example retrieval (show, don't tell)
    few_shot_text: str | None = None
    try:
        from app.services.pattern_injection import (
            format_few_shot_examples,
            retrieve_few_shot_examples,
        )

        async with async_session_factory() as _fs_db:
            few_shot_examples = await retrieve_few_shot_examples(
                raw_prompt=prompt, db=_fs_db, trace_id=trace_id,
                prompt_embedding=_prompt_embedding,
            )
        few_shot_text = format_few_shot_examples(few_shot_examples)
        if few_shot_text:
            context_sources["few_shot_examples"] = True
    except Exception:
        logger.debug("Sampling few-shot retrieval failed. trace_id=%s", trace_id)

    optimize_msg = loader.render("optimize.md", {
        "raw_prompt": prompt,
        "analysis_summary": analysis_summary,
        "strategy_instructions": strategy_instructions,
        "codebase_context": codebase_context,
        "strategy_intelligence": strategy_intelligence,
        "applied_patterns": applied_patterns_text,
        "few_shot_examples": few_shot_text,
        "divergence_alerts": divergence_alerts,
    })

    logger.info(
        "optimize_inject: trace_id=%s input_chars=%d (~%d tokens) "
        "prompt=%d codebase=%d strategy_intel=%d patterns=%d fewshot=%d",
        trace_id, len(optimize_msg), len(optimize_msg) // 4,
        len(prompt),
        len(codebase_context) if codebase_context else 0,
        len(strategy_intelligence) if strategy_intelligence else 0,
        len(applied_patterns_text) if applied_patterns_text else 0,
        len(few_shot_text) if few_shot_text else 0,
    )

    dynamic_max_tokens = compute_optimize_max_tokens(len(prompt))
    try:
        optimization, optimize_model = await _sampling_request_structured(
            ctx, system_prompt, optimize_msg, OptimizationResult,
            max_tokens=dynamic_max_tokens,
        )
    except Exception:
        logger.warning("Structured optimization parsing failed, falling back to text")
        text, optimize_model = await _sampling_request_plain(
            ctx, system_prompt, optimize_msg,
            max_tokens=dynamic_max_tokens,
        )
        try:
            optimization = _parse_text_response(text, OptimizationResult)
        except Exception:
            cleaned, summary = split_prompt_and_changes(text.strip())
            optimization = OptimizationResult(
                optimized_prompt=cleaned,
                changes_summary=summary,
                strategy_used=effective_strategy,
            )
    # Post-cleanup: strip leaked ## Changes / ## Applied Patterns
    # from optimized_prompt on both structured and text-fallback paths.
    from app.utils.text_cleanup import sanitize_optimization_result

    _clean_prompt, _clean_changes = sanitize_optimization_result(
        optimization.optimized_prompt, optimization.changes_summary,
    )
    optimization = OptimizationResult(
        optimized_prompt=_clean_prompt,
        changes_summary=_clean_changes,
        strategy_used=optimization.strategy_used,
    )

    model_ids["optimize"] = optimize_model
    phase_durations["optimize_ms"] = int((time.monotonic() - phase_t0) * 1000)
    if trace_logger:
        trace_logger.log_phase(
            trace_id=trace_id, phase="optimize",
            duration_ms=phase_durations["optimize_ms"],
            tokens_in=0, tokens_out=0,
            model=optimize_model, provider="mcp_sampling",
            result={"strategy_used": effective_strategy},
        )
    await notify_event_bus("optimization_status", {
        "trace_id": trace_id, "phase": "optimizing", "state": "complete",
    })

    # ------------------------------------------------------------------
    # Phase 3: Score
    # ------------------------------------------------------------------
    optimized_scores: DimensionScores | None = None
    original_scores: DimensionScores | None = None
    deltas: dict[str, float] | None = None
    scoring_mode = "skipped"

    scoring_enabled = prefs.get("pipeline.enable_scoring", prefs_snapshot)
    if scoring_enabled is None:
        scoring_enabled = True  # default on

    heur_original = HeuristicScorer.score_prompt(prompt)
    heur_optimized = HeuristicScorer.score_prompt(
        optimization.optimized_prompt, original=prompt,
    )

    if scoring_enabled:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "scoring", "state": "running",
        })
        phase_t0 = time.monotonic()
        logger.info("Sampling pipeline Phase 3: Score")
        scoring_system = loader.load("scoring.md")
        # For sampling: append explicit JSON output directive so the IDE LLM
        # doesn't write a markdown essay. This does NOT modify scoring.md
        # (which is shared with the internal pipeline).
        scoring_system += (
            "\n\nYou MUST output ONLY valid JSON matching the ScoreResult schema. "
            "No markdown, no reasoning text, no commentary outside the JSON structure."
        )

        original_first = random.choice([True, False])
        if original_first:
            prompt_a, prompt_b = prompt, optimization.optimized_prompt
        else:
            prompt_a, prompt_b = optimization.optimized_prompt, prompt

        scorer_msg = (
            f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
            f"<prompt-b>\n{prompt_b}\n</prompt-b>"
        )

        _divergence_flags: list[str] = []
        scores: ScoreResult | None = None
        try:
            scores, score_model = await _sampling_request_structured(
                ctx, scoring_system, scorer_msg, ScoreResult,
                max_tokens=1024,
            )
            model_ids["score"] = score_model
        except Exception:
            scoring_mode = "heuristic"
            logger.warning("Score parsing failed, falling back to heuristic-only", exc_info=True)

        if scores:
            if original_first:
                llm_original = scores.prompt_a_scores
                llm_optimized = scores.prompt_b_scores
            else:
                llm_original = scores.prompt_b_scores
                llm_optimized = scores.prompt_a_scores

            # 4f: Fetch historical stats for z-score normalization
            historical_stats = await _fetch_historical_stats()

            blended_original = blend_scores(llm_original, heur_original, historical_stats)
            blended_optimized = blend_scores(llm_optimized, heur_optimized, historical_stats)

            original_scores = blended_original.to_dimension_scores()
            optimized_scores = blended_optimized.to_dimension_scores()
            deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)
            scoring_mode = "hybrid"

            # Log scoring trace for observability
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                get_event_logger().log_decision(
                    path="hot", op="score", decision="scored",
                    optimization_id=trace_id,
                    context={
                        "scoring_mode": "hybrid",
                        "overall": optimized_scores.overall,
                        "intent_label": analysis.intent_label,
                        "blended": blended_optimized.as_dict(),
                        "raw_llm": blended_optimized.raw_llm,
                        "raw_heuristic": blended_optimized.raw_heuristic,
                        "deltas": deltas,
                        "divergence": blended_optimized.divergence_flags,
                        "normalization": blended_optimized.normalization_applied,
                        "strategy": effective_strategy,
                        "task_type": analysis.task_type,
                    },
                )
            except RuntimeError:
                pass

            _divergence_flags = blended_optimized.divergence_flags or []
            if _divergence_flags:
                warnings.append(
                    "Score divergence between LLM and heuristic on: "
                    + ", ".join(_divergence_flags)
                )
        else:
            # LLM scoring failed or was unavailable — use heuristic scores
            # directly (already computed above via HeuristicScorer.score_prompt).
            original_scores = DimensionScores.from_dict(heur_original)
            optimized_scores = DimensionScores.from_dict(heur_optimized)
            deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)
            scoring_mode = "heuristic"
            logger.info("Using heuristic-only scores (LLM scorer unavailable)")

            # Log fallback event
            try:
                from app.services.taxonomy.event_logger import get_event_logger
                get_event_logger().log_decision(
                    path="hot", op="score", decision="fallback",
                    optimization_id=trace_id,
                    context={
                        "scoring_mode": "heuristic",
                        "reason": "LLM scorer unavailable",
                        "heuristic_scores": heur_optimized,
                    },
                )
            except RuntimeError:
                pass

        phase_durations["score_ms"] = int((time.monotonic() - phase_t0) * 1000)
        if trace_logger:
            trace_logger.log_phase(
                trace_id=trace_id, phase="score",
                duration_ms=phase_durations["score_ms"],
                tokens_in=0, tokens_out=0,
                model=model_ids.get("score", "unknown"), provider="mcp_sampling",
            )
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "scoring",
            "state": "complete" if scores else "skipped",
        })
    else:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "scoring", "state": "skipped",
        })

    # ------------------------------------------------------------------
    # 4e: Intent drift detection (non-fatal)
    # ------------------------------------------------------------------
    try:
        drift_warning = await _check_intent_drift(prompt, optimization.optimized_prompt)
        if drift_warning:
            warnings.append(drift_warning)
    except Exception as exc:
        logger.debug("Intent drift check skipped: %s", exc)

    # ------------------------------------------------------------------
    # Phase 4: Suggest (non-fatal)
    # ------------------------------------------------------------------
    suggestions: list[dict[str, str]] = []
    if optimized_scores and analysis.weaknesses:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "suggesting", "state": "running",
        })
        phase_t0 = time.monotonic()
        try:
            logger.info("Sampling pipeline Phase 4: Suggest")
            suggest_msg = loader.render("suggest.md", {
                "optimized_prompt": optimization.optimized_prompt,
                "scores": _json.dumps(optimized_scores.model_dump(), indent=2),
                "weaknesses": ", ".join(analysis.weaknesses) if analysis.weaknesses else "none identified",
                "strategy_used": effective_strategy,
                "score_deltas": "first optimization — no previous deltas",
                "score_trajectory": "first turn",
            })
            suggest_result, suggest_model = await _sampling_request_structured(
                ctx, system_prompt, suggest_msg, SuggestionsOutput,
                max_tokens=2048,
            )
            suggestions = suggest_result.suggestions
            model_ids["suggest"] = suggest_model
            logger.info("Sampling suggestions generated: %d items", len(suggestions))
        except Exception as exc:
            logger.warning("Sampling suggestion generation failed (non-fatal): %s", exc)
        phase_durations["suggest_ms"] = int((time.monotonic() - phase_t0) * 1000)
        if trace_logger:
            trace_logger.log_phase(
                trace_id=trace_id, phase="suggest",
                duration_ms=phase_durations.get("suggest_ms", 0),
                tokens_in=0, tokens_out=0,
                model=model_ids.get("suggest", "unknown"), provider="mcp_sampling",
            )
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "suggesting",
            "state": "complete" if suggestions else "skipped",
        })
    else:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "suggesting", "state": "skipped",
        })

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    elapsed_ms = int((time.monotonic() - start) * 1000)
    opt_id = str(uuid.uuid4())

    # B1: project_id frozen at entry by caller. Fallback to repo-chain
    # resolution only when the caller (older MCP client) didn't supply one —
    # keeps backward compatibility for anything still going through the
    # legacy resolve_repo_project() path. New code MUST pass project_id.
    if project_id is not None:
        _project_id: str | None = project_id
    else:
        _, _project_id = await resolve_repo_project(repo_full_name)

    async with async_session_factory() as db:
        db_opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt=optimization.optimized_prompt,
            task_type=analysis.task_type if analysis.task_type in VALID_TASK_TYPES else "general",
            intent_label=(getattr(analysis, "intent_label", None) or "general")[:MAX_INTENT_LABEL_LENGTH],
            domain=effective_domain,
            domain_raw=domain_raw,
            cluster_id=cluster_id,
            strategy_used=effective_strategy,
            changes_summary=optimization.changes_summary,
            score_clarity=optimized_scores.clarity if optimized_scores else None,
            score_specificity=optimized_scores.specificity if optimized_scores else None,
            score_structure=optimized_scores.structure if optimized_scores else None,
            score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
            score_conciseness=optimized_scores.conciseness if optimized_scores else None,
            overall_score=optimized_scores.overall if optimized_scores else None,
            provider="mcp_sampling",
            routing_tier="sampling",
            model_used=model_ids.get("optimize", "unknown"),
            scoring_mode=scoring_mode,
            duration_ms=elapsed_ms,
            tokens_by_phase=phase_durations,
            models_by_phase=model_ids,
            repo_full_name=repo_full_name,
            project_id=_project_id,
            context_sources=context_sources,
            status="completed",
            trace_id=trace_id,
            original_scores=original_scores.model_dump() if original_scores else None,
            score_deltas=deltas,
            heuristic_flags=_divergence_flags or None,
            suggestions=suggestions,
        )
        # Compute weighted improvement score from deltas.
        if deltas:
            _imp = sum(
                deltas.get(dim, 0) * w
                for dim, w in DIMENSION_WEIGHTS.items()
            )
            db_opt.improvement_score = round(max(0.0, min(10.0, _imp)), 2)
        db.add(db_opt)

        # Track applied patterns in join table
        if applied_pattern_ids:
            await _track_applied_patterns(db, opt_id, applied_pattern_ids)

        # Record injection provenance (which clusters influenced this optimization).
        # Uses flush() to eagerly detect constraint violations — expunges on
        # failure so the main Optimization commit is not affected.
        if auto_injected_cluster_ids:
            try:
                from app.models import OptimizationPattern

                _inj_sim_map = {
                    ip.cluster_id: ip.similarity
                    for ip in auto_injected_patterns
                    if ip.cluster_id
                }
                _inj_pending: list = []
                for _cid in auto_injected_cluster_ids:
                    _rec = OptimizationPattern(
                        optimization_id=opt_id,
                        cluster_id=_cid,
                        relationship="injected",
                        similarity=_inj_sim_map.get(_cid),
                    )
                    db.add(_rec)
                    _inj_pending.append(_rec)
                await db.flush()
                logger.info(
                    "Injection provenance (sampling): %d records for opt=%s. trace_id=%s",
                    len(_inj_pending), opt_id[:8], trace_id,
                )
            except Exception as _inj_exc:
                for _rec in _inj_pending:
                    try:
                        db.expunge(_rec)
                    except Exception:
                        pass
                logger.warning(
                    "Injection provenance failed (sampling, non-fatal, expunged): %s trace_id=%s",
                    _inj_exc, trace_id,
                )

        await db.commit()

    # Increment usage counts AFTER successful commit (Spec 7.8)
    if applied_cluster_ids:
        await _increment_pattern_usage(applied_cluster_ids)

    # Notify backend event bus (MCP runs in a separate process)
    await notify_event_bus("optimization_created", {
        "id": opt_id,
        "trace_id": trace_id,
        "task_type": analysis.task_type,
        "intent_label": getattr(analysis, "intent_label", None) or "general",
        "domain": effective_domain,
        "domain_raw": domain_raw,
        "strategy_used": effective_strategy,
        "overall_score": optimized_scores.overall if optimized_scores else None,
        "provider": "mcp_sampling",
        "status": "completed",
    })

    logger.info(
        "Sampling pipeline completed in %dms: id=%s strategy=%s overall=%s scoring=%s models=%s phases=%s",
        elapsed_ms, opt_id, effective_strategy,
        optimized_scores.overall if optimized_scores else scoring_mode,
        scoring_mode, model_ids, phase_durations,
    )

    return {
        "optimization_id": opt_id,
        "trace_id": trace_id,
        "optimized_prompt": optimization.optimized_prompt,
        "task_type": analysis.task_type,
        "strategy_used": effective_strategy,
        "changes_summary": optimization.changes_summary,
        "scores": optimized_scores.model_dump() if optimized_scores else heur_optimized,
        "original_scores": original_scores.model_dump() if original_scores else heur_original,
        "score_deltas": deltas if deltas else {},
        "scoring_mode": scoring_mode,
        "pipeline_mode": "sampling",
        "model_used": model_ids.get("optimize", "unknown"),
        "models_by_phase": model_ids,
        "suggestions": suggestions,
        "warnings": warnings,
        "intent_label": getattr(analysis, "intent_label", None) or "general",
        "domain": effective_domain,
    }


# ---------------------------------------------------------------------------
# Sampling analyze: standalone analysis + baseline scoring
# ---------------------------------------------------------------------------


async def run_sampling_analyze(
    ctx: Context,
    prompt: str,
    *,
    repo_full_name: str | None = None,
    project_id: str | None = None,
) -> dict:
    """Two-phase sampling pipeline: analyze + baseline score.

    Used by ``synthesis_analyze`` when no local LLM provider is available
    but the MCP client supports sampling.

    ``project_id`` is frozen at entry by the caller (B1). Falls back to
    ``resolve_repo_project()`` only when the caller didn't supply one,
    preserving backward compatibility.
    """
    start = time.monotonic()
    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    phase_durations: dict[str, int] = {}
    context_sources: dict[str, Any] = {
        "explore": False,
        "patterns": False,
        "adaptation": False,
        "workspace": False,
    }

    from app.services.trace_logger import TraceLogger

    try:
        trace_logger: TraceLogger | None = TraceLogger(DATA_DIR / "traces")
    except OSError:
        trace_logger = None

    # --- Phase 1: Analyze ---
    phase_t0 = time.monotonic()
    system_prompt = loader.load("agent-guidance.md")
    try:
        from app.tools._shared import get_domain_resolver as _get_dr_analyze
        _analyze_resolver = _get_dr_analyze()
        _analyze_known_domains = (
            ", ".join(sorted(_analyze_resolver.domain_labels))
            if _analyze_resolver.domain_labels
            else "backend, frontend, database, data, devops, security, fullstack, general"
        )
    except Exception:
        _analyze_known_domains = "backend, frontend, database, data, devops, security, fullstack, general"
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": strategy_loader.format_available(),
        "known_domains": _analyze_known_domains,
    })

    try:
        analysis, _analyze_model = await _sampling_request_structured(
            ctx, system_prompt, analyze_msg, AnalysisResult,
        )
    except Exception:
        logger.warning("Structured analysis parsing failed in analyze-only, using fallback")
        try:
            text, _analyze_model = await _sampling_request_plain(
                ctx, system_prompt, analyze_msg,
            )
            try:
                analysis = _parse_text_response(text, AnalysisResult)
            except Exception:
                analysis = _build_analysis_from_text(text, "auto", raw_prompt=prompt)
        except Exception:
            analysis = _build_analysis_from_text("", "auto", raw_prompt=prompt)
            _analyze_model = "unknown"

    analyze_ms = int((time.monotonic() - phase_t0) * 1000)
    phase_durations["analyze_ms"] = analyze_ms
    if trace_logger:
        trace_logger.log_phase(
            trace_id="(pending)",  # trace_id assigned later
            phase="analyze",
            duration_ms=analyze_ms,
            tokens_in=0, tokens_out=0,
            model=_analyze_model, provider="mcp_sampling",
            result={"task_type": analysis.task_type, "strategy": analysis.selected_strategy},
        )
    logger.info(
        "Sampling analyze Phase 1 complete in %dms: task_type=%s strategy=%s",
        analyze_ms, analysis.task_type, analysis.selected_strategy,
    )

    # Domain resolution via DomainResolver (mirrors pipeline.py)
    _analyze_domain_raw = getattr(analysis, "domain", None) or "general"
    _analyze_confidence = semantic_check(analysis.task_type, prompt, analysis.confidence)

    # Upgrade "general" to a specific type when strong keywords are present
    _upgraded_task_type = semantic_upgrade_general(analysis.task_type, prompt)
    if _upgraded_task_type != analysis.task_type:
        analysis.task_type = _upgraded_task_type  # type: ignore[assignment]

    try:
        from app.tools._shared import get_domain_resolver
        _analyze_resolver = get_domain_resolver()
        effective_domain = await _analyze_resolver.resolve(_analyze_domain_raw, _analyze_confidence, raw_prompt=prompt)
    except (ValueError, Exception):
        effective_domain = "general"

    # Domain mapping (Spec Section 4.2, 4.4)
    domain_raw = (getattr(analysis, "domain", None) or "general")[:MAX_DOMAIN_RAW_LENGTH]  # pre-gate, truncated
    cluster_id: str | None = None

    try:
        from app.services.taxonomy import get_engine

        _sampling_engine = get_engine()
        async with async_session_factory() as _db:
            mapping = await _sampling_engine.map_domain(
                domain_raw=domain_raw,
                db=_db,
                applied_pattern_ids=None,
            )
        cluster_id = mapping.cluster_id

        if cluster_id:
            logger.info(
                "Domain mapped (sampling/analyze): '%s' -> '%s'",
                domain_raw, mapping.taxonomy_label,
            )
    except Exception as exc:
        logger.warning("Domain mapping failed (sampling/analyze, non-fatal): %s", exc)

    # --- Phase 2: Baseline score ---
    phase_t0 = time.monotonic()
    scoring_system = loader.load("scoring.md")
    # For sampling: append explicit JSON output directive (same as main pipeline)
    scoring_system += (
        "\n\nYou MUST output ONLY valid JSON matching the ScoreResult schema. "
        "No markdown, no reasoning text, no commentary outside the JSON structure."
    )
    scorer_msg = (
        f"<prompt-a>\n{prompt}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt}\n</prompt-b>"
    )

    # Compute heuristic scores once (used in both try and except branches)
    heur_scores = HeuristicScorer.score_prompt(prompt)

    _score_model = "unknown"
    try:
        score_result, _score_model = await _sampling_request_structured(
            ctx, scoring_system, scorer_msg, ScoreResult,
            max_tokens=1024,
        )
        # Hybrid blend
        historical_stats = await _fetch_historical_stats()
        blended = blend_scores(score_result.prompt_a_scores, heur_scores, historical_stats)
        baseline = blended.to_dimension_scores()
    except Exception as exc:
        logger.warning("Sampling baseline score failed, using heuristic-only: %s", exc)
        baseline = DimensionScores(
            clarity=heur_scores.get("clarity", 5.0),
            specificity=heur_scores.get("specificity", 5.0),
            structure=heur_scores.get("structure", 5.0),
            faithfulness=heur_scores.get("faithfulness", 5.0),
            conciseness=heur_scores.get("conciseness", 5.0),
        )
    phase_durations["score_ms"] = int((time.monotonic() - phase_t0) * 1000)
    if trace_logger:
        trace_logger.log_phase(
            trace_id="(pending)",
            phase="score",
            duration_ms=phase_durations["score_ms"],
            tokens_in=0, tokens_out=0,
            model=_score_model, provider="mcp_sampling",
        )

    overall = baseline.overall
    total_ms = int((time.monotonic() - start) * 1000)

    # --- Persist ---
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    # B1: honor caller-frozen project_id if supplied; else fall back to
    # legacy repo-chain resolution (auto-resolves from last linked repo).
    if project_id is not None:
        _sa_repo = repo_full_name
        _sa_project_id: str | None = project_id
        if _sa_repo is None:
            # Caller gave us a project_id but no repo name — resolve the
            # repo alone for reporting (project_id is authoritative).
            try:
                _sa_repo, _ = await resolve_repo_project()
            except Exception:
                _sa_repo = None
    else:
        _sa_repo, _sa_project_id = await resolve_repo_project(repo_full_name)

    async with async_session_factory() as db:
        opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt="",
            task_type=analysis.task_type if analysis.task_type in VALID_TASK_TYPES else "general",
            intent_label=(getattr(analysis, "intent_label", None) or "general")[:MAX_INTENT_LABEL_LENGTH],
            domain=effective_domain,
            domain_raw=domain_raw,
            cluster_id=cluster_id,
            strategy_used=analysis.selected_strategy,
            changes_summary="",
            score_clarity=baseline.clarity,
            score_specificity=baseline.specificity,
            score_structure=baseline.structure,
            score_faithfulness=baseline.faithfulness,
            score_conciseness=baseline.conciseness,
            overall_score=overall,
            provider="mcp_sampling",
            routing_tier="sampling",
            model_used=_analyze_model,
            scoring_mode="baseline",
            status="analyzed",
            trace_id=trace_id,
            duration_ms=total_ms,
            tokens_by_phase=phase_durations,
            models_by_phase={"analyze": _analyze_model, "score": _score_model},
            repo_full_name=_sa_repo,
            project_id=_sa_project_id,
            context_sources=context_sources,
        )
        db.add(opt)
        await db.commit()

    # --- Notify event bus ---
    await notify_event_bus("optimization_analyzed", {
        "id": opt_id,
        "trace_id": trace_id,
        "task_type": analysis.task_type,
        "strategy": analysis.selected_strategy,
        "overall_score": overall,
        "provider": "mcp_sampling",
        "status": "analyzed",
    })

    # --- Build actionable next steps ---
    dim_scores = {
        "clarity": baseline.clarity,
        "specificity": baseline.specificity,
        "structure": baseline.structure,
        "faithfulness": baseline.faithfulness,
        "conciseness": baseline.conciseness,
    }
    next_steps = [
        "Run `synthesis_optimize(prompt=..., strategy='%s')` to improve this prompt"
        % analysis.selected_strategy,
    ]
    for weakness in analysis.weaknesses[:3]:
        next_steps.append("Address: %s" % weakness)

    weakest_dim = min(dim_scores, key=dim_scores.get)  # type: ignore[arg-type]
    weakest_val = dim_scores[weakest_dim]
    if weakest_val < 7.0:
        next_steps.append(
            "Focus on %s (scored %.1f/10) — this is the biggest opportunity for improvement"
            % (weakest_dim, weakest_val)
        )

    return {
        "optimization_id": opt_id,
        "task_type": analysis.task_type,
        "weaknesses": analysis.weaknesses,
        "strengths": analysis.strengths,
        "selected_strategy": analysis.selected_strategy,
        "strategy_rationale": analysis.strategy_rationale,
        "confidence": analysis.confidence,
        "baseline_scores": dim_scores,
        "overall_score": overall,
        "duration_ms": total_ms,
        "next_steps": next_steps,
        "optimization_ready": {
            "prompt": prompt,
            "strategy": analysis.selected_strategy,
        },
        "intent_label": getattr(analysis, "intent_label", None) or "general",
        "domain": effective_domain,
    }


# ---------------------------------------------------------------------------
# Internal helpers (non-public)
# ---------------------------------------------------------------------------


# _run_explore_phase removed — explore synthesis now handled by enrichment service
# (background on link/reindex, pre-computed context passed to run_sampling_pipeline).


async def _resolve_applied_pattern_text(
    applied_pattern_ids: list[str],
) -> tuple[str | None, set[str]]:
    """Resolve meta-pattern texts (read-only — no usage increment).

    Returns:
        (applied_text, cluster_ids) — text for optimizer context + family IDs
        for deferred usage increment after successful completion.
    """
    try:
        from app.models import MetaPattern

        async with async_session_factory() as db:
            result = await db.execute(
                select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
            )
            patterns = result.scalars().all()
            if not patterns:
                return None, set()

            lines = [f"- {p.pattern_text}" for p in patterns]
            applied_text = (
                "The following proven patterns from past optimizations "
                "should be applied where relevant:\n"
                + "\n".join(lines)
            )

            cluster_ids = {p.cluster_id for p in patterns}
            logger.info(
                "Sampling: resolved %d applied patterns from %d families",
                len(patterns), len(cluster_ids),
            )
            return applied_text, cluster_ids
    except Exception as exc:
        logger.warning("Failed to resolve applied patterns in sampling: %s", exc)
        return None, set()


async def _increment_pattern_usage(cluster_ids: set[str]) -> None:
    """Increment usage counts for applied pattern families (post-optimization)."""
    if not cluster_ids:
        return
    try:
        from app.models import PromptCluster
        from app.services.taxonomy import get_engine

        engine = get_engine()
        async with async_session_factory() as db:
            for fid in cluster_ids:
                try:
                    await engine.increment_usage(fid, db)
                except Exception as usage_exc:
                    logger.warning("Usage propagation failed for %s: %s", fid, usage_exc)
                    # Fallback: atomic SQL increment (no tree walk)
                    from sqlalchemy import update as sa_update
                    await db.execute(
                        sa_update(PromptCluster)
                        .where(PromptCluster.id == fid)
                        .values(usage_count=PromptCluster.usage_count + 1)
                    )
            await db.commit()
    except Exception as exc:
        logger.warning("Sampling usage increment failed: %s", exc)


    # _resolve_strategy_intelligence() removed — replaced by resolve_strategy_intelligence()


async def _check_intent_drift(
    original_prompt: str, optimized_prompt: str,
) -> str | None:
    """Check semantic similarity between original and optimized prompt.

    Returns a warning string if similarity is below 0.5, or None.
    """
    import numpy as np

    from app.services.embedding_service import EmbeddingService

    svc = EmbeddingService()
    orig_vec = await svc.aembed_single(original_prompt)
    opt_vec = await svc.aembed_single(optimized_prompt)
    similarity = float(
        np.dot(orig_vec, opt_vec)
        / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9)
    )

    if similarity < 0.5:
        logger.warning("Sampling intent drift detected: similarity=%.2f", similarity)
        return (
            f"Intent drift detected: semantic similarity {similarity:.2f} "
            f"between original and optimized prompt is below threshold (0.50)"
        )
    return None


async def _fetch_historical_stats() -> dict | None:
    """Fetch score distribution for z-score normalization (non-fatal)."""
    try:
        from app.services.optimization_service import OptimizationService

        async with async_session_factory() as db:
            svc = OptimizationService(db)
            return await svc.get_score_distribution(
                exclude_scoring_modes=["heuristic"],
            )
    except Exception as exc:
        logger.debug("Historical stats unavailable for sampling normalization: %s", exc)
        return None


async def _track_applied_patterns(
    db: Any, opt_id: str, applied_pattern_ids: list[str],
) -> None:
    """Record applied patterns in the OptimizationPattern join table."""
    try:
        from app.models import MetaPattern, OptimizationPattern

        for pid in applied_pattern_ids:
            mp_result = await db.execute(
                select(MetaPattern).where(MetaPattern.id == pid)
            )
            mp = mp_result.scalar_one_or_none()
            if mp:
                db.add(OptimizationPattern(
                    optimization_id=opt_id,
                    cluster_id=mp.cluster_id,
                    meta_pattern_id=mp.id,
                    relationship="applied",
                ))
    except Exception as exc:
        logger.warning("Failed to track applied patterns in sampling: %s", exc)
