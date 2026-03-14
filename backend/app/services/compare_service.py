"""Comparison engine — classification, data extraction, insight generation.

Compares two Optimization records side-by-side, classifying the relationship
(REFORGE / STRATEGY / EVOLVED / CROSS), extracting structured data from each,
and generating ranked insights and merge directives.

Used by:
  - ``compare.py`` router (Task 4) calls ``compute_comparison()``
  - ``merge_service.py`` (Task 3) reads the ``CompareResponse``
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, AsyncGenerator

import numpy as np

from app.schemas.compare_models import (
    AdaptationComparison,
    CompareGuidance,
    CompareResponse,
    ContextComparison,
    EfficiencyComparison,
    ScoreComparison,
    StrategyComparison,
    StructuralComparison,
    ValidationComparison,
)

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

# ── Situation labels ──────────────────────────────────────────────────────

_SITUATION_LABELS = {
    "REFORGE": "Same prompt, same framework — reforged",
    "STRATEGY": "Same prompt, different framework — strategy shift",
    "EVOLVED": "Related prompts — evolved",
    "CROSS": "Distinct prompts — cross-comparison",
}

# ── Score dimension names (display → model attribute) ─────────────────────

_SCORE_DIMS = {
    "clarity": "clarity_score",
    "faithfulness": "faithfulness_score",
    "specificity": "specificity_score",
    "structure": "structure_score",
    "conciseness": "conciseness_score",
}


# ── 1. classify_situation ─────────────────────────────────────────────────

def classify_situation(
    similarity: float,
    fw_a: str | None,
    fw_b: str | None,
    used_embeddings: bool = True,
) -> str:
    """Pure function: classify the comparison relationship.

    Cosine thresholds (embeddings):
      >= 0.85 + same framework   → REFORGE
      >= 0.85 + different         → STRATEGY
      0.45 – 0.84                → EVOLVED
      < 0.45                     → CROSS

    Levenshtein thresholds (fallback — shifted lower because Levenshtein
    penalizes word reordering that cosine similarity tolerates):
      >= 0.80 → HIGH
      0.35 – 0.79 → MODERATE
      < 0.35 → LOW
    """
    high = 0.85 if used_embeddings else 0.80
    low = 0.45 if used_embeddings else 0.35

    if similarity >= high:
        if fw_a and fw_b and fw_a == fw_b:
            return "REFORGE"
        return "STRATEGY"
    if similarity >= low:
        return "EVOLVED"
    return "CROSS"


# ── 2. compute_similarity ────────────────────────────────────────────────

async def compute_similarity(text_a: str, text_b: str) -> tuple[float, bool]:
    """Compute semantic similarity between two texts.

    Uses the embedding service for cosine similarity when available.
    Falls back to normalized Levenshtein ratio when embeddings are not ready.

    Returns:
        Tuple of (similarity_score, used_embeddings). The boolean flag
        indicates whether cosine similarity (True) or Levenshtein fallback
        (False) was used — callers may need to apply different classification
        thresholds.
    """
    try:
        from app.services.embedding_service import get_embedding_service

        svc = get_embedding_service()
        if await svc.ensure_loaded():
            vec_a = await svc.embed_single(text_a)
            vec_b = await svc.embed_single(text_b)
            if vec_a.size > 0 and vec_b.size > 0:
                # Cosine similarity (vectors are already L2-normalized)
                sim = float(np.dot(vec_a, vec_b))
                return max(0.0, min(1.0, sim)), True
    except Exception:
        logger.debug("Embedding similarity failed, falling back to Levenshtein")

    # Fallback: normalized Levenshtein ratio
    return _levenshtein_ratio(text_a, text_b), False


def _levenshtein_ratio(a: str, b: str) -> float:
    """Compute normalized Levenshtein similarity: 1 - edit_distance / max_len."""
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    dist = _levenshtein_distance(a, b)
    return 1.0 - dist / max_len


def _levenshtein_distance(a: str, b: str) -> int:
    """Compute Levenshtein edit distance using O(min(m,n)) space."""
    if len(a) < len(b):
        return _levenshtein_distance(b, a)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            if ca == cb:
                curr.append(prev[j])
            else:
                curr.append(1 + min(prev[j], prev[j + 1], curr[j]))
        prev = curr
    return prev[-1]


# ── 3. extract_scores ────────────────────────────────────────────────────

def extract_scores(opt: Any) -> dict:
    """Pull clarity, faithfulness, specificity, structure, conciseness, overall."""
    return {
        "clarity": getattr(opt, "clarity_score", None),
        "faithfulness": getattr(opt, "faithfulness_score", None),
        "specificity": getattr(opt, "specificity_score", None),
        "structure": getattr(opt, "structure_score", None),
        "conciseness": getattr(opt, "conciseness_score", None),
        "overall": getattr(opt, "overall_score", None),
    }


# ── 4. extract_structural ────────────────────────────────────────────────

def extract_structural(opt: Any) -> dict:
    """Compute word counts, sentence count, and expansion ratio."""
    import re

    raw = getattr(opt, "raw_prompt", "") or ""
    optimized = getattr(opt, "optimized_prompt", "") or ""
    input_words = len(raw.split())
    output_words = len(optimized.split())
    expansion = output_words / input_words if input_words > 0 else 0.0
    complexity = getattr(opt, "complexity", None)
    # Sentence count: split on sentence-ending punctuation
    sentences = len(re.split(r"[.!?]+", optimized.strip())) if optimized.strip() else 0
    return {
        "input_words": input_words,
        "output_words": output_words,
        "expansion": round(expansion, 2),
        "complexity": complexity,
        "sentence_count": sentences,
    }


# ── 5. extract_efficiency ────────────────────────────────────────────────

def extract_efficiency(opt: Any) -> dict:
    """Extract timing, token counts, cost, and score-per-token.

    Falls back to summing per-stage token counts from ``stage_durations``
    when the pipeline-wide ``total_input_tokens``/``total_output_tokens``
    columns are not populated (e.g. CLI provider).
    """
    duration_ms = getattr(opt, "duration_ms", None)
    input_tokens = getattr(opt, "total_input_tokens", None) or 0
    output_tokens = getattr(opt, "total_output_tokens", None) or 0
    total_tokens = (input_tokens + output_tokens) if (input_tokens or output_tokens) else None

    # Fallback: sum per-stage token counts from stage_durations JSON
    stage_tokens: dict[str, int] | None = None
    raw_stages = getattr(opt, "stage_durations", None)
    if raw_stages:
        if isinstance(raw_stages, str):
            try:
                import json as _json
                raw_stages = _json.loads(raw_stages)
            except (ValueError, TypeError):
                raw_stages = None
        if isinstance(raw_stages, dict):
            stage_tokens = {}
            fallback_total = 0
            for stage_name, stage_data in raw_stages.items():
                if isinstance(stage_data, dict):
                    # Prefer input_tokens+output_tokens; fall back to token_count
                    s_in = stage_data.get("input_tokens", 0) or 0
                    s_out = stage_data.get("output_tokens", 0) or 0
                    s_count = stage_data.get("token_count", 0) or 0
                    stage_tok = (s_in + s_out) if (s_in or s_out) else s_count
                    if stage_tok:
                        stage_tokens[stage_name] = stage_tok
                        fallback_total += stage_tok
            if total_tokens is None and fallback_total > 0:
                total_tokens = fallback_total

    # Final fallback: estimate from prompt text length (~1.3 tokens/word)
    is_estimated = bool(getattr(opt, "usage_is_estimated", False))
    if total_tokens is None:
        raw_text = getattr(opt, "raw_prompt", "") or ""
        opt_text = getattr(opt, "optimized_prompt", "") or ""
        word_count = len(raw_text.split()) + len(opt_text.split())
        if word_count > 0:
            total_tokens = int(word_count * 1.3)
            is_estimated = True

    cost = getattr(opt, "estimated_cost_usd", None)
    overall = getattr(opt, "overall_score", None)

    score_per_token = None
    if overall is not None and total_tokens:
        score_per_token = round(overall / total_tokens * 1000, 4)

    return {
        "duration_ms": duration_ms,
        "tokens": total_tokens,
        "cost": cost,
        "score_per_token": score_per_token,
        "stage_tokens": stage_tokens if stage_tokens else None,
        "is_estimated": is_estimated,
    }


# ── 6. extract_strategy ──────────────────────────────────────────────────

def extract_strategy(opt: Any) -> dict:
    """Extract framework, strategy source, rationale, guardrails, and optimizer notes."""
    guardrails = _parse_json_field(getattr(opt, "active_guardrails", None), default=[])
    secondary = _parse_json_field(getattr(opt, "secondary_frameworks", None), default=[])
    return {
        "framework": getattr(opt, "primary_framework", None),
        "source": getattr(opt, "strategy_source", None),
        "rationale": getattr(opt, "strategy_rationale", None),
        "guardrails": guardrails if isinstance(guardrails, list) else [],
        "secondary_frameworks": secondary if isinstance(secondary, list) else [],
        "approach_notes": getattr(opt, "approach_notes", None),
        "optimization_notes": getattr(opt, "optimization_notes", None),
    }


# ── 7. extract_context ───────────────────────────────────────────────────

def extract_context(opt: Any) -> dict:
    """Extract repo linkage, codebase context, task type, and instruction count."""
    repo = getattr(opt, "linked_repo_full_name", None)
    codebase = getattr(opt, "codebase_context_snapshot", None)
    has_codebase = bool(codebase)
    compliance = _parse_json_field(
        getattr(opt, "per_instruction_compliance", None), default=[],
    )
    instruction_count = len(compliance) if isinstance(compliance, list) else 0
    return {
        "repo": repo,
        "has_codebase": has_codebase,
        "instruction_count": instruction_count,
        "task_type": getattr(opt, "task_type", None),
    }


# ── 8. extract_validation ────────────────────────────────────────────────

def extract_validation(opt: Any) -> dict:
    """Extract verdict, parsed issues, parsed changes, improvement flag, weaknesses, and strengths."""
    issues = _parse_json_field(getattr(opt, "issues", None), default=[])
    changes = _parse_json_field(getattr(opt, "changes_made", None), default=[])
    weaknesses = _parse_json_field(getattr(opt, "weaknesses", None), default=[])
    strengths = _parse_json_field(getattr(opt, "strengths", None), default=[])
    return {
        "verdict": getattr(opt, "verdict", None),
        "issues": issues if isinstance(issues, list) else [],
        "changes_made": changes if isinstance(changes, list) else [],
        "is_improvement": getattr(opt, "is_improvement", None),
        "weaknesses": weaknesses if isinstance(weaknesses, list) else [],
        "strengths": strengths if isinstance(strengths, list) else [],
    }


# ── 9. extract_adaptation ────────────────────────────────────────────────

async def extract_adaptation(a: Any, b: Any) -> AdaptationComparison:
    """Compare adaptation snapshots between two optimizations.

    Queries real ``Feedback`` records between the two optimizations'
    creation timestamps for an accurate ``feedbacks_between`` count.
    Weight shifts and guardrails come from adaptation snapshot diffs.
    """
    snap_a = _parse_json_field(getattr(a, "adaptation_snapshot", None), default={})
    snap_b = _parse_json_field(getattr(b, "adaptation_snapshot", None), default={})
    if not isinstance(snap_a, dict):
        snap_a = {}
    if not isinstance(snap_b, dict):
        snap_b = {}

    # Weight shifts: compare dimension weights between snapshots
    weights_a = snap_a.get("dimension_weights", {})
    weights_b = snap_b.get("dimension_weights", {})
    all_dims = set(weights_a.keys()) | set(weights_b.keys())
    weight_shifts = {}
    for dim in all_dims:
        wa = weights_a.get(dim, 0.0)
        wb = weights_b.get(dim, 0.0)
        delta = round(wb - wa, 4)
        if abs(delta) > 0.001:
            weight_shifts[dim] = delta

    # Guardrails added in B but not in A
    guardrails_a = set(
        extract_strategy(a).get("guardrails", []),
    )
    guardrails_b = set(
        extract_strategy(b).get("guardrails", []),
    )
    guardrails_added = sorted(guardrails_b - guardrails_a)

    # Count real Feedback records between the two optimizations
    feedbacks_between = 0
    created_a = getattr(a, "created_at", None)
    created_b = getattr(b, "created_at", None)
    user_id = getattr(a, "user_id", None)
    if user_id and created_a and created_b:
        try:
            from sqlalchemy import func as sa_func
            from sqlalchemy import select as sa_select

            from app.database import async_session
            from app.models.feedback import Feedback

            earlier = min(created_a, created_b)
            later = max(created_a, created_b)
            async with async_session() as db:
                count = await db.scalar(
                    sa_select(sa_func.count())
                    .select_from(Feedback)
                    .where(
                        Feedback.user_id == user_id,
                        Feedback.created_at >= earlier,
                        Feedback.created_at <= later,
                    )
                )
                feedbacks_between = count or 0
        except Exception:
            # Fall back to snapshot-based count on DB error
            fb_count_a = snap_a.get("feedback_count", 0)
            fb_count_b = snap_b.get("feedback_count", 0)
            feedbacks_between = max(0, fb_count_b - fb_count_a)

    return AdaptationComparison(
        feedbacks_between=feedbacks_between,
        weight_shifts=weight_shifts,
        guardrails_added=guardrails_added,
    )


# ── 10. compute_modifiers ────────────────────────────────────────────────

async def compute_modifiers(a: Any, b: Any) -> list[str]:
    """Return list of applicable modifier strings."""
    modifiers: list[str] = []

    repo_a = getattr(a, "linked_repo_full_name", None)
    repo_b = getattr(b, "linked_repo_full_name", None)

    if bool(repo_a) != bool(repo_b):
        modifiers.append("repo_added")
    elif repo_a and repo_b and repo_a != repo_b:
        modifiers.append("repo_changed")

    # Adapted: time gap > 1hr AND feedback exists between runs
    created_a = getattr(a, "created_at", None)
    created_b = getattr(b, "created_at", None)
    if created_a and created_b:
        try:
            from datetime import datetime

            def _to_dt(val: Any) -> datetime | None:
                if isinstance(val, datetime):
                    return val
                if isinstance(val, str):
                    return datetime.fromisoformat(val)
                return None

            dt_a = _to_dt(created_a)
            dt_b = _to_dt(created_b)
            if dt_a and dt_b:
                gap = abs((dt_b - dt_a).total_seconds())
                # Check both time gap AND feedback between runs
                adaptation = await extract_adaptation(a, b)
                if gap > 3600 and adaptation.feedbacks_between > 0:
                    modifiers.append("adapted")
        except (ValueError, TypeError):
            pass

    # Complexity shift
    complexity_a = getattr(a, "complexity", None)
    complexity_b = getattr(b, "complexity", None)
    if complexity_a and complexity_b and complexity_a != complexity_b:
        modifiers.append("complexity_shift")

    return modifiers


# ── 11. generate_top_insights ─────────────────────────────────────────────

def generate_top_insights(
    scores: dict,
    structural: dict,
    efficiency: dict,
    strategy: dict,
    context: dict,
    validation: dict,
    adaptation: dict,
    situation: str,
) -> list[str]:
    """Template-driven insight generation. Ranks by delta magnitude, returns top 3."""
    candidates: list[tuple[float, str]] = []

    deltas = scores.get("deltas", {})
    floors = scores.get("floors", [])
    ceilings = scores.get("ceilings", [])

    fw_a = strategy.get("a_framework", "A")
    fw_b = strategy.get("b_framework", "B")

    # Score-delta insights
    for dim, delta in deltas.items():
        if delta is None:
            continue
        abs_delta = abs(delta)
        if abs_delta >= 0.5:
            side = fw_a if delta > 0 else fw_b
            candidates.append((
                abs_delta,
                f"{side}'s approach drives +{abs_delta:.1f} {dim} advantage",
            ))

    # Floor insights
    for dim in floors:
        suggestion = _floor_suggestion(dim)
        candidates.append((
            3.0,  # Fixed priority for floors
            f"Both score low on {dim} — {suggestion} to break ceiling",
        ))

    # Ceiling insights
    for dim in ceilings:
        candidates.append((
            1.0,
            f"Both excel at {dim} (9+) — preserve this strength in any merge",
        ))

    # Efficiency insight
    a_dur = efficiency.get("a_duration_ms")
    b_dur = efficiency.get("b_duration_ms")
    if a_dur and b_dur and abs(a_dur - b_dur) > 1000:
        slower = "A" if a_dur > b_dur else "B"
        diff_s = abs(a_dur - b_dur) / 1000
        # Check if slower side has codebase context
        has_codebase_a = context.get("a_has_codebase", False)
        has_codebase_b = context.get("b_has_codebase", False)
        if (slower == "A" and has_codebase_a) or (slower == "B" and has_codebase_b):
            # Find which dimension benefited most
            best_dim = max(deltas.items(), key=lambda x: abs(x[1] or 0), default=(None, 0))
            if best_dim[0]:
                assessment = "worthwhile" if abs(best_dim[1] or 0) > 1.0 else "marginal"
                candidates.append((
                    2.5,
                    f"{slower}'s codebase context: +{diff_s:.1f}s for "
                    f"+{abs(best_dim[1] or 0):.1f} {best_dim[0]} — ROI {assessment}",
                ))

    # Context insight: repo added
    if context.get("a_repo") != context.get("b_repo"):
        if context.get("b_repo") and not context.get("a_repo"):
            candidates.append((
                2.0,
                f"B added repo context ({context['b_repo']}) — check if specificity improved",
            ))
        elif context.get("a_repo") and not context.get("b_repo"):
            candidates.append((
                2.0,
                f"A had repo context ({context['a_repo']}) that B lacks — may explain score differences",
            ))

    # Adaptation insight
    fb_between = adaptation.get("feedbacks_between", 0)
    weight_shifts = adaptation.get("weight_shifts", {})
    if fb_between > 0 and weight_shifts:
        shifted_dims = ", ".join(weight_shifts.keys())
        candidates.append((
            2.0,
            f"{fb_between} feedbacks between runs shifted weights on {shifted_dims}",
        ))

    # Sort by magnitude descending and take top 3
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in candidates[:3]]


def _floor_suggestion(dim: str) -> str:
    """Return a suggestion for a floor dimension."""
    suggestions = {
        "clarity": "add explicit role and goal framing",
        "faithfulness": "tighten output constraints to match intent",
        "specificity": "include concrete examples or constraints",
        "structure": "add section headers or numbered steps",
        "conciseness": "trim redundant instructions and qualifiers",
    }
    return suggestions.get(dim, "review and strengthen this dimension")


# ── 12. generate_merge_directives ─────────────────────────────────────────

def generate_merge_directives(
    scores: dict,
    structural: dict,
    efficiency: dict,
    strategy: dict,
    context: dict,
    validation: dict,
) -> list[str]:
    """Ordered by delta magnitude. Each cites the data point that justifies it."""
    directives: list[tuple[float, str]] = []

    deltas = scores.get("deltas", {})
    fw_a = strategy.get("a_framework", "A")
    fw_b = strategy.get("b_framework", "B")

    # Per-dimension directives
    for dim, delta in deltas.items():
        if delta is None:
            continue
        abs_delta = abs(delta)
        if abs_delta >= 0.5:
            winner = fw_a if delta > 0 else fw_b
            directives.append((
                abs_delta,
                f"Preserve {winner}'s {dim} approach — drives +{abs_delta:.1f} {dim}",
            ))

    # Structural directive
    a_exp = structural.get("a_expansion", 0)
    b_exp = structural.get("b_expansion", 0)
    if a_exp and b_exp:
        if abs(a_exp - b_exp) > 0.5:
            more_concise = "A" if a_exp < b_exp else "B"
            directives.append((
                1.5,
                f"Use {more_concise}'s structure density "
                f"({min(a_exp, b_exp):.1f}x vs {max(a_exp, b_exp):.1f}x expansion)",
            ))

    # Repo context directive
    if context.get("a_has_codebase") or context.get("b_has_codebase"):
        has_repo_side = "A" if context.get("a_has_codebase") else "B"
        directives.append((
            1.0,
            f"Retain {has_repo_side}'s codebase-grounded specificity",
        ))

    # Validation issues directive
    a_issues = validation.get("a_issues", [])
    b_issues = validation.get("b_issues", [])
    if a_issues or b_issues:
        combined = set(a_issues) | set(b_issues)
        directives.append((
            0.8,
            f"Address flagged issues: {', '.join(sorted(combined)[:3])}",
        ))

    directives.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in directives]


# ── 13. generate_cross_patterns ───────────────────────────────────────────

def generate_cross_patterns(a: Any, b: Any, scores: dict) -> list[str]:
    """CROSS-only patterns. Returns empty for other situations."""
    patterns: list[str] = []

    a_scores = scores.get("a_scores", {})
    b_scores = scores.get("b_scores", {})
    fw_a = getattr(a, "primary_framework", None) or "A"
    fw_b = getattr(b, "primary_framework", None) or "B"
    task_a = getattr(a, "task_type", None)
    task_b = getattr(b, "task_type", None)

    # Task type comparison
    if task_a and task_b:
        if task_a != task_b:
            patterns.append(
                f"Different task types ({task_a} vs {task_b}) — scores may not be directly comparable"
            )
        else:
            patterns.append(
                f"Same task type ({task_a}) — framework choice is the key variable"
            )

    # Framework suitability comparison
    if fw_a != fw_b:
        a_overall = a_scores.get("overall")
        b_overall = b_scores.get("overall")
        if a_overall is not None and b_overall is not None:
            if abs(a_overall - b_overall) >= 1.0:
                better = fw_a if a_overall > b_overall else fw_b
                patterns.append(
                    f"{better} outperforms by {abs(a_overall - b_overall):.1f} overall for this prompt shape"
                )

    # Structural approach comparison
    raw_a = getattr(a, "raw_prompt", "") or ""
    raw_b = getattr(b, "raw_prompt", "") or ""
    len_ratio = len(raw_a.split()) / max(len(raw_b.split()), 1)
    if len_ratio > 2.0 or len_ratio < 0.5:
        a_words = len(raw_a.split())
        b_words = len(raw_b.split())
        patterns.append(
            f"Significant input length disparity "
            f"({a_words} vs {b_words} words) — different prompt philosophies"
        )

    return patterns


# ── 14. compute_comparison (orchestrator) ─────────────────────────────────

async def compute_comparison(
    a: Any,
    b: Any,
    provider: "LLMProvider | None" = None,
) -> CompareResponse:
    """Orchestrator: extract, classify, generate insights, optional LLM guidance."""
    # Extract all data
    scores_a = extract_scores(a)
    scores_b = extract_scores(b)
    struct_a = extract_structural(a)
    struct_b = extract_structural(b)
    eff_a = extract_efficiency(a)
    eff_b = extract_efficiency(b)
    strat_a = extract_strategy(a)
    strat_b = extract_strategy(b)
    ctx_a = extract_context(a)
    ctx_b = extract_context(b)
    val_a = extract_validation(a)
    val_b = extract_validation(b)
    adaptation = await extract_adaptation(a, b)
    modifiers = await compute_modifiers(a, b)

    # Compute similarity
    raw_a = getattr(a, "raw_prompt", "") or ""
    raw_b = getattr(b, "raw_prompt", "") or ""
    similarity, used_embeddings = await compute_similarity(raw_a, raw_b)

    # Classify situation (thresholds differ based on similarity method)
    fw_a = getattr(a, "primary_framework", None)
    fw_b = getattr(b, "primary_framework", None)
    situation = classify_situation(similarity, fw_a, fw_b, used_embeddings=used_embeddings)

    # Build score comparison
    deltas: dict[str, float | None] = {}
    ceilings: list[str] = []
    floors: list[str] = []
    for dim in _SCORE_DIMS:
        sa = scores_a.get(dim)
        sb = scores_b.get(dim)
        if sa is not None and sb is not None:
            deltas[dim] = round(sa - sb, 2)
            if sa >= 9 and sb >= 9:
                ceilings.append(dim)
            if sa < 5 and sb < 5:
                floors.append(dim)
        else:
            deltas[dim] = None

    overall_a = scores_a.get("overall")
    overall_b = scores_b.get("overall")
    overall_delta = round(overall_a - overall_b, 2) if overall_a is not None and overall_b is not None else None

    winner = None
    if overall_delta is not None:
        if overall_delta > 0:
            winner = "a"
        elif overall_delta < 0:
            winner = "b"

    score_comparison = ScoreComparison(
        dimensions=list(_SCORE_DIMS.keys()),
        a_scores=scores_a,
        b_scores=scores_b,
        deltas=deltas,
        overall_delta=overall_delta,
        winner=winner,
        ceilings=ceilings,
        floors=floors,
    )

    # Build structural comparison
    structural_comparison = StructuralComparison(
        a_input_words=struct_a["input_words"],
        b_input_words=struct_b["input_words"],
        a_output_words=struct_a["output_words"],
        b_output_words=struct_b["output_words"],
        a_expansion=struct_a["expansion"],
        b_expansion=struct_b["expansion"],
        a_complexity=struct_a["complexity"],
        b_complexity=struct_b["complexity"],
    )

    # Build efficiency comparison
    efficiency_comparison = EfficiencyComparison(
        a_duration_ms=eff_a["duration_ms"],
        b_duration_ms=eff_b["duration_ms"],
        a_tokens=eff_a["tokens"],
        b_tokens=eff_b["tokens"],
        a_cost=eff_a["cost"],
        b_cost=eff_b["cost"],
        a_score_per_token=eff_a["score_per_token"],
        b_score_per_token=eff_b["score_per_token"],
        a_stage_tokens=eff_a.get("stage_tokens"),
        b_stage_tokens=eff_b.get("stage_tokens"),
        a_is_estimated=eff_a.get("is_estimated", False),
        b_is_estimated=eff_b.get("is_estimated", False),
    )

    # Build strategy comparison
    strategy_comparison = StrategyComparison(
        a_framework=strat_a["framework"],
        a_source=strat_a["source"],
        a_rationale=strat_a["rationale"],
        a_guardrails=strat_a["guardrails"],
        a_optimization_notes=strat_a.get("optimization_notes"),
        b_framework=strat_b["framework"],
        b_source=strat_b["source"],
        b_rationale=strat_b["rationale"],
        b_guardrails=strat_b["guardrails"],
        b_optimization_notes=strat_b.get("optimization_notes"),
    )

    # Build context comparison
    context_comparison = ContextComparison(
        a_repo=ctx_a["repo"],
        b_repo=ctx_b["repo"],
        a_has_codebase=ctx_a["has_codebase"],
        b_has_codebase=ctx_b["has_codebase"],
        a_instruction_count=ctx_a["instruction_count"],
        b_instruction_count=ctx_b["instruction_count"],
        a_task_type=ctx_a.get("task_type"),
        b_task_type=ctx_b.get("task_type"),
    )

    # Build validation comparison
    validation_comparison = ValidationComparison(
        a_verdict=val_a["verdict"],
        b_verdict=val_b["verdict"],
        a_issues=val_a["issues"],
        b_issues=val_b["issues"],
        a_changes_made=val_a["changes_made"],
        b_changes_made=val_b["changes_made"],
        a_is_improvement=val_a["is_improvement"],
        b_is_improvement=val_b["is_improvement"],
    )

    # Aggregated dicts for insight generation
    scores_data = {
        "deltas": deltas,
        "floors": floors,
        "ceilings": ceilings,
        "a_scores": scores_a,
        "b_scores": scores_b,
    }
    structural_data = {
        "a_input_words": struct_a["input_words"],
        "b_input_words": struct_b["input_words"],
        "a_expansion": struct_a["expansion"],
        "b_expansion": struct_b["expansion"],
    }
    efficiency_data = {
        "a_duration_ms": eff_a["duration_ms"],
        "b_duration_ms": eff_b["duration_ms"],
    }
    strategy_data = {
        "a_framework": strat_a["framework"],
        "b_framework": strat_b["framework"],
    }
    context_data = {
        "a_repo": ctx_a["repo"],
        "b_repo": ctx_b["repo"],
        "a_has_codebase": ctx_a["has_codebase"],
        "b_has_codebase": ctx_b["has_codebase"],
    }
    validation_data = {
        "a_verdict": val_a["verdict"],
        "b_verdict": val_b["verdict"],
        "a_issues": val_a["issues"],
        "b_issues": val_b["issues"],
    }
    adaptation_data = {
        "feedbacks_between": adaptation.feedbacks_between,
        "weight_shifts": adaptation.weight_shifts,
    }

    # Generate insights and directives
    top_insights = generate_top_insights(
        scores=scores_data,
        structural=structural_data,
        efficiency=efficiency_data,
        strategy=strategy_data,
        context=context_data,
        validation=validation_data,
        adaptation=adaptation_data,
        situation=situation,
    )

    merge_directives = generate_merge_directives(
        scores=scores_data,
        structural=structural_data,
        efficiency=efficiency_data,
        strategy=strategy_data,
        context=context_data,
        validation=validation_data,
    )

    # Cross patterns only for CROSS situation
    cross_patterns = (
        generate_cross_patterns(a, b, scores_data)
        if situation == "CROSS"
        else []
    )

    # Build insight headline
    insight_headline = _build_insight_headline(
        situation, overall_delta, fw_a, fw_b, similarity, deltas=deltas,
    )

    # Optional LLM guidance
    guidance = None
    if provider is not None:
        guidance = await _generate_llm_guidance(
            provider=provider,
            situation=situation,
            scores_a=scores_a,
            scores_b=scores_b,
            strategy_a=strat_a,
            strategy_b=strat_b,
            top_insights=top_insights,
            merge_directives=merge_directives,
        )

    return CompareResponse(
        situation=situation,
        situation_label=_SITUATION_LABELS[situation],
        insight_headline=insight_headline,
        modifiers=modifiers,
        a=a.to_dict() if hasattr(a, "to_dict") else {},
        b=b.to_dict() if hasattr(b, "to_dict") else {},
        scores=score_comparison,
        structural=structural_comparison,
        efficiency=efficiency_comparison,
        strategy=strategy_comparison,
        context=context_comparison,
        validation=validation_comparison,
        adaptation=adaptation,
        top_insights=top_insights,
        cross_patterns=cross_patterns,
        a_is_trashed=getattr(a, "deleted_at", None) is not None,
        b_is_trashed=getattr(b, "deleted_at", None) is not None,
        guidance=guidance,
    )


# ── 15. stream_comparison (SSE generator) ─────────────────────────────────

async def stream_comparison(
    a: Any,
    b: Any,
    provider: "LLMProvider | None" = None,
) -> AsyncGenerator[dict, None]:
    """Yield real progress events during comparison, then the final result.

    Each event is a dict with ``type`` = ``"step"`` or ``"result"``.
    Step events have ``step`` (id) and ``label`` (display text).
    The result event has ``data`` (full CompareResponse as dict).
    """
    yield {"type": "step", "step": "similarity", "label": "Computing semantic similarity..."}
    raw_a = getattr(a, "raw_prompt", "") or ""
    raw_b = getattr(b, "raw_prompt", "") or ""
    similarity, used_embeddings = await compute_similarity(raw_a, raw_b)

    yield {"type": "step", "step": "classification", "label": "Classifying relationship..."}
    fw_a = getattr(a, "primary_framework", None)
    fw_b = getattr(b, "primary_framework", None)
    situation = classify_situation(similarity, fw_a, fw_b, used_embeddings=used_embeddings)

    yield {"type": "step", "step": "extraction", "label": "Extracting score intelligence..."}
    scores_a = extract_scores(a)
    scores_b = extract_scores(b)
    struct_a = extract_structural(a)
    struct_b = extract_structural(b)
    eff_a = extract_efficiency(a)
    eff_b = extract_efficiency(b)
    strat_a = extract_strategy(a)
    strat_b = extract_strategy(b)
    ctx_a = extract_context(a)
    ctx_b = extract_context(b)
    val_a = extract_validation(a)
    val_b = extract_validation(b)
    adaptation = await extract_adaptation(a, b)
    modifiers = await compute_modifiers(a, b)

    yield {"type": "step", "step": "insights", "label": "Generating insights and directives..."}

    # Build score comparison (same logic as compute_comparison)
    deltas: dict[str, float | None] = {}
    ceilings: list[str] = []
    floors: list[str] = []
    for dim in _SCORE_DIMS:
        sa = scores_a.get(dim)
        sb = scores_b.get(dim)
        if sa is not None and sb is not None:
            deltas[dim] = round(sa - sb, 2)
            if sa >= 9 and sb >= 9:
                ceilings.append(dim)
            if sa < 5 and sb < 5:
                floors.append(dim)
        else:
            deltas[dim] = None

    overall_a = scores_a.get("overall")
    overall_b = scores_b.get("overall")
    overall_delta = (
        round(overall_a - overall_b, 2)
        if overall_a is not None and overall_b is not None
        else None
    )
    winner = None
    if overall_delta is not None:
        winner = "a" if overall_delta > 0 else ("b" if overall_delta < 0 else None)

    score_comparison = ScoreComparison(
        dimensions=list(_SCORE_DIMS.keys()),
        a_scores=scores_a, b_scores=scores_b, deltas=deltas,
        overall_delta=overall_delta, winner=winner,
        ceilings=ceilings, floors=floors,
    )

    # Build sub-models
    structural_comparison = StructuralComparison(
        a_input_words=struct_a["input_words"], b_input_words=struct_b["input_words"],
        a_output_words=struct_a["output_words"], b_output_words=struct_b["output_words"],
        a_expansion=struct_a["expansion"], b_expansion=struct_b["expansion"],
        a_complexity=struct_a["complexity"], b_complexity=struct_b["complexity"],
    )
    efficiency_comparison = EfficiencyComparison(
        a_duration_ms=eff_a["duration_ms"], b_duration_ms=eff_b["duration_ms"],
        a_tokens=eff_a["tokens"], b_tokens=eff_b["tokens"],
        a_cost=eff_a["cost"], b_cost=eff_b["cost"],
        a_score_per_token=eff_a["score_per_token"], b_score_per_token=eff_b["score_per_token"],
        a_stage_tokens=eff_a.get("stage_tokens"), b_stage_tokens=eff_b.get("stage_tokens"),
        a_is_estimated=eff_a.get("is_estimated", False), b_is_estimated=eff_b.get("is_estimated", False),
    )
    strategy_comparison = StrategyComparison(
        a_framework=strat_a["framework"], a_source=strat_a["source"],
        a_rationale=strat_a["rationale"], a_guardrails=strat_a["guardrails"],
        a_optimization_notes=strat_a.get("optimization_notes"),
        b_framework=strat_b["framework"], b_source=strat_b["source"],
        b_rationale=strat_b["rationale"], b_guardrails=strat_b["guardrails"],
        b_optimization_notes=strat_b.get("optimization_notes"),
    )
    context_comparison = ContextComparison(
        a_repo=ctx_a["repo"], b_repo=ctx_b["repo"],
        a_has_codebase=ctx_a["has_codebase"], b_has_codebase=ctx_b["has_codebase"],
        a_instruction_count=ctx_a["instruction_count"],
        b_instruction_count=ctx_b["instruction_count"],
        a_task_type=ctx_a.get("task_type"), b_task_type=ctx_b.get("task_type"),
    )
    validation_comparison = ValidationComparison(
        a_verdict=val_a["verdict"], b_verdict=val_b["verdict"],
        a_issues=val_a["issues"], b_issues=val_b["issues"],
        a_changes_made=val_a["changes_made"], b_changes_made=val_b["changes_made"],
        a_is_improvement=val_a["is_improvement"],
        b_is_improvement=val_b["is_improvement"],
    )

    # Aggregated dicts for insight generation
    scores_data = {
        "deltas": deltas, "floors": floors, "ceilings": ceilings,
        "a_scores": scores_a, "b_scores": scores_b,
    }
    structural_data = {
        "a_input_words": struct_a["input_words"],
        "b_input_words": struct_b["input_words"],
        "a_expansion": struct_a["expansion"],
        "b_expansion": struct_b["expansion"],
    }
    efficiency_data = {
        "a_duration_ms": eff_a["duration_ms"],
        "b_duration_ms": eff_b["duration_ms"],
    }
    strategy_data = {
        "a_framework": strat_a["framework"],
        "b_framework": strat_b["framework"],
    }
    context_data = {
        "a_repo": ctx_a["repo"], "b_repo": ctx_b["repo"],
        "a_has_codebase": ctx_a["has_codebase"],
        "b_has_codebase": ctx_b["has_codebase"],
    }
    validation_data = {
        "a_verdict": val_a["verdict"], "b_verdict": val_b["verdict"],
        "a_issues": val_a["issues"], "b_issues": val_b["issues"],
    }
    adaptation_data = {
        "feedbacks_between": adaptation.feedbacks_between,
        "weight_shifts": adaptation.weight_shifts,
    }

    top_insights = generate_top_insights(
        scores=scores_data, structural=structural_data,
        efficiency=efficiency_data, strategy=strategy_data,
        context=context_data, validation=validation_data,
        adaptation=adaptation_data, situation=situation,
    )
    merge_directives = generate_merge_directives(
        scores=scores_data, structural=structural_data,
        efficiency=efficiency_data, strategy=strategy_data,
        context=context_data, validation=validation_data,
    )
    cross_patterns = (
        generate_cross_patterns(a, b, scores_data)
        if situation == "CROSS" else []
    )
    insight_headline = _build_insight_headline(
        situation, overall_delta, fw_a, fw_b, similarity, deltas=deltas,
    )

    # LLM guidance — the bottleneck
    guidance = None
    if provider is not None:
        yield {
            "type": "step", "step": "guidance",
            "label": "LLM generating guidance...",
        }
        guidance = await _generate_llm_guidance(
            provider=provider,
            situation=situation,
            scores_a=scores_a, scores_b=scores_b,
            strategy_a=strat_a, strategy_b=strat_b,
            top_insights=top_insights,
            merge_directives=merge_directives,
        )

    yield {"type": "step", "step": "complete", "label": "Analysis complete"}

    result = CompareResponse(
        situation=situation,
        situation_label=_SITUATION_LABELS[situation],
        insight_headline=insight_headline,
        modifiers=modifiers,
        a=a.to_dict() if hasattr(a, "to_dict") else {},
        b=b.to_dict() if hasattr(b, "to_dict") else {},
        scores=score_comparison,
        structural=structural_comparison,
        efficiency=efficiency_comparison,
        strategy=strategy_comparison,
        context=context_comparison,
        validation=validation_comparison,
        adaptation=adaptation,
        top_insights=top_insights,
        cross_patterns=cross_patterns,
        a_is_trashed=getattr(a, "deleted_at", None) is not None,
        b_is_trashed=getattr(b, "deleted_at", None) is not None,
        guidance=guidance,
    )
    yield {"type": "result", "data": result.model_dump()}


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_json_field(val: Any, default: Any = None) -> Any:
    """Parse a JSON field defensively — may be a string or already parsed."""
    if val is None:
        return default
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return default
    return val


def _build_insight_headline(
    situation: str,
    overall_delta: float | None,
    fw_a: str | None,
    fw_b: str | None,
    similarity: float,
    deltas: dict[str, float | None] | None = None,
) -> str:
    """Build a concise headline summarizing the comparison.

    Appends the top 2 dimension deltas (by magnitude) when available.
    """
    if situation == "REFORGE":
        if overall_delta is not None and abs(overall_delta) >= 0.5:
            direction = "improved" if overall_delta > 0 else "regressed"
            base = f"Reforged with {fw_a or 'same framework'}: {direction} by {abs(overall_delta):.1f}"
        else:
            base = f"Reforged with {fw_a or 'same framework'}: marginal change"
    elif situation == "STRATEGY":
        if overall_delta is not None:
            better = fw_a if overall_delta > 0 else fw_b
            base = f"Strategy shift: {better} leads by {abs(overall_delta):.1f}"
        else:
            base = f"Strategy shift: {fw_a} vs {fw_b}"
    elif situation == "EVOLVED":
        base = f"Evolved prompt ({similarity:.0%} similar): comparing growth"
    else:
        base = f"Cross-comparison ({similarity:.0%} similar): distinct prompts"

    # Append top 2 dimension deltas for specificity
    if deltas:
        ranked = sorted(
            ((d, v) for d, v in deltas.items() if v is not None and v != 0.0),
            key=lambda x: abs(x[1]),
            reverse=True,
        )[:2]
        if ranked:
            parts = [f"{dim} {val:+.1f}" for dim, val in ranked]
            base += f", {', '.join(parts)}"

    return base


async def _generate_llm_guidance(
    provider: "LLMProvider",
    situation: str,
    scores_a: dict,
    scores_b: dict,
    strategy_a: dict,
    strategy_b: dict,
    top_insights: list[str],
    merge_directives: list[str],
) -> CompareGuidance | None:
    """Call the provider (Haiku-class) for comparison guidance."""
    try:
        system = (
            "You are a prompt engineering expert analyzing two optimization results. "
            "Provide concise, actionable guidance for improving the prompt. "
            "Respond in JSON matching the schema exactly."
        )

        user = (
            f"Situation: {situation}\n"
            f"A scores: {json.dumps(scores_a)}\n"
            f"B scores: {json.dumps(scores_b)}\n"
            f"A framework: {strategy_a.get('framework')}\n"
            f"B framework: {strategy_b.get('framework')}\n"
            f"Insights: {json.dumps(top_insights)}\n"
            f"Directives: {json.dumps(merge_directives)}\n\n"
            "Return JSON with keys: headline (str), merge_suggestion (str), "
            "strengths_a (list[str]), strengths_b (list[str]), "
            "persistent_weaknesses (list[str]), actionable (list[str]), "
            "merge_directives (list[str])."
        )

        schema = CompareGuidance.model_json_schema()
        result = await provider.complete_json(system, user, "claude-haiku-4-5", schema=schema)
        return CompareGuidance.model_validate(result)
    except Exception:
        logger.warning("LLM guidance generation failed", exc_info=True)
        return None
