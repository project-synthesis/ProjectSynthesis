"""Sampling-based pipeline — runs optimization phases via MCP sampling/createMessage.

Extracted from mcp_server.py.  When the MCP client supports sampling, these
functions execute the full analyze → optimize → score → suggest pipeline
through the IDE's LLM instead of requiring a local provider.

Key features over a plain text-only sampling call:
- **Structured output via tool calling** — each phase sends a Pydantic-derived
  ``Tool`` schema via ``tools`` + ``tool_choice`` so the IDE returns typed JSON.
  Falls back to text parsing if the client does not support tool calling in
  sampling.
- **Model preferences per phase** — ``ModelPreferences`` hints steer the IDE
  towards the right model class (e.g. Opus for optimize, Haiku for suggest).
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
from mcp.types import (
    CreateMessageResult,
    ModelHint,
    ModelPreferences,
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

from app.config import DATA_DIR, PROMPTS_DIR, settings
from app.database import async_session_factory
from app.models import Optimization
from app.providers.base import LLMProvider, TokenUsage
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    ScoreResult,
    SuggestionsOutput,
)
from app.services.event_notification import notify_event_bus
from app.services.heuristic_scorer import HeuristicScorer
from app.services.pipeline_constants import CODING_KEYWORDS, CONFIDENCE_GATE
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# ---------------------------------------------------------------------------
# Timeout ceiling for individual sampling requests (seconds)
# ---------------------------------------------------------------------------

_SAMPLING_TIMEOUT_SECONDS: float = 120.0

# ---------------------------------------------------------------------------
# Model preference presets per pipeline phase
# ---------------------------------------------------------------------------

_PHASE_PRESETS: dict[str, dict[str, Any]] = {
    "analyze": {"hint": settings.MODEL_SONNET, "intelligence": 0.6, "speed": 0.7},
    "optimize": {"hint": settings.MODEL_OPUS, "intelligence": 0.9, "speed": 0.3},
    "score": {"hint": settings.MODEL_SONNET, "intelligence": 0.6, "speed": 0.7},
    "suggest": {"hint": settings.MODEL_HAIKU, "intelligence": 0.3, "speed": 0.9},
}

# Map user preference names to full model IDs for hints
_PREF_TO_MODEL: dict[str, str] = {
    "sonnet": settings.MODEL_SONNET,
    "opus": settings.MODEL_OPUS,
    "haiku": settings.MODEL_HAIKU,
}


# ---------------------------------------------------------------------------
# Helpers: Pydantic → MCP Tool, model preferences
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


def _resolve_model_preferences(
    phase: str,
    prefs_snapshot: dict | None = None,
) -> ModelPreferences:
    """Map a pipeline phase to ``ModelPreferences`` with model hints.

    Uses the phase preset as baseline.  If a user preference snapshot is
    provided and contains a model selection for the phase, the hint is
    overridden to the user's choice.
    """
    preset = _PHASE_PRESETS.get(phase, _PHASE_PRESETS["analyze"])

    # Determine hint model ID (presets use full model IDs from settings)
    hint_name = preset["hint"]

    # Override from user preferences when available
    if prefs_snapshot:
        pref_key_map = {
            "analyze": "analyzer",
            "optimize": "optimizer",
            "score": "scorer",
            "suggest": None,  # always Haiku
        }
        pref_key = pref_key_map.get(phase)
        if pref_key:
            models_conf = prefs_snapshot.get("models", {})
            user_choice = models_conf.get(pref_key, "")
            # User choice is a short name (e.g. "opus") — map to full ID
            if user_choice in _PREF_TO_MODEL:
                hint_name = _PREF_TO_MODEL[user_choice]

    return ModelPreferences(
        hints=[ModelHint(name=hint_name)],
        intelligencePriority=preset["intelligence"],
        speedPriority=preset["speed"],
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
    model_preferences: ModelPreferences | None = None,
) -> tuple[str, str]:
    """Send a text-only sampling request.  Returns ``(text, model_id)``."""
    kwargs: dict[str, Any] = {
        "messages": [SamplingMessage(role="user", content=TextContent(type="text", text=user))],
        "system_prompt": system,
        "max_tokens": max_tokens,
    }
    if model_preferences is not None:
        kwargs["model_preferences"] = model_preferences

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


def _parse_text_response(text: str, model_cls: type[T]) -> T:
    """Parse a text response into a Pydantic model.

    Tries direct JSON parse, then markdown code-block extraction.
    """
    # Try direct JSON
    try:
        return model_cls.model_validate_json(text)
    except Exception:
        pass

    # Try extracting from code block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return model_cls.model_validate_json(match.group(1))

    raise ValueError(
        f"Cannot parse sampling response as {model_cls.__name__}: {text[:200]}"
    )


async def _sampling_request_structured(
    ctx: Context,
    system: str,
    user: str,
    output_model: type[T],
    *,
    max_tokens: int = 16384,
    model_preferences: ModelPreferences | None = None,
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
        if model_preferences is not None:
            kwargs["model_preferences"] = model_preferences

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

    except (TypeError, AttributeError) as exc:
        # Client doesn't support tools in sampling — fall back to plain text
        logger.info(
            "Structured sampling not supported (client raised %s: %s), falling back to text",
            type(exc).__name__, exc,
        )
        text, model_id = await _sampling_request_plain(
            ctx, system, user,
            max_tokens=max_tokens,
            model_preferences=model_preferences,
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


# ---------------------------------------------------------------------------
# SamplingLLMAdapter — wraps MCP sampling as an LLMProvider for CodebaseExplorer
# ---------------------------------------------------------------------------


class SamplingLLMAdapter(LLMProvider):
    """Minimal ``LLMProvider`` wrapper that delegates to MCP sampling.

    Only ``complete_parsed()`` is implemented — sufficient for
    ``CodebaseExplorer`` which needs a single Haiku synthesis call.

    Note: The ``model`` parameter in ``complete_parsed()`` is intentionally
    ignored.  The adapter always uses Haiku model preferences (the "suggest"
    phase preset), matching ``CodebaseExplorer``'s design assumption.
    """

    name = "mcp_sampling"

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self.last_usage: TokenUsage | None = None

    async def complete_parsed(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        output_format: type[T],
        max_tokens: int = 16384,
        effort: str | None = None,
    ) -> T:
        """Delegate to structured sampling with Haiku preferences."""
        prefs = _resolve_model_preferences("suggest")  # Haiku
        parsed, _model_id = await _sampling_request_structured(
            self._ctx, system_prompt, user_message, output_format,
            max_tokens=max_tokens,
            model_preferences=prefs,
        )
        return parsed


# ---------------------------------------------------------------------------
# Sampling pipeline: full optimize flow
# ---------------------------------------------------------------------------


async def run_sampling_pipeline(
    ctx: Context,
    prompt: str,
    strategy_override: str | None,
    codebase_guidance: str | None,
    *,
    repo_full_name: str | None = None,
    applied_pattern_ids: list[str] | None = None,
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

    context_sources: dict[str, bool] = {
        "explore": False,
        "patterns": False,
        "adaptation": False,
        "workspace": codebase_guidance is not None,
    }

    # Notify: pipeline start
    await notify_event_bus("optimization_start", {
        "trace_id": trace_id,
        "provider": "mcp_sampling",
    })

    # ------------------------------------------------------------------
    # Phase 0: Explore (optional — codebase context injection)
    # ------------------------------------------------------------------
    codebase_context: str | None = None
    explore_enabled = prefs.get("pipeline.enable_explore", prefs_snapshot)

    if explore_enabled and repo_full_name:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "explore", "state": "running",
        })
        phase_t0 = time.monotonic()
        try:
            logger.info("Sampling pipeline Phase 0: Explore")
            codebase_context = await _run_explore_phase(
                ctx, loader, prompt, repo_full_name,
            )
            if codebase_context:
                context_sources["explore"] = True
                logger.info(
                    "Sampling explore context injected (%d chars)",
                    len(codebase_context),
                )
        except Exception as exc:
            logger.warning("Sampling explore failed (non-fatal): %s", exc)
        phase_durations["explore_ms"] = int((time.monotonic() - phase_t0) * 1000)
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "explore",
            "state": "complete" if codebase_context else "skipped",
        })
    else:
        await notify_event_bus("optimization_status", {
            "trace_id": trace_id, "phase": "explore", "state": "skipped",
        })

    # ------------------------------------------------------------------
    # Phase 1: Analyze
    # ------------------------------------------------------------------
    await notify_event_bus("optimization_status", {
        "trace_id": trace_id, "phase": "analyzing", "state": "running",
    })
    phase_t0 = time.monotonic()
    logger.info("Sampling pipeline Phase 1: Analyze")
    available_strategies = strategy_loader.format_available()
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": available_strategies,
    })

    analyze_prefs = _resolve_model_preferences("analyze", prefs_snapshot)
    try:
        analysis, analyze_model = await _sampling_request_structured(
            ctx, system_prompt, analyze_msg, AnalysisResult,
            model_preferences=analyze_prefs,
        )
    except Exception:
        # Last resort fallback
        logger.warning("Structured analysis parsing failed, using fallback")
        text, analyze_model = await _sampling_request_plain(
            ctx, system_prompt, analyze_msg, model_preferences=analyze_prefs,
        )
        try:
            analysis = _parse_text_response(text, AnalysisResult)
        except Exception:
            analysis = AnalysisResult(
                task_type="general",
                weaknesses=["Could not parse analysis"],
                strengths=["Prompt provided"],
                selected_strategy="auto",
                strategy_rationale="Fallback due to parse failure",
                confidence=0.5,
            )
    model_ids["analyze"] = analyze_model
    phase_durations["analyze_ms"] = int((time.monotonic() - phase_t0) * 1000)
    await notify_event_bus("optimization_status", {
        "trace_id": trace_id, "phase": "analyzing", "state": "complete",
    })

    # Semantic check + confidence gate (mirrors pipeline.py)
    confidence = analysis.confidence
    if analysis.task_type == "coding":
        words = set(prompt.lower().split())
        if not words & CODING_KEYWORDS:
            logger.warning(
                "Semantic check: task_type='coding' but no coding keywords in prompt"
            )
            confidence = max(0.0, confidence - 0.2)

    # Domain confidence gate (mirrors pipeline.py — 0.6 threshold)
    effective_domain = getattr(analysis, "domain", None) or "general"
    if confidence < 0.6:
        logger.info("Low confidence (%.2f) — overriding domain to 'general'", confidence)
        effective_domain = "general"

    # Domain mapping (Spec Section 4.2, 4.4)
    domain_raw = effective_domain
    taxonomy_node_id = None

    try:
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy import (
            TaxonomyEngine,
            TaxonomyMapping,  # noqa: F401
        )

        _sampling_engine = TaxonomyEngine(
            embedding_service=EmbeddingService(),
            provider=None,  # sampling pipeline has no local provider
        )
        async with async_session_factory() as _db:
            mapping = await _sampling_engine.map_domain(
                domain_raw=domain_raw,
                db=_db,
                applied_pattern_ids=applied_pattern_ids,
            )
        taxonomy_node_id = mapping.taxonomy_node_id

        if taxonomy_node_id:
            logger.info(
                "Domain mapped (sampling): '%s' -> '%s'",
                domain_raw, mapping.taxonomy_label,
            )
    except Exception as exc:
        logger.warning("Domain mapping failed (sampling, non-fatal): %s", exc)

    effective_strategy = analysis.selected_strategy
    if confidence < CONFIDENCE_GATE and not strategy_override:
        logger.info(
            "Confidence gate triggered (%.2f < %.2f), overriding strategy to 'auto'",
            confidence, CONFIDENCE_GATE,
        )
        effective_strategy = "auto"

    if strategy_override:
        effective_strategy = strategy_override

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
        f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
        f"Strengths: {', '.join(analysis.strengths)}\n"
        f"Strategy: {effective_strategy}\n"
        f"Rationale: {analysis.strategy_rationale}"
    )

    # 4b: Resolve applied meta-patterns
    applied_patterns_text: str | None = None
    if applied_pattern_ids:
        applied_patterns_text = await _resolve_applied_patterns(applied_pattern_ids)
    if applied_patterns_text is not None:
        context_sources["patterns"] = True

    # 4c: Adaptation state
    adaptation_state: str | None = None
    adaptation_enabled = prefs.get("pipeline.enable_adaptation", prefs_snapshot)
    if adaptation_enabled:
        adaptation_state = await _resolve_adaptation_state(analysis.task_type)
    if adaptation_state is not None:
        context_sources["adaptation"] = True

    optimize_msg = loader.render("optimize.md", {
        "raw_prompt": prompt,
        "analysis_summary": analysis_summary,
        "strategy_instructions": strategy_instructions,
        "codebase_guidance": codebase_guidance,
        "codebase_context": codebase_context,
        "adaptation_state": adaptation_state,
        "applied_patterns": applied_patterns_text,
    })

    optimize_prefs = _resolve_model_preferences("optimize", prefs_snapshot)
    try:
        optimization, optimize_model = await _sampling_request_structured(
            ctx, system_prompt, optimize_msg, OptimizationResult,
            model_preferences=optimize_prefs,
        )
    except Exception:
        logger.warning("Structured optimization parsing failed, falling back to text")
        text, optimize_model = await _sampling_request_plain(
            ctx, system_prompt, optimize_msg, model_preferences=optimize_prefs,
        )
        try:
            optimization = _parse_text_response(text, OptimizationResult)
        except Exception:
            optimization = OptimizationResult(
                optimized_prompt=text.strip(),
                changes_summary="Optimized via sampling (raw response)",
                strategy_used=effective_strategy,
            )
    model_ids["optimize"] = optimize_model
    phase_durations["optimize_ms"] = int((time.monotonic() - phase_t0) * 1000)
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

        original_first = random.choice([True, False])
        if original_first:
            prompt_a, prompt_b = prompt, optimization.optimized_prompt
        else:
            prompt_a, prompt_b = optimization.optimized_prompt, prompt

        scorer_msg = (
            f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
            f"<prompt-b>\n{prompt_b}\n</prompt-b>"
        )

        score_prefs = _resolve_model_preferences("score", prefs_snapshot)
        scores: ScoreResult | None = None
        try:
            scores, score_model = await _sampling_request_structured(
                ctx, scoring_system, scorer_msg, ScoreResult,
                model_preferences=score_prefs,
            )
            model_ids["score"] = score_model
        except Exception:
            scoring_mode = "heuristic"
            logger.warning("Score parsing failed, falling back to heuristic-only")

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

            if blended_optimized.divergence_flags:
                warnings.append(
                    "Score divergence between LLM and heuristic on: "
                    + ", ".join(blended_optimized.divergence_flags)
                )

        phase_durations["score_ms"] = int((time.monotonic() - phase_t0) * 1000)
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
            })
            suggest_prefs = _resolve_model_preferences("suggest", prefs_snapshot)
            suggest_result, suggest_model = await _sampling_request_structured(
                ctx, system_prompt, suggest_msg, SuggestionsOutput,
                max_tokens=2048,
                model_preferences=suggest_prefs,
            )
            suggestions = suggest_result.suggestions
            model_ids["suggest"] = suggest_model
            logger.info("Sampling suggestions generated: %d items", len(suggestions))
        except Exception as exc:
            logger.warning("Sampling suggestion generation failed (non-fatal): %s", exc)
        phase_durations["suggest_ms"] = int((time.monotonic() - phase_t0) * 1000)
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

    async with async_session_factory() as db:
        db_opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt=optimization.optimized_prompt,
            task_type=analysis.task_type,
            intent_label=getattr(analysis, "intent_label", None) or "general",
            domain=effective_domain,
            domain_raw=domain_raw,
            taxonomy_node_id=taxonomy_node_id,
            strategy_used=effective_strategy,
            changes_summary=optimization.changes_summary,
            score_clarity=optimized_scores.clarity if optimized_scores else None,
            score_specificity=optimized_scores.specificity if optimized_scores else None,
            score_structure=optimized_scores.structure if optimized_scores else None,
            score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
            score_conciseness=optimized_scores.conciseness if optimized_scores else None,
            overall_score=optimized_scores.overall if optimized_scores else None,
            provider="mcp_sampling",
            model_used=model_ids.get("optimize", "unknown"),
            scoring_mode=scoring_mode,
            duration_ms=elapsed_ms,
            tokens_by_phase=phase_durations,
            context_sources=context_sources,
            status="completed",
            trace_id=trace_id,
            original_scores=original_scores.model_dump() if original_scores else None,
            score_deltas=deltas,
        )
        db.add(db_opt)

        # Track applied patterns in join table
        if applied_pattern_ids:
            await _track_applied_patterns(db, opt_id, applied_pattern_ids)

        await db.commit()

    # Notify backend event bus (MCP runs in a separate process)
    await notify_event_bus("optimization_created", {
        "id": opt_id,
        "trace_id": trace_id,
        "task_type": analysis.task_type,
        "intent_label": getattr(analysis, "intent_label", None) or "general",
        "domain": effective_domain,
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
        "suggestions": suggestions,
        "warnings": warnings,
        "intent_label": getattr(analysis, "intent_label", None) or "general",
        "domain": effective_domain,
    }


# ---------------------------------------------------------------------------
# Sampling analyze: standalone analysis + baseline scoring
# ---------------------------------------------------------------------------


async def run_sampling_analyze(ctx: Context, prompt: str) -> dict:
    """Two-phase sampling pipeline: analyze + baseline score.

    Used by ``synthesis_analyze`` when no local LLM provider is available
    but the MCP client supports sampling.
    """
    start = time.monotonic()
    loader = PromptLoader(PROMPTS_DIR)
    strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")

    prefs = PreferencesService(DATA_DIR)
    prefs_snapshot = prefs.load()

    phase_durations: dict[str, int] = {}
    context_sources: dict[str, bool] = {
        "explore": False,
        "patterns": False,
        "adaptation": False,
        "workspace": False,
    }

    # --- Phase 1: Analyze ---
    phase_t0 = time.monotonic()
    system_prompt = loader.load("agent-guidance.md")
    analyze_msg = loader.render("analyze.md", {
        "raw_prompt": prompt,
        "available_strategies": strategy_loader.format_available(),
    })

    analyze_prefs = _resolve_model_preferences("analyze", prefs_snapshot)
    analysis, _analyze_model = await _sampling_request_structured(
        ctx, system_prompt, analyze_msg, AnalysisResult,
        model_preferences=analyze_prefs,
    )

    analyze_ms = int((time.monotonic() - phase_t0) * 1000)
    phase_durations["analyze_ms"] = analyze_ms
    logger.info(
        "Sampling analyze Phase 1 complete in %dms: task_type=%s strategy=%s",
        analyze_ms, analysis.task_type, analysis.selected_strategy,
    )

    # Domain confidence gate (mirrors pipeline.py)
    effective_domain = getattr(analysis, "domain", None) or "general"
    if analysis.confidence < 0.6:
        logger.info("Low confidence (%.2f) — overriding domain to 'general'", analysis.confidence)
        effective_domain = "general"

    # Domain mapping (Spec Section 4.2, 4.4)
    domain_raw = effective_domain
    taxonomy_node_id: str | None = None

    try:
        from app.services.embedding_service import EmbeddingService
        from app.services.taxonomy import (
            TaxonomyEngine,
            TaxonomyMapping,  # noqa: F401
        )

        _sampling_engine = TaxonomyEngine(
            embedding_service=EmbeddingService(),
            provider=None,  # sampling pipeline has no local provider
        )
        async with async_session_factory() as _db:
            mapping = await _sampling_engine.map_domain(
                domain_raw=domain_raw,
                db=_db,
                applied_pattern_ids=None,
            )
        taxonomy_node_id = mapping.taxonomy_node_id

        if taxonomy_node_id:
            logger.info(
                "Domain mapped (sampling/analyze): '%s' -> '%s'",
                domain_raw, mapping.taxonomy_label,
            )
    except Exception as exc:
        logger.warning("Domain mapping failed (sampling/analyze, non-fatal): %s", exc)

    # --- Phase 2: Baseline score ---
    phase_t0 = time.monotonic()
    scoring_system = loader.load("scoring.md")
    scorer_msg = (
        f"<prompt-a>\n{prompt}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt}\n</prompt-b>"
    )

    # Compute heuristic scores once (used in both try and except branches)
    heur_scores = HeuristicScorer.score_prompt(prompt)

    score_prefs = _resolve_model_preferences("score", prefs_snapshot)
    try:
        score_result, _score_model = await _sampling_request_structured(
            ctx, scoring_system, scorer_msg, ScoreResult,
            model_preferences=score_prefs,
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

    overall = baseline.overall
    total_ms = int((time.monotonic() - start) * 1000)

    # --- Persist ---
    opt_id = str(uuid.uuid4())
    trace_id = str(uuid.uuid4())

    async with async_session_factory() as db:
        opt = Optimization(
            id=opt_id,
            raw_prompt=prompt,
            optimized_prompt="",
            task_type=analysis.task_type,
            intent_label=getattr(analysis, "intent_label", None) or "general",
            domain=effective_domain,
            domain_raw=domain_raw,
            taxonomy_node_id=taxonomy_node_id,
            strategy_used=analysis.selected_strategy,
            changes_summary="",
            score_clarity=baseline.clarity,
            score_specificity=baseline.specificity,
            score_structure=baseline.structure,
            score_faithfulness=baseline.faithfulness,
            score_conciseness=baseline.conciseness,
            overall_score=overall,
            provider="mcp_sampling",
            model_used=_analyze_model,
            scoring_mode="baseline",
            status="analyzed",
            trace_id=trace_id,
            duration_ms=total_ms,
            tokens_by_phase=phase_durations,
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


async def _run_explore_phase(
    ctx: Context,
    loader: PromptLoader,
    prompt: str,
    repo_full_name: str,
) -> str | None:
    """Run explore phase using sampling as the LLM backend.

    Resolves GitHub token from DB, creates a SamplingLLMAdapter, and
    delegates to CodebaseExplorer.  Non-fatal: returns None on any error.
    """
    from app.models import GitHubToken, LinkedRepo
    from app.services.codebase_explorer import CodebaseExplorer
    from app.services.embedding_service import EmbeddingService
    from app.services.github_client import GitHubClient
    from app.services.github_service import GitHubService

    async with async_session_factory() as db:
        # Find linked repo to get session_id
        result = await db.execute(
            select(LinkedRepo).where(LinkedRepo.full_name == repo_full_name).limit(1)
        )
        linked = result.scalar_one_or_none()
        if not linked:
            logger.info("Explore skipped: repo %s not linked", repo_full_name)
            return None

        # Get encrypted token
        token_result = await db.execute(
            select(GitHubToken).where(GitHubToken.session_id == linked.session_id).limit(1)
        )
        token_row = token_result.scalar_one_or_none()
        if not token_row:
            logger.info("Explore skipped: no GitHub token for session %s", linked.session_id)
            return None

        token = GitHubService.decrypt_token(token_row.token_encrypted)

    adapter = SamplingLLMAdapter(ctx)
    explorer = CodebaseExplorer(
        prompt_loader=loader,
        github_client=GitHubClient(),
        embedding_service=EmbeddingService(),
        provider=adapter,
    )
    return await explorer.explore(
        raw_prompt=prompt,
        repo_full_name=repo_full_name,
        branch="main",
        token=token,
    )


async def _resolve_applied_patterns(
    applied_pattern_ids: list[str],
) -> str | None:
    """Resolve meta-pattern texts and increment family usage counts."""
    try:
        from app.models import MetaPattern, PatternFamily

        async with async_session_factory() as db:
            result = await db.execute(
                select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
            )
            patterns = result.scalars().all()
            if not patterns:
                return None

            lines = [f"- {p.pattern_text}" for p in patterns]
            applied_text = (
                "The following proven patterns from past optimizations "
                "should be applied where relevant:\n"
                + "\n".join(lines)
            )

            # Propagate usage counts up the taxonomy tree (Spec 7.8)
            family_ids = {p.family_id for p in patterns}
            try:
                from app.services.embedding_service import EmbeddingService
                from app.services.taxonomy import TaxonomyEngine

                _engine = TaxonomyEngine(embedding_service=EmbeddingService())
                for fid in family_ids:
                    await _engine.increment_usage(fid, db)
            except Exception as usage_exc:
                logger.warning("Sampling usage propagation failed: %s", usage_exc)
                # Fallback: at least increment the family directly
                for fid in family_ids:
                    fam_result = await db.execute(
                        select(PatternFamily).where(PatternFamily.id == fid)
                    )
                    fam = fam_result.scalar_one_or_none()
                    if fam:
                        fam.usage_count = (fam.usage_count or 0) + 1

            await db.commit()

            logger.info(
                "Sampling: injecting %d applied patterns from %d families",
                len(patterns), len(family_ids),
            )
            return applied_text
    except Exception as exc:
        logger.warning("Failed to resolve applied patterns in sampling: %s", exc)
        return None


async def _resolve_adaptation_state(task_type: str) -> str | None:
    """Render adaptation state for the given task type."""
    try:
        from app.services.adaptation_tracker import AdaptationTracker

        async with async_session_factory() as db:
            tracker = AdaptationTracker(db)
            return await tracker.render_adaptation_state(task_type)
    except Exception as exc:
        logger.warning("Failed to resolve adaptation state in sampling: %s", exc)
        return None


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
                    family_id=mp.family_id,
                    meta_pattern_id=mp.id,
                    relationship="applied",
                ))
    except Exception as exc:
        logger.warning("Failed to track applied patterns in sampling: %s", exc)
