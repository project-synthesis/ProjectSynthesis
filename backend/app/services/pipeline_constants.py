"""Shared constants and helpers for the optimization pipeline.

Used by both the internal pipeline (``pipeline.py``) and the sampling-based
pipeline (``sampling_pipeline.py``) to ensure identical gating behavior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain discovery thresholds (ADR-004)
# ---------------------------------------------------------------------------
DOMAIN_DISCOVERY_MIN_MEMBERS = 3
DOMAIN_DISCOVERY_MIN_COHERENCE = 0.3  # domain_raw consistency is the real quality gate
DOMAIN_DISCOVERY_CONSISTENCY = 0.60  # 60% of members share the same domain_raw primary

# Candidate domain detection — lower thresholds for early visibility
DOMAIN_DISCOVERY_CANDIDATE_MIN_MEMBERS = 2
DOMAIN_DISCOVERY_CANDIDATE_MIN_COHERENCE = 0.2

# Domain quality
DOMAIN_COHERENCE_FLOOR = 0.3

# Domain proliferation ceiling (ADR-004 Risk 1)
DOMAIN_COUNT_CEILING = 30

# Cross-cluster pooled evidence floor — when several tiny clusters under
# "general" all share a consistent primary ``domain_raw``, their collective
# member count crosses this floor to trigger organic domain promotion even
# though no single cluster meets DOMAIN_DISCOVERY_MIN_MEMBERS.
# Per-cluster internal consistency (DOMAIN_DISCOVERY_CONSISTENCY) still applies.
DOMAIN_DISCOVERY_POOL_MIN_MEMBERS = 3

# Sparse-DB bootstrap threshold — below this total-optimization count, the
# per-cluster member floor relaxes by 1 (3 → 2) so a 2-prompt signal on a
# fresh DB can promote organically.  Above this threshold, the standard
# DOMAIN_DISCOVERY_MIN_MEMBERS floor applies.  ADR-006 compliant: applies
# to ANY label, not just seed labels — purely data-density-aware.
DOMAIN_DISCOVERY_BOOTSTRAP_DB_THRESHOLD = 20

# Per-project domain visibility threshold (Hybrid taxonomy, 2026-04-19).
# A top-level domain is visible to a given project when either (a) its
# per-project member count ≥ DOMAIN_DISCOVERY_MIN_MEMBERS (3), or (b) its
# per-project share of optimizations ≥ VISIBILITY_THRESHOLD_FRACTION (5%).
# Catches fresh-project regime where absolute counts are tiny but a
# single concentrated signal dominates.  Canonical "general" is always
# visible (it's the taxonomy root default).
VISIBILITY_THRESHOLD_FRACTION = 0.05

# Signal staleness ratio (ADR-004 Risk 2) — refresh when member_count doubles
SIGNAL_REFRESH_MEMBER_RATIO = 2.0

# Domain archival suggestion thresholds (ADR-004 Risk 1 self-correction)
DOMAIN_ARCHIVAL_IDLE_DAYS = 90
DOMAIN_ARCHIVAL_MIN_USAGE = 3

# Color constraints — domain colors must avoid perceptual proximity to:
# 1. Tier accents (internal=#00e5ff, sampling=#22ff88, passthrough=#fbbf24)
# 2. Brand neon palette (fixed semantic assignments per brand guidelines)
BRAND_RESERVED_COLORS = [
    "#00e5ff",  # neon-cyan (internal tier / primary identity)
    "#22ff88",  # neon-green (sampling tier / success)
    "#fbbf24",  # neon-yellow (passthrough tier / warnings)
    "#a855f7",  # neon-purple (processed / elevated)
    "#ff3366",  # neon-red (danger)
    "#ff8c00",  # neon-orange (attention)
    "#4d8eff",  # neon-blue (information)
    "#ff6eb4",  # neon-pink (creativity)
    "#00d4aa",  # neon-teal (extraction)
    "#7b61ff",  # neon-indigo (reasoning)
]

# Domain raw field truncation — caps Optimization.domain_raw at persistence sites
MAX_DOMAIN_RAW_LENGTH = 200

# Intent label and cluster label caps
MAX_INTENT_LABEL_LENGTH = 100
MAX_CLUSTER_LABEL_LENGTH = 100

# Valid task type values — validated at all persistence sites
VALID_TASK_TYPES: frozenset[str] = frozenset(
    {"coding", "writing", "analysis", "creative", "data", "system", "general"}
)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum analyzer confidence to trust its strategy selection.
# Below this threshold, the pipeline overrides the selected strategy to "auto".
CONFIDENCE_GATE = 0.7

# Repo relevance gate.  Single-threshold cosine floor computed against a
# repo-anchored embedding of the form ``"Project: {repo_full_name}\n{synthesis}"``.
# The richer anchor lets the embedding capture the project identity in addition
# to its tech-stack signature, so focused prompts about one subsystem of a broad
# repo stay above the floor without needing a secondary overlap gate.
#
# Root-fix rationale: the previous two-stage gate (cosine floor + domain entity
# overlap) dropped legitimate prompts when ``extract_domain_vocab()``'s freq>=3
# cutoff produced too-sparse vocab for a repo.  One well-calibrated embedding
# threshold over a repo-anchored synthesis is both simpler and more reliable.
REPO_RELEVANCE_FLOOR = 0.15

# Organic-preservation gate for unknown domain labels in DomainResolver.
# Below this threshold an unknown label collapses to ``"general"``; at/above
# it the label is preserved so the warm-path taxonomy can observe the raw
# signal and promote a new domain node once enough coherent members arrive.
#
# Root-fix rationale: the gate used to be 0.7 under the old "collapse to
# general by default" semantics — a strict threshold made sense when
# preservation was the exceptional path.  Now that organic preservation IS
# the intended behavior, the gate should only filter truly garbage/empty
# labels, not well-formed-but-under-confident ones (e.g. analyzer returning
# "frontend" with 0.55 blended confidence because task-type ambiguity
# dragged the overall score down).  0.5 is the natural midpoint — above it
# the label is more likely right than wrong.
DOMAIN_CONFIDENCE_GATE = 0.5

# Preferred fallback strategy when confidence gate triggers or validation fails.
FALLBACK_STRATEGY = "auto"

# ---------------------------------------------------------------------------
# Score-informed strategy recommendation
# ---------------------------------------------------------------------------
STRATEGY_REC_CANDIDATE_POOL = 30
STRATEGY_REC_SIMILARITY_THRESHOLD = 0.45
STRATEGY_REC_MIN_SAMPLES = 3
STRATEGY_REC_CONFIDENCE_BOOST = 0.15
STRATEGY_REC_DOMINANCE_MARGIN = 0.5


@dataclass
class StrategyRecommendation:
    """Result of score-informed strategy recommendation."""

    recommended_strategy: str | None
    confidence_boost: float
    evidence_count: int
    score_by_strategy: dict[str, float] = field(default_factory=dict)


async def recommend_strategy_from_history(
    raw_prompt: str,
    db,
    available_strategies: list[str],
    trace_id: str,
    *,
    prompt_embedding=None,
) -> StrategyRecommendation:
    """Recommend the best strategy based on historical score data for similar prompts.

    Finds past optimizations similar to the incoming prompt (by raw embedding
    cosine), groups by strategy, and identifies which strategy produced the
    highest scores. Uses z-score weighting so only above-median results
    contribute (same philosophy as ``compute_score_correlated_target``).

    Args:
        raw_prompt: User's raw prompt text.
        db: Active async DB session.
        available_strategies: Valid strategy names from StrategyLoader.
        trace_id: Pipeline trace ID for log correlation.
        prompt_embedding: Pre-computed embedding to avoid double-embedding.

    Returns:
        StrategyRecommendation with recommended_strategy (None if insufficient data).
    """
    import numpy as np
    from sqlalchemy import select

    from app.models import Optimization
    from app.services.embedding_service import EmbeddingService

    empty = StrategyRecommendation(
        recommended_strategy=None, confidence_boost=0.0, evidence_count=0,
    )

    try:
        if prompt_embedding is None:
            embedding_svc = EmbeddingService()
            prompt_embedding = await embedding_svc.aembed_single(raw_prompt)

        result = await db.execute(
            select(
                Optimization.embedding,
                Optimization.strategy_used,
                Optimization.overall_score,
            ).where(
                Optimization.embedding.isnot(None),
                Optimization.overall_score.isnot(None),
                Optimization.strategy_used.isnot(None),
                Optimization.status == "completed",
            ).order_by(
                Optimization.created_at.desc(),
            ).limit(STRATEGY_REC_CANDIDATE_POOL)
        )
        rows = result.all()

        if not rows:
            return empty

        # Cosine similarity filter
        neighbors: list[tuple[float, str, float]] = []
        for emb_bytes, strategy, score in rows:
            try:
                emb = np.frombuffer(emb_bytes, dtype=np.float32)
                sim = float(
                    np.dot(prompt_embedding, emb)
                    / (np.linalg.norm(prompt_embedding) * np.linalg.norm(emb) + 1e-9)
                )
                if sim >= STRATEGY_REC_SIMILARITY_THRESHOLD and strategy in available_strategies:
                    neighbors.append((sim, strategy, score))
            except (ValueError, TypeError):
                continue

        if not neighbors:
            return empty

        # Z-score weighting: only above-median scores contribute
        scores = [n[2] for n in neighbors]
        sorted_scores = sorted(scores)
        n = len(sorted_scores)
        median = (
            sorted_scores[n // 2]
            if n % 2 == 1
            else (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2.0
        )
        mean_s = sum(scores) / n
        stdev = (sum((s - mean_s) ** 2 for s in scores) / n) ** 0.5

        # Group by strategy with score-weighted contributions
        strategy_signals: dict[str, list[tuple[float, float]]] = {}
        for _, strategy, score in neighbors:
            contribution = 1.0 if stdev < 0.01 else max(0.0, (score - median) / stdev)
            strategy_signals.setdefault(strategy, []).append((contribution, score))

        # Score-weighted average per strategy (skip those below min_samples)
        score_by_strategy: dict[str, float] = {}
        for strategy, signals in strategy_signals.items():
            if len(signals) < STRATEGY_REC_MIN_SAMPLES:
                continue
            total_c = sum(c for c, _ in signals)
            if total_c < 1e-9:
                score_by_strategy[strategy] = sum(s for _, s in signals) / len(signals)
            else:
                score_by_strategy[strategy] = sum(c * s for c, s in signals) / total_c

        if not score_by_strategy:
            return StrategyRecommendation(
                recommended_strategy=None, confidence_boost=0.0,
                evidence_count=len(neighbors), score_by_strategy={},
            )

        best = max(score_by_strategy, key=score_by_strategy.get)  # type: ignore[arg-type]

        # Dominance check: is best significantly better than runner-up?
        sorted_vals = sorted(score_by_strategy.values(), reverse=True)
        boost = 0.0
        if len(sorted_vals) >= 2 and stdev > 0.01:
            if (sorted_vals[0] - sorted_vals[1]) / stdev >= STRATEGY_REC_DOMINANCE_MARGIN:
                boost = STRATEGY_REC_CONFIDENCE_BOOST
        elif len(sorted_vals) == 1:
            boost = STRATEGY_REC_CONFIDENCE_BOOST * 0.5

        logger.debug(
            "Strategy recommendation: best=%s boost=%.2f evidence=%d scores=%s trace_id=%s",
            best, boost, len(neighbors), score_by_strategy, trace_id,
        )

        return StrategyRecommendation(
            recommended_strategy=best,
            confidence_boost=boost,
            evidence_count=len(neighbors),
            score_by_strategy=score_by_strategy,
        )
    except Exception as exc:
        logger.debug("recommend_strategy_from_history failed: %s", exc)
        return empty


# Keywords used by the semantic check to validate a task_type="coding"
# classification.  If none of these appear in the prompt, confidence is
# reduced by 0.2 before the gate check.
CODING_KEYWORDS: set[str] = {
    "function", "class", "api", "code", "program",
    "script", "endpoint", "database", "module", "import",
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def resolve_fallback_strategy(available: list[str]) -> str:
    """Return a validated fallback strategy name.

    Checks that FALLBACK_STRATEGY exists in the available list. If not,
    returns the first available strategy. If no strategies exist at all,
    returns FALLBACK_STRATEGY anyway (StrategyLoader.load handles this
    gracefully with generic guidance text).
    """
    if not available:
        return FALLBACK_STRATEGY
    if FALLBACK_STRATEGY in available:
        return FALLBACK_STRATEGY
    return available[0]


def semantic_check(task_type: str, raw_prompt: str, confidence: float) -> float:
    """Reduce confidence if task_type is ``coding`` but no coding keywords found.

    Used by both internal and sampling pipelines to catch misclassified prompts
    before the confidence gate.  A 0.2 reduction typically pushes borderline
    cases below ``CONFIDENCE_GATE``, triggering the fallback strategy.
    """
    if task_type == "coding":
        words = set(raw_prompt.lower().split())
        if not words & CODING_KEYWORDS:
            logger.warning(
                "Semantic check: task_type='coding' but no coding keywords in prompt"
            )
            confidence = max(0.0, confidence - 0.2)
    return confidence


# ---------------------------------------------------------------------------
# Keyword sets for upgrading ``general`` to a specific task_type.
#
# Kept intentionally conservative — only high-signal keywords that almost
# always indicate a particular task_type.  Each set has a "strong" subset
# where a single match suffices, and a broader set where 2+ matches are
# required.
# ---------------------------------------------------------------------------

_UPGRADE_SIGNALS: dict[str, tuple[set[str], set[str]]] = {
    # (strong_keywords — 1 match enough, broad_keywords — need 2+)
    "coding": (
        {"implement", "refactor", "debug", "deploy", "migrate"},
        {"build", "api", "endpoint", "function", "code", "module", "fix",
         "database", "schema", "test", "class", "calculate"},
    ),
    "analysis": (
        {"analyze", "evaluate", "diagnose"},
        {"compare", "assess", "metrics", "framework", "review", "benchmark",
         "trade-off", "tradeoff", "investigate"},
    ),
    "writing": (
        {"draft", "blog", "article", "essay"},
        {"write", "copy", "email", "editorial", "narrative", "publish",
         "document", "template"},
    ),
    "data": (
        {"etl", "dataframe", "pandas"},
        {"dataset", "csv", "pipeline", "aggregate", "visualization",
         "transform"},
    ),
    "system": (
        {"orchestrate", "prompt engineer"},
        {"automate", "agent", "workflow", "infrastructure"},
    ),
}


def semantic_upgrade_general(task_type: str, raw_prompt: str) -> str:
    """Upgrade ``general`` to a specific task_type when keywords are strong.

    Called after the LLM / heuristic analyzer returns a task_type.  When the
    result is ``"general"`` but the prompt contains clear signals for a
    specific type, overrides to that type.  Two thresholds:

    * **Strong**: a single keyword from the strong set is enough.
    * **Broad**: requires 2+ keyword matches from the broad set.

    If multiple types qualify, the one with the highest combined match
    count wins.  On tie, the first in declaration order wins.

    Used by both internal and sampling pipelines after ``semantic_check()``.
    """
    if task_type != "general":
        return task_type

    prompt_lower = raw_prompt.lower()
    words = set(prompt_lower.split())

    best_type: str | None = None
    best_score: int = 0

    for candidate, (strong, broad) in _UPGRADE_SIGNALS.items():
        # Strong keywords: check both word-boundary (single words) and
        # substring (multi-word phrases like "prompt engineer")
        strong_hits = len(words & strong)
        # Multi-word strong keywords need substring check
        for kw in strong:
            if " " in kw and kw in prompt_lower:
                strong_hits += 1

        broad_hits = len(words & broad)

        if strong_hits >= 1 or broad_hits >= 2:
            score = strong_hits * 3 + broad_hits
            if score > best_score:
                best_score = score
                best_type = candidate

    if best_type:
        logger.info(
            "Semantic upgrade: task_type 'general' → '%s' "
            "(strong keyword or 2+ broad matches found in prompt)",
            best_type,
        )
        return best_type

    return task_type


# ---------------------------------------------------------------------------
# A2: Intent-aware auto-resolution
# ---------------------------------------------------------------------------
# When the analyzer picks ``"auto"``, the default path falls straight to the
# coarse task-type map (``_auto_task_map`` in ``resolve_effective_strategy``).
# That map ignores the semantic shape of the request: a ``task_type=analysis``
# prompt whose ``intent_label`` clearly says "audit" or "debug" should land on
# chain-of-thought, not the generic ``meta-prompting`` default.
#
# The keyword table below is intentionally short. It covers verbs/nouns that
# unambiguously pick one strategy over another. Ambiguous intents (``make``,
# ``improve``) fall through to the task-type map — we'd rather under-trigger
# than misroute.
_INTENT_STRATEGY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "chain-of-thought": (
        "audit", "debug", "diagnose", "review",
        "compare", "investigate", "trace", "root cause",
    ),
    "structured-output": (
        "extract", "classify", "list", "schema",
        "tabulate", "enumerate",
    ),
    "role-playing": (
        "story", "poem", "narrative", "character",
        "dialogue", "persona",
    ),
    # meta-prompting stays the task-type-default fallback (no keyword table).
}


# Task-type → generic default strategy. Exposed at module level so step 5b can
# detect when the current effective strategy is "the LLM (or auto map) picked
# the generic default" and let a strong intent keyword override it. Kept in
# sync with ``_auto_task_map`` inside ``resolve_effective_strategy``.
_TASK_TYPE_DEFAULTS: dict[str, str] = {
    "coding": "meta-prompting",
    "analysis": "meta-prompting",
    "writing": "role-playing",
    "creative": "role-playing",
    "data": "structured-output",
    "system": "meta-prompting",
    "general": "meta-prompting",
}


def _resolve_intent_strategy(
    intent_label: str | None,
    available: list[str],
    blocked: set[str],
) -> str | None:
    """Return an intent-driven strategy pick, or None when no keyword matches.

    Selection order is dictionary order — the first matching strategy wins.
    A pick is discarded when the strategy is unavailable on disk or blocked
    by adaptation, since the caller has a task-type fallback waiting.
    """
    if not intent_label:
        return None
    low = intent_label.lower()
    for strategy, keywords in _INTENT_STRATEGY_KEYWORDS.items():
        if any(kw in low for kw in keywords):
            if strategy in available and strategy not in blocked:
                return strategy
            # Strategy matched intent but is unavailable/blocked — keep
            # scanning other strategies (their keywords may still hit).
    return None


def resolve_effective_strategy(
    selected_strategy: str,
    available: list[str],
    blocked_strategies: set[str],
    confidence: float,
    strategy_override: str | None,
    trace_id: str,
    *,
    data_recommendation: StrategyRecommendation | None = None,
    task_type: str | None = None,
    intent_label: str | None = None,
) -> str:
    """Post-analysis strategy resolution chain.

    Applies, in order:
      1. **Disk validation** — reject hallucinated strategy names.
      2. **Adaptation block** — override low-approval strategies.
      3. **Data recommendation** — use historical score data when analyzer
         confidence is below the gate and data has strong signal.
      4. **Confidence gate** — override when analyzer confidence is low.
      5. **Explicit override** — user's explicit choice always wins.
      6. **Auto resolution** — resolve "auto" to a task-type-appropriate
         named strategy so the optimizer always gets concrete techniques.

    Both internal and sampling pipelines call this to ensure identical
    decision logic.
    """
    fallback = resolve_fallback_strategy(available)
    effective = selected_strategy

    # 1. Validate strategy exists on disk (prevent hallucinated names)
    if effective and available and effective not in available:
        logger.warning(
            "Analyzer selected unknown strategy '%s' (available: %s) — "
            "falling back to '%s'. trace_id=%s",
            effective, ", ".join(available), fallback, trace_id,
        )
        effective = fallback

    # 2. Enforce adaptation block
    if effective in blocked_strategies and not strategy_override:
        logger.info(
            "Overriding blocked strategy '%s' to '%s' (low approval rate). trace_id=%s",
            effective, fallback, trace_id,
        )
        effective = fallback

    # 3. Data recommendation: when confidence is below gate and data
    #    has strong signal, use the data-recommended strategy instead
    #    of falling through to the generic "auto" fallback.
    if (
        data_recommendation is not None
        and data_recommendation.recommended_strategy is not None
        and not strategy_override
        and confidence < CONFIDENCE_GATE
        and data_recommendation.confidence_boost > 0
        and data_recommendation.recommended_strategy not in blocked_strategies
    ):
        logger.info(
            "Data-recommended strategy '%s' (evidence=%d, boost=%.2f) "
            "overrides low-confidence analyzer pick '%s'. trace_id=%s",
            data_recommendation.recommended_strategy,
            data_recommendation.evidence_count,
            data_recommendation.confidence_boost,
            effective,
            trace_id,
        )
        effective = data_recommendation.recommended_strategy
    elif confidence < CONFIDENCE_GATE and not strategy_override:
        # 4. Confidence gate (original behavior)
        logger.info(
            "Confidence gate triggered (%.2f < %.2f), overriding strategy to '%s'",
            confidence, CONFIDENCE_GATE, fallback,
        )
        effective = fallback

    # 5. Explicit override always wins (final)
    if strategy_override:
        effective = strategy_override

    # 5b. Intent-aware resolution: before falling to the coarse task-type
    # map, inspect the analyzer's intent_label for strong keyword hits
    # (e.g. "audit"/"debug" → chain-of-thought). See A2 — task-type alone
    # routed an audit prompt to meta-prompting, which is a poor fit.
    #
    # A2 follow-up (2026-04-21 live verification): in production the LLM
    # analyzer returns the *task-type default* (e.g. "meta-prompting" for
    # analysis) directly rather than "auto". The original condition
    # `effective == "auto"` therefore never fired on real traffic — the
    # intent table sat dormant. Broadened to also fire when the current
    # strategy equals the task-type default: at that point the LLM is
    # effectively echoing the generic map, and a strong intent keyword is
    # the more specific signal. LLM picks diverging from the default
    # (few-shot, chain-of-thought, role-playing when task_type≠creative…)
    # are still respected — that divergence is the LLM saying "I chose
    # this specifically".
    if task_type and not strategy_override:
        task_default = _TASK_TYPE_DEFAULTS.get(task_type)
        if effective == "auto" or (task_default and effective == task_default):
            intent_pick = _resolve_intent_strategy(
                intent_label, available, blocked_strategies,
            )
            if intent_pick and intent_pick != effective:
                logger.info(
                    "%s→%s via intent_label='%s' (task_type=%s). trace_id=%s",
                    effective, intent_pick, intent_label, task_type, trace_id,
                )
                effective = intent_pick

    # 6. Auto resolution: "auto" should never reach the optimizer.
    # Resolve it to a task-type-appropriate named strategy so the
    # optimizer always gets concrete technique guidance.
    if effective == "auto" and task_type:
        resolved = _TASK_TYPE_DEFAULTS.get(task_type, "meta-prompting")
        if resolved in available and resolved not in blocked_strategies:
            logger.info(
                "Auto→%s resolution (task_type=%s). trace_id=%s",
                resolved, task_type, trace_id,
            )
            effective = resolved

    return effective


def compute_optimize_max_tokens(prompt_len: int) -> int:
    """Dynamic output budget: scale with input length, cap at 131072 (128K).

    Opus 4.7 supports 128K output tokens.  The optimize/refine phases use
    streaming (``complete_parsed_streaming``) which prevents HTTP timeouts,
    so the full 128K capacity is safely available.
    """
    return min(max(16384, prompt_len // 4 * 2), 131072)


# Reduced output budget for classification/evaluation phases.
# Analyze and score produce 500-2000 tokens; 4096 gives generous headroom
# while cutting the prior 16384 default that wasted model compute.
ANALYZE_MAX_TOKENS = 4096
SCORE_MAX_TOKENS = 4096

# ---------------------------------------------------------------------------
# A3: Analyze-phase effort ceiling
# ---------------------------------------------------------------------------
# The analyze phase is a classification task — it returns structured
# AnalysisResult JSON (~50 output tokens). Deep thinking at `max` on
# sonnet-4-6 burns 200+s of thinking tokens for no measurable quality gain.
# `task_budget` is Opus 4.7 only, so an effort clamp is the only lever that
# works across model families. `high` is the hard ceiling for this phase;
# optimize/score deliberately remain unclamped since they benefit from deep
# thinking.
ANALYZE_EFFORT_CEILING = "high"
_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")


def clamp_analyze_effort(pref: str | None) -> str:
    """Clamp an effort preference to the analyze-phase ceiling.

    ``pref`` above the ceiling is lowered to the ceiling; at-or-below passes
    through. Unknown / missing input defaults to ``"low"`` — we'd rather a
    typo cost a few ms than a few minutes.
    """
    if not pref:
        return "low"
    normalized = pref.lower()
    if normalized not in _EFFORT_ORDER:
        return "low"
    idx = _EFFORT_ORDER.index(normalized)
    ceil_idx = _EFFORT_ORDER.index(ANALYZE_EFFORT_CEILING)
    return _EFFORT_ORDER[min(idx, ceil_idx)]

# Cross-cluster pattern injection (Phase 0 — unified embedding architecture)
CROSS_CLUSTER_MIN_SOURCE_COUNT = 2     # min global_source_count to qualify
CROSS_CLUSTER_MAX_PATTERNS = 5         # max cross-cluster patterns per injection
CROSS_CLUSTER_RELEVANCE_FLOOR = 0.35   # min composite relevance score
CROSS_CLUSTER_SIMILARITY_THRESHOLD = 0.82  # cosine threshold for pattern dedup
