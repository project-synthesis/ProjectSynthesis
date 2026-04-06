"""Heuristic scorer for hybrid scoring and structural analysis.

Used for MCP passthrough mode where the IDE's LLM self-rates optimized prompts.
Provides lightweight, dependency-free scoring without requiring an LLM call.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


class HeuristicScorer:
    """Static scoring utilities for passthrough pipeline validation."""

    # ------------------------------------------------------------------
    # Structural heuristics
    # ------------------------------------------------------------------

    @staticmethod
    def heuristic_structure(prompt: str) -> float:
        """Score prompt structure based on formatting indicators.

        Scoring:
        - Baseline: 4.0
        - Markdown headers (## / # lines): +2.5 for ≥3, +2.0 for ≥2, +1.0 for exactly 1
        - List items (- / * / numbered): +2.0 for ≥4 items, +1.5 for ≥2 items, +0.5 for 1 item
        - XML-style tags (<tag> or </tag>): +1 for ≥2 distinct uses
        - Output format mention (output / format / return / json / schema): +1

        Result is capped at 10.0.
        """
        score = 4.0

        # Headers: lines that start with one or more '#' characters
        headers = re.findall(r"(?m)^#{1,6}\s+\S", prompt)
        n_headers = len(headers)
        if n_headers >= 3:
            score += 2.5
        elif n_headers >= 2:
            score += 2.0
        elif n_headers == 1:
            score += 1.0

        # List items: lines starting with '-', '*', '+', or a digit followed by '.'
        list_items = re.findall(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S", prompt)
        n_items = len(list_items)
        if n_items >= 4:
            score += 2.0
        elif n_items >= 2:
            score += 1.5
        elif n_items == 1:
            score += 0.5

        # XML-style tags
        xml_tags = re.findall(r"</?[A-Za-z][A-Za-z0-9_-]*\s*/?>", prompt)
        if len(xml_tags) >= 2:
            score += 1.0

        # Output format mention
        if re.search(
            r"\b(?:output|format|return|json|schema|yaml|xml|markdown)\b",
            prompt,
            re.IGNORECASE,
        ):
            score += 1.0

        return round(max(1.0, min(10.0, score)), 2)

    @staticmethod
    def heuristic_conciseness(prompt: str) -> float:
        """Score prompt conciseness as information density.

        Instead of penalizing length, measures how much of the content
        contributes useful information. A long, structured prompt with
        high information density scores well.
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

        # Base 6.0 + TTR adjustment (0.5 midpoint for long prompts)
        score = 6.0 + (ttr - 0.5) * 4.0

        # Structural density bonus: headers and lists compress information.
        # Well-structured prompts with domain-term repetition shouldn't be
        # penalized by low TTR — structure IS conciseness.
        headers = len(re.findall(r"(?m)^#{1,6}\s+\S", prompt))
        lists = len(re.findall(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S", prompt))
        if headers >= 2 and lists >= 3:
            score += 1.5
        elif headers >= 2 and lists >= 2:
            score += 1.0
        elif headers >= 1 and lists >= 2:
            score += 0.5

        # Filler penalty
        for pattern in fillers:
            matches = re.findall(pattern, prompt, re.IGNORECASE)
            score -= 0.8 * len(matches)

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

        total = 2.5
        for pattern, flags, cap in categories:
            hits = len(re.findall(pattern, prompt, flags))
            if hits > 0:
                category_score = min(1.0 + 0.3 * (hits - 1), cap)
                total += category_score

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
        has_headers = bool(re.search(r"(?m)^#{1,6}\s+\S", prompt))
        xml_opens = set(re.findall(r"<([A-Za-z][A-Za-z0-9_-]*)(?:\s[^>]*)?>", prompt))
        xml_closes = set(re.findall(r"</([A-Za-z][A-Za-z0-9_-]*)>", prompt))
        xml_pairs = len(xml_opens & xml_closes)
        list_items = len(re.findall(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S", prompt))
        has_sections = has_headers or xml_pairs >= 2 or list_items >= 3
        if has_sections:
            score += 1.0
        if re.search(
            r"\b(?:output|format|return|json|schema|yaml|xml|markdown)\b",
            prompt, re.IGNORECASE,
        ):
            score += 0.5

        # --- Precision signals (up to +3.0) ---
        precision_checks: list[tuple[str, int]] = [
            (r"\b(?:must|shall|should)\b", 0),                             # explicit constraints
            (r"(?:->|:\s*(?:str|int|float|bool|list|dict))\b", 0),        # typed parameters
            (r"```|^    \S", re.MULTILINE),                                # code blocks / indented code
            (r"<role>|\byou are\b|^##?\s+role\b", re.IGNORECASE | re.MULTILINE),  # role framing
            (r"\b(?:raise|error|edge\s*case|handle)\b", re.IGNORECASE),   # error/edge handling
            (r"\b(?:scope|boundar|limitation|constraint)\b", re.IGNORECASE),  # scoping language
        ]
        precision_hits = sum(
            1 for pat, flags in precision_checks if re.search(pat, prompt, flags)
        )
        score += min(precision_hits * 0.5, 3.0)

        # --- Ambiguity penalty (max -3.0) ---
        ambiguity_words = [
            "maybe", "perhaps", "somehow", "something",
            "stuff", "things", "etc", "possibly",
        ]
        ambiguity_hits = 0
        for word in ambiguity_words:
            pattern = rf"\b{word}\b(?![_a-zA-Z0-9])"
            matches = re.findall(pattern, prompt, re.IGNORECASE)
            for m in matches:
                start = prompt.lower().find(m.lower())
                if start > 0 and prompt[start - 1] == "_":
                    continue
                ambiguity_hits += 1
        score -= min(ambiguity_hits * 0.5, 3.0)

        return round(max(1.0, min(10.0, score)), 2)

    @staticmethod
    def heuristic_faithfulness(original: str, optimized: str) -> float:
        """Faithfulness via embedding cosine similarity between original and optimized.

        Returns 5.0 (neutral) if embedding is unavailable or inputs are invalid.
        """
        if not original or not optimized:
            return 5.0
        try:
            import numpy as np

            from app.services.embedding_service import EmbeddingError, EmbeddingService
            svc = EmbeddingService()
            orig_vec = svc.embed_single(original)
            opt_vec = svc.embed_single(optimized)
            similarity = float(
                np.dot(orig_vec, opt_vec)
                / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9)
            )
            # Map similarity (0-1) to score (1-10). Sweet spot: 0.6-0.85
            if similarity >= 0.85:
                return 9.0
            elif similarity >= 0.6:
                return 6.0 + (similarity - 0.6) / 0.25 * 3.0
            else:
                return max(1.0, similarity * 10.0)
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

