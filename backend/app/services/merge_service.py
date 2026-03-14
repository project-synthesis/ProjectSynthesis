"""Merge service — LLM prompt construction and streaming merge execution.

Builds a comprehensive system prompt from a CompareResponse payload and
streams the merged prompt via the LLM provider.

Used by:
  - ``compare.py`` router (Task 4) calls ``stream_merge()``
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, AsyncGenerator

from app.schemas.compare_models import CompareResponse

if TYPE_CHECKING:
    from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)


def build_merge_system_prompt(compare: CompareResponse) -> str:
    """Build a comprehensive system prompt from the full CompareResponse payload.

    Pure function — no I/O, no side effects. Returns a multi-section prompt
    string with 11 structured intelligence sections, all slot-filled from
    the comparison data.
    """
    sections: list[str] = []

    # ── Preamble ─────────────────────────────────────────────────────────
    sections.append(
        "You are a master-class prompt synthesis engine. You have been given "
        "two optimized prompts (A and B) along with deep analytical intelligence "
        "about their performance characteristics. Your task: produce a single "
        "merged prompt that extracts the maximum value from both parents while "
        "eliminating their weaknesses."
    )

    # ── 1. SITUATION ─────────────────────────────────────────────────────
    scores = compare.scores
    adaptation = compare.adaptation
    similarity_note = (
        f"Overall delta: {scores.overall_delta:+.1f}"
        if scores.overall_delta is not None
        else "Overall delta: N/A"
    )
    modifiers_str = ", ".join(compare.modifiers) if compare.modifiers else "none"
    sections.append(
        f"## SITUATION\n"
        f"Classification: {compare.situation} — {compare.situation_label}\n"
        f"Headline: {compare.insight_headline}\n"
        f"{similarity_note}\n"
        f"Modifiers: {modifiers_str}\n"
        f"Adaptation trajectory: {adaptation.feedbacks_between} feedback(s) between runs"
    )

    # ── 2. SCORE INTELLIGENCE ────────────────────────────────────────────
    dim_rows: list[str] = []
    for dim in scores.dimensions:
        a_val = scores.a_scores.get(dim)
        b_val = scores.b_scores.get(dim)
        delta = scores.deltas.get(dim)
        a_str = f"{a_val:.1f}" if a_val is not None else "—"
        b_str = f"{b_val:.1f}" if b_val is not None else "—"
        delta_str = f"{delta:+.1f}" if delta is not None else "—"
        if delta is not None and delta > 0:
            winner = "A"
            strength = "strong" if abs(delta) >= 1.5 else "moderate" if abs(delta) >= 0.5 else "weak"
        elif delta is not None and delta < 0:
            winner = "B"
            strength = "strong" if abs(delta) >= 1.5 else "moderate" if abs(delta) >= 0.5 else "weak"
        else:
            winner = "tie"
            strength = "—"
        dim_rows.append(f"  {dim:<16} {a_str:>5}  {b_str:>5}  {delta_str:>6}  {winner:<4}  {strength}")

    dim_table = "\n".join(dim_rows)
    winner_str = scores.winner.upper() if scores.winner else "TIE"

    ceilings_str = ", ".join(scores.ceilings) if scores.ceilings else "none"
    floors_str = ", ".join(scores.floors) if scores.floors else "none"

    # Key patterns narrative
    patterns: list[str] = []
    for dim in scores.dimensions:
        delta = scores.deltas.get(dim)
        if delta is not None and abs(delta) >= 1.5:
            side = "A" if delta > 0 else "B"
            patterns.append(f"{dim}: {side} dominates ({delta:+.1f})")
    for dim in scores.dimensions:
        delta = scores.deltas.get(dim)
        if delta is not None and delta == 0.0:
            a_val = scores.a_scores.get(dim)
            patterns.append(f"{dim}: tied at {a_val}")

    patterns_str = "; ".join(patterns) if patterns else "No dominant patterns"

    sections.append(
        f"## SCORE INTELLIGENCE\n"
        f"Overall winner: {winner_str} (delta {similarity_note})\n"
        f"Ceilings (both >= 9): {ceilings_str}\n"
        f"Floors (both < 5): {floors_str}\n\n"
        f"  {'Dimension':<16} {'A':>5}  {'B':>5}  {'Delta':>6}  {'Win':<4}  Signal\n"
        f"  {'-' * 60}\n"
        f"{dim_table}\n\n"
        f"Key patterns: {patterns_str}"
    )

    # ── 3. STRUCTURAL INTELLIGENCE ───────────────────────────────────────
    structural = compare.structural
    # Efficiency conclusion
    if structural.a_expansion < structural.b_expansion:
        efficiency_note = "A is more expansive (higher expansion ratio)"
    elif structural.a_expansion > structural.b_expansion:
        efficiency_note = "B is more expansive (higher expansion ratio)"
    else:
        efficiency_note = "Both have equal expansion ratios"

    sections.append(
        f"## STRUCTURAL INTELLIGENCE\n"
        f"Input words:  A={structural.a_input_words}, B={structural.b_input_words}\n"
        f"Output words: A={structural.a_output_words}, B={structural.b_output_words}\n"
        f"Expansion ratio: A={structural.a_expansion:.1f}x, B={structural.b_expansion:.1f}x\n"
        f"Complexity: A={structural.a_complexity or 'unknown'}, B={structural.b_complexity or 'unknown'}\n"
        f"Efficiency conclusion: {efficiency_note}"
    )

    # ── 4. STRATEGY INTELLIGENCE ─────────────────────────────────────────
    strategy = compare.strategy
    a_guardrails_str = ", ".join(strategy.a_guardrails) if strategy.a_guardrails else "none"
    b_guardrails_str = ", ".join(strategy.b_guardrails) if strategy.b_guardrails else "none"

    cross_fw = ""
    if strategy.a_framework and strategy.b_framework:
        if strategy.a_framework == strategy.b_framework:
            cross_fw = f"Same framework ({strategy.a_framework}) — merge focuses on execution quality"
        else:
            cross_fw = (
                f"Cross-framework ({strategy.a_framework} vs {strategy.b_framework}) — "
                f"merge should combine structural strengths of both"
            )

    sections.append(
        f"## STRATEGY INTELLIGENCE\n"
        f"A: {strategy.a_framework or 'unknown'} (selection: {strategy.a_source or 'unknown'})\n"
        f"  Rationale: {strategy.a_rationale or 'none'}\n"
        f"  Guardrails: {a_guardrails_str}\n"
        f"B: {strategy.b_framework or 'unknown'} (selection: {strategy.b_source or 'unknown'})\n"
        f"  Rationale: {strategy.b_rationale or 'none'}\n"
        f"  Guardrails: {b_guardrails_str}\n"
        f"Cross-framework analysis: {cross_fw}"
    )

    # ── 5. CONTEXT INTELLIGENCE ──────────────────────────────────────────
    ctx = compare.context
    a_repo_str = ctx.a_repo or "none"
    b_repo_str = ctx.b_repo or "none"

    if ctx.a_has_codebase and not ctx.b_has_codebase:
        roi = "A has codebase context; B does not — merge should preserve A's contextual grounding"
    elif ctx.b_has_codebase and not ctx.a_has_codebase:
        roi = "B has codebase context; A does not — merge should preserve B's contextual grounding"
    elif ctx.a_has_codebase and ctx.b_has_codebase:
        roi = "Both have codebase context — merge can leverage full contextual awareness"
    else:
        roi = "Neither has codebase context — merge operates on prompt text alone"

    sections.append(
        f"## CONTEXT INTELLIGENCE\n"
        f"Repo: A={a_repo_str}, B={b_repo_str}\n"
        f"Codebase context: A={'yes' if ctx.a_has_codebase else 'no'}, "
        f"B={'yes' if ctx.b_has_codebase else 'no'}\n"
        f"Instruction count: A={ctx.a_instruction_count}, B={ctx.b_instruction_count}\n"
        f"ROI assessment: {roi}"
    )

    # ── 6. ADAPTATION INTELLIGENCE ───────────────────────────────────────
    weight_shifts_lines: list[str] = []
    for dim, shift in adaptation.weight_shifts.items():
        weight_shifts_lines.append(f"  {dim}: {shift:+.3f}")
    weight_shifts_str = "\n".join(weight_shifts_lines) if weight_shifts_lines else "  none"

    guardrails_added_str = ", ".join(adaptation.guardrails_added) if adaptation.guardrails_added else "none"

    # Trajectory interpretation
    if adaptation.feedbacks_between == 0:
        trajectory = "No feedback between runs — no adaptation signal"
    elif adaptation.feedbacks_between <= 2:
        trajectory = "Light feedback — early adaptation signal"
    else:
        trajectory = "Sustained feedback — established adaptation trajectory"

    sections.append(
        f"## ADAPTATION INTELLIGENCE\n"
        f"Feedbacks between runs: {adaptation.feedbacks_between}\n"
        f"Weight shifts:\n{weight_shifts_str}\n"
        f"Guardrails added: {guardrails_added_str}\n"
        f"Trajectory: {trajectory}"
    )

    # ── 7. EFFICIENCY INTELLIGENCE ───────────────────────────────────────
    eff = compare.efficiency
    a_dur = f"{eff.a_duration_ms}ms" if eff.a_duration_ms is not None else "N/A"
    b_dur = f"{eff.b_duration_ms}ms" if eff.b_duration_ms is not None else "N/A"
    a_tok = str(eff.a_tokens) if eff.a_tokens is not None else "N/A"
    b_tok = str(eff.b_tokens) if eff.b_tokens is not None else "N/A"
    a_cost = f"${eff.a_cost:.4f}" if eff.a_cost is not None else "N/A"
    b_cost = f"${eff.b_cost:.4f}" if eff.b_cost is not None else "N/A"
    a_spt = f"{eff.a_score_per_token:.1f}" if eff.a_score_per_token is not None else "N/A"
    b_spt = f"{eff.b_score_per_token:.1f}" if eff.b_score_per_token is not None else "N/A"

    sections.append(
        f"## EFFICIENCY INTELLIGENCE\n"
        f"Duration: A={a_dur}, B={b_dur}\n"
        f"Tokens: A={a_tok}, B={b_tok}\n"
        f"Cost: A={a_cost}, B={b_cost}\n"
        f"Score/token ratio: A={a_spt}, B={b_spt}"
    )

    # ── 8. VALIDATION INTELLIGENCE ───────────────────────────────────────
    val = compare.validation
    a_issues_str = ", ".join(val.a_issues) if val.a_issues else "none"
    b_issues_str = ", ".join(val.b_issues) if val.b_issues else "none"
    a_changes_str = ", ".join(val.a_changes_made) if val.a_changes_made else "none"
    b_changes_str = ", ".join(val.b_changes_made) if val.b_changes_made else "none"

    # Weaknesses / strengths from full optimization records
    a_weaknesses = compare.a.get("weaknesses", []) or []
    b_weaknesses = compare.b.get("weaknesses", []) or []
    a_strengths = compare.a.get("strengths", []) or []
    b_strengths = compare.b.get("strengths", []) or []
    a_weak_str = ", ".join(a_weaknesses[:5]) if a_weaknesses else "none"
    b_weak_str = ", ".join(b_weaknesses[:5]) if b_weaknesses else "none"
    a_str_str = ", ".join(a_strengths[:5]) if a_strengths else "none"
    b_str_str = ", ".join(b_strengths[:5]) if b_strengths else "none"

    sections.append(
        f"## VALIDATION INTELLIGENCE\n"
        f"A verdict: {val.a_verdict or 'N/A'}\n"
        f"  Issues: {a_issues_str}\n"
        f"  Changes made: {a_changes_str}\n"
        f"  Weaknesses identified: {a_weak_str}\n"
        f"  Strengths identified: {a_str_str}\n"
        f"B verdict: {val.b_verdict or 'N/A'}\n"
        f"  Issues: {b_issues_str}\n"
        f"  Changes made: {b_changes_str}\n"
        f"  Weaknesses identified: {b_weak_str}\n"
        f"  Strengths identified: {b_str_str}"
    )

    # ── 9. MERGE DIRECTIVES ──────────────────────────────────────────────
    directives: list[str] = []
    if compare.guidance and compare.guidance.merge_directives:
        # Order by delta magnitude — pair each directive with the dimension
        # that has the largest remaining delta
        sorted_dims = sorted(
            scores.dimensions,
            key=lambda d: abs(scores.deltas.get(d, 0) or 0),
            reverse=True,
        )
        for i, directive in enumerate(compare.guidance.merge_directives):
            justification_dim = sorted_dims[i] if i < len(sorted_dims) else "general"
            delta_val = scores.deltas.get(justification_dim, 0) or 0
            directives.append(
                f"  {i + 1}. {directive} "
                f"(justified by {justification_dim} delta {delta_val:+.1f})"
            )
    else:
        directives.append("  No specific directives — use score intelligence to guide merge")

    directives_str = "\n".join(directives)
    sections.append(f"## MERGE DIRECTIVES\n{directives_str}")

    # ── 10. DIMENSION TARGETS ────────────────────────────────────────────
    target_lines: list[str] = []
    for dim in scores.dimensions:
        a_val = scores.a_scores.get(dim)
        b_val = scores.b_scores.get(dim)
        delta = scores.deltas.get(dim)

        if a_val is None and b_val is None:
            target_lines.append(f"  {dim}: target N/A")
            continue

        a_safe = a_val if a_val is not None else 0.0
        b_safe = b_val if b_val is not None else 0.0

        if delta is not None and delta == 0.0:
            # Shared weakness or shared ceiling — bump by 0.5
            target = min(a_safe + 0.5, 10.0)
            target_lines.append(f"  {dim}: {target:.1f} (shared level {a_safe:.1f} + 0.5 improvement)")
        else:
            # Winner dimension — target is max(A, B)
            target = max(a_safe, b_safe)
            winner_side = "A" if a_safe >= b_safe else "B"
            target_lines.append(f"  {dim}: {target:.1f} (from {winner_side})")

    targets_str = "\n".join(target_lines)
    sections.append(f"## DIMENSION TARGETS\n{targets_str}")

    # ── 11. CONSTRAINTS ──────────────────────────────────────────────────
    shorter_words = min(structural.a_output_words, structural.b_output_words)
    longer_words = max(structural.a_output_words, structural.b_output_words)

    sections.append(
        f"## CONSTRAINTS\n"
        f"- Output ONLY the merged prompt text. No commentary, no explanations, "
        f"no preamble, no postscript.\n"
        f"- Do not hallucinate information not present in either parent prompt.\n"
        f"- Target the shorter prompt's length range: {shorter_words}–{longer_words} words.\n"
        f"- Preserve all factual content and domain-specific terminology from both parents.\n"
        f"- The merged prompt must be self-contained and immediately usable."
    )

    return "\n\n".join(sections)


async def stream_merge(
    provider: LLMProvider,
    compare: CompareResponse,
    model: str = "auto",
) -> AsyncGenerator[str, None]:
    """Stream a merged prompt from two compared optimizations.

    Builds the system prompt from the CompareResponse, constructs a user
    message with both optimized prompts, and streams the LLM response.

    Args:
        provider: LLM provider instance.
        compare: Full comparison payload.
        model: Model to use. "auto" uses the provider's default.

    Yields:
        Text chunks as they arrive from the LLM.
    """
    system_prompt = build_merge_system_prompt(compare)

    # Build user message with both prompts labeled by framework name
    a_label = compare.strategy.a_framework or "A"
    b_label = compare.strategy.b_framework or "B"
    a_prompt = compare.a.get("optimized_prompt", "")
    b_prompt = compare.b.get("optimized_prompt", "")

    user_message = (
        f"## Prompt A ({a_label})\n\n{a_prompt}\n\n"
        f"## Prompt B ({b_label})\n\n{b_prompt}"
    )

    # Resolve model — "auto" uses the optimize stage's model from MODEL_ROUTING
    # (Opus 4.6 by default) since merge is a quality-critical creative task.
    from app.providers.base import MODEL_ROUTING
    resolved_model = model if model != "auto" else MODEL_ROUTING.get("optimize", "claude-opus-4-6")

    logger.info(
        "Starting merge stream: model=%s, situation=%s",
        resolved_model,
        compare.situation,
    )

    async for chunk in provider.stream(system=system_prompt, user=user_message, model=resolved_model):
        yield chunk
