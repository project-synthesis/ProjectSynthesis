"""Handler for synthesis_save_result MCP tool.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import logging
import uuid

from mcp.server.fastmcp import Context
from sqlalchemy import select

from app.config import PROMPTS_DIR, settings
from app.database import async_session_factory
from app.models import Optimization
from app.schemas.mcp_models import SaveResultOutput
from app.schemas.pipeline_contracts import DimensionScores
from app.services.event_notification import notify_event_bus
from app.services.heuristic_scorer import HeuristicScorer
from app.services.heuristic_suggestions import generate_heuristic_suggestions
from app.services.pipeline_constants import (
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    VALID_TASK_TYPES,
)
from app.services.preferences import PreferencesService
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.tools._shared import DATA_DIR, get_domain_resolver
from app.utils.text_cleanup import parse_domain, split_prompt_and_changes, title_case_label, validate_intent_label

logger = logging.getLogger(__name__)


async def handle_save_result(
    trace_id: str,
    optimized_prompt: str,
    changes_summary: str | None,
    task_type: str | None,
    strategy_used: str | None,
    scores: dict | None,
    model: str | None,
    codebase_context: str | None,
    ctx: Context | None,
    *,
    domain: str | None = None,
    intent_label: str | None = None,
) -> SaveResultOutput:
    """Persist an optimization result from an external LLM."""
    logger.info("synthesis_save_result called: trace_id=%s model=%s", trace_id, model)

    # Validate output length — reject excessively large prompts early
    if len(optimized_prompt) > settings.MAX_RAW_PROMPT_CHARS:
        raise ValueError(
            f"optimized_prompt too long ({len(optimized_prompt)} chars). "
            f"Maximum is {settings.MAX_RAW_PROMPT_CHARS} characters."
        )

    # Normalize strategy_used — external LLMs often return verbose rationales
    if strategy_used:
        strategy_loader = StrategyLoader(PROMPTS_DIR / "strategies")
        strategy_used = strategy_loader.normalize_strategy(strategy_used)

    # Check scoring preference
    prefs = PreferencesService(DATA_DIR)
    scoring_enabled = prefs.get("pipeline.enable_scoring")
    if scoring_enabled is None:
        scoring_enabled = True

    # Determine scoring mode and compute final scores
    clean_scores: dict[str, float] = {}
    heuristic_flags: list[str] = []
    scoring_mode = "skipped" if not scoring_enabled else "heuristic"

    if scores and scoring_enabled:
        for k, v in scores.items():
            try:
                clean_scores[k] = max(1.0, min(10.0, float(v)))
            except (ValueError, TypeError):
                clean_scores[k] = 5.0

    # Persist — look up pending optimization created by prepare, or create new
    async with async_session_factory() as db:
        result = await db.execute(
            select(Optimization).where(Optimization.trace_id == trace_id)
        )
        opt = result.scalar_one_or_none()

        # Determine strategy compliance
        strategy_compliance = "unknown"
        if opt and opt.strategy_used and strategy_used:
            if opt.strategy_used == strategy_used:
                strategy_compliance = "matched"
            else:
                strategy_compliance = "partial"
                logger.info(
                    "Strategy mismatch: requested=%s, used=%s",
                    opt.strategy_used,
                    strategy_used,
                )
        elif strategy_used:
            strategy_compliance = "matched"

        # Clean external LLM output — strip preambles, fences, meta-headers,
        # and extract changes summary if the caller didn't provide one.
        # Must run BEFORE heuristic scoring so scores reflect clean text.
        optimized_prompt, extracted_changes = split_prompt_and_changes(optimized_prompt)
        if not changes_summary and extracted_changes:
            changes_summary = extracted_changes

        # Compute scores
        heuristic_scores: dict[str, float] = {}
        final_scores: dict[str, float] = {}
        overall: float | None = None
        original_scores: dict[str, float] | None = None
        deltas: dict[str, float] | None = None

        if scoring_enabled:
            heuristic_scores = HeuristicScorer.score_prompt(
                optimized_prompt,
                original=opt.raw_prompt if opt and opt.raw_prompt else None,
            )

            # Fetch historical stats for z-score normalization (shared by
            # both hybrid and original-score blending).
            historical_stats: dict | None = None
            try:
                from app.services.optimization_service import OptimizationService
                opt_svc = OptimizationService(db)
                historical_stats = await opt_svc.get_score_distribution(
                    exclude_scoring_modes=["heuristic", "hybrid_passthrough"],
                )
            except Exception as exc:
                logger.debug("Could not fetch score distribution: %s", exc)

            if clean_scores:
                try:
                    # Build DimensionScores directly from external scores —
                    # no bias correction.  Z-score normalization (inside
                    # blend_scores) and dimension-specific heuristic weights
                    # already guard against LLM overconfidence.
                    ide_scores = DimensionScores.from_dict(clean_scores)
                    blended = blend_scores(
                        ide_scores, heuristic_scores, historical_stats,
                    )
                    opt_dims = blended.to_dimension_scores()
                    final_scores = opt_dims.to_dict()
                    heuristic_flags = blended.divergence_flags or []
                    scoring_mode = "hybrid_passthrough"

                except Exception as exc:
                    logger.warning("Hybrid blending failed, falling back to heuristic: %s", exc)
                    final_scores = heuristic_scores
                    scoring_mode = "heuristic"
            else:
                final_scores = heuristic_scores
                scoring_mode = "heuristic"

            opt_ds = DimensionScores.from_dict(final_scores)
            overall = opt_ds.overall

            # Compute original scores and deltas — both sides must use
            # the same scoring pipeline for symmetric comparison.
            if opt and opt.raw_prompt:
                original_heur = HeuristicScorer.score_prompt(opt.raw_prompt)
                if scoring_mode == "hybrid_passthrough":
                    # Blend originals through the same pipeline as optimized
                    try:
                        blended_orig = blend_scores(
                            DimensionScores.from_dict(original_heur),
                            original_heur, historical_stats,
                        )
                        orig_ds = blended_orig.to_dimension_scores()
                        original_scores = orig_ds.to_dict()
                    except Exception:
                        original_scores = original_heur
                else:
                    original_scores = original_heur
                orig_ds = DimensionScores.from_dict(original_scores)
                deltas = DimensionScores.compute_deltas(orig_ds, opt_ds)

        # Generate heuristic suggestions (zero-LLM)
        from app.services.heuristic_analyzer import HeuristicAnalyzer
        _analyzer = HeuristicAnalyzer()
        raw_for_analysis = opt.raw_prompt if opt and opt.raw_prompt else ""
        _analysis = await _analyzer.analyze(raw_for_analysis, db)
        suggestions = generate_heuristic_suggestions(
            dimension_scores=final_scores,
            weaknesses=_analysis.weaknesses,
            strategy_used=strategy_used or (opt.strategy_used if opt else "auto") or "auto",
        )

        # Truncate codebase context if provided
        context_snapshot = None
        if codebase_context:
            context_snapshot = codebase_context[: settings.MAX_CODEBASE_CONTEXT_CHARS]

        # Extract primary domain for whitelist validation; full string preserved in domain_raw
        domain_primary, _ = parse_domain(domain)

        if opt:
            opt.optimized_prompt = optimized_prompt
            _raw_tt = task_type or opt.task_type or "general"
            opt.task_type = _raw_tt if _raw_tt in VALID_TASK_TYPES else "general"
            opt.strategy_used = strategy_used or opt.strategy_used or "auto"
            opt.changes_summary = changes_summary or ""
            try:
                _resolver = get_domain_resolver()
                # confidence=1.0: passthrough has no analyzer confidence — domain trusted from external LLM or heuristic
                validated_domain = await _resolver.resolve(
                    domain or opt.domain or "general", confidence=1.0,
                )
            except (ValueError, Exception):
                validated_domain = "general"
            opt.domain = validated_domain
            # cluster_id is set asynchronously via optimization_created event → taxonomy hot path
            opt.domain_raw = (domain or opt.domain_raw or "general")[:MAX_DOMAIN_RAW_LENGTH]
            _raw_il = (intent_label or opt.intent_label or "general")[:MAX_INTENT_LABEL_LENGTH]
            opt.intent_label = validate_intent_label(title_case_label(_raw_il), opt.raw_prompt)
            opt.score_clarity = final_scores.get("clarity")
            opt.score_specificity = final_scores.get("specificity")
            opt.score_structure = final_scores.get("structure")
            opt.score_faithfulness = final_scores.get("faithfulness")
            opt.score_conciseness = final_scores.get("conciseness")
            opt.overall_score = overall
            opt.original_scores = original_scores
            opt.score_deltas = deltas
            opt.model_used = model or "external"
            opt.models_by_phase = {"optimize": model or "external"}
            opt.scoring_mode = scoring_mode
            opt.heuristic_flags = heuristic_flags if heuristic_flags else None
            opt.suggestions = suggestions
            opt.status = "completed"
            if context_snapshot:
                opt.codebase_context_snapshot = context_snapshot
            opt_id = opt.id
        else:
            opt_id = str(uuid.uuid4())
            try:
                _new_resolver = get_domain_resolver()
                _new_domain = await _new_resolver.resolve(
                    domain or "general", confidence=1.0,
                )
            except (ValueError, Exception):
                _new_domain = "general"
            opt = Optimization(
                id=opt_id,
                raw_prompt="",
                optimized_prompt=optimized_prompt,
                task_type=(task_type or "general") if (task_type or "general") in VALID_TASK_TYPES else "general",
                strategy_used=strategy_used or "auto",
                changes_summary=changes_summary or "",
                domain=_new_domain,
                domain_raw=(domain or "general")[:MAX_DOMAIN_RAW_LENGTH],
                intent_label=validate_intent_label(
                    title_case_label((intent_label or "general")[:MAX_INTENT_LABEL_LENGTH]),
                    optimized_prompt,  # raw_prompt is empty for new passthrough saves
                ),
                score_clarity=final_scores.get("clarity"),
                score_specificity=final_scores.get("specificity"),
                score_structure=final_scores.get("structure"),
                score_faithfulness=final_scores.get("faithfulness"),
                score_conciseness=final_scores.get("conciseness"),
                overall_score=overall,
                provider="mcp_passthrough",
                routing_tier="passthrough",
                model_used=model or "external",
                models_by_phase={"optimize": model or "external"},
                scoring_mode=scoring_mode,
                heuristic_flags=heuristic_flags if heuristic_flags else None,
                suggestions=suggestions,
                status="completed",
                trace_id=trace_id,
                codebase_context_snapshot=context_snapshot,
            )
            db.add(opt)

        await db.commit()

        await notify_event_bus("optimization_created", {
            "id": opt_id,
            "trace_id": trace_id,
            "task_type": opt.task_type,
            "intent_label": opt.intent_label or "general",
            "domain": opt.domain or "general",
            "domain_raw": opt.domain_raw or "general",
            "strategy_used": opt.strategy_used,
            "overall_score": overall,
            "provider": opt.provider,
            "status": "completed",
        })

    logger.info(
        "synthesis_save_result completed: optimization_id=%s strategy_compliance=%s flags=%d",
        opt_id, strategy_compliance, len(heuristic_flags),
    )

    return SaveResultOutput(
        optimization_id=opt_id,
        scoring_mode=scoring_mode,
        scores={k: round(v, 2) for k, v in final_scores.items()} if final_scores else {},
        original_scores=original_scores,
        score_deltas=deltas,
        overall_score=overall,
        strategy_compliance=strategy_compliance,
        heuristic_flags=heuristic_flags,
        suggestions=suggestions,
    )
