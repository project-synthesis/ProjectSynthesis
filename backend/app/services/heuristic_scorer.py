"""Heuristic scorer for hybrid scoring and structural analysis.

Used for MCP passthrough mode where the IDE's LLM self-rates optimized prompts.
Provides lightweight, dependency-free scoring without requiring an LLM call.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Technical-prompt conciseness calibration (#10).
# Technical specs that repeat domain vocabulary (``pipeline`` / ``schema``
# / ``service``) have low TTR by construction — that reflects information
# density, not verbosity.  When ``_count_technical_nouns(prompt)`` meets
# the threshold, multiply the raw TTR by the multiplier before the band
# mapping.  Calibration target: a technical prompt and an equivalent-TTR
# prose prompt should score within ~0.3 points; pre-fix delta was 0.
TECHNICAL_CONTEXT_THRESHOLD = 3
TECHNICAL_TTR_MULTIPLIER = 1.15


def _count_technical_nouns(prompt: str) -> int:
    """Count distinct ``_TECHNICAL_NOUNS`` hits in ``prompt``.

    Uses word-boundary matching so ``"backend-api"`` counts both nouns
    and ``"apply"`` does not match ``"api"``.  Lazy-imports the noun set
    from ``task_type_classifier`` to avoid a circular import (scorer is
    imported by every pipeline tier; classifier isn't).
    """
    from app.services.task_type_classifier import _TECHNICAL_NOUNS

    prompt_lower = prompt.lower()
    hits = 0
    for noun in _TECHNICAL_NOUNS:
        # Word-boundary match — avoids "apply" triggering on "api".
        if re.search(rf"\b{re.escape(noun)}\b", prompt_lower):
            hits += 1
            if hits >= TECHNICAL_CONTEXT_THRESHOLD:
                return hits  # short-circuit: only threshold comparison matters
    return hits




class HeuristicScorer:
    """Static scoring utilities for passthrough pipeline validation."""

    # ------------------------------------------------------------------
    # Shared regex patterns (DRY — used by structure, clarity, conciseness)
    # ------------------------------------------------------------------

    _RE_HEADERS = r"(?m)^#{1,6}\s+\S"
    _RE_LIST_ITEMS = r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S"
    _RE_XML_OPEN = r"<([A-Za-z][A-Za-z0-9_-]*)(?:\s[^>]*)?>"
    _RE_XML_CLOSE = r"</([A-Za-z][A-Za-z0-9_-]*)>"
    _RE_XML_ANY = r"</?[A-Za-z][A-Za-z0-9_-]*\s*/?>"
    _RE_FORMAT_MENTION = r"\b(?:output|format|return|json|schema|yaml|xml|markdown)\b"

    @staticmethod
    def _count_structural_signals(prompt: str) -> dict[str, int]:
        """Parse structural formatting signals once for reuse across heuristics."""
        n_headers = len(re.findall(HeuristicScorer._RE_HEADERS, prompt))
        n_list_items = len(re.findall(HeuristicScorer._RE_LIST_ITEMS, prompt))
        # XML section pairs: count tags with matching open/close
        xml_opens = set(re.findall(HeuristicScorer._RE_XML_OPEN, prompt))
        xml_closes = set(re.findall(HeuristicScorer._RE_XML_CLOSE, prompt))
        n_xml_sections = len(xml_opens & xml_closes)
        n_xml_tags = len(re.findall(HeuristicScorer._RE_XML_ANY, prompt))
        has_format = bool(re.search(
            HeuristicScorer._RE_FORMAT_MENTION, prompt, re.IGNORECASE,
        ))
        return {
            "n_headers": n_headers,
            "n_list_items": n_list_items,
            "n_xml_sections": n_xml_sections,
            "n_xml_tags": n_xml_tags,
            "has_format_mention": has_format,
        }

    # ------------------------------------------------------------------
    # Structural heuristics
    # ------------------------------------------------------------------

    @staticmethod
    def heuristic_structure(prompt: str) -> float:
        """Score prompt structure based on formatting indicators.

        Markdown headers and XML section pairs are treated as equivalent
        structural signals.  Both bonuses are additive — prompts using
        both patterns for different purposes get credit for each.
        """
        sig = HeuristicScorer._count_structural_signals(prompt)
        score = 4.0

        # --- Markdown headers ---
        if sig["n_headers"] >= 3:
            score += 2.5
        elif sig["n_headers"] >= 2:
            score += 2.0
        elif sig["n_headers"] == 1:
            score += 1.0

        # --- XML section pairs (paired open/close tags) ---
        if sig["n_xml_sections"] >= 3:
            score += 2.5
        elif sig["n_xml_sections"] >= 2:
            score += 2.0
        elif sig["n_xml_sections"] == 1:
            score += 1.0
        elif sig["n_xml_tags"] >= 2:
            # Unpaired XML tags (e.g., self-closing or data delimiters)
            score += 1.0

        # --- List items ---
        if sig["n_list_items"] >= 4:
            score += 2.0
        elif sig["n_list_items"] >= 2:
            score += 1.5
        elif sig["n_list_items"] == 1:
            score += 0.5

        # --- Output format mention ---
        if sig["has_format_mention"]:
            score += 1.0

        return round(max(1.0, min(10.0, score)), 2)

    @staticmethod
    def heuristic_conciseness(prompt: str) -> float:
        """Score prompt conciseness as information density.

        Instead of penalizing length, measures how much of the content
        contributes useful information. A long, structured prompt with
        high information density scores well.

        Technical-prompt calibration (#10): prompts containing
        ≥``TECHNICAL_CONTEXT_THRESHOLD`` technical nouns from
        ``_TECHNICAL_NOUNS`` (the canonical coding/system/data vocabulary)
        receive a ``TECHNICAL_TTR_MULTIPLIER`` boost on the Type-Token
        Ratio before the score-band mapping.  Rationale: technical specs
        repeat domain terminology ("pipeline", "schema", "service") —
        that's information density, not verbosity.  Without the boost,
        well-structured technical prompts were systematically under-scored
        relative to prose with identical TTR.
        """
        fillers = [
            r"\bplease note that\b",
            r"\bit is (?:very |quite |extremely )?important (?:that|to)\b",
            r"\bmake sure to\b",
            r"\bbasically\b",
            r"\bessentially\b",
            r"\bsort of\b",
            r"\bkind of\b",
            r"\bjust\b",
            r"\bperhaps\b",
            r"\bgenerally\b",
            r"\bas much as possible\b",
            r"\bin a way that\b",
            r"\btry to\b",
        ]

        words = re.findall(r"\b[a-zA-Z']+\b", prompt.lower())
        total = len(words)
        if total == 0:
            return 6.0

        unique = len(set(words))
        ttr = unique / total

        # Technical-prompt calibration.  Count distinct technical nouns;
        # ≥3 hits indicates a technical prompt whose low TTR reflects
        # domain-vocabulary density, not verbosity.
        if _count_technical_nouns(prompt) >= TECHNICAL_CONTEXT_THRESHOLD:
            # Cap at 1.0 so a high-baseline TTR doesn't overflow after
            # multiplication (would otherwise drive score into the 9-10
            # band for the wrong reason).
            ttr = min(1.0, ttr * TECHNICAL_TTR_MULTIPLIER)

        # Base 6.0 + TTR adjustment (0.5 midpoint for long prompts)
        score = 6.0 + (ttr - 0.5) * 4.0

        # Structural density bonus: headers and lists compress information.
        # Well-structured prompts with domain-term repetition shouldn't be
        # penalized by low TTR — structure IS conciseness.  Tiered bonus
        # scales with structural complexity (cap +3.0).
        sig = HeuristicScorer._count_structural_signals(prompt)
        headers = sig["n_headers"]
        lists = sig["n_list_items"]
        has_code = bool(re.search(r"```", prompt))
        struct_bonus = 0.0
        if headers >= 1 or lists >= 2:
            struct_bonus += 1.0  # base: any meaningful structure
        if headers >= 2:
            struct_bonus += 0.5  # multi-section organization
        if headers >= 4:
            struct_bonus += 0.5  # deeply structured
        if lists >= 3:
            struct_bonus += 0.5  # dense list usage
        if has_code and headers >= 1:
            struct_bonus += 0.5  # code + headers = info-dense format
        score += min(struct_bonus, 3.0)

        # Filler penalty
        for pattern in fillers:
            matches = re.findall(pattern, prompt, re.IGNORECASE)
            score -= 0.8 * len(matches)

        # Minimum information gate: short prompts get a ceiling.
        # Brevity without substance is not conciseness.
        if total < 15:
            ceiling = 4.0 + (total / 15) * 4.0
            score = min(score, ceiling)

        return round(max(1.0, min(10.0, score)), 2)

    @staticmethod
    def heuristic_specificity(prompt: str) -> float:
        """Score how specific and constrained the prompt is.

        10 categories with graduated density scoring.  Each first hit in
        a category adds +1.0; additional hits add +0.3 (category cap +2.0).
        Broadened beyond coding patterns to cover creative, analytical,
        and writing prompts.
        """
        categories: list[tuple[str, int, float]] = [
            # (pattern, re_flags, category_cap)
            # 1. Modal obligations
            (r"\b(?:must|shall|should|require[ds]?|ensure[ds]?)\b", re.IGNORECASE, 2.0),
            # 2. Outcome verbs
            (r"\b(?:return|raise|output|yield|produce|generate|include|handle)\b", re.IGNORECASE, 2.0),
            # 3. Type annotations + function signatures
            (r"\b(?:str|int|float|bool|list|dict|tuple|set)\b|->", 0, 2.0),
            # 4. Format keywords
            (r"\b(?:format|schema|json|yaml|xml|csv|markdown|html)\b", re.IGNORECASE, 2.0),
            # 5. Example markers
            (r"\bfor example\b|\be\.g\.\b|\bsuch as\b|\bexamples?\b", re.IGNORECASE, 2.0),
            # 6. Numeric constraints (capped at 1.0 — avoids incidental numbers)
            (r"\b\d+(?:\.\d+)?\b", 0, 1.0),
            # 7. Error/exception types
            (r"\b\w+(?:Error|Exception)\b", 0, 2.0),
            # 8. Exclusion/negation constraints
            (r"\b(?:never|exclude|except|without|avoid)\b|(?:do|must|should)\s+not\b", re.IGNORECASE, 2.0),
            # 9. Temporal/quantity constraints
            (r"\b(?:exactly|at\s+least|at\s+most|no\s+more\s+than|within|maximum|minimum)\b", re.IGNORECASE, 2.0),
            # 10. Audience/tone/style
            (r"\b(?:first\s+person|third\s+person|formal|informal|tone|audience|voice|tense)\b", re.IGNORECASE, 2.0),
        ]

        total = 3.0  # Raised from 2.5 — most optimized prompts are at least somewhat specific
        categories_hit = 0
        for pattern, flags, cap in categories:
            hits = len(re.findall(pattern, prompt, flags))
            if hits > 0:
                categories_hit += 1
                category_score = min(1.0 + 0.3 * (hits - 1), cap)
                total += category_score

        # Density bonus: reward concentrated specificity in shorter prompts.
        # A 50-word prompt hitting 4 categories is MORE specific per-word than
        # a 500-word prompt hitting the same 4. Cap bonus at 1.5.
        word_count = max(1, len(prompt.split()))
        if categories_hit >= 2:
            density = categories_hit / (word_count / 40)
            total += min(1.5, density * 0.5)

        return round(max(1.0, min(10.0, total)), 2)

    @staticmethod
    def heuristic_clarity(prompt: str) -> float:
        """Clarity via precision signals and ambiguity density.

        Measures how unambiguously the prompt communicates intent.
        Structural organization gets a light bonus (capped at +1.5 to
        avoid correlating with the structure dimension).  The core
        differentiator is precision signals and ambiguity penalties.
        """
        score = 5.0

        # --- Organizational clarity (capped +1.5) ---
        sig = HeuristicScorer._count_structural_signals(prompt)
        has_sections = sig["n_headers"] >= 1 or sig["n_xml_sections"] >= 2 or sig["n_list_items"] >= 3
        if has_sections:
            score += 1.0
        if sig["has_format_mention"]:
            score += 0.5

        # --- Precision signals (up to +3.0) ---
        precision_checks: list[tuple[str, int]] = [
            (r"\b(?:must|shall|should)\b", 0),                             # explicit constraints
            (r"(?:->|:\s*(?:str|int|float|bool|list|dict))\b", 0),        # typed parameters
            (r"```|^    \S", re.MULTILINE),                                # code blocks / indented code
            (r"<role>|\byou are\b|^##?\s+role\b", re.IGNORECASE | re.MULTILINE),  # role framing
            (r"\b(?:raise|error|edge\s*case)\b|\bhandle\s+(?:error|exception|failure|edge)",  # error/edge handling
             re.IGNORECASE),
            (r"\b(?:scope|boundar|limitation|constraint)\b", re.IGNORECASE),  # scoping language
        ]
        precision_hits = sum(
            1 for pat, flags in precision_checks if re.search(pat, prompt, flags)
        )
        score += min(precision_hits * 0.5, 3.0)

        # --- Ambiguity penalty (max -3.0) ---
        # Only penalize genuinely vague language, not identifiers or
        # compound terms (e.g. "Maybe-null", "etc_config", "perhaps_valid").
        ambiguity_words = [
            "maybe", "perhaps", "somehow", "something",
            "stuff", "things", "etc", "possibly",
        ]
        ambiguity_hits = 0
        prompt_lower = prompt.lower()
        for word in ambiguity_words:
            # Match standalone words not touching identifiers on either side
            pattern = rf"(?<![_a-zA-Z0-9])\b{word}\b(?![_a-zA-Z0-9-])"
            for m in re.finditer(pattern, prompt_lower):
                ctx_before = prompt_lower[max(0, m.start() - 15):m.start()].rstrip()
                ctx_after = prompt_lower[m.end():m.end() + 20].lstrip()
                # Skip "etc" used as a field/identifier name
                if word == "etc" and (
                    ctx_before.endswith(("the", "an", "a", "its", "my"))
                    or ctx_after.startswith(("field", "config", "value"))
                ):
                    continue
                # Skip "something" immediately clarified ("something useful —
                # specifically", "something like X")
                if word == "something" and re.match(
                    r"\w+\s*[—\-–:]\s*specifically\b|\blike\b", ctx_after
                ):
                    continue
                # Skip "things" in enumeration context ("the following things:")
                if word == "things" and (
                    ctx_before.endswith("following") or ctx_after.startswith(":")
                ):
                    continue
                ambiguity_hits += 1
        score -= min(ambiguity_hits * 0.5, 3.0)

        return round(max(1.0, min(10.0, score)), 2)

    @staticmethod
    def heuristic_faithfulness(
        original: str,
        optimized: str,
    ) -> float:
        """Faithfulness via asymmetrical projection metric.

        If the optimized prompt expands the original (increasing length), the cosine
        similarity organically drops due to added framing/reasoning tokens. This metric
        projects the original vector mathematically into the expanded space using the
        logarithmic length ratio, recovering the true faithfulness without penalizing
        the length increase. Contractions (summaries) fall back to raw cosine, preserving
        penalties for dropped constraints. Returns 5.0 (neutral) if embedding unavailable.
        """
        if not original or not optimized:
            return 5.0
        try:
            import math

            import numpy as np

            from app.services.embedding_service import EmbeddingError, EmbeddingService
            svc = EmbeddingService()
            orig_vec = svc.embed_single(original)
            opt_vec = svc.embed_single(optimized)
            similarity = float(
                np.dot(orig_vec, opt_vec)
                / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9)
            )

            # Asymmetrical inclusion projection
            l1 = max(40, len(original))
            l2 = max(40, len(optimized))
            projection = similarity * (math.log(max(l1, l2)) / math.log(l1))
            projection = min(1.0, projection)

            # Map projection (0-1) to score (1-10).
            # Because expansions are log-boosted back to high projections (~0.85-1.0),
            # this piecewise function organically outputs high faithfulness (8-10) for them.
            if projection >= 0.85:
                score = min(10.0, 9.0 + (projection - 0.85) * 6.67)
            elif projection >= 0.50:
                score = 7.0 + (projection - 0.50) / 0.35 * 2.0
            elif projection >= 0.30:
                score = 4.0 + (projection - 0.30) / 0.20 * 3.0
            else:
                score = max(1.0, projection * 13.3)

            return round(max(1.0, min(10.0, score)), 2)
        except (ImportError, EmbeddingError, ValueError, MemoryError):
            logger.debug("Embedding unavailable for faithfulness heuristic — returning neutral score")
            return 5.0

    # ------------------------------------------------------------------
    # Convenience facade
    # ------------------------------------------------------------------

    @classmethod
    def score_prompt(
        cls,
        prompt: str,
        original: str | None = None,
    ) -> dict[str, float]:
        """Compute all 5 heuristic dimension scores for a prompt.

        Args:
            prompt: The prompt to score.
            original: If provided, used for faithfulness comparison.
                      If None, faithfulness defaults to 5.0 (self-baseline).

        Returns:
            Dict with keys: clarity, specificity, structure, faithfulness, conciseness.
        """
        return {
            "clarity": cls.heuristic_clarity(prompt),
            "specificity": cls.heuristic_specificity(prompt),
            "structure": cls.heuristic_structure(prompt),
            "faithfulness": (
                cls.heuristic_faithfulness(original, prompt)
                if original
                else 5.0
            ),
            "conciseness": cls.heuristic_conciseness(prompt),
        }

