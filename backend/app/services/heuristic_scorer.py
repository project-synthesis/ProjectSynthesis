"""Heuristic scorer for passthrough bias correction and structural analysis.

Used for MCP passthrough mode where the IDE's LLM self-rates optimized prompts.
Provides lightweight, dependency-free scoring without requiring an LLM call.
"""

from __future__ import annotations

import logging
import re

from app.config import settings

logger = logging.getLogger(__name__)


class HeuristicScorer:
    """Static scoring utilities for passthrough pipeline validation."""

    # ------------------------------------------------------------------
    # Bias correction
    # ------------------------------------------------------------------

    @staticmethod
    def apply_bias_correction(
        scores: dict[str, float],
        factor: float | None = None,
    ) -> dict[str, float]:
        """Apply a discount factor to LLM self-ratings to correct for overconfidence.

        Args:
            scores: Mapping of dimension name → raw LLM score.
            factor: Multiplicative discount. Defaults to ``settings.BIAS_CORRECTION_FACTOR``
                    (0.85 = 15 % discount).

        Returns:
            New dict with each score multiplied by *factor*, clamped to [1.0, 10.0],
            and rounded to 2 decimal places.
        """
        if factor is None:
            factor = settings.BIAS_CORRECTION_FACTOR
        return {
            dim: round(max(1.0, min(10.0, score * factor)), 2)
            for dim, score in scores.items()
        }

    # ------------------------------------------------------------------
    # Structural heuristics
    # ------------------------------------------------------------------

    @staticmethod
    def heuristic_structure(prompt: str) -> float:
        """Score prompt structure based on formatting indicators.

        Scoring:
        - Baseline: 3.0
        - Markdown headers (## / # lines): +2 for ≥2, +1 for exactly 1
        - List items (- / * / numbered): +1.5 for ≥2 items, +0.5 for 1 item
        - XML-style tags (<tag> or </tag>): +1 for ≥2 distinct uses
        - Output format mention (output / format / return / json / schema): +1

        Result is capped at 10.0.
        """
        score = 3.0

        # Headers: lines that start with one or more '#' characters
        headers = re.findall(r"(?m)^#{1,6}\s+\S", prompt)
        n_headers = len(headers)
        if n_headers >= 2:
            score += 2.0
        elif n_headers == 1:
            score += 1.0

        # List items: lines starting with '-', '*', '+', or a digit followed by '.'
        list_items = re.findall(r"(?m)^\s*[-*+]\s+\S|^\s*\d+\.\s+\S", prompt)
        n_items = len(list_items)
        if n_items >= 2:
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

        return min(10.0, score)

    @staticmethod
    def heuristic_conciseness(prompt: str) -> float:
        """Score prompt conciseness via type-token ratio and filler detection.

        Scoring:
        - Base: 5.0
        - Type-Token Ratio (unique words / total words): adjust proportionally
          relative to 0.6 midpoint — higher TTR → higher score
        - Filler phrases: −0.8 per detected filler (cumulative)

        Result is clamped to [1.0, 10.0].
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
            return 5.0

        unique = len(set(words))
        ttr = unique / total  # 0..1

        # Adjust from base 5.0 proportionally: TTR of 0.6 → no adjustment,
        # higher → bonus, lower → penalty (scaled by 5 to give ±range)
        score = 5.0 + (ttr - 0.6) * 5.0

        # Penalise filler phrases
        for pattern in fillers:
            matches = re.findall(pattern, prompt, re.IGNORECASE)
            score -= 0.8 * len(matches)

        return round(max(1.0, min(10.0, score)), 2)

    @staticmethod
    def heuristic_specificity(prompt: str) -> float:
        """Score how specific and constrained the prompt is.

        Constraint indicators (each unique hit adds 1.3 to base 2.0):
        - Modal obligations: must / shall / should
        - Outcome verbs: return / raise / output / yield
        - Type hints (e.g. ``str``, ``int``, ``list``, ``dict``, ``bool``, ``float``)
        - Format keywords: format / schema / json / yaml / xml / csv / markdown
        - Example keywords: for example / e.g. / such as / example:
        - Numeric constraints: any standalone integer or decimal (e.g. "3 items")

        Each *category* is counted once (binary hit). Result is capped at 10.0.
        """
        checks: list[tuple[str, int]] = [
            # (pattern, re_flags)
            (r"\b(?:must|shall|should)\b", re.IGNORECASE),
            (r"\b(?:return|raise|output|yield)\b", re.IGNORECASE),
            (r"\b(?:str|int|float|bool|list|dict|tuple|set)\b", 0),
            (r"\b(?:format|schema|json|yaml|xml|csv|markdown)\b", re.IGNORECASE),
            (r"\bfor example\b|\be\.g\.\b|\bsuch as\b|\bexample:", re.IGNORECASE),
            (r"\b\d+(?:\.\d+)?\b", 0),
        ]

        hits = sum(
            1 for pattern, flags in checks if re.search(pattern, prompt, flags)
        )
        score = 2.0 + hits * 1.3
        return round(min(10.0, score), 2)

    @staticmethod
    def heuristic_clarity(prompt: str) -> float:
        """Clarity via readability score and ambiguity markers."""
        try:
            import textstat
            flesch = textstat.flesch_reading_ease(prompt)
        except Exception:
            logger.debug("textstat unavailable for clarity heuristic — using default Flesch score")
            flesch = 50.0

        # Map Flesch score (0-100) to our scale (1-10)
        # Higher Flesch = easier to read = more clear
        score = 3.0 + (flesch / 100.0) * 5.0

        # Penalize ambiguity markers
        ambiguity = ["maybe", "perhaps", "somehow", "something", "stuff", "things", "etc"]
        hits = sum(1 for w in ambiguity if w in prompt.lower())
        score -= hits * 0.5

        return round(max(1.0, min(10.0, score)), 1)

    @staticmethod
    def heuristic_faithfulness(original: str, optimized: str) -> float:
        """Faithfulness via embedding cosine similarity between original and optimized."""
        try:
            import numpy as np

            from app.services.embedding_service import EmbeddingService
            svc = EmbeddingService()
            orig_vec = svc.embed_single(original)
            opt_vec = svc.embed_single(optimized)
            similarity = float(np.dot(orig_vec, opt_vec) / (np.linalg.norm(orig_vec) * np.linalg.norm(opt_vec) + 1e-9))
            # Map similarity (0-1) to score (1-10). Sweet spot: 0.6-0.85
            if similarity >= 0.85:
                return 9.0
            elif similarity >= 0.6:
                return 6.0 + (similarity - 0.6) / 0.25 * 3.0
            else:
                return max(1.0, similarity * 10.0)
        except Exception:
            logger.debug("Embedding unavailable for faithfulness heuristic — returning neutral score")
            return 5.0  # neutral default if embedding unavailable

    # ------------------------------------------------------------------
    # Divergence detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_divergence(
        llm_scores: dict[str, float],
        heuristic_scores: dict[str, float],
        threshold: float = 2.0,
    ) -> list[str]:
        """Return dimension names where |llm_score − heuristic_score| > threshold.

        Only dimensions present in *both* dicts are compared.

        Args:
            llm_scores: Scores assigned by the LLM.
            heuristic_scores: Scores from the heuristic methods.
            threshold: Absolute difference that triggers a divergence flag.

        Returns:
            Sorted list of dimension names that diverge beyond *threshold*.
        """
        diverged = [
            dim
            for dim in llm_scores
            if dim in heuristic_scores
            and abs(llm_scores[dim] - heuristic_scores[dim]) > threshold
        ]
        if diverged:
            logger.info(
                "Score divergence detected in %d dimension(s): %s (threshold=%.1f)",
                len(diverged), diverged, threshold,
            )
        return sorted(diverged)
