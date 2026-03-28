"""Shared constants and helpers for the optimization pipeline.

Used by both the internal pipeline (``pipeline.py``) and the sampling-based
pipeline (``sampling_pipeline.py``) to ensure identical gating behavior.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain discovery thresholds (ADR-004)
# ---------------------------------------------------------------------------
DOMAIN_DISCOVERY_MIN_MEMBERS = 5
DOMAIN_DISCOVERY_MIN_COHERENCE = 0.6
DOMAIN_DISCOVERY_CONSISTENCY = 0.60  # 60% of members share the same domain_raw primary

# Domain quality
DOMAIN_COHERENCE_FLOOR = 0.3

# Domain proliferation ceiling (ADR-004 Risk 1)
DOMAIN_COUNT_CEILING = 30

# Signal staleness ratio (ADR-004 Risk 2) — refresh when member_count doubles
SIGNAL_REFRESH_MEMBER_RATIO = 2.0

# Domain archival suggestion thresholds (ADR-004 Risk 1 self-correction)
DOMAIN_ARCHIVAL_IDLE_DAYS = 90
DOMAIN_ARCHIVAL_MIN_USAGE = 3

# Color constraints — domain colors must avoid perceptual proximity to tier accents
TIER_ACCENTS = ["#00e5ff", "#22ff88", "#fbbf24"]  # internal, sampling, passthrough

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Minimum analyzer confidence to trust its strategy selection.
# Below this threshold, the pipeline overrides the selected strategy to "auto".
CONFIDENCE_GATE = 0.7

# Lower threshold for domain override — wrong domain only affects clustering,
# not optimization quality.
DOMAIN_CONFIDENCE_GATE = 0.6

# Preferred fallback strategy when confidence gate triggers or validation fails.
FALLBACK_STRATEGY = "auto"

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


def resolve_effective_strategy(
    selected_strategy: str,
    available: list[str],
    blocked_strategies: set[str],
    confidence: float,
    strategy_override: str | None,
    trace_id: str,
) -> str:
    """Post-analysis strategy resolution chain.

    Applies, in order:
      1. **Disk validation** — reject hallucinated strategy names.
      2. **Adaptation block** — override low-approval strategies.
      3. **Confidence gate** — override when analyzer confidence is low.
      4. **Explicit override** — user's explicit choice always wins.

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

    # 3. Confidence gate
    if confidence < CONFIDENCE_GATE and not strategy_override:
        logger.info(
            "Confidence gate triggered (%.2f < %.2f), overriding strategy to '%s'",
            confidence, CONFIDENCE_GATE, fallback,
        )
        effective = fallback

    # 4. Explicit override always wins (final)
    if strategy_override:
        effective = strategy_override

    return effective


def compute_optimize_max_tokens(prompt_len: int) -> int:
    """Dynamic output budget: scale with input length, cap at 131072 (128K).

    Opus 4.6 supports 128K output tokens.  The optimize/refine phases use
    streaming (``complete_parsed_streaming``) which prevents HTTP timeouts,
    so the full 128K capacity is safely available.
    """
    return min(max(16384, prompt_len // 4 * 2), 131072)


# Reduced output budget for classification/evaluation phases.
# Analyze and score produce 500-2000 tokens; 4096 gives generous headroom
# while cutting the prior 16384 default that wasted model compute.
ANALYZE_MAX_TOKENS = 4096
SCORE_MAX_TOKENS = 4096
