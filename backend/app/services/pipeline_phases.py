"""Pipeline phase helpers — extracted from ``pipeline.py`` (Phase 3D).

Splits the monolithic ``PipelineOrchestrator.run()`` method into cohesive
units that can be reasoned about and tested independently:

* ``resolve_blocked_strategies`` — pre-analyze DB scan for feedback-blocked
  strategies
* ``resolve_post_analyze_state`` — post-analyze domain resolution +
  classification agreement + taxonomy mapping + strategy recommendation
* ``build_optimize_context`` — applied patterns + few-shot retrieval +
  optimize message rendering
* ``run_hybrid_scoring`` — A/B randomized scoring LLM call + hybrid blend
  + drift check
* ``persist_and_commit`` — build Optimization row, track applied patterns,
  commit, propagate usage counts, publish ``optimization_created`` event

The orchestrator retains the async-generator shell that yields SSE events
before and after each LLM call.  Helpers are plain ``async def``
functions that take a ``PipelineRunContext`` dataclass holding shared
state.

Copyright 2025-2026 Project Synthesis contributors.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Optimization
from app.providers.base import LLMProvider, TokenUsage
from app.schemas.pipeline_contracts import (
    AnalysisResult,
    DimensionScores,
    OptimizationResult,
    PipelineResult,
    ScoreResult,
    SuggestionsOutput,
    get_dimension_weights,
)
from app.services.classification_agreement import get_classification_agreement
from app.services.domain_detector import enrich_domain_qualifier
from app.services.heuristic_scorer import HeuristicScorer
from app.services.pattern_injection import (
    InjectedPattern,
    format_injected_patterns,
    record_injection_provenance,
)
from app.services.pipeline_constants import (
    AMBIGUOUS_WRITING_LEAD_VERBS,
    MAX_DOMAIN_RAW_LENGTH,
    MAX_INTENT_LABEL_LENGTH,
    PROSE_OUTPUT_CUES,
    SCORE_MAX_TOKENS,
    VALID_TASK_TYPES,
    WRITING_LEAD_VERBS,
    resolve_effective_strategy,
    semantic_check,
    semantic_upgrade_general,
)
from app.services.preferences import PreferencesService
from app.services.prompt_loader import PromptLoader
from app.services.score_blender import blend_scores
from app.services.strategy_loader import StrategyLoader
from app.services.task_type_classifier import (
    rescue_task_type_via_structural_evidence,
)
from app.services.trace_logger import TraceLogger
from app.utils.text_cleanup import title_case_label, validate_intent_label

logger = logging.getLogger(__name__)

# B5+ task-type lock vocab lives in ``pipeline_constants`` so the lock
# (here) and the trim (in ``context_enrichment``) cannot drift.  See the
# ``B5 / B5+ writing-about-code path`` block in that module.


def _normalize_llm_domain(domain: str, known_primaries: set[str]) -> str:
    """Reconcile LLM-output domain styles into canonical ``primary: qualifier``.

    The analyze.md prompt instructs the LLM in two parallel ways:
      - ``primary: qualifier`` colon syntax for cross-cutting concerns on a
        known domain (``"backend: auth middleware"``).
      - ``primary-qualifier`` hyphen syntax for invented sub-domains
        (``"backend-auth"``, ``"data-ml"``).

    The hyphen variant is misparsed downstream because :func:`parse_domain`
    splits only on ``:``, so ``"backend-observability"`` becomes a brand-new
    primary instead of a backend qualifier. This caused cycle-3 prompt #7
    (score 9.0) to land under ``general`` with the never-before-seen
    ``backend-observability`` domain string instead of joining the existing
    backend subtree.

    Resolution: when a hyphen is present and the prefix matches a known
    primary domain (registered in :class:`DomainResolver`), rewrite to
    canonical colon syntax. Untouched if no hyphen, prefix unknown, or
    colon is already present.

    Args:
        domain: LLM-output domain string.
        known_primaries: Set of currently-registered top-level domain
            labels (lower-case) from :attr:`DomainResolver.domain_labels`.

    Returns:
        Canonicalized domain string. Idempotent.
    """
    if not domain or ":" in domain or "-" not in domain:
        return domain
    primary, _, qualifier = domain.partition("-")
    primary = primary.strip().lower()
    # Lowercase the qualifier too for parity with parse_domain() and the
    # rest of the domain pipeline (every other place stores qualifiers
    # lowercase). Without this ``Backend-OBSERVABILITY`` would emerge as
    # ``backend: OBSERVABILITY`` and break the lower-case lookup contract.
    qualifier = qualifier.strip().lower()
    if primary in known_primaries and qualifier:
        return f"{primary}: {qualifier}"
    return domain


# ---------------------------------------------------------------------------
# Shared phase result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class PostAnalyzeState:
    """Resolved state after the analyze LLM call finishes.

    Contains everything downstream phases need: effective domain/strategy,
    cluster mapping, precomputed prompt embedding, and data-recommended
    strategy.  Confidence is kept so downstream observability can surface
    it alongside the semantic check.
    """

    confidence: float
    effective_task_type: str
    effective_domain: str
    domain_raw: str
    cluster_id: str | None
    taxonomy_label: str | None
    taxonomy_breadcrumb: list[str]
    prompt_embedding: list[float] | None
    data_recommendation: Any
    effective_strategy: str


@dataclass
class OptimizeContextBundle:
    """Rendered optimize message plus metadata for trace/context logging.

    ``context_updates`` is a dict that the orchestrator merges into its
    own ``context_sources`` — avoids pushing mutation into the helper.
    """

    optimize_msg: str
    applied_patterns_text: str | None
    few_shot_text: str | None
    context_updates: dict[str, Any]
    dynamic_max_tokens: int


@dataclass
class ScoringOutput:
    """Hybrid scoring result ready for persistence + event emission."""

    llm_original_scores: Any
    llm_optimized_scores: Any
    original_scores: DimensionScores
    optimized_scores: DimensionScores
    deltas: dict[str, float]
    divergence_flags: list[str]
    warnings: list[str]
    blended_raw_llm: dict[str, Any]
    blended_raw_heuristic: dict[str, Any]
    normalization_applied: bool
    score_duration_ms: int
    score_model: str
    # C4: deterministic heuristic-only baseline.  Stable across runs
    # because it never touches the LLM.  Used as the canonical anchor
    # for delta and improvement_score computation.  Optional (last) to
    # keep legacy callers / tests that hand-construct ScoringOutput
    # working without code change.
    heuristic_baseline_scores: DimensionScores | None = None


# ---------------------------------------------------------------------------
# Provider helpers (duplicated thin wrappers used by extracted helpers)
# ---------------------------------------------------------------------------


def _get_provider_usage(provider: LLMProvider) -> TokenUsage:
    """Return last token usage from provider, or zeros if unavailable."""
    usage = getattr(provider, "last_usage", None)
    if isinstance(usage, TokenUsage):
        return usage
    return TokenUsage()


# ---------------------------------------------------------------------------
# Phase 0.5 — Pre-analyze strategy blocking
# ---------------------------------------------------------------------------


async def resolve_blocked_strategies(
    db: AsyncSession, *, enabled: bool, strategy_override: str | None,
) -> set[str]:
    """Scan feedback history for strategies with low approval rates.

    Runs before the analyzer so the analyzer's ``available_strategies``
    list excludes anything the user has consistently disliked.  Returns
    an empty set when strategy intelligence is disabled or when the
    caller has already pinned a strategy via ``strategy_override``.
    """
    if not enabled or strategy_override:
        return set()

    blocked: set[str] = set()
    try:
        from sqlalchemy import select as sa_select

        from app.models import StrategyAffinity
        from app.services.adaptation_tracker import AdaptationTracker
        result = await db.execute(sa_select(StrategyAffinity))
        all_rows = result.scalars().all()
        by_strategy: dict[str, list[float]] = {}
        by_strategy_total: dict[str, int] = {}
        for row in all_rows:
            total = (row.thumbs_up or 0) + (row.thumbs_down or 0)
            if total >= AdaptationTracker._MIN_FEEDBACK_FOR_GATE:
                by_strategy.setdefault(row.strategy, []).append(row.approval_rate)
                by_strategy_total[row.strategy] = (
                    by_strategy_total.get(row.strategy, 0) + total
                )
        for strat, rates in by_strategy.items():
            avg = sum(rates) / len(rates)
            if avg < AdaptationTracker._BLOCK_THRESHOLD:
                blocked.add(strat)
                logger.info(
                    "Strategy '%s' blocked pre-analysis: avg_approval=%.2f across %d task types",
                    strat, avg, len(rates),
                )
    except Exception as exc:
        logger.debug("Adaptation pre-filter unavailable: %s", exc)
    return blocked


# ---------------------------------------------------------------------------
# Phase 1.5 — Post-analyze orchestration
# ---------------------------------------------------------------------------


async def resolve_post_analyze_state(
    *,
    raw_prompt: str,
    analysis: AnalysisResult,
    db: AsyncSession,
    strategy_loader: StrategyLoader,
    domain_resolver: Any | None,
    taxonomy_engine: Any | None,
    strategy_override: str | None,
    blocked_strategies: set[str],
    heuristic_task_type: str | None,
    heuristic_domain: str | None,
    applied_pattern_ids: list[str] | None,
    trace_id: str,
) -> PostAnalyzeState:
    """Resolve domain + taxonomy mapping + strategy from analyzer output.

    Runs between the analyzer LLM call and the optimizer.  Pure async
    orchestration: no event yields, no provider calls, only the
    domain-resolver and taxonomy-engine side channels.
    """
    # Semantic check + domain confidence gate
    confidence = semantic_check(analysis.task_type, raw_prompt, analysis.confidence)

    # Upgrade "general" to a specific type when strong keywords are present
    effective_task_type = semantic_upgrade_general(analysis.task_type, raw_prompt)
    if effective_task_type != analysis.task_type:
        analysis.task_type = effective_task_type  # type: ignore[assignment]

    # Rescue creative/writing → coding when the prompt has structural code
    # evidence (snake_case, PascalCase+separator, technical nouns). The
    # classifier's `creative` signals (`create:0.5`, `design:0.7`,
    # `concept:0.6`) are deliberately broad so prose creativity prompts route
    # correctly, but they collide with code vocabulary. Structural evidence
    # in the first sentence beats semantic vibes — same B2 philosophy used by
    # the enrichment-profile rescue.
    try:
        rescued, reason = rescue_task_type_via_structural_evidence(
            analysis.task_type, raw_prompt,
        )
        if reason:
            logger.info(
                "Task-type rescue: %s trace_id=%s",
                reason, trace_id,
            )
            analysis.task_type = rescued  # type: ignore[assignment]
            effective_task_type = rescued
    except Exception:
        logger.debug(
            "Task-type rescue failed (non-fatal) trace_id=%s",
            trace_id, exc_info=True,
        )

    # B5+ task-type LOCK (cycle-10 + cycle-11 forensics): when the
    # prompt's LEAD VERB is unambiguously a prose-writing verb AND the
    # LLM analyzer flipped to ``coding`` (because of inline code refs in
    # the body), prefer ``writing`` so the writing-rubric scoring +
    # role-playing strategy stays in effect.  The lead verb is the
    # user's clearest intent signal — far more reliable than the
    # heuristic's task-type vote on prompts that mix writing intent
    # with technical content.
    #
    # Cycle-11 finding (the original guard ``heuristic_task_type IN
    # writing/creative`` was too restrictive): on a prompt like
    # ``Write a brief reference page for `POST /api/clusters/match` ...``,
    # the heuristic classified as ``analysis`` (not ``writing``) because
    # technical-noun density beat the single ``Write`` keyword.  LLM
    # then flipped to ``coding``, and the lock didn't engage.  Result:
    # writing intent bypassed, length budget bloated to 3.9× (writing
    # tasks should stay tight), conciseness dropped to 6.1.
    #
    # See ``pipeline_constants.WRITING_LEAD_VERBS`` /
    # ``AMBIGUOUS_WRITING_LEAD_VERBS`` / ``PROSE_OUTPUT_CUES`` for the lock
    # vocabulary + compound-verb guard (shared with the codebase trim in
    # ``context_enrichment.enrich``).
    if analysis.task_type == "coding" and raw_prompt.strip():
        _first_word = raw_prompt.strip().split()[0].lower().strip(".,;:!?")
        if _first_word in WRITING_LEAD_VERBS:
            _lock = _first_word not in AMBIGUOUS_WRITING_LEAD_VERBS
            if not _lock:
                # Disambiguate "Write" via first-sentence prose-output cue.
                _first_sentence_lower = (
                    raw_prompt.split(".")[0] if "." in raw_prompt else raw_prompt
                ).lower()
                _lock = any(cue in _first_sentence_lower for cue in PROSE_OUTPUT_CUES)
            if _lock:
                logger.info(
                    "B5+ task-type lock: LLM said coding but lead verb '%s' "
                    "indicates writing intent (heuristic was %s) — preferring "
                    "writing. trace_id=%s",
                    _first_word, heuristic_task_type, trace_id,
                )
                analysis.task_type = "writing"  # type: ignore[assignment]
                effective_task_type = "writing"

    # Phase 1.5: Post-LLM domain reconciliation — MUST run BEFORE
    # ``domain_resolver.resolve()`` so the resolver sees the canonical
    # form. Two transforms layered:
    #
    #   1. Hyphen-style sub-domains (``"backend-observability"``) get
    #      misparsed by :func:`parse_domain` as new primaries — caught here
    #      by :func:`_normalize_llm_domain` against the live domain registry.
    #   2. The LLM frequently returns a bare primary (``"backend"``) even
    #      when tracing/instrumentation/observability dominate the prompt's
    #      first sentence. Reason: the LLM hasn't seen the organic
    #      Haiku-generated qualifier vocabulary; the heuristic analyzer has,
    #      but its qualifier-enriched output was previously discarded.
    #
    # Ordering matters: if reconciliation ran AFTER ``resolver.resolve()``
    # (the cycle-3-fix v1 layout), ``effective_domain`` would lock in from
    # the un-normalized string and downstream consumers (E1 agreement
    # tracking, ``Optimization.domain``, strategy-intelligence keys) would
    # diverge from ``domain_raw``. Code-review SEV-MAJOR caught this; the
    # fix is purely a sequencing change — the transforms themselves are
    # unchanged.
    if domain_resolver is not None:
        original_domain = analysis.domain or "general"
        normalized = _normalize_llm_domain(
            original_domain,
            domain_resolver.domain_labels,
        )
        if normalized != original_domain:
            logger.info(
                "Hyphenated sub-domain normalized: '%s' → '%s' trace_id=%s",
                original_domain, normalized, trace_id,
            )
        analysis.domain = normalized
    if analysis.domain and ":" not in analysis.domain:
        try:
            enriched = enrich_domain_qualifier(
                analysis.domain, raw_prompt.lower(),
            )
            if enriched != analysis.domain:
                logger.info(
                    "Post-LLM qualifier enrichment: '%s' → '%s' trace_id=%s",
                    analysis.domain, enriched, trace_id,
                )
                analysis.domain = enriched
        except Exception:
            logger.debug(
                "Post-LLM qualifier enrichment failed (non-fatal) trace_id=%s",
                trace_id, exc_info=True,
            )

    logger.info(
        "Domain resolution: raw='%s' confidence=%.2f (analyzer=%.2f) trace_id=%s",
        analysis.domain, confidence, analysis.confidence, trace_id,
    )

    if domain_resolver is not None:
        effective_domain = await domain_resolver.resolve(
            analysis.domain or "general", confidence, raw_prompt=raw_prompt,
        )
        logger.info(
            "Domain resolved: '%s' → '%s' trace_id=%s",
            analysis.domain, effective_domain, trace_id,
        )
    else:
        effective_domain = "general"

    # E1: Heuristic vs LLM classification agreement tracking — runs
    # AFTER resolver so ``effective_domain`` reflects the resolved label.
    if heuristic_task_type is not None:
        try:
            get_classification_agreement().record(
                heuristic_task_type=heuristic_task_type,
                heuristic_domain=heuristic_domain or "general",
                llm_task_type=effective_task_type,
                llm_domain=effective_domain,
                prompt_snippet=raw_prompt[:80],
            )
        except Exception:
            logger.debug("Classification agreement tracking failed", exc_info=True)

    # Phase 1.5b: Domain mapping via taxonomy engine
    domain_raw = (analysis.domain or "general")[:MAX_DOMAIN_RAW_LENGTH]
    cluster_id: str | None = None
    taxonomy_label: str | None = None
    taxonomy_breadcrumb: list[str] = []
    try:
        from app.services.taxonomy import TaxonomyMapping

        if taxonomy_engine is not None:
            mapping: TaxonomyMapping = await taxonomy_engine.map_domain(
                domain_raw=domain_raw,
                db=db,
                applied_pattern_ids=applied_pattern_ids,
            )
            cluster_id = mapping.cluster_id
            taxonomy_label = mapping.taxonomy_label
            taxonomy_breadcrumb = mapping.taxonomy_breadcrumb

            if cluster_id:
                logger.info(
                    "Domain mapped: '%s' -> node '%s' (%s) trace_id=%s",
                    domain_raw, taxonomy_label,
                    " > ".join(taxonomy_breadcrumb), trace_id,
                )
            else:
                logger.info(
                    "Domain unmapped: '%s' (below alignment floor) trace_id=%s",
                    domain_raw, trace_id,
                )
        else:
            logger.debug("Taxonomy engine not available — skipping domain mapping")
    except Exception as exc:
        logger.warning(
            "Domain mapping failed (non-fatal): %s trace_id=%s",
            exc, trace_id,
        )

    # Pre-compute prompt embedding once for all downstream consumers
    prompt_embedding: list[float] | None = None
    try:
        from app.services.embedding_service import EmbeddingService as _EmbSvc

        prompt_embedding = await _EmbSvc().aembed_single(raw_prompt)
    except Exception as emb_exc:
        logger.warning(
            "Prompt embedding failed (downstream consumers will re-embed independently): "
            "%s trace_id=%s",
            emb_exc, trace_id,
        )

    # Score-informed strategy recommendation from historical data
    data_recommendation = None
    try:
        from app.services.pipeline_constants import recommend_strategy_from_history

        data_recommendation = await recommend_strategy_from_history(
            raw_prompt=raw_prompt,
            db=db,
            available_strategies=strategy_loader.list_strategies(),
            trace_id=trace_id,
            prompt_embedding=prompt_embedding,
        )
    except Exception:
        logger.debug("Strategy recommendation unavailable. trace_id=%s", trace_id)

    effective_strategy = resolve_effective_strategy(
        selected_strategy=analysis.selected_strategy,
        available=strategy_loader.list_strategies(),
        blocked_strategies=blocked_strategies,
        confidence=confidence,
        strategy_override=strategy_override,
        trace_id=trace_id,
        data_recommendation=data_recommendation,
        task_type=analysis.task_type,
        intent_label=analysis.intent_label,
    )

    return PostAnalyzeState(
        confidence=confidence,
        effective_task_type=effective_task_type,
        effective_domain=effective_domain,
        domain_raw=domain_raw,
        cluster_id=cluster_id,
        taxonomy_label=taxonomy_label,
        taxonomy_breadcrumb=taxonomy_breadcrumb,
        prompt_embedding=prompt_embedding,
        data_recommendation=data_recommendation,
        effective_strategy=effective_strategy,
    )


# ---------------------------------------------------------------------------
# Phase 2 setup — optimize message builder
# ---------------------------------------------------------------------------


async def build_optimize_context(
    *,
    raw_prompt: str,
    analysis: AnalysisResult,
    effective_strategy: str,
    effective_domain: str,
    prompt_loader: PromptLoader,
    strategy_loader: StrategyLoader,
    db: AsyncSession,
    applied_pattern_ids: list[str] | None,
    auto_injected_patterns: list[InjectedPattern],
    codebase_context: str | None,
    strategy_intelligence: str | None,
    divergence_alerts: str | None,
    prompt_embedding: list[float] | None,
    trace_id: str,
) -> OptimizeContextBundle:
    """Render the optimize user message with all context blocks merged.

    Resolves explicit applied patterns from the knowledge graph, merges
    them with auto-injected patterns, retrieves few-shot examples, and
    renders ``optimize.md``.  Returns the rendered message plus
    ``context_updates`` for the orchestrator to merge into its SSE
    payload.
    """
    strategy_instructions = strategy_loader.load(effective_strategy)

    # C2: surface original-prompt heuristics (length + conciseness) to the
    # optimizer.  Without these signals the optimizer always elaborates,
    # producing 10x expansions of already-concise prompts that score badly
    # on conciseness.  The heuristic conciseness scorer is regex/word-count —
    # cheap, deterministic, no LLM call.  Tier the expansion advice so the
    # optimizer has explicit guidance, not just "scale to task type" prose.
    try:
        from app.services.heuristic_scorer import HeuristicScorer
        _heur_baseline = HeuristicScorer.score_prompt(raw_prompt)
        _orig_conc = float(_heur_baseline.get("conciseness", 5.0))
    except Exception:
        _orig_conc = 5.0
    _orig_len = len(raw_prompt)

    if _orig_conc >= 7.0 and _orig_len < 500:
        _expansion_advice = (
            f"Length budget: original is {_orig_len} chars with conciseness="
            f"{_orig_conc:.1f}/10 — already terse and well-formed. Aim for "
            f"≤3× expansion ({_orig_len * 3} chars max). Sharpen vocabulary, "
            f"deduce one load-bearing implication, and STOP — do not "
            f"scaffold into ## Why this matters / ## Deliverables / ## "
            f"Constraints sections. BUT keep one bullet list when there "
            f"are ≥3 distinct constraints (a flat paragraph forfeits "
            f"~3 points on the structure scoring dimension; a single "
            f"`- bullet` list reclaims them at minimal length cost)."
        )
    elif _orig_conc >= 6.0 and _orig_len < 1000:
        _expansion_advice = (
            f"Length budget: original is {_orig_len} chars with conciseness="
            f"{_orig_conc:.1f}/10 — moderately concise. Aim for ≤5× expansion "
            f"({_orig_len * 5} chars max) unless task type genuinely demands "
            f"high depth (specs, multi-concern features). Use one "
            f"`##` header + one bullet list, not a 5-section RFP."
        )
    else:
        _expansion_advice = (
            f"Length budget: original is {_orig_len} chars with conciseness="
            f"{_orig_conc:.1f}/10 — verbose or under-structured. Restructure "
            f"freely; expansion ratio is not the constraint here."
        )

    analysis_summary = (
        f"Task type: {analysis.task_type}\n"
        f"Domain: {effective_domain}\n"
        f"Weaknesses: {', '.join(analysis.weaknesses)}\n"
        f"Strengths: {', '.join(analysis.strengths)}\n"
        f"Strategy: {effective_strategy}\n"
        f"Rationale: {analysis.strategy_rationale}\n"
        f"{_expansion_advice}"
    )

    applied_patterns_text: str | None = None
    if applied_pattern_ids:
        try:
            from app.models import MetaPattern

            mp_q_result = await db.execute(
                select(MetaPattern).where(MetaPattern.id.in_(applied_pattern_ids))
            )
            patterns = mp_q_result.scalars().all()
            if patterns:
                lines = [f"- {p.pattern_text}" for p in patterns]
                applied_patterns_text = (
                    "The following proven patterns from past optimizations "
                    "should be applied where relevant:\n"
                    + "\n".join(lines)
                )
                logger.info(
                    "Injecting %d applied patterns into optimizer context. trace_id=%s",
                    len(patterns), trace_id,
                )
        except Exception as exc:
            logger.warning("Failed to resolve applied patterns: %s", exc)

    applied_patterns_text = format_injected_patterns(
        auto_injected_patterns, applied_patterns_text,
    )

    few_shot_text: str | None = None
    context_updates: dict[str, Any] = {}
    try:
        from app.services.pattern_injection import (
            format_few_shot_examples,
            retrieve_few_shot_examples,
        )

        few_shot_examples = await retrieve_few_shot_examples(
            raw_prompt=raw_prompt, db=db, trace_id=trace_id,
            prompt_embedding=prompt_embedding,
        )
        few_shot_text = format_few_shot_examples(few_shot_examples)
        if few_shot_text:
            context_updates["few_shot_examples"] = True
    except Exception:
        logger.debug("Few-shot retrieval failed. trace_id=%s", trace_id)

    optimize_msg = prompt_loader.render("optimize.md", {
        "raw_prompt": raw_prompt,
        "analysis_summary": analysis_summary,
        "strategy_instructions": strategy_instructions,
        "codebase_context": codebase_context,
        "strategy_intelligence": strategy_intelligence,
        "applied_patterns": applied_patterns_text,
        "few_shot_examples": few_shot_text,
        "divergence_alerts": divergence_alerts,
    })

    from app.services.pipeline_constants import compute_optimize_max_tokens

    dynamic_max_tokens = compute_optimize_max_tokens(len(raw_prompt))

    def _content_len(value: str | None) -> int:
        """Content-only char count — None/empty/whitespace-only → 0.

        Prevents the log from counting wrapper tags (e.g. a template that
        renders ``<strategy-intelligence></strategy-intelligence>`` when the
        inner value is absent) as real payload. Match the enrichment log's
        semantics: ``none`` means zero chars.
        """
        if not value or not value.strip():
            return 0
        return len(value)

    logger.info(
        "optimize_inject: trace_id=%s input_chars=%d (~%d tokens) "
        "prompt=%d codebase=%d strategy_intel=%d patterns=%d fewshot=%d",
        trace_id, len(optimize_msg), len(optimize_msg) // 4,
        len(raw_prompt),
        _content_len(codebase_context),
        _content_len(strategy_intelligence),
        _content_len(applied_patterns_text),
        _content_len(few_shot_text),
    )

    return OptimizeContextBundle(
        optimize_msg=optimize_msg,
        applied_patterns_text=applied_patterns_text,
        few_shot_text=few_shot_text,
        context_updates=context_updates,
        dynamic_max_tokens=dynamic_max_tokens,
    )


# ---------------------------------------------------------------------------
# Phase 3 — hybrid scoring
# ---------------------------------------------------------------------------


async def run_hybrid_scoring(
    *,
    raw_prompt: str,
    optimization: OptimizationResult,
    analysis: AnalysisResult,
    effective_strategy: str,
    provider: LLMProvider,
    prompt_loader: PromptLoader,
    trace_logger: TraceLogger | None,
    prefs: PreferencesService,
    prefs_snapshot: dict,
    scorer_model: str,
    trace_id: str,
    db: AsyncSession,
    call_provider: Callable,
) -> ScoringOutput:
    """Run Phase 3: A/B scoring LLM call + hybrid blend + drift check.

    Returns a ``ScoringOutput`` ready for persistence.  The orchestrator
    wraps this with the ``status running/complete`` events and the
    ``score_card`` payload emission.
    """
    # Randomize A/B assignment
    original_first = random.choice([True, False])
    if original_first:
        prompt_a = raw_prompt
        prompt_b = optimization.optimized_prompt
        presentation_order = "original_first"
    else:
        prompt_a = optimization.optimized_prompt
        prompt_b = raw_prompt
        presentation_order = "optimized_first"

    logger.info(
        "Scorer presentation_order=%s trace_id=%s",
        presentation_order, trace_id,
    )

    scoring_system = prompt_loader.load("scoring.md")
    scorer_msg = (
        f"<prompt-a>\n{prompt_a}\n</prompt-a>\n\n"
        f"<prompt-b>\n{prompt_b}\n</prompt-b>"
    )

    phase_start = time.monotonic()
    scores: ScoreResult = await call_provider(
        provider,
        system_prompt=scoring_system,
        user_message=scorer_msg,
        output_format=ScoreResult,
        model=scorer_model,
        effort=prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low",
        max_tokens=SCORE_MAX_TOKENS,
        cache_ttl="1h",
    )

    score_model = scorer_model
    if isinstance(provider.last_model, str):
        score_model = provider.last_model

    score_duration = int((time.monotonic() - phase_start) * 1000)

    usage = _get_provider_usage(provider)
    if trace_logger:
        trace_logger.log_phase(
            trace_id=trace_id, phase="score",
            duration_ms=score_duration,
            tokens_in=usage.input_tokens, tokens_out=usage.output_tokens,
            model=scorer_model, provider=provider.name,
            result={"effort": prefs.get("pipeline.scorer_effort", prefs_snapshot) or "low"},
        )

    # Map A/B scores back to original/optimized
    if original_first:
        llm_original_scores = scores.prompt_a_scores
        llm_optimized_scores = scores.prompt_b_scores
    else:
        llm_original_scores = scores.prompt_b_scores
        llm_optimized_scores = scores.prompt_a_scores

    heur_original = HeuristicScorer.score_prompt(raw_prompt)
    heur_optimized = HeuristicScorer.score_prompt(
        optimization.optimized_prompt,
        original=raw_prompt,
    )

    historical_stats: dict | None = None
    try:
        from app.services.optimization_service import OptimizationService
        opt_svc = OptimizationService(db)
        historical_stats = await opt_svc.get_score_distribution(
            exclude_scoring_modes=["heuristic"],
        )
    except Exception as exc:
        logger.debug("Historical stats unavailable for normalization: %s", exc)

    blended_original = blend_scores(
        llm_original_scores, heur_original, historical_stats,
        prompt_text=raw_prompt,
        task_type=analysis.task_type if analysis else None,
    )
    blended_optimized = blend_scores(
        llm_optimized_scores, heur_optimized, historical_stats,
        prompt_text=optimization.optimized_prompt,
        task_type=analysis.task_type if analysis else None,
    )

    original_scores = blended_original.to_dimension_scores()
    optimized_scores = blended_optimized.to_dimension_scores()

    logger.info(
        "Hybrid scoring complete: llm_opt=%.1f heur_opt=%s blended_opt=%.1f "
        "divergence=%s normalized=%s trace_id=%s",
        llm_optimized_scores.overall,
        {k: round(v, 1) for k, v in heur_optimized.items()},
        optimized_scores.overall,
        blended_optimized.divergence_flags,
        blended_optimized.normalization_applied,
        trace_id,
    )

    deltas = DimensionScores.compute_deltas(original_scores, optimized_scores)

    # Observability event
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

    if optimized_scores.faithfulness < 6.0:
        logger.warning(
            "Low faithfulness score (%.1f) — optimization may have altered intent. trace_id=%s",
            optimized_scores.faithfulness, trace_id,
        )

    # Intent drift check
    warnings: list[str] = []
    divergence_flags = blended_optimized.divergence_flags or []
    if divergence_flags:
        warnings.append(
            "Score divergence between LLM and heuristic on: "
            + ", ".join(divergence_flags)
        )

    try:
        import numpy as np

        from app.services.embedding_service import EmbeddingService

        drift_svc = EmbeddingService()
        orig_vec = await drift_svc.aembed_single(raw_prompt)
        opt_vec = await drift_svc.aembed_single(optimization.optimized_prompt)
        similarity = float(
            np.dot(orig_vec, opt_vec)
            / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9)
        )

        if similarity < 0.5:
            warnings.append(
                f"Intent drift detected: semantic similarity {similarity:.2f} "
                f"between original and optimized prompt is below threshold (0.50)"
            )
            logger.warning(
                "Intent drift detected: similarity=%.2f trace_id=%s",
                similarity, trace_id,
            )
    except (ImportError, RuntimeError, ValueError, MemoryError) as exc:
        logger.debug("Intent drift check skipped: %s", exc)

    # C4: heuristic-only baseline for the original prompt.  Computed from
    # the SAME ``heur_original`` we already have — it's the unblended,
    # un-normalized version of the original-side score.  Persisted to
    # ``heuristic_baseline_scores`` for downstream delta + improvement
    # computation that needs immunity from LLM-judge noise.
    heuristic_baseline_scores = DimensionScores(
        clarity=float(heur_original.get("clarity", 5.0)),
        specificity=float(heur_original.get("specificity", 5.0)),
        structure=float(heur_original.get("structure", 5.0)),
        faithfulness=float(heur_original.get("faithfulness", 5.0)),
        conciseness=float(heur_original.get("conciseness", 5.0)),
    )

    return ScoringOutput(
        llm_original_scores=llm_original_scores,
        llm_optimized_scores=llm_optimized_scores,
        original_scores=original_scores,
        optimized_scores=optimized_scores,
        heuristic_baseline_scores=heuristic_baseline_scores,
        deltas=deltas,
        divergence_flags=divergence_flags,
        warnings=warnings,
        blended_raw_llm=blended_optimized.raw_llm,
        blended_raw_heuristic=blended_optimized.raw_heuristic,
        normalization_applied=blended_optimized.normalization_applied,
        score_duration_ms=score_duration,
        score_model=score_model,
    )


# ---------------------------------------------------------------------------
# Phase 4 — suggestion generation
# ---------------------------------------------------------------------------


async def run_suggestion_phase(
    *,
    optimization: OptimizationResult,
    optimized_scores: DimensionScores,
    analysis: AnalysisResult,
    effective_strategy: str,
    prompt_loader: PromptLoader,
    system_prompt: str,
    provider: LLMProvider,
    trace_id: str,
    call_provider: Callable,
) -> list[dict[str, str]]:
    """Call Haiku to produce 3 follow-up suggestions.  Returns [] on failure."""
    try:
        suggest_msg = prompt_loader.render("suggest.md", {
            "optimized_prompt": optimization.optimized_prompt,
            "scores": json.dumps(optimized_scores.model_dump(), indent=2),
            "weaknesses": ", ".join(analysis.weaknesses) if analysis.weaknesses else "none identified",
            "strategy_used": effective_strategy,
            "score_deltas": "first optimization — no previous deltas",
            "score_trajectory": "first turn",
        })

        suggest_result: SuggestionsOutput = await call_provider(
            provider,
            system_prompt=system_prompt,
            user_message=suggest_msg,
            output_format=SuggestionsOutput,
            model=settings.MODEL_HAIKU,
            max_tokens=2048,
        )
        logger.info(
            "Suggestions generated: %d items. trace_id=%s",
            len(suggest_result.suggestions), trace_id,
        )
        return suggest_result.suggestions
    except Exception as exc:
        logger.warning("Suggestion generation failed (non-fatal): %s", exc)
        return []


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@dataclass
class PersistenceInputs:
    opt_id: str
    raw_prompt: str
    analysis: AnalysisResult
    optimization: OptimizationResult
    effective_strategy: str
    effective_domain: str
    domain_raw: str
    cluster_id: str | None
    scoring: ScoringOutput | None
    suggestions: list[dict[str, str]]
    phase_durations: dict[str, int]
    model_ids: dict[str, str]
    optimizer_model: str
    provider_name: str
    repo_full_name: str | None
    project_id: str | None
    context_sources: dict[str, Any] | None
    trace_id: str
    duration_ms: int
    applied_pattern_ids: list[str] | None
    auto_injected_cluster_ids: list[str]
    taxonomy_engine: Any | None
    divergence_flags: list[str] = field(default_factory=list)
    # ``list[InjectedPattern]`` from auto_inject_patterns. Untyped here to
    # keep the contract module-import-light; persist_and_propagate calls
    # ``record_injection_provenance`` post-commit, which is the only
    # consumer.
    auto_injected_patterns: list[Any] = field(default_factory=list)
    # ``cluster_id → cosine_similarity`` produced by ``auto_inject_patterns``
    # during the topic-cluster scan. Forwarded to
    # ``record_injection_provenance`` so post-commit topic rows carry the
    # similarity score the in-line write used to capture. Without this,
    # every topic-row provenance entry would land with ``similarity=NULL``.
    auto_injected_similarity_map: dict[str, float] = field(default_factory=dict)


async def persist_and_propagate(
    db: AsyncSession, inputs: PersistenceInputs,
) -> None:
    """Build Optimization row, track applied patterns, commit, propagate usage.

    Commits the main DB session; opens a fresh session for post-commit
    usage propagation so expired objects from the committed session
    can't leak.  Publishes ``optimization_created`` to the event bus.
    """
    analysis = inputs.analysis
    scoring = inputs.scoring
    optimized_scores = scoring.optimized_scores if scoring else None
    original_scores = scoring.original_scores if scoring else None
    heuristic_baseline = scoring.heuristic_baseline_scores if scoring else None
    deltas = scoring.deltas if scoring else None

    db_opt = Optimization(
        id=inputs.opt_id,
        raw_prompt=inputs.raw_prompt,
        optimized_prompt=inputs.optimization.optimized_prompt,
        task_type=analysis.task_type if analysis.task_type in VALID_TASK_TYPES else "general",
        intent_label=validate_intent_label(
            title_case_label(analysis.intent_label or "general"),
            inputs.raw_prompt,
        )[:MAX_INTENT_LABEL_LENGTH],
        domain=inputs.effective_domain,
        domain_raw=inputs.domain_raw,
        cluster_id=inputs.cluster_id,
        strategy_used=inputs.effective_strategy,
        changes_summary=inputs.optimization.changes_summary,
        score_clarity=optimized_scores.clarity if optimized_scores else None,
        score_specificity=optimized_scores.specificity if optimized_scores else None,
        score_structure=optimized_scores.structure if optimized_scores else None,
        score_faithfulness=optimized_scores.faithfulness if optimized_scores else None,
        score_conciseness=optimized_scores.conciseness if optimized_scores else None,
        overall_score=optimized_scores.overall if optimized_scores else None,
        provider=inputs.provider_name,
        routing_tier="internal",
        model_used=inputs.model_ids.get("optimize", inputs.optimizer_model),
        scoring_mode="hybrid" if optimized_scores else "skipped",
        duration_ms=inputs.duration_ms,
        status="completed",
        trace_id=inputs.trace_id,
        repo_full_name=inputs.repo_full_name,
        project_id=inputs.project_id,
        context_sources=inputs.context_sources or {},
        original_scores=original_scores.model_dump() if original_scores else None,
        heuristic_baseline_scores=(
            heuristic_baseline.model_dump() if heuristic_baseline else None
        ),
        score_deltas=deltas,
        tokens_by_phase=inputs.phase_durations,
        models_by_phase=inputs.model_ids,
        heuristic_flags=inputs.divergence_flags or None,
        suggestions=inputs.suggestions,
    )
    # C4: Compute improvement_score from the deterministic heuristic
    # baseline when available — shields the score from LLM-judge noise on
    # the original-side A/B presentation.  Falls back to the LLM-blended
    # ``deltas`` when no heuristic baseline is recorded (legacy rows or
    # heuristic-only scoring mode).  ``deltas`` itself is preserved as-is
    # for backward compat, callers, and side-by-side comparison.
    if heuristic_baseline and optimized_scores:
        heuristic_lift = {
            dim: getattr(optimized_scores, dim) - getattr(heuristic_baseline, dim)
            for dim in get_dimension_weights(inputs.analysis.task_type)
        }
        imp = sum(
            heuristic_lift.get(dim, 0) * w
            for dim, w in get_dimension_weights(inputs.analysis.task_type).items()
        )
        db_opt.improvement_score = round(max(0.0, min(10.0, imp)), 2)
    elif deltas:
        imp = sum(
            deltas.get(dim, 0) * w
            for dim, w in get_dimension_weights(inputs.analysis.task_type).items()
        )
        db_opt.improvement_score = round(max(0.0, min(10.0, imp)), 2)
    db.add(db_opt)

    # Track applied patterns in join table (relationship: "applied")
    applied_cluster_ids: set[str] = set()
    if inputs.applied_pattern_ids:
        try:
            from app.models import MetaPattern, OptimizationPattern

            for pid in inputs.applied_pattern_ids:
                mp_result = await db.execute(
                    select(MetaPattern).where(MetaPattern.id == pid)
                )
                mp = mp_result.scalar_one_or_none()
                if mp:
                    db.add(OptimizationPattern(
                        optimization_id=inputs.opt_id,
                        cluster_id=mp.cluster_id,
                        meta_pattern_id=mp.id,
                        relationship="applied",
                    ))
                    applied_cluster_ids.add(mp.cluster_id)
        except Exception as exc:
            logger.warning("Failed to track applied patterns: %s", exc)

    await db.commit()

    # Post-persist injection provenance.  ``auto_inject_patterns`` ran
    # PRE-persist (the patterns had to flow into the optimizer prompt)
    # with ``record_provenance=False`` so the FK-on-Optimization check
    # would not fire inside a SAVEPOINT and silently rollback every
    # ``relationship='injected'`` row.  Now that the parent row is
    # committed, we can write provenance cleanly.
    if inputs.auto_injected_cluster_ids or inputs.auto_injected_patterns:
        try:
            await record_injection_provenance(
                db,
                optimization_id=inputs.opt_id,
                cluster_ids=list(inputs.auto_injected_cluster_ids),
                injected=list(inputs.auto_injected_patterns),
                similarity_map=inputs.auto_injected_similarity_map,
                trace_id=inputs.trace_id,
            )
            await db.commit()
        except Exception as prov_exc:
            logger.warning(
                "Post-persist injection provenance write failed (non-fatal): %s",
                prov_exc,
            )

    # T1.3-lite — increment useful/unused counters on every
    # ``OptimizationPattern`` row attached to this optimization based on
    # the host's overall_score.  Builds attribution data without paying
    # the cost of pattern-ablation re-scoring.  A separate commit so a
    # counter failure can never roll back provenance.
    try:
        from app.services.pattern_injection import record_pattern_usefulness

        _overall = (
            optimized_scores.overall if optimized_scores is not None else None
        )
        bumped = await record_pattern_usefulness(
            db,
            optimization_id=inputs.opt_id,
            overall_score=_overall,
        )
        if bumped:
            await db.commit()
    except Exception as cnt_exc:
        logger.debug(
            "Pattern usefulness bump skipped (non-fatal): %s", cnt_exc,
        )

    # Include auto-injected cluster IDs in usage propagation
    if inputs.auto_injected_cluster_ids:
        applied_cluster_ids.update(inputs.auto_injected_cluster_ids)

    # Propagate usage counts AFTER successful commit (Spec 7.8)
    # Use a fresh session — the original db session may be expired post-commit
    if applied_cluster_ids and inputs.taxonomy_engine:
        try:
            from app.database import async_session_factory

            async with async_session_factory() as usage_db:
                for fid in applied_cluster_ids:
                    try:
                        await inputs.taxonomy_engine.increment_usage(fid, usage_db)
                    except Exception as usage_exc:
                        logger.warning("Usage propagation failed for %s: %s", fid, usage_exc)
                        # Fallback: atomic SQL increment (no tree walk)
                        try:
                            from sqlalchemy import update as sa_upd

                            from app.models import PromptCluster
                            await usage_db.execute(
                                sa_upd(PromptCluster)
                                .where(PromptCluster.id == fid)
                                .values(usage_count=PromptCluster.usage_count + 1)
                            )
                        except Exception:
                            pass
                await usage_db.commit()
        except Exception as exc:
            logger.warning("Post-commit usage propagation failed: %s", exc)

    # Publish real-time event
    try:
        from app.services.event_bus import event_bus
        event_bus.publish("optimization_created", {
            "id": inputs.opt_id,
            "trace_id": inputs.trace_id,
            "task_type": analysis.task_type,
            "intent_label": analysis.intent_label or "general",
            "domain": inputs.effective_domain,
            "domain_raw": inputs.domain_raw,
            "strategy_used": inputs.effective_strategy,
            "overall_score": optimized_scores.overall if optimized_scores else None,
            "provider": inputs.provider_name,
            "status": "completed",
        })
    except Exception:
        logger.debug("Event bus publish failed", exc_info=True)


def build_pipeline_result(inputs: PersistenceInputs) -> PipelineResult:
    """Assemble the final PipelineResult for the ``optimization_complete`` event.

    Applies the same ``context_sources`` sanitization fallback the
    orchestrator previously had inline.
    """
    analysis = inputs.analysis
    scoring = inputs.scoring
    optimized_scores = scoring.optimized_scores if scoring else None
    original_scores = scoring.original_scores if scoring else None
    heuristic_baseline = scoring.heuristic_baseline_scores if scoring else None
    deltas = scoring.deltas if scoring else None

    result_kwargs = dict(
        id=inputs.opt_id,
        trace_id=inputs.trace_id,
        raw_prompt=inputs.raw_prompt,
        optimized_prompt=inputs.optimization.optimized_prompt,
        task_type=analysis.task_type,
        strategy_used=inputs.effective_strategy,
        changes_summary=inputs.optimization.changes_summary,
        optimized_scores=optimized_scores,
        original_scores=original_scores,
        heuristic_baseline_scores=heuristic_baseline,
        score_deltas=deltas,
        overall_score=optimized_scores.overall if optimized_scores else None,
        provider=inputs.provider_name,
        routing_tier="internal",
        model_used=inputs.model_ids.get("optimize", inputs.optimizer_model),
        models_by_phase=inputs.model_ids,
        scoring_mode="hybrid" if optimized_scores else "skipped",
        duration_ms=inputs.duration_ms,
        status="completed",
        suggestions=inputs.suggestions,
        context_sources=inputs.context_sources or {},
        warnings=scoring.warnings if scoring else [],
        intent_label=analysis.intent_label,
        domain=inputs.effective_domain,
        repo_full_name=inputs.repo_full_name,
    )
    try:
        return PipelineResult(**result_kwargs)
    except Exception as val_err:
        logger.warning(
            "PipelineResult validation failed, retrying with sanitized "
            "context_sources: %s", val_err,
        )
        result_kwargs["context_sources"] = {
            k: v for k, v in (inputs.context_sources or {}).items()
            if isinstance(v, (bool, str, int, float, type(None)))
        }
        return PipelineResult(**result_kwargs)


async def persist_failed_optimization(
    db: AsyncSession,
    *,
    opt_id: str,
    raw_prompt: str,
    trace_id: str,
    duration_ms: int,
    provider: LLMProvider,
    optimizer_model: str,
    model_ids: dict[str, str],
    error_message: str,
) -> None:
    """Roll back + write a ``status='failed'`` Optimization row + publish event."""
    try:
        await db.rollback()
        failed_opt = Optimization(
            id=opt_id,
            raw_prompt=raw_prompt,
            status="failed",
            routing_tier="internal",
            trace_id=trace_id,
            duration_ms=duration_ms,
            provider=provider.name,
            model_used=optimizer_model,
            models_by_phase=model_ids,
        )
        db.add(failed_opt)
        await db.commit()
    except Exception as db_exc:
        logger.error("Failed to persist failed optimization: %s", db_exc)

    try:
        from app.services.event_bus import event_bus
        event_bus.publish("optimization_failed", {
            "trace_id": trace_id,
            "error": error_message,
        })
    except Exception:
        pass


__all__ = [
    "OptimizeContextBundle",
    "PersistenceInputs",
    "PostAnalyzeState",
    "ScoringOutput",
    "build_optimize_context",
    "build_pipeline_result",
    "persist_and_propagate",
    "persist_failed_optimization",
    "resolve_blocked_strategies",
    "resolve_post_analyze_state",
    "run_hybrid_scoring",
    "run_suggestion_phase",
]
