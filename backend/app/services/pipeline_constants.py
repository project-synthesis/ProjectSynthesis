"""Shared constants for the optimization pipeline.

Used by both the internal pipeline (``pipeline.py``) and the sampling-based
pipeline (``sampling_pipeline.py``) to ensure identical gating behavior.
"""

# Minimum analyzer confidence to trust its strategy selection.
# Below this threshold, the pipeline overrides the selected strategy to "auto".
CONFIDENCE_GATE = 0.7

# Preferred fallback strategy when confidence gate triggers or validation fails.
FALLBACK_STRATEGY = "auto"


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

# Keywords used by the semantic check to validate a task_type="coding"
# classification.  If none of these appear in the prompt, confidence is
# reduced by 0.2 before the gate check.
CODING_KEYWORDS: set[str] = {
    "function", "class", "api", "code", "program",
    "script", "endpoint", "database", "module", "import",
}


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
